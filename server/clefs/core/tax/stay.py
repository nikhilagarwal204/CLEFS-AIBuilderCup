"""Stay-level tax: compose_stay (04 spec D3), gross decomposition for one
night (02 spec 2.3), Tourism Dirham liability over stayed nights and the
remittance due date.

Money in and out is minor units of the hotel's currency. Inside, every fee is
summed exactly across lines and dates and rounded once per stay, so a group
quote equals the sum of its parts to the fil (04 spec 4.6 mixed golden).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from clefs.core.money import D, from_minor, fx_rate, quantize, to_minor
from clefs.core.money import ccy as norm_ccy
from clefs.core.tax import calc
from clefs.core.tax.models import PERCENT_ORDER, StayLine, StayTaxBreakdown, TaxProfile
from clefs.core.tax.rules import HotelTaxContext, load_context

# ─── Helpers ───


def _bedrooms(cur: Any, hotel_slug: str, codes: Iterable[str | None]) -> dict[str, int]:
    """room_types.bedrooms per code (1 when unknown: a room is one unit)."""
    wanted = sorted({c for c in codes if c})
    if not wanted:
        return {}
    cur.execute(
        "SELECT code, bedrooms FROM room_types WHERE hotel_slug = %s AND code = ANY(%s)",
        (hotel_slug, wanted),
    )
    return {r["code"]: max(int(r["bedrooms"] or 1), 1) for r in cur.fetchall()}


def _rule_refs(profiles: Iterable[TaxProfile], ctx: HotelTaxContext) -> list[dict]:
    """[{id, rule_key, fee_code, rule_status}] of every rule a stay used."""
    by_id = {r["id"]: r for r in ctx.rules}
    ids: set[int] = set()
    for p in profiles:
        ids.update(f.rule_id for f in p.fees if f.rule_id is not None)
        if p.td_rule_id is not None:
            ids.add(p.td_rule_id)
    return [
        {"id": i, "rule_key": by_id[i]["rule_key"], "fee_code": by_id[i]["fee_code"], "rule_status": by_id[i]["rule_status"]}
        for i in sorted(ids)
        if i in by_id
    ]


def _extra_notices(ctx: HotelTaxContext, profiles: list[TaxProfile]) -> list[str]:
    out: list[str] = []
    if ctx.emirate == "DXB" and any(p.property_category is None for p in profiles):
        out.append("The hotel has no property category or star rating, so no Tourism Dirham rate applies.")
    if len({p.convention for p in profiles}) > 1:
        out.append("The hotel's stacking convention changes during this stay; each night uses its own.")
    return out


# ─── Amounts in another currency ───
# A reservation priced in USD (an OTA's foreign-currency booking) carries
# USD amounts, but the profile holds the hotel's AED TD rate and quantizes to
# AED. Percentages do not care about currency; the fixed TD rate does (AED 10
# read as USD 10 is 3.67x the fee). So the profile is restated: TD converted
# on the stay date, and every result quantized to (and labelled with) the
# currency the amounts are really in.


def profile_in_currency(cur: Any, profile: TaxProfile, currency: str, on: date) -> TaxProfile:
    """`profile` restated in `currency`. Without an FX rate (no fx_rates row,
    no peg) the TD is left out rather than guessed, and a notice says so."""
    target = norm_ccy(currency)
    if target == norm_ccy(profile.currency):
        return profile
    rate = fx_rate(cur, profile.currency, target, on)
    if rate is None:
        note = (f"No {profile.currency}->{target} rate on {on.isoformat()}: the Tourism Dirham is left out of "
                f"this {target} stay.")
        return replace(profile, currency=target, td_rate=Decimal(0), notices=profile.notices + (note,))
    return replace(profile, currency=target, td_rate=D(profile.td_rate) * rate)


# ─── compose_stay ───


def compose_stay(
    cur: Any,
    hotel_slug: str,
    *,
    lines: list[StayLine],
    prior_chain_nights: int = 0,
    currency: str | None = None,
) -> StayTaxBreakdown:
    """04 spec D3. Every room type of a stay in one call. TD is charged per
    unit (rooms, times bedrooms when the rule is per bedroom) on nights 1..cap
    of the consecutive stay, counted from the stay's first night (plus
    prior_chain_nights already stayed in a back-to-back reservation).

    `currency` is the currency the line rates are in (default: the hotel's).
    A foreign-currency stay gets its TD converted per stay date and the
    breakdown comes back in, and labelled with, that currency."""
    ctx = load_context(cur, hotel_slug, min((ln.stay_date for ln in lines), default=None),
                       max((ln.stay_date for ln in lines), default=None))
    ccy = norm_ccy(currency) if currency else ctx.currency
    if not lines:
        return StayTaxBreakdown(0, 0, 0, 0, 0, 0, 0, ccy, ctx.fallback_convention)
    beds = _bedrooms(cur, hotel_slug, (ln.room_type_code for ln in lines if ln.bedrooms is None))
    index = calc.td_night_indexes((ln.stay_date for ln in lines), prior_nights=prior_chain_nights)
    profiles: dict[date, TaxProfile] = {}
    room_minor = 0
    exact = {c: Decimal(0) for c in PERCENT_ORDER}
    td_exact = Decimal(0)
    units_by_night: dict[date, int] = {}
    fx_notes: list[str] = []  # only what the currency restatement added
    for ln in lines:
        p = profiles.get(ln.stay_date)
        if p is None:
            base = ctx.profile(ln.stay_date)
            p = profiles[ln.stay_date] = profile_in_currency(cur, base, ccy, ln.stay_date)
            fx_notes.extend(n for n in p.notices[len(base.notices):] if n not in fx_notes)
        rooms = int(ln.rooms)
        line_room = int(ln.rate_minor) * rooms
        room_minor += line_room
        parts, _ = calc.split_room(from_minor(line_room, ccy), p)
        for c, v in parts.items():
            exact[c] += v
        if p.td_rate and calc.td_liable(index[ln.stay_date], p.td_cap_nights):
            bedrooms = ln.bedrooms if ln.bedrooms is not None else beds.get(ln.room_type_code or "", 1)
            units = rooms * (max(int(bedrooms), 1) if p.td_per_bedroom else 1)
            td_exact += D(p.td_rate) * units
            units_by_night[ln.stay_date] = units_by_night.get(ln.stay_date, 0) + units
    fees = {c: to_minor(quantize(v, ccy), ccy) for c, v in exact.items()}
    td = to_minor(quantize(td_exact, ccy), ccy)
    plist = [profiles[d] for d in sorted(profiles)]
    start, end = min(profiles), max(profiles)
    return StayTaxBreakdown(
        room=room_minor,
        sc=fees["SC"],
        mf=fees["MF"],
        vat=fees["VAT"],
        td=td,
        td_nights_charged=len(units_by_night),
        td_units=max(units_by_night.values(), default=0),
        currency=ccy,
        convention=plist[0].convention,
        tf=fees["TF"],
        dest=fees["DEST"],
        total=room_minor + sum(fees.values()) + td,
        fee_rules=_rule_refs(plist, ctx),
        notices=ctx.notices(start, end) + _extra_notices(ctx, plist) + fx_notes,
    )


# ─── decompose one night ───


def decompose_night(
    cur: Any,
    hotel_slug: str,
    *,
    gross_minor: int,
    stay_date: date,
    rooms: int = 1,
    room_type_code: str | None = None,
    bedrooms: int | None = None,
    td_liable: bool = True,
) -> dict:
    """02 spec 2.3 decompose(gross, stay_date, hotel): one night's guest-paid
    gross (all its rooms, TD included) -> {room, sc, mf, tf, dest, vat, td,
    gross} in minor units, plus currency, convention, factor, fee_rules and
    notices. td_liable=False for a night that never started (no-show), or
    past the 30-night cap, so no TD is stripped."""
    ctx = load_context(cur, hotel_slug, stay_date, stay_date)
    p = ctx.profile(stay_date)
    units = 0
    if td_liable and p.td_rate:
        if bedrooms is None and p.td_per_bedroom:
            bedrooms = _bedrooms(cur, hotel_slug, [room_type_code]).get(room_type_code or "", 1)
        units = int(rooms) * (max(int(bedrooms or 1), 1) if p.td_per_bedroom else 1)
    b = calc.decompose(from_minor(int(gross_minor), ctx.currency), units, p)
    return b.minor() | {
        "currency": ctx.currency,
        "convention": p.convention,
        "factor": str(b.factor.normalize()),
        "td_units": units,
        "fee_rules": _rule_refs([p], ctx),
        "notices": ctx.notices(stay_date, stay_date) + _extra_notices(ctx, [p]),
    }


# ─── Tourism Dirham liability ───

_STAYED_NIGHTS_SQL = """
    WITH chains AS (
        SELECT DISTINCT COALESCE(r.stay_chain_id, r.id) AS chain_id
        FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
        WHERE n.hotel_slug = %(slug)s AND n.stayed AND n.stay_date BETWEEN %(start)s AND %(end)s
          AND r.status NOT IN ('CANCELLED', 'NO_SHOW')
    )
    SELECT COALESCE(r.stay_chain_id, r.id) AS chain_id, n.reservation_id, n.room_seq, n.stay_date, n.rooms,
           -- hotel_rooms.bedrooms defaults to 1 (NOT NULL), so the larger of room and
           -- type wins: an unconfigured room never hides a two-bedroom type.
           GREATEST(COALESCE(hr.bedrooms, 1), COALESCE(rt.bedrooms, 1)) AS bedrooms
    FROM reservation_nights n
    JOIN reservations r ON r.id = n.reservation_id
    JOIN chains c ON c.chain_id = COALESCE(r.stay_chain_id, r.id)
    LEFT JOIN hotel_rooms hr ON hr.hotel_slug = n.hotel_slug AND hr.room_number = n.room_number
    LEFT JOIN room_types rt ON rt.hotel_slug = n.hotel_slug
                           AND rt.code = COALESCE(n.room_type_code, r.room_type_code)
    WHERE n.hotel_slug = %(slug)s AND r.hotel_slug = %(slug)s AND n.stayed AND n.stay_date <= %(end)s
      AND r.status NOT IN ('CANCELLED', 'NO_SHOW')
    ORDER BY chain_id, n.room_seq, n.stay_date
"""


def td_liability(cur: Any, hotel_slug: str, *, start: date, end: date) -> dict:
    """TD owed for stayed nights in [start, end] (02 spec 2.3):
    sum of units x rate(category, date) with units = rooms x (bedrooms if
    per_bedroom), and the night index counted across the back-to-back chain
    (reservations.stay_chain_id) per room line, so night 31+ of a chain owes
    nothing. Nights before `start` only feed the index. Cancelled, no-show
    and unstayed nights never count (a Hotel Room Night starts at check-in).

    -> {liable_unit_nights, capped_unit_nights, td_expected_minor, currency,
        by_reservation: {reservation_id: {unit_nights, td_minor}}, fee_rules,
        notices}"""
    ctx = load_context(cur, hotel_slug, None, end)
    cur.execute(_STAYED_NIGHTS_SQL, {"slug": hotel_slug, "start": start, "end": end})
    rows = cur.fetchall()
    lines: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        lines.setdefault((r["chain_id"], r["room_seq"]), []).append(r)
    profiles: dict[date, TaxProfile] = {}
    liable = capped = 0
    total = Decimal(0)
    by_res: dict[int, dict] = {}
    for nights in lines.values():
        index = calc.td_night_indexes(n["stay_date"] for n in nights)
        for n in nights:
            d = n["stay_date"]
            if d < start:
                continue
            p = profiles.get(d) or profiles.setdefault(d, ctx.profile(d))
            units = int(n["rooms"]) * (max(int(n["bedrooms"] or 1), 1) if p.td_per_bedroom else 1)
            if not calc.td_liable(index[d], p.td_cap_nights):
                capped += units
                continue
            amount = D(p.td_rate) * units
            if not amount:
                continue
            liable += units
            total += amount
            acc = by_res.setdefault(int(n["reservation_id"]), {"unit_nights": 0, "td_exact": Decimal(0)})
            acc["unit_nights"] += units
            acc["td_exact"] += amount
    ccy = ctx.currency
    return {
        "liable_unit_nights": liable,
        "capped_unit_nights": capped,
        "td_expected_minor": to_minor(quantize(total, ccy), ccy),
        "currency": ccy,
        "by_reservation": {
            rid: {"unit_nights": v["unit_nights"], "td_minor": to_minor(quantize(v["td_exact"], ccy), ccy)}
            for rid, v in sorted(by_res.items())
        },
        "fee_rules": _rule_refs(profiles.values(), ctx),
        "notices": ctx.notices(start, end),
    }


# ─── Remittance ───


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def td_remittance_due_date(cur: Any, hotel_slug: str, month: date) -> date:
    """TD collected in `month` is due by the 15th of the next month (AR
    2/2020: before the 16th), plus the months of any deferral rule that
    applies to the hotel for that collection month (an unverified deferral
    only with the owner's override)."""
    m = _month_start(month)
    ctx = load_context(cur, hotel_slug, m, _month_end(m))
    return calc.remittance_due_date(m, ctx.deferral_months("TD", m))


def td_month(cur: Any, hotel_slug: str, month: date) -> dict:
    """One collection month for the td_ledger rollup (Profit Guard owns the
    ledger): {month, liable_unit_nights, capped_unit_nights,
    td_expected_minor, currency, due_date, deferral_months, notices}."""
    m = _month_start(month)
    end = _month_end(m)
    ctx = load_context(cur, hotel_slug, m, end)
    months = ctx.deferral_months("TD", m)
    liab = td_liability(cur, hotel_slug, start=m, end=end)
    return {
        "month": m,
        "liable_unit_nights": liab["liable_unit_nights"],
        "capped_unit_nights": liab["capped_unit_nights"],
        "td_expected_minor": liab["td_expected_minor"],
        "currency": liab["currency"],
        "due_date": calc.remittance_due_date(m, months),
        "deferral_months": months,
        "notices": liab["notices"],
    }
