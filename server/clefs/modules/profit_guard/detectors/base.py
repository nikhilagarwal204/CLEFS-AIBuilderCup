"""Shared detector plumbing (02 spec 3.3): the run context, the scope a run
covers, the FindingDraft every detector returns, the comparison tolerance,
fingerprints and the dispute deadlines.

Every detector is `run(ctx, scope) -> list[FindingDraft]`. A draft is pure
data: storage, confidence and signalling happen once, in store.py, so a
detector never writes and can run inside the public audit's sandbox too.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock
from clefs.core import time as htime

# ─── Tolerance and rounding ───

TOL_FLOOR_MINOR = 100            # 100 fils (1.00 in any 2-decimal currency)
TOL_PCT = Decimal("0.002")       # 0.2 %


def tol(amount_minor: int | None) -> int:
    """02 spec 3.3: tol = max(100 fils, 0.2% x amount), in minor units."""
    a = abs(int(amount_minor or 0))
    return max(TOL_FLOOR_MINOR, int((Decimal(a) * TOL_PCT).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def rnd(x: Decimal | int | float) -> int:
    """Round once, half up, to minor units."""
    return int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pct_amount(amount_minor: int, pct: Decimal) -> int:
    """pct percent (15 = 15%) of an amount in minor units."""
    return rnd(Decimal(amount_minor) * Decimal(pct) / 100)


def month_key(d: date | datetime | None) -> str:
    if d is None:
        return "undated"
    return f"{d.year:04d}-{d.month:02d}"


# ─── Fingerprint ───


def fingerprint(detector: str, ota: str | None, res_norm: str | None, doc: str | None) -> str:
    """sha1(detector|ota|ota_res_id_norm|statement doc_no or month): a
    re-run or a re-import updates the finding, never duplicates it."""
    raw = "|".join([detector, ota or "", res_norm or "", doc or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ─── Scope and context ───

RUN_KINDS = ("commit", "nightly", "deadline", "manual", "audit")


@dataclass
class Scope:
    """What one run covers. `batch_id` limits statement, payout, card and
    bank reads to one committed batch; otherwise the window [since, until]
    applies (a statement's doc date or a stay's check-out)."""

    kind: str = "manual"
    batch_id: int | None = None
    ota_code: str | None = None
    since: date | None = None
    until: date | None = None

    @property
    def full_scan(self) -> bool:
        """A run that saw every candidate may close what it no longer sees."""
        return (self.batch_id is None and self.ota_code is None and self.since is None
                and self.kind in ("nightly", "deadline", "manual"))


@dataclass
class Ctx:
    cur: Any
    hotel: Any
    now: datetime
    today: date
    policies: dict
    run_id: str
    # Signals that are not findings (L5's possible guest overcharge, a
    # SERVICE_RISK): store.py raises them after the findings.
    side_signals: list[dict] = field(default_factory=list)
    cache: dict = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.hotel.slug

    @property
    def currency(self) -> str:
        return self.hotel.currency


def make_ctx(cur: Any, hotel: Any, policies: dict, run_id: str) -> Ctx:
    now = clock.now()
    return Ctx(cur=cur, hotel=hotel, now=now, today=htime.local_date(hotel, now), policies=policies, run_id=run_id)


# ─── The draft ───


@dataclass
class FindingDraft:
    detector: str
    klass: str                       # RECOVERABLE | PREVENTABLE | STRUCTURAL | COMPLIANCE
    amount_minor: int
    currency: str
    base_confidence: Decimal
    ota_code: str | None = None
    res_norm: str | None = None      # ota_res_id_norm, or the item (card id, comparable key)
    doc: str | None = None           # statement doc_no, payout ref or YYYY-MM: the fingerprint's last part
    group: str | None = None         # signal group ("doc:<no>" or "month:<YYYY-MM>")
    match_score: Decimal = Decimal(1)
    missing: list[str] = field(default_factory=list)   # optional inputs that were absent
    calc: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    deadline_at: datetime | None = None
    reservation_id: int | None = None
    statement_line_id: int | None = None
    signal: bool = True              # False: stored, never signalled (STRUCTURAL, Visibility's parity rows)
    urgency_days: int | None = None  # days to the deadline for L8/L14 (<= 1: one signal per item)
    severity: int | None = None
    note: str | None = None
    # Commission credit already netted off amount_minor (data.credited). It
    # becomes the finding's credit baseline: the ledger counts only credit
    # above it as recovery, so one credit is never netted and recovered.
    credited_minor: int = 0
    # Booking-level netting (review merge-double-nets): a detector that nets
    # the booking's credit (and L3/L4's fee share) per LINE sets gross_minor
    # to the line's own claim and deduct_minor to the booking-level
    # deduction other than credit. net_once() then adds up a booking's lines
    # of one document and subtracts the deduction and the credit ONCE (a
    # two-line booking used to net its credit twice and baseline 2 x C).
    # floor_base_minor: what the final tolerance is taken on (0: drop at or
    # below zero only).
    gross_minor: int | None = None
    deduct_minor: int = 0
    floor_base_minor: int = 0

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.detector, self.ota_code, self.res_norm, self.doc)

    @property
    def subject_ref(self) -> str:
        """"<group>|<item>": the signal group is read back from storage by
        this prefix, and the item names the booking in dispute packets."""
        return f"{self.group or 'item'}|{self.res_norm or ''}"


def net_once(drafts: list[FindingDraft]) -> list[FindingDraft]:
    """Group drafts carrying gross_minor by fingerprint (one booking in one
    document), sum their line claims and subtract the booking deduction
    and the booking credit once each (max over the lines: they are the
    same booking-level figure). Drops a booking whose net is at or below
    its floor. Drafts without gross_minor pass through untouched."""
    buckets: list[list[FindingDraft]] = []
    by: dict[str, list[FindingDraft]] = {}
    for d in drafts:
        if d.gross_minor is None:
            buckets.append([d])
            continue
        if d.fingerprint not in by:
            by[d.fingerprint] = []
            buckets.append(by[d.fingerprint])
        by[d.fingerprint].append(d)
    out: list[FindingDraft] = []
    for same in buckets:
        first = same[0]
        if first.gross_minor is None:
            out.append(first)
            continue
        gross = sum(int(d.gross_minor or 0) for d in same)
        deduct = max(int(d.deduct_minor) for d in same)
        cred = max(int(d.credited_minor) for d in same)
        amount = gross - deduct - cred
        floor_base = sum(int(d.floor_base_minor) for d in same)
        if amount <= (tol(floor_base) if floor_base else 0):
            continue
        if len(same) > 1:
            first.calc = list(first.calc) + [step(
                "Lines combined", "Σ line claims - booking deduction - already credited (each once)",
                {"lines": len(same), "gross_minor": gross, "deduct_minor": deduct, "credited_minor": cred}, amount)]
            first.evidence = [e for d in same for e in d.evidence]
            first.match_score = min(d.match_score for d in same)
            first.missing = sorted({m for d in same for m in d.missing})
            deadlines = [d.deadline_at for d in same if d.deadline_at is not None]
            first.deadline_at = min(deadlines) if deadlines else None
        first.amount_minor, first.credited_minor, first.gross_minor = amount, cred, gross
        out.append(first)
    return out


def step(label: str, formula: str, inputs: dict | None = None, result_minor: int | None = None,
         result: Any = None) -> dict:
    """One CalcStep (models.CalcStep shape), JSON-safe."""
    clean = {k: (str(v) if isinstance(v, (Decimal, date, datetime)) else v) for k, v in (inputs or {}).items()}
    out: dict[str, Any] = {"label": label, "formula": formula, "inputs": clean, "result_minor": result_minor}
    if result is not None:
        out["result"] = str(result)
    return out


# ─── Deadlines (hotel-local instants, stored as TIMESTAMPTZ) ───


def local_instant(hotel: Any, d: date, hhmm: str = "00:00") -> datetime:
    """`d` at `hhmm` in the hotel's timezone (aware)."""
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(d, time(h, m), tzinfo=hotel.tz)


def end_of_local_day(hotel: Any, d: date) -> datetime:
    """The instant `d` ends locally (the next day's 00:00): a card whose
    charge deadline is today can still be charged all day."""
    return local_instant(hotel, d + timedelta(days=1))


def add_business_days(hotel: Any, d: date, n: int) -> date:
    """n working days after d, skipping the hotel's weekend (UAE Sat-Sun)."""
    out, left = d, n
    while left > 0:
        out += timedelta(days=1)
        if not htime.is_weekend(hotel, out):
            left -= 1
    return out


def next_month_day(d: date, day: int) -> date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, day)


def dispute_deadline(hotel: Any, terms: dict | None, *, check_out: date | None, doc_date: date | None,
                     collect_model: str | None = None) -> tuple[datetime | None, str | None]:
    """The dispute window from the contract's dispute_rules (first rule
    that has its input): 48 h after check-out 00:00 local (Booking), N
    business days after the invoice (Agoda), the 4th of the next month at
    12:00 local (Expedia Hotel Collect). (None, None) when nothing applies."""
    for rule in (terms or {}).get("dispute_rules") or []:
        kind = str(rule.get("kind") or "").upper()
        if kind == "HOURS_AFTER_CHECKOUT" and check_out is not None:
            hours = int(rule.get("hours") or rule.get("h") or 48)
            return local_instant(hotel, check_out) + timedelta(hours=hours), f"{kind}:{hours}"
        if kind == "BUSINESS_DAYS_AFTER_INVOICE" and doc_date is not None:
            days = int(rule.get("days") or rule.get("d") or 14)
            return end_of_local_day(hotel, add_business_days(hotel, doc_date, days)), f"{kind}:{days}"
        if kind == "DAY_OF_NEXT_MONTH" and check_out is not None:
            applies = str(rule.get("applies_to") or "")
            if applies == "hotel_collect" and collect_model not in (None, "HOTEL_COLLECT"):
                continue
            day = int(rule.get("day") or 4)
            return local_instant(hotel, next_month_day(check_out, day), "12:00"), f"{kind}:{day}"
    return None, None


def days_left(ctx: Ctx, deadline: datetime | None) -> int | None:
    """Whole days from now to the deadline, rounded up (0 = due today)."""
    if deadline is None:
        return None
    secs = (deadline - ctx.now).total_seconds()
    return int(-(-secs // 86400)) if secs > 0 else int(secs // 86400)


def row_ev(label: str, table: str, row_id: Any, **extra: Any) -> dict:
    """An evidence entry pointing at one stored row."""
    return {"kind": "row", "label": label, "ref": {"table": table, "id": row_id, **extra}}
