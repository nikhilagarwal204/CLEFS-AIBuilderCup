"""
Profit Guard public API (frozen signatures; other modules import only this file).

04 spec 7.2: the signatures are frozen (FROZEN_API); the Profit Guard lane
implements the bodies. Every function keeps its documented "nothing yet"
answer for a hotel with no data (an empty hotel, an unknown channel, an
empty breakdown), which is what callers such as Inquiry Guard, Guests
journeys and Finance handle. Money is minor units + currency (Canon 2).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any, TypedDict

from clefs.core import stays
from clefs.core.errors import ClefsError
from clefs.core.hotels import find_hotel

# ─── Shapes ───


class DistNight(TypedDict):
    """Distribution cost of one stay night on one channel (Finance's pulse)."""

    stay_date: date
    channel_code: str
    is_ota: bool
    commission_minor: int
    payment_fee_minor: int
    booking_engine_fee_minor: int
    currency: str


class CxlCost(TypedDict):
    """What cancellations cost on one day and channel (lost commission
    recovery, fees), for Finance's cancellation leakage."""

    cancel_date: date
    channel_code: str
    cost_minor: int
    currency: str


def _mods():
    """The module's internals, imported late: api.py is imported by other
    modules at their import time, before this module's tables may matter."""
    from clefs.modules.profit_guard import contracts, repo

    return contracts, repo


# ─── OTA leaks (Inquiry Guard) ───


def record_ota_leak(
    cur: Any,
    hotel_slug: str,
    *,
    source: str,
    inquiry_ref: str,
    reservation_ref: str | None,
    channel_code: str,
    evidence: dict,
    commission_minor: int | None = None,
    currency: str | None = None,
) -> dict | None:
    """Inquiry Guard reports a direct inquiry that later booked through an OTA
    (matched inside the 30-day window, Canon 19; output stays internal).

    Returns {finding_ref, commission_minor, currency, basis:
    'statement_line'|'expected'|'provided'}; Profit Guard fills the commission
    itself when none is given (currency None = the hotel's). None when the
    channel is not an OTA of the hotel or no commission can be established:
    Inquiry Guard then keeps its own expected commission for the internal
    record. The finding is stored internal_only (never signalled, exported
    or shown outside the owner's console)."""
    contracts, repo = _mods()
    hotel = find_hotel(cur, hotel_slug)
    if hotel is None:
        return None
    kinds = repo.channel_kinds(cur, hotel_slug)
    channel = (channel_code or "").strip().upper()
    if not channel or not repo.is_ota_channel(kinds, channel):
        return None
    res = repo.find_reservation_ref(cur, hotel_slug, reservation_ref, ota_code=channel)
    calc: list[dict] = []
    line_ids: list[int] = []
    if commission_minor is not None:
        amount, ccy, basis = int(commission_minor), (currency or hotel.currency).upper(), "provided"
    else:
        actual = repo.statement_commission(cur, hotel_slug, res["id"]) if res else None
        if actual is not None:
            (amount, ccy, line_ids), basis = actual, "statement_line"
        elif res is not None:
            ec = contracts.expected_commission(cur, hotel_slug, ota_code=channel,
                                               breakdown=stays.reservation_breakdown(cur, res["id"]),
                                               on=res["check_in"])
            if ec is None:
                return None
            amount, ccy, basis, calc = ec["commission_minor"], ec["currency"], "expected", ec["calc"]
        else:
            return None
    key = f"INQ|{channel}|{source}|{inquiry_ref}|{res['id'] if res else reservation_ref}"
    ev = [{"kind": "row", "label": "direct inquiry", "ref": {"source": source, "inquiry_ref": inquiry_ref}}]
    if res is not None:
        ev.append({"kind": "row", "label": "OTA reservation", "ref": {"reservation_id": res["id"]}})
    ev += [{"kind": "row", "label": "statement line", "ref": {"statement_line_id": i}} for i in line_ids]
    if evidence:
        # The reporter's evidence may hold dates or an identity hmac (bytes):
        # stored as JSON, bytes as hex (the row stays internal, Canon 19).
        safe = json.loads(json.dumps(evidence, default=lambda o: bytes(o).hex()
                                     if isinstance(o, (bytes, bytearray, memoryview)) else str(o)))
        ev.append({"kind": "quote", "label": "reported by " + str(source)[:40], "value": safe})
    try:
        confidence = min(max(Decimal(str((evidence or {}).get("confidence", "0.8"))), Decimal(0)), Decimal(1))
    except ArithmeticError:
        confidence = Decimal("0.8")
    row, _created = repo.upsert_finding(cur, hotel_slug, {
        "detector": "INQ", "fingerprint": hashlib.sha1(key.encode()).hexdigest(), "ota_code": channel,
        "reservation_id": res["id"] if res else None, "statement_line_id": line_ids[0] if line_ids else None,
        "subject_ref": f"inquiry:{inquiry_ref}", "class": "PREVENTABLE", "amount_minor": amount, "currency": ccy,
        "confidence": confidence.quantize(Decimal("0.001")), "calc": calc, "evidence": ev, "internal_only": True,
        "source": str(source)[:40] or "inquiries",
    }, actor=str(source)[:40] or "inquiries")
    return {"finding_ref": f"pfg_finding:{row['id']}", "commission_minor": amount, "currency": ccy, "basis": basis}


def channel_economics(cur: Any, hotel_slug: str, *, start: date, end: date) -> list[dict]:
    """Net ADR by channel over stayed nights in [start, end]: [{channel_code,
    channel, is_ota, room_nights, gross, commission, net, net_adr, currency}]
    (Decimal strings) plus the minor-unit fields room_rev_fils,
    commission_fils (Finance check 3) and the rest of the 02 spec 3.6 terms.
    Empty when no night was stayed in the range."""
    _contracts, repo = _mods()
    if find_hotel(cur, hotel_slug) is None:
        return []
    return repo.channel_economics(cur, hotel_slug, start=start, end=end,
                                  sc_as_revenue=treat_sc_as_revenue(cur, hotel_slug))


# ─── Commission and distribution cost ───


def expected_commission(cur: Any, hotel_slug: str, *, ota_code: str, breakdown: Any, on: date) -> dict | None:
    """{commission_minor, base_minor, pct, currency, basis} for a stay whose
    `breakdown` is a core.tax StayTaxBreakdown (from core.tax.compose_stay or
    core.stays.reservation_breakdown; callers always pass keywords). basis is
    'contract' (confirmed terms) or 'default' (the OTA's public-audit
    defaults); the dict also carries contract_id, base_rule,
    base_components, uplifts and the calc trace. None for a channel with no
    terms or a breakdown without a room component."""
    contracts, _repo = _mods()
    return contracts.expected_commission(cur, hotel_slug, ota_code=ota_code, breakdown=breakdown, on=on)


def reference_ota(cur: Any, hotel_slug: str, *, as_of: date) -> tuple[str, Decimal] | None:
    """(ota_code, commission pct) of the hotel's reference OTA on `as_of`:
    the owner's choice (policies.reference_ota), else the OTA with the most
    room nights over the trailing 90 days. None when the hotel has no OTA
    business in that window or no terms for it."""
    contracts, repo = _mods()
    if find_hotel(cur, hotel_slug) is None:
        return None
    ota = repo.get_policies(cur, hotel_slug).get("reference_ota") or repo.top_ota(cur, hotel_slug, as_of=as_of)
    if not ota:
        return None
    terms = contracts.contract_on(cur, hotel_slug, ota, as_of)
    if terms is None:
        return None
    return terms["ota_code"], Decimal(str(terms["base_commission_pct"]))


def distribution_cost_by_night(cur: Any, hotel_slug: str, *, start: date, end: date) -> list[DistNight]:
    """Per night and channel over [start, end] (stayed nights, every
    channel; direct channels carry payment and booking-engine fees only).
    [] when nothing was stayed."""
    _contracts, repo = _mods()
    if find_hotel(cur, hotel_slug) is None:
        return []
    return repo.distribution_by_night(cur, hotel_slug, start=start, end=end)


def distribution_cost_for_reservation(cur: Any, hotel_slug: str, *, reservation_id: int) -> int:
    """Total distribution cost of one reservation (commission + payment fee
    + booking-engine fee) in minor units of the hotel's currency; 0 for an
    unknown reservation or one in a currency with no rate."""
    _contracts, repo = _mods()
    hotel = find_hotel(cur, hotel_slug)
    if hotel is None:
        return 0
    res = repo.find_reservation_ref(cur, hotel_slug, reservation_id)
    if res is None or res["id"] != reservation_id:
        return 0
    c = repo.stay_costs(cur, hotel_slug, res)
    total = c["commission_minor"] + c["payment_fee_minor"] + c["booking_engine_fee_minor"]
    if (c["currency"] or hotel.currency) == hotel.currency:
        return total
    from clefs.core import money

    try:
        return money.convert_minor(cur, total, c["currency"], hotel.currency, res["check_in"])
    except ClefsError:
        return 0


def cancellation_cost_by_date(cur: Any, hotel_slug: str, *, start: date, end: date) -> list[CxlCost]:
    """Per cancellation date and channel over [start, end]: the commission
    OTAs invoiced on cancelled and no-show stays (net of credit notes). []
    until such a line is imported."""
    _contracts, repo = _mods()
    hotel = find_hotel(cur, hotel_slug)
    if hotel is None:
        return []
    return repo.cancellation_costs(cur, hotel, start=start, end=end)


# ─── Rate evidence and settings ───


def record_rate_evidence(cur: Any, hotel_slug: str, *, evidence: dict, emit_signal: bool = False) -> int:
    """Store one rate observation (Visibility's Google Hotels parity rows go
    in with emit_signal=False, so L7 never signals a gap twice). `evidence`
    takes channel, stay_date and price (Decimal string) or price_minor, plus
    the optional rate_evidence columns. Returns the evidence id; 0 when the
    observation is incomplete (nothing stored)."""
    _contracts, repo = _mods()
    hotel = find_hotel(cur, hotel_slug)
    if hotel is None or not evidence:
        return 0
    cur.execute("SAVEPOINT pfg_rate_evidence")
    try:
        row = repo.insert_rate_evidence(cur, hotel_slug, hotel.currency, {**evidence, "emit_signal": emit_signal})
    except ClefsError:
        cur.execute("ROLLBACK TO SAVEPOINT pfg_rate_evidence")
        return 0
    finally:
        cur.execute("RELEASE SAVEPOINT pfg_rate_evidence")
    return int(row["id"])


def parity_tolerance_pct(cur: Any, hotel_slug: str) -> Decimal:
    """A price gap at or under this percentage is parity (owner policy,
    default 1.0)."""
    _contracts, repo = _mods()
    return Decimal(str(repo.get_policies(cur, hotel_slug)["parity_tolerance_pct"]))


def treat_sc_as_revenue(cur: Any, hotel_slug: str) -> bool:
    """The single source of the service-charge treatment (04 spec 6.4): True
    counts the 10% service charge as hotel revenue (the default)."""
    _contracts, repo = _mods()
    return bool(repo.get_policies(cur, hotel_slug)["treat_sc_as_revenue"])


# ─── Dispute packets (Brief) ───


def dispute_packet(cur: Any, hotel_slug: str, *, action_id: int) -> dict | None:
    """The packet PDF of a pg.dispute action as {file_id, filename}, rendered
    on first ask; None when the action has no packet. Brief's WhatsApp door
    sends it as a DOCUMENT message (brief/whatsapp.py looks this up)."""
    from clefs.modules.profit_guard import disputes

    return disputes.packet_for_action(cur, hotel_slug, action_id=action_id)
