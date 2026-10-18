"""Findings into signals (02 spec 3.3, 3.9).

One signal per (detector, OTA, statement or month): the brief says "12
Booking no-show commissions, Sep invoice, AED 1,020" rather than 12 lines.
L8 and L14 give a finding its own signal once its deadline is 1 day away
or less. A group is always rebuilt from storage (open findings that pass
the gate), so a re-run, a dismissal or an auto-close all converge on the
same signal; an empty group resolves its signal.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from clefs.core import money
from clefs.core import signals as core_signals
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.modules.profit_guard import confidence as conf
from clefs.modules.profit_guard.detectors import proposals
from clefs.modules.profit_guard.detectors.base import Ctx, days_left
from clefs.modules.profit_guard.detectors.l08_vcc_uncharged import severity as l08_severity

MODULE = "profit_guard"
log = logging.getLogger(__name__)

# ─── Kinds (02 spec 3.9) ───

# key: (label, section, cadence, owner role). Daily is money at risk only
# (Canon 18): deadlines, cards about to lapse, parity gaps.
KINDS: dict[str, tuple[str, str, str, str]] = {
    "pg.leak.detected": ("OTA overcharge found", "money_lost", "weekly", "finance"),
    "pg.deadline.approaching": ("OTA deadline approaching", "money_available", "daily", "front_desk"),
    "pg.vcc.expiring": ("Virtual card to charge", "money_available", "daily", "finance"),
    "pg.vcc.expired": ("Virtual card expired uncharged", "money_lost", "weekly", "finance"),
    "pg.payout.shortfall": ("OTA payout short", "money_lost", "weekly", "finance"),
    "pg.parity.breach": ("OTA undercuts your price", "money_available", "daily", "revenue"),
    "pg.config.fix_available": ("Channel setting to fix", "money_available", "weekly", "revenue"),
    "pg.compliance.vat_rcm": ("Reverse-charge VAT to self-account", "compliance", "monthly", "finance"),
    "pg.folio.overcharge_risk": ("Possible guest overcharge", "service_risk", "weekly", "gm"),
    "pg.detector.review": ("Leak check paused for review", "compliance", "weekly", "owner"),
}

KIND_FOR = {
    "L1": "pg.leak.detected", "L1b": "pg.config.fix_available", "L2": "pg.leak.detected", "L3": "pg.leak.detected",
    "L4": "pg.leak.detected", "L5": "pg.leak.detected", "L6": "pg.config.fix_available", "L7": "pg.parity.breach",
    "L8": "pg.vcc.expiring", "L9": "pg.vcc.expired", "L10": "pg.payout.shortfall", "L11": "pg.config.fix_available",
    "L12": "pg.leak.detected", "L13": "pg.leak.detected", "L14": "pg.deadline.approaching",
    "L15": "pg.compliance.vat_rcm",
}

# (singular, plural) nouns for titles.
NOUNS = {
    "L1": ("commission on excluded fees", "commissions on excluded fees"),
    "L1b": ("fee setting to fix", "fee settings to fix"),
    "L2": ("commission rate above contract", "commission rates above contract"),
    "L3": ("no-show commission", "no-show commissions"),
    "L4": ("early-departure commission", "early-departure commissions"),
    "L5": ("commission base above PMS", "commission bases above PMS"),
    "L6": ("booking over the discount cap", "bookings over the discount cap"),
    "L7": ("parity breach", "parity breaches"),
    "L8": ("virtual card to charge", "virtual cards to charge"),
    "L9": ("expired virtual card", "expired virtual cards"),
    "L10": ("payout shortfall", "payout shortfalls"),
    "L11": ("FX loss on a payout", "FX losses on payouts"),
    "L12": ("refund without commission credit", "refunds without commission credit"),
    "L13": ("chargeback", "chargebacks"),
    "L14": ("no-show or early departure to report", "no-shows or early departures to report"),
    "L15": ("reverse-charge VAT month", "reverse-charge VAT months"),
}

# Recommended actions name REGISTERED action types (core.signals contract):
# the pg.* types come from profit_guard/actions.py; a VAT-return entry has no
# verifier of its own, so it is the core hand task (integrator fix, Wave 2).
ACTIONS = {
    "RECOVERABLE": ("pg.dispute", "Review and dispute"),
    "PREVENTABLE": ("pg.config_fix", "Fix the setting"),
    "COMPLIANCE": ("core.task", "Record in the VAT return"),
}
ACTION_OVERRIDES = {"L8": ("pg.vcc_charge", "Charge the card"),
                    "L10": ("pg.payout_query", "Query the payout with the OTA"),
                    "L14": ("pg.mark_no_show", "Report it in the extranet"),
                    "L7": ("pg.config_fix", "Fix the rate on the channel")}
IMPACT_KIND = {"RECOVERABLE": "recoverable", "PREVENTABLE": "cost_leak", "COMPLIANCE": "risk",
               "STRUCTURAL": "cost_leak"}
PER_ITEM = ("L8", "L14")
OTA_NAMES = {"BOOKING": "Booking.com", "AGODA": "Agoda", "EXPEDIA": "Expedia"}


def register_kinds() -> None:
    """Idempotent, and never fights another part of the module that
    registered the same key first (core raises on a second origin)."""
    for key, (label, section, cadence, role) in KINDS.items():
        if core_signals.get_kind(key) is None:
            core_signals.register_signal_kind(key, label=label, default_owner_role=role, section=section,
                                              cadence=cadence, icon="shield")


register_kinds()


# ─── Group keys ───


def group_of(row: dict) -> str:
    """The signal group stored in subject_ref ("<group>|<item>")."""
    return str(row.get("subject_ref") or "item|").split("|", 1)[0]


def group_key(detector: str, ota: str | None, group: str) -> str:
    return f"pg:{detector}:{ota or '-'}:{group}"


def item_key(row: dict) -> str:
    return f"pg:{row['detector']}:{row.get('ota_code') or '-'}:item:{row['fingerprint'][:16]}"


def _label(group: str) -> str:
    kind, _, val = group.partition(":")
    if kind == "doc":
        return f"statement {val}"
    if kind == "month" and len(val) == 7:
        from datetime import date as _d
        return _d(int(val[:4]), int(val[5:]), 1).strftime("%b %Y")
    return val or group


def _impact(ctx: Ctx, rows: list[dict]) -> int:
    total = 0
    for r in rows:
        if r["currency"] == ctx.currency:
            total += int(r["amount_minor"])
            continue
        try:
            total += money.convert_minor(ctx.cur, int(r["amount_minor"]), r["currency"], ctx.currency, ctx.today)
        except ClefsError:
            continue  # no rate: the finding still lists, the headline skips it
    return total


# ─── Reading a group back ───


def open_rows(ctx: Ctx, detector: str, ota: str | None, group: str) -> list[dict]:
    ctx.cur.execute(
        """
        SELECT * FROM pfg_findings
         WHERE hotel_slug = %s AND detector = %s AND ota_code IS NOT DISTINCT FROM %s
           AND split_part(COALESCE(subject_ref, ''), '|', 1) = %s
           AND status IN ('DETECTED', 'ACCEPTED') AND NOT internal_only AND class <> 'STRUCTURAL'
         ORDER BY amount_minor DESC, id
        """,
        (ctx.slug, detector, ota, group),
    )
    # The alert is about what is still owed: a partial credit the ledger
    # already verified on an unfiled claim comes off its amount here.
    rows = [{**dict(r), "amount_minor": max(0, int(r["amount_minor"]) - int(r["recovered_minor"] or 0))}
            for r in ctx.cur.fetchall()]
    th = conf.thresholds(ctx.policies, ctx.cache.get("tuning", {}).get(detector))
    return [r for r in rows if conf.signal_gate(Decimal(str(r["confidence"])), int(r["amount_minor"]), th,
                                                now=ctx.now) is None]


def _urgency(ctx: Ctx, row: dict) -> tuple[int, float] | None:
    """(calendar days, hours) to the deadline. L8's tiers count calendar
    days to the charge-deadline date (deadline_at is the end of that day);
    L14's alerts count hours."""
    dl = row.get("deadline_at")
    if dl is None:
        return None
    hours = (dl - ctx.now).total_seconds() / 3600
    if row["detector"] == "L8":
        last_day = htime.local_date(ctx.hotel, dl - timedelta(microseconds=1))
        return (last_day - ctx.today).days, hours
    left = days_left(ctx, dl)
    return (left if left is not None else 0), hours


def _is_item(ctx: Ctx, row: dict) -> bool:
    if row["detector"] not in PER_ITEM:
        return False
    u = _urgency(ctx, row)
    if u is None:
        return False
    return u[0] <= 1 if row["detector"] == "L8" else u[1] <= 24


# ─── Upsert ───


def _severity(ctx: Ctx, row: dict, known: dict[str, int]) -> int:
    if row["fingerprint"] in known:
        return known[row["fingerprint"]]
    u = _urgency(ctx, row) if row["detector"] in PER_ITEM else None
    if u is not None and row["detector"] == "L8":
        return l08_severity(u[0])
    if u is not None:
        return 4 if u[1] <= 6 else 3 if u[1] <= 24 else 2
    return 2


def _signal_in(ctx: Ctx, detector: str, ota: str | None, rows: list[dict], *, key: str, label: str,
               severity: int, subject: tuple[str, str]) -> core_signals.SignalIn:
    klass = rows[0]["class"]
    impact = _impact(ctx, rows)
    n = len(rows)
    one, many = NOUNS.get(detector, ("finding", "findings"))
    ota_name = OTA_NAMES.get(ota or "", (ota or "OTA").title())
    money_txt = money.fmt_money(impact, ctx.currency, getattr(ctx.hotel, "locale", None) or "en-AE", minor=True)
    title = f"{n} {ota_name} {one if n == 1 else many}, {label}, {money_txt}"
    act_type, act_label = ACTION_OVERRIDES.get(detector) or ACTIONS.get(klass, ("core.task", "Review"))
    deadlines = [r["deadline_at"] for r in rows if r.get("deadline_at") is not None]
    weight = sum(int(r["amount_minor"]) for r in rows) or 1
    confidence = sum(Decimal(str(r["confidence"])) * int(r["amount_minor"]) for r in rows) / weight
    return core_signals.SignalIn(
        module=MODULE, kind=KIND_FOR[detector], dedupe_key=key, title=title[:300],
        summary=f"{detector}: {n} finding{'s' if n != 1 else ''} from Profit Guard. Open each for its calculation.",
        severity=max(1, min(4, severity)), impact_minor=impact, currency=ctx.currency,
        impact_kind=IMPACT_KIND.get(klass), confidence=float(min(Decimal(1), confidence)),
        evidence=[core_signals.Evidence(kind="row", label=f"Finding {r['id']}", value=int(r["amount_minor"]),
                                        ref={"table": "pfg_findings", "id": r["id"]}) for r in rows[:25]],
        recommended_action=core_signals.RecommendedAction(
            type=act_type, label=act_label, params={"finding_ids": [r["id"] for r in rows[:200]]},
            due_in_hours=None, expected_value=money.from_minor(impact, ctx.currency)),
        due_at=min(deadlines) if deadlines else None, subject_type=subject[0], subject_id=subject[1],
    )


def _link(ctx: Ctx, signal_id: int, ids: list[int]) -> None:
    if ids:
        ctx.cur.execute("UPDATE pfg_findings SET signal_id = %s WHERE hotel_slug = %s AND id = ANY(%s)",
                        (signal_id, ctx.slug, ids))


def refresh_group(ctx: Ctx, detector: str, ota: str | None, group: str, *, known: dict[str, int] | None = None) -> dict:
    """Rebuild one group's signals from storage. Returns counts."""
    known = known or {}
    rows = open_rows(ctx, detector, ota, group)
    items = [r for r in rows if _is_item(ctx, r)]
    rest = [r for r in rows if not _is_item(ctx, r)]
    out = {"group": 0, "items": 0, "resolved": 0, "proposed": 0}
    todo: list[tuple[dict, core_signals.SignalIn, list[dict]]] = []
    for r in items:
        sin = _signal_in(ctx, detector, ota, [r], key=item_key(r), label=_label(group),
                         severity=_severity(ctx, r, known), subject=("pfg_finding", str(r["id"])))
        s = core_signals.upsert_signal(ctx.cur, ctx.slug, sin)
        _link(ctx, s["id"], [r["id"]])
        todo.append((s, sin, [r]))
        out["items"] += 1
    key = group_key(detector, ota, group)
    if rest:
        sev = max(_severity(ctx, r, known) for r in rest)
        sin = _signal_in(ctx, detector, ota, rest, key=key, label=_label(group), severity=sev,
                         subject=("pfg_finding_group", key))
        s = core_signals.upsert_signal(ctx.cur, ctx.slug, sin)
        _link(ctx, s["id"], [r["id"] for r in rest])
        # The group proposes first: when an item left the group (its
        # deadline came within a day), the group's stale proposal is
        # regrouped before the item's own is checked for cover.
        todo.insert(0, (s, sin, rest))
        out["group"] = 1
    else:
        proposals.retire(ctx, key)
        if core_signals.resolve_signal(ctx.cur, ctx.slug, key, resolution="no open findings"):
            out["resolved"] += 1
    for s, sin, rs in todo:
        ra = sin.recommended_action
        if ra is not None and proposals.sync(
                ctx, signal=s, signal_key=sin.dedupe_key, act_type=ra.type, label=ra.label, detector=detector,
                ota=ota, rows=rs, title=sin.title, impact_minor=sin.impact_minor):
            out["proposed"] += 1
    return out


def resolve_items(ctx: Ctx, rows: list[dict]) -> int:
    """Resolve the per-item signals of findings that closed."""
    n = 0
    for r in rows:
        if r["detector"] not in PER_ITEM:
            continue
        proposals.retire(ctx, item_key(r))
        if core_signals.resolve_signal(ctx.cur, ctx.slug, item_key(r), resolution="finding closed"):
            n += 1
    return n


def raise_side_signals(ctx: Ctx) -> int:
    """Side signals (L5's PMS-above-OTA service risk) run LAST in the
    detector transaction, so one malformed dict used to roll back every
    finding of the run and kill pg.detect_on_commit (QA OWN-04). Each one
    now gets defaults for the optional-but-required fields and its own
    savepoint: a bad side signal is logged and skipped, never fatal."""
    n = 0
    for i, raw in enumerate(ctx.side_signals):
        s = dict(raw)
        s.setdefault("confidence", 0.6)
        s.setdefault("severity", 2)
        sp = f"pg_side_{i}"
        ctx.cur.execute(f"SAVEPOINT {sp}")
        try:
            core_signals.upsert_signal(ctx.cur, ctx.slug, core_signals.SignalIn(module=MODULE, **s))
        except Exception:  # noqa: BLE001 - a side signal is advisory; the findings must survive
            ctx.cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            log.warning("profit_guard: side signal %s skipped", s.get("dedupe_key"), exc_info=True)
        else:
            n += 1
        ctx.cur.execute(f"RELEASE SAVEPOINT {sp}")
    return n
