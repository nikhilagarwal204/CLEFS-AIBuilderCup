"""
Money at risk (04 spec 4.5): room_nights x expected_ADR x stage_probability,
always labelled "est." wherever it is shown.

- room_nights = rooms x nights; a missing part takes the segment default
  (assumption A6) and the row is marked estimated (confidence x 0.6).
- expected_ADR comes from demand.api.expected_adr, else the same-weekday
  ADR over the trailing 28 days in core/stays, else the whole trailing 28
  days (a new hotel with no Tuesday yet still gets a number).
- stage_probability is the recalibrated inq_stage_probabilities row when
  there is one, else the assumed prior (A5).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import money
from clefs.core.hotels import Hotel
from clefs.core.time import local_date
from clefs.modules.demand import api as demand_api
from clefs.modules.inquiries.models import (
    ESTIMATE_CONFIDENCE_FACTOR,
    PRIOR_PROBABILITY,
    SEGMENT_DEFAULT_ROOM_NIGHTS,
)

# ─── Room nights ───


def room_nights(row: dict) -> tuple[int, bool]:
    """(room_nights, estimated)."""
    seg = row.get("segment") or "unknown"
    d_rooms, d_nights = SEGMENT_DEFAULT_ROOM_NIGHTS.get(seg, SEGMENT_DEFAULT_ROOM_NIGHTS["unknown"])
    nights = row.get("nights")
    if not nights and row.get("check_in") and row.get("check_out"):
        nights = (row["check_out"] - row["check_in"]).days
    rooms = row.get("rooms")
    estimated = not (rooms and nights)
    return int((rooms or d_rooms) * (nights or d_nights)), estimated


# ─── Expected ADR ───


def _trailing_adr(cur: Any, hotel: Hotel, weekday_of: date | None) -> int | None:
    today = local_date(hotel)
    start = today - timedelta(days=28)
    dow_sql = "AND EXTRACT(ISODOW FROM n.stay_date) = %s" if weekday_of else ""
    params: list[Any] = [hotel.slug, start, today, hotel.currency]
    if weekday_of:
        params.append(weekday_of.isoweekday())
    # WHY rate * rooms: reservation_nights.rate_minor is per room per night
    # (core/stays/nights.py), so a 3-room row earns its rate three times.
    # Summing the rate once against SUM(rooms) understated ADR on group rows
    # and so money at risk; finance pulse and profit_guard use the same form.
    cur.execute(
        f"""
        SELECT SUM(n.rate_minor * n.rooms)::numeric AS revenue, SUM(n.rooms) AS rooms
        FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
        WHERE n.hotel_slug = %s AND n.stay_date >= %s AND n.stay_date < %s AND n.currency = %s
          AND r.status NOT IN ('CANCELLED', 'NO_SHOW') AND n.rate_minor > 0 {dow_sql}
        """,
        params,
    )
    row = cur.fetchone()
    if not row or not row["rooms"]:
        return None
    return int((Decimal(row["revenue"]) / Decimal(row["rooms"])).quantize(Decimal(1), ROUND_HALF_UP))


def expected_adr(cur: Any, hotel: Hotel, row: dict) -> tuple[int | None, str]:
    """(minor units in the hotel's currency, source)."""
    check_in = row.get("check_in")
    if check_in:
        check_out = row.get("check_out") or check_in + timedelta(days=1)
        got = demand_api.expected_adr(
            cur, hotel.slug, check_in=check_in, check_out=check_out, room_type_code=row.get("room_type_code")
        )
        if got and got.get("amount") is not None:
            ccy = got.get("currency") or hotel.currency
            minor = money.to_minor(Decimal(str(got["amount"])), ccy)
            if ccy != hotel.currency:
                minor = money.convert_minor(cur, minor, ccy, hotel.currency, local_date(hotel))
            return int(minor), "demand"
    adr = _trailing_adr(cur, hotel, check_in)
    if adr is not None:
        return adr, "stays_same_weekday" if check_in else "stays_trailing"
    if check_in:
        adr = _trailing_adr(cur, hotel, None)
        if adr is not None:
            return adr, "stays_trailing"
    return None, "none"


# ─── Stage probability ───


def probability_key(stage: str, engaged: bool) -> str:
    return "quoted_engaged" if stage == "quoted" and engaged else stage


def stage_probability(cur: Any, hotel_slug: str, stage: str, engaged: bool) -> Decimal | None:
    key = probability_key(stage, engaged)
    if key not in PRIOR_PROBABILITY:
        return None  # closed stages carry no risk
    cur.execute("SELECT p FROM inq_stage_probabilities WHERE hotel_slug = %s AND stage = %s", (hotel_slug, key))
    got = cur.fetchone()
    return Decimal(got["p"]) if got else PRIOR_PROBABILITY[key]


# ─── The number ───


def compute(cur: Any, hotel: Hotel, row: dict) -> dict:
    """The money-at-risk columns for this row (not written)."""
    rn, estimated = room_nights(row)
    adr, _source = expected_adr(cur, hotel, row)
    p = stage_probability(cur, hotel.slug, row["stage"], bool(row.get("guest_engaged")))
    risk = None
    if adr is not None:
        risk = 0 if p is None else int((Decimal(rn) * Decimal(adr) * p).quantize(Decimal(1), ROUND_HALF_UP))
    return {
        "room_nights_est": rn,
        "room_nights_estimated": estimated,
        "expected_adr_minor": adr,
        "stage_probability": p,
        "money_at_risk_minor": risk,
    }


def confidence(row: dict) -> float:
    """Signal confidence: extraction confidence (0.5 when a person has not
    read it yet), x 0.6 when the room-nights were defaulted."""
    base = Decimal(str(row.get("extraction_confidence") or "0.5"))
    if row.get("room_nights_estimated"):
        base *= ESTIMATE_CONFIDENCE_FACTOR
    return float(min(base, Decimal(1)))
