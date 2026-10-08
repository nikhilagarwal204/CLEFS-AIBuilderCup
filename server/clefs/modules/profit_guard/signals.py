"""Profit Guard signals outside the leak detectors (02 spec 3.5, 3.7, 3.9).

The detectors part (detectors/signals.py) owns the leak kinds and their
grouped findings. This file registers the rest of the 3.9 table and raises
them:

- `pg.dispute.ready_for_approval`: a packet waits for the owner or finance.
- `pg.dispute.follow_up`: a submitted packet still has no credit.
- `pg.recovery.verified`: a credit was matched (VERIFIED section), one
  signal per OTA per hotel-local day so twelve credits on one credit note
  read as one line in the brief.
- `pg.import.needs_review`: an OTA file is stuck in mapping or review.
- `tax.td.remittance_due`, `tax.td.anomaly`, `tax.rule.unverified`: the
  Tourism Dirham ledger, the T1-T7/TX1 checks and the unverified-rule banner.

Cadence follows Canon 18: daily is money at risk only (approvals with a
dispute window, remittance deadlines); everything else is weekly.
Compliance never ranks into the daily top 5 (core marks it unrankable).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from clefs.core import money
from clefs.core import signals as core_signals
from clefs.core import time as htime
from clefs.core.errors import ClefsError

MODULE = "profit_guard"

# ─── Kinds ───

# key: (label, section, cadence, default owner role)
KINDS: dict[str, tuple[str, str, str, str]] = {
    "pg.dispute.ready_for_approval": ("Dispute packet ready for approval", "money_available", "daily", "owner"),
    "pg.dispute.follow_up": ("OTA dispute still without a credit", "money_available", "daily", "finance"),
    "pg.recovery.verified": ("OTA credit verified", "verified", "weekly", "finance"),
    "pg.import.needs_review": ("OTA file needs review", "service_risk", "weekly", "finance"),
    "tax.td.remittance_due": ("Tourism Dirham remittance due", "compliance", "daily", "finance"),
    "tax.td.anomaly": ("Tourism Dirham posting issue", "compliance", "weekly", "finance"),
    "tax.rule.unverified": ("Unverified tax rule in force", "compliance", "weekly", "owner"),
}


def register_kinds() -> None:
    """Idempotent: a kind another import already registered is left alone."""
    for key, (label, section, cadence, role) in KINDS.items():
        if core_signals.get_kind(key) is None:
            core_signals.register_signal_kind(key, label=label, default_owner_role=role, section=section,
                                              cadence=cadence, icon="shield")


register_kinds()

OTA_NAMES = {"BOOKING": "Booking.com", "AGODA": "Agoda", "EXPEDIA": "Expedia"}


def ota_name(code: str | None) -> str:
    return OTA_NAMES.get((code or "").upper(), code or "OTA")


def _fmt(hotel: Any, amount_minor: int, currency: str | None = None) -> str:
    return money.fmt_money(int(amount_minor), currency or hotel.currency,
                           getattr(hotel, "locale", None) or "en-AE", minor=True)


def _to_hotel(cur: Any, hotel: Any, amount_minor: int, currency: str, on: date) -> int | None:
    """An amount in the hotel's currency (None when no rate exists)."""
    if not currency or currency == hotel.currency:
        return int(amount_minor)
    try:
        return money.convert_minor(cur, int(amount_minor), currency, hotel.currency, on)
    except ClefsError:
        return None


# ─── Disputes ───


def dispute_key(packet_id: int) -> str:
    return f"pg:dispute:{packet_id}:approve"


def followup_key(packet_id: int) -> str:
    return f"pg:dispute:{packet_id}:follow_up"


def raise_dispute_ready(cur: Any, hotel: Any, packet: dict, *, deadline: datetime | None = None) -> dict:
    """The packet waits for the owner or finance. The deadline is the
    earliest dispute window among its findings: approving after it lapses
    usually forfeits the claim, hence the daily cadence."""
    n = len(packet.get("finding_ids") or [])
    title = (f"Approve a {ota_name(packet['ota_code'])} dispute: {n} item{'s' if n != 1 else ''}, "
             f"{_fmt(hotel, packet['total_minor'], packet['currency'])}")
    impact = _to_hotel(cur, hotel, int(packet["total_minor"]), packet["currency"], htime.local_date(hotel))
    return core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module=MODULE, kind="pg.dispute.ready_for_approval", dedupe_key=dispute_key(packet["id"]), title=title[:300],
        summary="Review the packet (bookings, contract clause, calculation) and approve it before submitting it "
                "in the extranet or by email.",
        severity=3, impact_minor=impact, currency=hotel.currency if impact is not None else None,
        impact_kind="recoverable" if impact is not None else None, confidence=0.9,
        evidence=[core_signals.Evidence(kind="row", label=f"Dispute packet {packet['id']}",
                                        ref={"table": "pfg_dispute_packets", "id": packet["id"]})],
        recommended_action=core_signals.RecommendedAction(
            type="pg.dispute", label="Approve the dispute", params={"packet_id": packet["id"]}, owner_role="owner",
            expected_value=money.from_minor(impact, hotel.currency) if impact is not None else None),
        owner_role="owner", due_at=deadline, subject_type="pfg_dispute_packet", subject_id=str(packet["id"]),
    ))


def raise_dispute_follow_up(cur: Any, hotel: Any, packet: dict, *, days_open: int) -> dict:
    title = (f"{ota_name(packet['ota_code'])} dispute {packet['id']} still has no credit after {days_open} days, "
             f"{_fmt(hotel, packet['total_minor'], packet['currency'])}")
    impact = _to_hotel(cur, hotel, int(packet["total_minor"]) - int(packet.get("recovered_minor") or 0),
                       packet["currency"], htime.local_date(hotel))
    return core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module=MODULE, kind="pg.dispute.follow_up", dedupe_key=followup_key(packet["id"]), title=title[:300],
        summary="Chase the OTA's support with the packet reference; the credit usually lands on the next invoice.",
        severity=2, impact_minor=impact, currency=hotel.currency if impact is not None else None,
        impact_kind="recoverable" if impact is not None else None, confidence=0.8,
        evidence=[core_signals.Evidence(kind="row", label=f"Dispute packet {packet['id']}",
                                        ref={"table": "pfg_dispute_packets", "id": packet["id"]})],
        recommended_action=core_signals.RecommendedAction(
            type="core.task", label="Follow up with the OTA", params={"packet_id": packet["id"]},
            owner_role="finance"),
        owner_role="finance", subject_type="pfg_dispute_packet", subject_id=str(packet["id"]),
    ))


def resolve_dispute(cur: Any, hotel_slug: str, packet_id: int, *, approval: bool = True, follow_up: bool = False,
                    resolution: str = "handled") -> int:
    n = 0
    if approval and core_signals.resolve_signal(cur, hotel_slug, dispute_key(packet_id), resolution=resolution):
        n += 1
    if follow_up and core_signals.resolve_signal(cur, hotel_slug, followup_key(packet_id), resolution=resolution):
        n += 1
    return n


# ─── Recoveries ───

CREDIT_CHANNEL = "credit"  # pfg_finding_events.channel of every credited amount (ledger.py writes it)


def recovery_key(ota_code: str | None, day: date) -> str:
    return f"pg:recovered:{ota_code or '-'}:{day.isoformat()}"


def raise_recovery_verified(cur: Any, hotel: Any, ota_code: str | None, day: date) -> dict | None:
    """One VERIFIED signal per OTA and hotel-local day, rebuilt from the
    ledger's credit events so a second credit the same day grows the line
    instead of adding one. None when nothing was credited that day."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=hotel.tz)
    cur.execute(
        """
        SELECT f.id, f.currency, e.amount_minor, e.evidence FROM pfg_finding_events e
          JOIN pfg_findings f ON f.id = e.finding_id
         WHERE e.hotel_slug = %s AND e.channel = %s AND f.ota_code IS NOT DISTINCT FROM %s
           AND e.at >= %s AND e.at < %s AND e.amount_minor > 0
         ORDER BY e.id
        """,
        (hotel.slug, CREDIT_CHANNEL, ota_code, start, start + timedelta(days=1)),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return None
    total = 0
    for r in rows:
        v = _to_hotel(cur, hotel, int(r["amount_minor"]), r["currency"], day)
        total += v or 0
    ids = sorted({r["id"] for r in rows})
    title = (f"{ota_name(ota_code)} credited {_fmt(hotel, total)} on {len(ids)} finding"
             f"{'s' if len(ids) != 1 else ''}")
    return core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module=MODULE, kind="pg.recovery.verified", dedupe_key=recovery_key(ota_code, day), title=title[:300],
        summary="Matched on the OTA's own credit note, statement or payout: this money is back.",
        severity=1, impact_minor=total, currency=hotel.currency, impact_kind="recoverable", confidence=0.95,
        evidence=[core_signals.Evidence(kind="row", label=f"Finding {i}", ref={"table": "pfg_findings", "id": i})
                  for i in ids[:25]],
        subject_type="pfg_recovery", subject_id=f"{ota_code or '-'}:{day.isoformat()}",
    ))


# ─── Imports ───

PG_KINDS = ("OTA_STATEMENT", "CREDIT_NOTE", "OTA_PAYOUT", "VCC_REPORT", "BANK", "CONTRACT_PDF")
STUCK_STATUSES = ("MAPPING_REQUIRED", "REVIEW")
STUCK_AFTER = timedelta(hours=24)
IMPORT_KEY = "pg:import:needs_review"


def refresh_import_review(cur: Any, hotel: Any, *, now: datetime) -> dict | None:
    """One signal while any Profit Guard file waits more than a day for a
    person (column mapping or PDF row review): until it is committed, no
    detector sees its lines. Resolved once nothing is stuck."""
    cur.execute(
        "SELECT id, kind, status, created_at FROM import_batches WHERE hotel_slug = %s AND kind = ANY(%s) "
        "AND status = ANY(%s) AND created_at <= %s ORDER BY created_at",
        (hotel.slug, list(PG_KINDS), list(STUCK_STATUSES), now - STUCK_AFTER),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        core_signals.resolve_signal(cur, hotel.slug, IMPORT_KEY, resolution="no file waiting")
        return None
    n = len(rows)
    return core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module=MODULE, kind="pg.import.needs_review", dedupe_key=IMPORT_KEY,
        title=f"{n} OTA file{'s' if n != 1 else ''} waiting for review before leak checks can run",
        summary="Confirm the column mapping or the extracted PDF rows, then commit.", severity=2, confidence=1.0,
        evidence=[core_signals.Evidence(kind="row", label=f"{r['kind']} batch {r['id']} ({r['status']})",
                                        ref={"table": "import_batches", "id": r["id"]}) for r in rows[:25]],
        recommended_action=core_signals.RecommendedAction(type="core.task", label="Review the upload",
                                                          params={"batch_ids": [r["id"] for r in rows[:50]]}),
        subject_type="import_batches", subject_id="profit_guard",
    ))


# ─── Tourism Dirham and tax findings ───

TAX_DETECTORS = ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "TX1")
TAX_KIND = {d: "tax.td.anomaly" for d in TAX_DETECTORS} | {"T7": "tax.td.remittance_due"}
TAX_NOUNS = {
    "T1": ("Tourism Dirham under-collection", "Tourism Dirham under-collections"),
    "T2": ("Tourism Dirham charged past night 30", "Tourism Dirham charges past night 30"),
    "T3": ("Tourism Dirham at the wrong rate", "Tourism Dirham postings at the wrong rate"),
    "T4": ("stay with VAT charged on the Tourism Dirham", "stays with VAT charged on the Tourism Dirham"),
    "T5": ("duplicate Tourism Dirham posting", "duplicate Tourism Dirham postings"),
    "T6": ("Tourism Dirham posted on a no-show", "Tourism Dirham postings on no-shows"),
    "T7": ("Tourism Dirham ledger gap", "Tourism Dirham ledger gaps"),
    "TX1": ("fee posted inside the applied exemption", "fees posted inside the applied exemption"),
}
TAX_ACTIONS = {
    "T1": "Post the missing Tourism Dirham before remitting",
    "T2": "Reverse the Tourism Dirham past night 30",
    "T3": "Correct the Tourism Dirham rate in the PMS",
    "T4": "Remove VAT from the Tourism Dirham line",
    "T5": "Reverse the duplicate and refund the guest",
    "T6": "Reverse the Tourism Dirham on the no-show",
    "T7": "Remit the Tourism Dirham or explain the gap",
    "TX1": "Reverse fees posted inside the exemption",
}


# T7 is the ledger gap itself: its action verifies from the ledger
# (actions.py tax.td_remit); the other tax fixes are PMS hand tasks.
TAX_ACTION_TYPE = {"T7": "tax.td_remit"}


def tax_group_key(detector: str, group: str) -> str:
    return f"tax:{detector}:{group}"


def _month_label(group: str) -> str:
    kind, _, val = group.partition(":")
    if kind == "month" and len(val) == 7:
        return date(int(val[:4]), int(val[5:]), 1).strftime("%b %Y")
    return val or group


def refresh_tax_group(cur: Any, hotel: Any, detector: str, group: str, *, policies: dict,
                      tuning_row: dict | None = None, now: datetime) -> str | None:
    """Rebuild one (detector, month) signal from the open findings. Tax
    items are compliance: the owner's minimum finding amount does not hide
    them (an AED 10 under-collection is still a filing error), only the
    confidence floor and a suppression do. Returns 'upserted', 'resolved'
    or None."""
    from clefs.modules.profit_guard import confidence as conf

    cur.execute(
        """
        SELECT * FROM pfg_findings WHERE hotel_slug = %s AND detector = %s
           AND split_part(COALESCE(subject_ref, ''), '|', 1) = %s AND status IN ('DETECTED', 'ACCEPTED')
           AND NOT internal_only ORDER BY amount_minor DESC, id
        """,
        (hotel.slug, detector, group),
    )
    th = {**conf.thresholds(policies, tuning_row), "min_amount_minor": 0}
    rows = [dict(r) for r in cur.fetchall()
            if conf.signal_gate(Decimal(str(r["confidence"])), int(r["amount_minor"]), th, now=now) is None]
    key = tax_group_key(detector, group)
    if not rows:
        return "resolved" if core_signals.resolve_signal(cur, hotel.slug, key, resolution="no open findings") \
            else None
    today = htime.local_date(hotel, now)
    impact = sum((_to_hotel(cur, hotel, int(r["amount_minor"]), r["currency"], today) or 0) for r in rows)
    n = len(rows)
    one, many = TAX_NOUNS[detector]
    deadlines = [r["deadline_at"] for r in rows if r.get("deadline_at") is not None]
    urgent = bool(deadlines) and min(deadlines) - now <= timedelta(days=5)
    s = core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module=MODULE, kind=TAX_KIND[detector], dedupe_key=key,
        title=f"{n} {one if n == 1 else many}, {_month_label(group)}, {_fmt(hotel, impact)}"[:300],
        summary=f"{detector}: open each finding for the nights and the rule applied.",
        severity=3 if urgent else 2, impact_minor=impact, currency=hotel.currency, impact_kind="risk",
        confidence=float(min(Decimal(1), max(Decimal(str(r["confidence"])) for r in rows))),
        evidence=[core_signals.Evidence(kind="row", label=f"Finding {r['id']}", value=int(r["amount_minor"]),
                                        ref={"table": "pfg_findings", "id": r["id"]}) for r in rows[:25]],
        recommended_action=core_signals.RecommendedAction(
            type=TAX_ACTION_TYPE.get(detector, "core.task"), label=TAX_ACTIONS[detector],
            params={"finding_ids": [r["id"] for r in rows[:200]],
                    **({"month": f"{group.partition(':')[2]}-01"} if group.startswith("month:") else {})},
            owner_role="finance"),
        due_at=min(deadlines) if deadlines else None, subject_type="pfg_finding_group", subject_id=key,
    ))
    cur.execute("UPDATE pfg_findings SET signal_id = %s WHERE hotel_slug = %s AND id = ANY(%s)",
                (s["id"], hotel.slug, [r["id"] for r in rows]))
    return "upserted"


def remittance_key(month: date) -> str:
    return f"tax:td:remit:{month.strftime('%Y-%m')}"


def raise_remittance_due(cur: Any, hotel: Any, row: dict, *, now: datetime) -> dict:
    """The monthly TD remittance reminder (10th and 14th): due before the
    16th (the 15th, or later under an applied deferral)."""
    month, due = row["month"], row["due_date"]
    amount = int(row["td_collected_minor"] or row["td_expected_minor"] or 0)
    days = (due - htime.local_date(hotel, now)).days if due else None
    when = "today" if days == 0 else (f"in {days} days" if days and days > 0 else "overdue")
    return core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module=MODULE, kind="tax.td.remittance_due", dedupe_key=remittance_key(month),
        title=f"Remit {_fmt(hotel, amount, row['currency'])} Tourism Dirham for {month.strftime('%b %Y')} ({when})",
        summary="Record the remittance in Profit Guard once paid, so the ledger closes.",
        severity=4 if days is not None and days <= 1 else 3, impact_minor=amount, currency=row["currency"],
        impact_kind="risk", confidence=0.95,
        evidence=[core_signals.Evidence(kind="row", label="TD ledger",
                                        ref={"table": "pfg_td_ledger", "month": month.isoformat()})],
        # tax.td_remit (actions.py) verifies itself from the ledger once paid.
        recommended_action=core_signals.RecommendedAction(type="tax.td_remit", label="Remit the Tourism Dirham",
                                                          params={"month": month.isoformat()}, owner_role="finance"),
        owner_role="finance", subject_type="pfg_td_ledger", subject_id=month.isoformat(),
        due_at=datetime.combine(due + timedelta(days=1), datetime.min.time(), tzinfo=hotel.tz) if due else None,
    ))


RULE_KEY = "tax:rule:unverified"


def refresh_rule_banner(cur: Any, hotel: Any, notices: list[str]) -> dict | None:
    """tax.rule.unverified while any announced-but-unverified rule meets the
    next 90 days (applied or not): the banner the console also shows."""
    lines = [n for n in notices if "not yet verified" in n]
    if not lines:
        core_signals.resolve_signal(cur, hotel.slug, RULE_KEY, resolution="no unverified rule")
        return None
    return core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module=MODULE, kind="tax.rule.unverified", dedupe_key=RULE_KEY,
        title=f"{len(lines)} announced tax rule{'s' if len(lines) != 1 else ''} not yet verified",
        summary=" ".join(lines)[:2000], severity=2, confidence=1.0,
        evidence=[core_signals.Evidence(kind="quote", label="Notice", value=n[:500]) for n in lines[:10]],
        recommended_action=core_signals.RecommendedAction(type="core.task", label="Confirm how the hotel applies it",
                                                          owner_role="owner"),
        subject_type="fee_rules", subject_id="unverified",
    ))


def refresh_findings_signals(cur: Any, hotel_slug: str, finding_ids: list[int]) -> int:
    """After a person or the ledger moves findings: rebuild their groups.
    Leak findings go through the detectors part; tax findings here."""
    if not finding_ids:
        return 0
    from clefs.core import clock
    from clefs.core.hotels import get_hotel
    from clefs.modules.profit_guard import confidence as conf
    from clefs.modules.profit_guard import detectors, repo

    detectors.refresh_signals_for(cur, hotel_slug, finding_ids)
    cur.execute("SELECT DISTINCT detector, split_part(COALESCE(subject_ref, ''), '|', 1) AS grp FROM pfg_findings "
                "WHERE hotel_slug = %s AND id = ANY(%s) AND detector = ANY(%s)",
                (hotel_slug, list(finding_ids), list(TAX_DETECTORS)))
    groups = [(r["detector"], r["grp"]) for r in cur.fetchall()]
    if groups:
        hotel = get_hotel(cur, hotel_slug)
        policies, tuning = repo.get_policies(cur, hotel_slug), conf.tuning_rows(cur, hotel_slug)
        for det, grp in groups:
            refresh_tax_group(cur, hotel, det, grp, policies=policies, tuning_row=tuning.get(det), now=clock.now())
    return len(finding_ids)
