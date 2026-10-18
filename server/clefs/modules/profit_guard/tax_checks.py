"""Tourism Dirham and fee compliance (02 spec 3.3 T1-T7, TX1; Canon: the
td_ledger and the TD detectors live in Profit Guard, the rules in
clefs.core.tax).

Per hotel-local month, from the PMS folio (folio_charges) against the rules
in force each night (clefs.core.tax.load_context):

| ID | Flags | Amount |
|---|---|---|
| T1 | fewer TD units posted than liable (charged per reservation, not per room) | missing units x rate |
| T2 | TD posted on continuous-stay night 31+ (the chain, back-to-back included) | the over-collection |
| T3 | a posting whose per-unit amount is not the rule's rate for the category | difference x units |
| T4 | VAT charged on the TD (VAT ~ v x (base + TD) instead of v x base) | v x TD |
| T5 | more TD units posted for a night than liable (duplicates) | the duplicate amount (a guest refund) |
| T6 | TD posted on a NO_SHOW (a Hotel Room Night starts at check-in) | the amount |
| T7 | td_ledger: collected != remitted (+- AED 1), or unremitted with 5 days or less to the due date | the gap |
| TX1 | TD or MF posted inside an exemption window the owner applied | the amount |

A posting whose amount is an exact multiple of the rate counts as that many
units (one line for a two-room booking is normal); only a non-multiple is a
rate error (T3). Every finding is COMPLIANCE except T5 (RECOVERABLE, owed
to the guest), grouped per (check, month) into one signal, and never in
the money totals. `pfg_td_ledger` holds, per month, the liability from
core.tax.td_month, what the folio collected and what was remitted.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock, money, tax
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.profit_guard import signals as pg_signals
from clefs.modules.profit_guard.detectors.base import FindingDraft, fingerprint, make_ctx, step

DETECTORS = pg_signals.TAX_DETECTORS
UNVERIFIED = "ANNOUNCED_UNVERIFIED"
LEDGER_TOL_MINOR = 100            # T7: collected vs remitted within AED 1
DUE_SOON_DAYS = 5                 # T7: unremitted with the due date this close
VAT_TOL_MINOR = 2                 # T4: rounding slack per reservation-month
BASE = {"T1": "0.9", "T2": "0.9", "T3": "0.9", "T4": "0.85", "T5": "0.95", "T6": "0.95", "T7": "0.9", "TX1": "0.9"}
FEE_CODES = ("ROOM", "SC", "MF", "TF", "VAT", "TD")


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def mkey(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def rnd(x: Decimal) -> int:
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ─── Data ───


@dataclass
class Night:
    """One (reservation, stay date): its units and the folio postings."""

    reservation_id: int
    stay_date: date
    status: str
    stayed: bool
    liable_units: int = 0
    capped_units: int = 0
    postings: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))

    def posted(self, code: str) -> int:
        return sum(p["signed"] for p in self.postings.get(code, []))


_NIGHTS_SQL = """
    WITH chains AS (
        SELECT DISTINCT COALESCE(r.stay_chain_id, r.id) AS chain_id
          FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
         WHERE n.hotel_slug = %(slug)s AND n.stay_date BETWEEN %(start)s AND %(end)s
    )
    SELECT r.id AS reservation_id, COALESCE(r.stay_chain_id, r.id) AS chain_id, r.status, n.room_seq, n.stay_date,
           n.rooms, n.stayed, GREATEST(COALESCE(hr.bedrooms, 1), COALESCE(rt.bedrooms, 1)) AS bedrooms
      FROM reservation_nights n
      JOIN reservations r ON r.id = n.reservation_id
      JOIN chains c ON c.chain_id = COALESCE(r.stay_chain_id, r.id)
      LEFT JOIN hotel_rooms hr ON hr.hotel_slug = n.hotel_slug AND hr.room_number = n.room_number
      LEFT JOIN room_types rt ON rt.hotel_slug = n.hotel_slug AND rt.code = COALESCE(n.room_type_code, r.room_type_code)
     WHERE n.hotel_slug = %(slug)s AND n.stay_date <= %(end)s
     ORDER BY chain_id, n.room_seq, n.stay_date
"""


class TaxDays:
    """The rules in force per night, loaded once per run."""

    def __init__(self, cur: Any, hotel_slug: str, start: date, end: date) -> None:
        self.ctx = tax.load_context(cur, hotel_slug, None, end)
        self._p: dict[date, Any] = {}

    def profile(self, d: date) -> Any:
        if d not in self._p:
            self._p[d] = self.ctx.profile(d)
        return self._p[d]

    def td_rate_minor(self, d: date) -> int:
        p = self.profile(d)
        return money.to_minor(money.quantize(p.td_rate, p.currency), p.currency)

    def td_exempt(self, d: date) -> bool:
        """The owner applied an announced exemption (rate 0) for this night."""
        p = self.profile(d)
        return p.td_rule_status == UNVERIFIED and p.td_rate == 0

    def mf_exempt(self, d: date) -> bool:
        mf = self.profile(d).fee("MF")
        return mf is not None and mf.rule_status == UNVERIFIED and mf.rate == 0


def load_nights(cur: Any, hotel_slug: str, start: date, end: date, days: TaxDays) -> dict[tuple[int, date], Night]:
    """Every reservation night in [start, end] with liable and capped TD
    units (the night index follows the stay chain per room line, as
    core.tax.td_liability does) and its folio postings by code."""
    cur.execute(_NIGHTS_SQL, {"slug": hotel_slug, "start": start, "end": end})
    rows = [dict(r) for r in cur.fetchall()]
    lines: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in rows:
        if r["stayed"] and r["status"] not in ("CANCELLED", "NO_SHOW"):
            lines[(r["chain_id"], r["room_seq"])].append(r)
    index: dict[tuple[int, int, date], int] = {}
    for key, ns in lines.items():
        for d, i in tax.td_night_indexes(n["stay_date"] for n in ns).items():
            index[(key[0], key[1], d)] = i
    out: dict[tuple[int, date], Night] = {}
    for r in rows:
        d = r["stay_date"]
        if not start <= d <= end:
            continue
        n = out.setdefault((r["reservation_id"], d), Night(r["reservation_id"], d, r["status"], bool(r["stayed"])))
        n.stayed = n.stayed or bool(r["stayed"])
        if not r["stayed"] or r["status"] in ("CANCELLED", "NO_SHOW"):
            continue
        p = days.profile(d)
        units = int(r["rooms"]) * (max(int(r["bedrooms"] or 1), 1) if p.td_per_bedroom else 1)
        i = index.get((r["chain_id"], r["room_seq"], d), 1)
        if p.td_cap_nights is not None and i > p.td_cap_nights:
            n.capped_units += units
        else:
            n.liable_units += units
    res_ids = sorted({k[0] for k in out})
    if res_ids:
        cur.execute(
            "SELECT id, reservation_id, COALESCE(stay_date, posted_date) AS d, code, amount_minor, qty, is_reversal "
            "FROM folio_charges WHERE hotel_slug = %s AND reservation_id = ANY(%s) AND code = ANY(%s) "
            "AND COALESCE(stay_date, posted_date) BETWEEN %s AND %s ORDER BY id",
            (hotel_slug, res_ids, list(FEE_CODES), start, end),
        )
        for p in cur.fetchall():
            n = out.get((p["reservation_id"], p["d"]))
            if n is None:
                continue
            amt = int(p["amount_minor"])
            signed = -abs(amt) if p["is_reversal"] else amt
            n.postings[p["code"]].append({"id": p["id"], "signed": signed, "qty": Decimal(str(p["qty"] or 1))})
    return out


# ─── Checks per month (T1-T6, TX1) ───


@dataclass
class Acc:
    """One reservation's running total for one check."""

    amount: int = 0
    nights: list[str] = field(default_factory=list)
    postings: list[int] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)


def _count_units(n: Night, rate: int) -> tuple[int, int]:
    """(units posted, T3 rate difference in minor units). An amount that is
    an exact multiple of the rate counts as that many units; any other
    amount counts its qty at the wrong rate. Reversals subtract."""
    units, diff = Decimal(0), Decimal(0)
    for p in n.postings.get("TD", []):
        amt = p["signed"]
        if amt % rate == 0:
            units += Decimal(amt // rate)
        else:
            q = p["qty"] if p["qty"] > 0 else Decimal(1)
            sign = 1 if amt > 0 else -1
            units += q * sign
            diff += Decimal(amt) - Decimal(rate) * q * sign
    return int(units), rnd(diff)


def _vat_base(days: TaxDays, n: Night) -> tuple[Decimal, int]:
    """(VAT rate, the night's posted amount in the VAT base) under the
    hotel's convention in force that night (TD is never in the base).
    The profile's fees carry the RULE bases as seeded ({ROOM, SC, MF});
    the convention decides the real one (C: VAT on ROOM only), so it is
    resolved here, or a convention C hotel's VAT-on-TD never matched
    (review money-correctness-t4-ignores-convention)."""
    p = days.profile(n.stay_date)
    vat = next((f for f in tax.apply_convention(p.fees, p.convention) if f.code == "VAT"), None)
    if vat is None:
        return Decimal(0), 0
    return vat.rate, sum(n.posted(code) for code in vat.base if code != "TD")


def check_reservation(days: TaxDays, ns: list[Night]) -> dict[str, Acc]:
    """T1-T6 and TX1 for one reservation's nights in one month."""
    acc: dict[str, Acc] = defaultdict(Acc)
    if ns[0].status == "CANCELLED":
        return acc
    if ns[0].status == "NO_SHOW":
        td = sum(n.posted("TD") for n in ns)
        if td > 0:
            a = acc["T6"]
            a.amount = td
            a.nights = [n.stay_date.isoformat() for n in ns if n.posted("TD")]
            a.postings = [p["id"] for n in ns for p in n.postings.get("TD", [])]
            a.steps.append(step("TD posted on a no-show", "Σ TD postings (no night started)", {"nights": len(ns)}, td))
        return acc
    vat_posted, vat_exp, vat_td, nights_seen = 0, Decimal(0), Decimal(0), 0
    for n in sorted(ns, key=lambda x: x.stay_date):
        if not n.stayed:
            continue
        d, td = n.stay_date, n.posted("TD")
        ids = [p["id"] for p in n.postings.get("TD", [])]
        mf = n.posted("MF")
        if days.mf_exempt(d) and mf > 0:
            _add(acc["TX1"], mf, d, [p["id"] for p in n.postings["MF"]], "MF posted in the applied exemption")
        if days.td_exempt(d):
            if td > 0:
                _add(acc["TX1"], td, d, ids, "TD posted in the applied exemption")
            continue
        rate = days.td_rate_minor(d)
        if rate > 0 and (td or n.liable_units or n.capped_units):
            posted, diff = _count_units(n, rate)
            if diff:
                _add(acc["T3"], abs(diff), d, ids, f"posted rate differs from {rate} per unit by {diff}")
            if posted > n.liable_units:
                extra = posted - n.liable_units
                over_cap = min(extra, n.capped_units)
                if over_cap:
                    _add(acc["T2"], over_cap * rate, d, ids, f"{over_cap} unit(s) past night 30")
                if extra - over_cap:
                    _add(acc["T5"], (extra - over_cap) * rate, d, ids,
                         f"{posted} unit(s) posted, {n.liable_units} liable")
            elif posted < n.liable_units:
                _add(acc["T1"], (n.liable_units - posted) * rate, d, ids,
                     f"{posted} unit(s) posted, {n.liable_units} liable")
        v, base = _vat_base(days, n)
        vat_posted += n.posted("VAT")
        vat_exp += v * base
        vat_td += v * max(td, 0)
        nights_seen += 1
    tol = VAT_TOL_MINOR + nights_seen
    if vat_td > 0 and vat_posted > 0 and abs(vat_posted - (vat_exp + vat_td)) <= tol and abs(vat_posted - vat_exp) > tol:
        a = acc["T4"]
        a.amount = rnd(vat_td)
        a.steps.append(step("VAT on the Tourism Dirham", "VAT posted ~ v x (base + TD), expected v x base",
                            {"vat_posted": vat_posted, "expected": rnd(vat_exp), "with_td": rnd(vat_exp + vat_td)},
                            a.amount))
    return acc


def _add(a: Acc, amount: int, d: date, postings: list[int], note: str) -> None:
    a.amount += int(amount)
    a.nights.append(d.isoformat())
    a.postings += postings
    if len(a.steps) < 40:
        a.steps.append(step(f"Night {d.isoformat()}", note, {}, int(amount)))


KLASS = {"T5": "RECOVERABLE"}


def month_drafts(ctx: Any, month: date, *, days: TaxDays | None = None) -> list[FindingDraft]:
    """T1-T6 and TX1 for one hotel-local month (drafts only, nothing stored)."""
    from clefs.modules.profit_guard.detectors.base import end_of_local_day
    from clefs.modules.profit_guard.detectors.l15_vat_reverse_charge import vat_return_due

    start, end = month_start(month), month_end(month)
    days = days or TaxDays(ctx.cur, ctx.slug, start, end)
    nights = load_nights(ctx.cur, ctx.slug, start, end, days)
    by_res: dict[int, list[Night]] = defaultdict(list)
    for (rid, _d), n in nights.items():
        by_res[rid].append(n)
    if not by_res:
        return []
    ctx.cur.execute("SELECT id, pms_res_id, check_in, check_out, status, rooms_count, channel_code FROM reservations "
                    "WHERE id = ANY(%s)", (sorted(by_res),))
    info = {r["id"]: dict(r) for r in ctx.cur.fetchall()}
    remit = tax.td_remittance_due_date(ctx.cur, ctx.slug, start)
    deadlines = {"T1": end_of_local_day(ctx.hotel, remit), "T7": end_of_local_day(ctx.hotel, remit),
                 "T4": end_of_local_day(ctx.hotel, vat_return_due(start))}
    out: list[FindingDraft] = []
    for rid in sorted(by_res):
        for det, a in sorted(check_reservation(days, by_res[rid]).items()):
            if a.amount <= 0:
                continue
            r = info.get(rid) or {}
            out.append(FindingDraft(
                detector=det, klass=KLASS.get(det, "COMPLIANCE"), amount_minor=a.amount, currency=ctx.currency,
                base_confidence=Decimal(BASE[det]), ota_code=None, res_norm=f"res:{rid}", doc=mkey(start),
                group=f"month:{mkey(start)}", reservation_id=rid, deadline_at=deadlines.get(det),
                calc=a.steps + [step("Total", "Σ nights", {"nights": len(a.nights)}, a.amount)],
                evidence=[{"kind": "row", "label": f"Reservation {r.get('pms_res_id') or rid}",
                           "ref": {"table": "reservations", "id": rid, "check_in": str(r.get("check_in")),
                                   "check_out": str(r.get("check_out")), "rooms": r.get("rooms_count")}}]
                + [{"kind": "row", "label": f"Folio posting {pid}", "ref": {"table": "folio_charges", "id": pid}}
                   for pid in sorted(set(a.postings))[:40]],
            ))
    return out


# ─── T7 and the ledger ───

_COLLECTED_SQL = """
    SELECT COALESCE(SUM(CASE WHEN is_reversal THEN -ABS(amount_minor) ELSE amount_minor END), 0) AS s
      FROM folio_charges f
     WHERE f.hotel_slug = %s AND f.code = 'TD' AND COALESCE(f.stay_date, f.posted_date) BETWEEN %s AND %s
"""


def ledger_status(collected: int, remitted: int, remitted_on: date | None) -> str:
    if remitted_on is None:
        return "OPEN"
    return "REMITTED" if abs(int(collected) - int(remitted)) <= LEDGER_TOL_MINOR else "MISMATCH"


def rollup_month(cur: Any, hotel_slug: str, month: date) -> dict:
    """Refresh one month of pfg_td_ledger: the liability from core.tax
    (liable unit-nights, expected TD, due date with any applied deferral)
    and what the folio collected. A recorded remittance is kept and its
    status re-judged against the new collected figure."""
    m, end = month_start(month), month_end(month)
    tm = tax.td_month(cur, hotel_slug, m)
    cur.execute(_COLLECTED_SQL, (hotel_slug, m, end))
    collected = int(cur.fetchone()["s"])
    cur.execute(
        """
        INSERT INTO pfg_td_ledger (hotel_slug, month, liable_room_nights, td_expected_minor, td_collected_minor,
                                   currency, due_date, status, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN', NOW())
        ON CONFLICT (hotel_slug, month) DO UPDATE SET
            liable_room_nights = EXCLUDED.liable_room_nights, td_expected_minor = EXCLUDED.td_expected_minor,
            td_collected_minor = EXCLUDED.td_collected_minor, currency = EXCLUDED.currency,
            due_date = EXCLUDED.due_date, updated_at = NOW()
        RETURNING *
        """,
        (hotel_slug, m, tm["liable_unit_nights"], tm["td_expected_minor"], collected, tm["currency"], tm["due_date"]),
    )
    row = dict(cur.fetchone())
    status = ledger_status(row["td_collected_minor"], row["td_remitted_minor"], row["remitted_on"])
    if status != row["status"]:
        cur.execute("UPDATE pfg_td_ledger SET status = %s WHERE hotel_slug = %s AND month = %s RETURNING *",
                    (status, hotel_slug, m))
        row = dict(cur.fetchone())
    row["notices"] = tm.get("notices") or []
    row["capped_unit_nights"] = tm.get("capped_unit_nights", 0)
    return row


def record_remittance(cur: Any, hotel_slug: str, month: date, *, amount_minor: int, remitted_on: date,
                      reference: str | None = None, actor: Any = None) -> dict:
    """PUT /tax/td-ledger/{month}: what was paid to DET and when. REMITTED
    when it matches what the folio collected within AED 1, else MISMATCH
    (a T7 finding says by how much)."""
    from clefs.audit import audit

    m = month_start(month)
    rollup_month(cur, hotel_slug, m)
    uid = getattr(getattr(actor, "principal", actor), "user_id", None) if actor is not None else None
    cur.execute(
        "UPDATE pfg_td_ledger SET td_remitted_minor = %s, remitted_on = %s, remit_reference = %s, updated_by = %s, "
        "updated_at = NOW() WHERE hotel_slug = %s AND month = %s RETURNING *",
        (int(amount_minor), remitted_on, (reference or None) and reference[:200], uid, hotel_slug, m),
    )
    row = dict(cur.fetchone())
    status = ledger_status(row["td_collected_minor"], row["td_remitted_minor"], row["remitted_on"])
    cur.execute("UPDATE pfg_td_ledger SET status = %s WHERE hotel_slug = %s AND month = %s RETURNING *",
                (status, hotel_slug, m))
    row = dict(cur.fetchone())
    if actor is not None and hasattr(actor, "hotel"):
        audit(cur, actor, "tax.td_remittance", "pfg_td_ledger", m.isoformat(), hotel_slug=hotel_slug,
              after={"remitted_minor": int(amount_minor), "remitted_on": remitted_on.isoformat(), "status": status})
    if status == "REMITTED":
        from clefs.core import signals as core_signals

        core_signals.resolve_signal(cur, hotel_slug, pg_signals.remittance_key(m), resolution="remitted")
    return row


def t7_drafts(ctx: Any, *, months: list[date] | None = None) -> list[FindingDraft]:
    """T7 over the ledger: a recorded remittance more than AED 1 off what
    the folio collected, or nothing remitted with the due date 5 days away
    or less (overdue included)."""
    from clefs.modules.profit_guard.detectors.base import end_of_local_day

    ctx.cur.execute("SELECT * FROM pfg_td_ledger WHERE hotel_slug = %s AND (%s::date[] IS NULL OR month = ANY(%s)) "
                    "ORDER BY month", (ctx.slug, months, months))
    out = []
    for r in [dict(x) for x in ctx.cur.fetchall()]:
        collected, remitted = int(r["td_collected_minor"]), int(r["td_remitted_minor"])
        if r["remitted_on"] is not None:
            gap = collected - remitted
            if abs(gap) <= LEDGER_TOL_MINOR:
                continue
            amount, why = abs(gap), f"collected {collected} vs remitted {remitted}"
        else:
            due = r["due_date"]
            owed = collected or int(r["td_expected_minor"])
            if due is None or owed <= 0 or (due - ctx.today).days > DUE_SOON_DAYS:
                continue
            amount, why = owed, f"unremitted, due {due.isoformat()}"
        out.append(FindingDraft(
            detector="T7", klass="COMPLIANCE", amount_minor=amount, currency=r["currency"],
            base_confidence=Decimal(BASE["T7"]), res_norm="ledger", doc=mkey(r["month"]),
            group=f"month:{mkey(r['month'])}",
            deadline_at=end_of_local_day(ctx.hotel, r["due_date"]) if r["due_date"] else None,
            calc=[step("Tourism Dirham ledger", why, {"collected": collected, "remitted": remitted,
                                                      "expected": int(r["td_expected_minor"])}, amount)],
            evidence=[{"kind": "row", "label": f"TD ledger {mkey(r['month'])}",
                       "ref": {"table": "pfg_td_ledger", "month": r["month"].isoformat()}}],
        ))
    return out


# ─── Run and store ───

CLOSE_NOTE = "No longer detected"


def _close_unseen(ctx: Any, detectors: tuple[str, ...], months: list[date] | None, seen: set[str]) -> list[dict]:
    """A corrected folio (or a recorded remittance) makes a finding go away
    on the next scan of its month: close it the way the detectors close
    theirs, so a person can tell 'fixed' from 'dismissed'."""
    groups = [f"month:{mkey(m)}" for m in months] if months is not None else None
    ctx.cur.execute(
        """
        UPDATE pfg_findings SET status = 'DISMISSED', dismiss_reason = 'ALREADY_HANDLED', dismiss_note = %s,
               updated_at = NOW()
         WHERE hotel_slug = %s AND detector = ANY(%s) AND status = 'DETECTED' AND source = 'detector'
           AND (%s::text[] IS NULL OR split_part(COALESCE(subject_ref, ''), '|', 1) = ANY(%s))
           AND NOT (fingerprint = ANY(%s))
        RETURNING *
        """,
        (CLOSE_NOTE, ctx.slug, list(detectors), groups, groups, list(seen) or [""]),
    )
    rows = [dict(r) for r in ctx.cur.fetchall()]
    for r in rows:
        ctx.cur.execute("INSERT INTO pfg_finding_events (finding_id, hotel_slug, from_status, to_status, actor, note, "
                        "amount_minor) VALUES (%s, %s, 'DETECTED', 'DISMISSED', %s, %s, %s)",
                        (r["id"], ctx.slug, f"detector:{r['detector']}", CLOSE_NOTE, r["amount_minor"]))
    return rows


def _reopen_if_back(ctx: Any, row: dict) -> dict:
    if not (row["status"] == "DISMISSED" and row.get("dismiss_reason") == "ALREADY_HANDLED"
            and row.get("dismiss_note") == CLOSE_NOTE):
        return row
    ctx.cur.execute("UPDATE pfg_findings SET status = 'DETECTED', dismiss_reason = NULL, dismiss_note = NULL, "
                    "updated_at = NOW() WHERE id = %s RETURNING *", (row["id"],))
    out = dict(ctx.cur.fetchone())
    ctx.cur.execute("INSERT INTO pfg_finding_events (finding_id, hotel_slug, from_status, to_status, actor, note, "
                    "amount_minor) VALUES (%s, %s, 'DISMISSED', 'DETECTED', %s, 'Detected again', %s)",
                    (row["id"], ctx.slug, f"detector:{row['detector']}", row["amount_minor"]))
    return out


def default_months(today: date, trigger: str) -> list[date]:
    """Nightly: last month (it just closed) and this month so far."""
    this = month_start(today)
    return [month_start(this - timedelta(days=1)), this]


def run_tax_checks(cur: Any, hotel_slug: str, *, trigger: str = "manual", months: list[date] | None = None,
                   run_id: str | None = None) -> dict:
    """T1-T6/TX1 over `months` (default: last and this month) plus T7 over
    the whole ledger, stored as findings and grouped into signals.
    trigger 'deadline' runs T7 only (the hourly watch)."""
    from clefs.modules.profit_guard import confidence as conf
    from clefs.modules.profit_guard import repo
    from clefs.modules.profit_guard.detectors import store

    hotel = get_hotel(cur, hotel_slug)
    ctx = make_ctx(cur, hotel, repo.get_policies(cur, hotel_slug), run_id or f"tax:{trigger}:{uuid.uuid4().hex[:12]}")
    store.load_tuning(ctx)
    scan = [] if trigger == "deadline" else sorted({month_start(m) for m in (months or default_months(ctx.today,
                                                                                                          trigger))})
    for m in scan:
        rollup_month(cur, hotel_slug, m)
    drafts: list[FindingDraft] = []
    for m in scan:
        drafts += month_drafts(ctx, m)
    drafts += t7_drafts(ctx)
    stored = store.persist(ctx, drafts)
    for s in stored:
        s.row = _reopen_if_back(ctx, s.row)
    seen = {s.draft.fingerprint for s in stored}
    closed = _close_unseen(ctx, ("T1", "T2", "T3", "T4", "T5", "T6", "TX1"), scan, seen) if scan else []
    closed += _close_unseen(ctx, ("T7",), None, seen)
    groups = {(s.draft.detector, s.draft.group) for s in stored}
    groups |= {(r["detector"], str(r["subject_ref"] or "").partition("|")[0]) for r in closed}
    tuning = ctx.cache.get("tuning") or conf.tuning_rows(cur, hotel_slug)
    counts: dict[str, int] = defaultdict(int)
    for s in stored:
        counts[s.draft.detector] += 1
    signalled = 0
    for det, grp in sorted(groups):
        if pg_signals.refresh_tax_group(cur, hotel, det, grp, policies=ctx.policies, tuning_row=tuning.get(det),
                                        now=ctx.now) == "upserted":
            signalled += 1
    return {"run_id": ctx.run_id, "trigger": trigger, "months": [m.isoformat() for m in scan],
            "findings": len(stored), "created": sum(int(s.created) for s in stored), "by_detector": dict(counts),
            "closed": len(closed), "signals": signalled}


def remit_reminder(cur: Any, hotel: Any, *, now: datetime | None = None) -> list[str]:
    """tax.td_remit_reminder (10th and 14th): roll the month that just
    closed into the ledger and remind finance of every unremitted month
    that has TD to pay."""
    now = now or clock.now()
    today = htime.local_date(hotel, now)
    rollup_month(cur, hotel.slug, month_start(month_start(today) - timedelta(days=1)))
    cur.execute("SELECT * FROM pfg_td_ledger WHERE hotel_slug = %s AND remitted_on IS NULL AND due_date IS NOT NULL "
                "AND month < %s ORDER BY month", (hotel.slug, month_start(today)))
    out = []
    for r in [dict(x) for x in cur.fetchall()]:
        if int(r["td_collected_minor"] or r["td_expected_minor"] or 0) <= 0:
            continue
        pg_signals.raise_remittance_due(cur, hotel, r, now=now)
        out.append(mkey(r["month"]))
    return out


# ─── Export ───

EXPORT_COLUMNS = ("reservation_id", "pms_res_id", "channel", "check_in", "check_out", "status", "rooms",
                  "liable_unit_nights", "capped_unit_nights", "td_expected", "td_posted", "difference", "currency")


def export_rows(cur: Any, hotel_slug: str, month: date) -> list[dict]:
    """Per reservation for one month: liable unit-nights and TD owed (the
    same night rules as core.tax.td_liability) against what the folio
    posted. Only reservations with a liability or a posting are listed."""
    start, end = month_start(month), month_end(month)
    days = TaxDays(cur, hotel_slug, start, end)
    nights = load_nights(cur, hotel_slug, start, end, days)
    acc: dict[int, dict] = {}
    for (rid, d), n in sorted(nights.items()):
        a = acc.setdefault(rid, {"liable": 0, "capped": 0, "expected": 0, "posted": 0})
        a["liable"] += n.liable_units
        a["capped"] += n.capped_units
        a["expected"] += n.liable_units * (0 if days.td_exempt(d) else days.td_rate_minor(d))
        a["posted"] += n.posted("TD")
    ids = [rid for rid, a in acc.items() if a["liable"] or a["posted"]]
    if not ids:
        return []
    cur.execute("SELECT id, pms_res_id, channel_code, check_in, check_out, status, rooms_count, currency "
                "FROM reservations WHERE id = ANY(%s) ORDER BY check_in, id", (ids,))
    out = []
    for r in cur.fetchall():
        a = acc[r["id"]]
        out.append({"reservation_id": r["id"], "pms_res_id": r["pms_res_id"], "channel": r["channel_code"],
                    "check_in": r["check_in"], "check_out": r["check_out"], "status": r["status"],
                    "rooms": r["rooms_count"], "liable_unit_nights": a["liable"], "capped_unit_nights": a["capped"],
                    "td_expected_minor": a["expected"], "td_posted_minor": a["posted"],
                    "difference_minor": a["posted"] - a["expected"], "currency": r["currency"]})
    return out


def export_csv(cur: Any, hotel_slug: str, month: date) -> str:
    """GET /tax/td-export.csv: an internal reconciliation file (not a DET
    upload format), one row per reservation plus a TOTAL row. Amounts are
    Decimal strings in the hotel's currency."""
    hotel = get_hotel(cur, hotel_slug)
    rows = export_rows(cur, hotel_slug, month)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(EXPORT_COLUMNS)

    def m(v: int, ccy: str) -> str:
        return money.money_str(int(v), ccy)

    for r in rows:
        w.writerow([r["reservation_id"], _cell(r["pms_res_id"]), _cell(r["channel"]), r["check_in"], r["check_out"],
                    r["status"], r["rooms"], r["liable_unit_nights"], r["capped_unit_nights"],
                    m(r["td_expected_minor"], r["currency"]), m(r["td_posted_minor"], r["currency"]),
                    m(r["difference_minor"], r["currency"]), r["currency"]])
    t = {k: sum(r[k] for r in rows) for k in ("liable_unit_nights", "capped_unit_nights", "td_expected_minor",
                                               "td_posted_minor", "difference_minor")}
    w.writerow(["TOTAL", "", "", "", "", "", "", t["liable_unit_nights"], t["capped_unit_nights"],
                m(t["td_expected_minor"], hotel.currency), m(t["td_posted_minor"], hotel.currency),
                m(t["difference_minor"], hotel.currency), hotel.currency])
    return buf.getvalue()


def _cell(v: Any) -> str:
    """No formula injection when the file is opened in a spreadsheet."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


# ─── Routes ───
# Mounted by the manifest (FOUNDATION-REQUEST: module.py lists
# tax_checks.router). Reads need profit.view, writes profit.manage.

from fastapi import APIRouter, Depends, Query, Response  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from clefs import db  # noqa: E402
from clefs.auth.deps import HotelCtx, require_perm  # noqa: E402

router = APIRouter(prefix="/hotels/{slug}/profit-guard/tax", tags=["profit_guard"])
VIEW = require_perm("profit.view")
MANAGE = require_perm("profit.manage")
LEDGER_MONEY = ("td_expected_minor", "td_collected_minor", "td_remitted_minor")


def parse_month(v: str) -> date:
    """'2026-09' or any date in the month."""
    try:
        return month_start(date.fromisoformat(v if len(v) > 7 else f"{v}-01"))
    except (TypeError, ValueError) as exc:
        raise ClefsError(400, "month must look like 2026-09", "invalid_month") from exc


def ledger_out(row: dict) -> dict:
    from clefs.modules.profit_guard import repo

    out = repo.out({k: v for k, v in row.items() if k not in ("notices",)}, money_fields=LEDGER_MONEY)
    out["month"] = mkey(row["month"])
    out["gap_minor"] = int(row["td_collected_minor"]) - int(row["td_remitted_minor"])
    out["gap"] = money.money_str(out["gap_minor"], row["currency"])
    return out


class RemitIn(BaseModel):
    amount: Decimal = Field(ge=0)
    remitted_on: date
    reference: str | None = Field(default=None, max_length=200)


class RunIn(BaseModel):
    months: list[str] | None = Field(default=None, max_length=24)


@router.get("/td-ledger")
def get_td_ledger(slug: str, month: str | None = None, months: int = Query(12, ge=1, le=36),
                  ctx: HotelCtx = Depends(VIEW)) -> dict:
    """One month (refreshed first) or the latest `months` months (the one
    that just closed and the current one are refreshed first)."""
    with db.db() as cur:
        hotel = get_hotel(cur, slug)
        today = htime.local_date(hotel, clock.now())
        wanted = [parse_month(month)] if month else default_months(today, "manual")
        fresh = {m: rollup_month(cur, slug, m) for m in wanted}
        notices = sorted({n for r in fresh.values() for n in r.get("notices") or []})
        if month:
            rows = [fresh[wanted[0]]]
        else:
            cur.execute("SELECT * FROM pfg_td_ledger WHERE hotel_slug = %s ORDER BY month DESC LIMIT %s",
                        (slug, months))
            rows = [dict(r) for r in cur.fetchall()]
        return {"currency": hotel.currency, "months": [ledger_out(r) for r in rows], "notices": notices}


@router.put("/td-ledger/{month}")
def put_td_ledger(slug: str, month: str, body: RemitIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        hotel = get_hotel(cur, slug)
        m = parse_month(month)
        row = record_remittance(cur, slug, m, amount_minor=money.to_minor(body.amount, hotel.currency),
                                remitted_on=body.remitted_on, reference=body.reference, actor=ctx)
        run_tax_checks(cur, slug, trigger="deadline")
        return ledger_out(row)


@router.get("/td-export.csv")
def get_td_export(slug: str, month: str, ctx: HotelCtx = Depends(VIEW)) -> Response:
    m = parse_month(month)
    with db.db(readonly=True) as cur:
        body = export_csv(cur, slug, m)
    return Response(body, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="td-reconciliation-{slug}-{mkey(m)}.csv"',
        "Cache-Control": "no-store"})


@router.post("/checks/run")
def post_run_checks(slug: str, body: RunIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Run T1-T7 and TX1 now (default: last and this month)."""
    from clefs.audit import audit

    with db.db() as cur:
        get_hotel(cur, slug)
        months = [parse_month(m) for m in body.months] if body.months else None
        out = run_tax_checks(cur, slug, trigger="manual", months=months)
        audit(cur, ctx, "tax.checks_run", "pfg_findings", out["run_id"], hotel_slug=slug,
              meta={"months": out["months"], "findings": out["findings"]})
        return out
