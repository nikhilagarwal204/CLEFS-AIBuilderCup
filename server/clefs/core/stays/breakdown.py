"""A reservation's tax breakdown (04 spec D3): the only way other lanes build
the `breakdown` that profit_guard.api.expected_commission takes.

Lines come from reservation_nights (one StayLine per night and room line,
rate per room per night) and go through core.tax.compose_stay in one call,
so every component rounds once over the stay and TD units follow each
line's room type. The TD 30-night cap counts across the stay chain.
"""

from __future__ import annotations

from typing import Any

from clefs.core import tax
from clefs.core.errors import ClefsError
from clefs.core.stays.reservations import get_reservation, prior_chain_nights


def stay_lines(cur: Any, reservation_id: int) -> list[tax.StayLine]:
    """StayLines of a reservation. After an early departure the nights never
    stayed are left out; a cancelled or no-show stay keeps its booked nights
    (the claim an OTA bills commission on)."""
    res = get_reservation(cur, reservation_id)
    if res is None:
        raise ClefsError(404, "reservation not found", "reservation_not_found")
    end = res["actual_check_out"] if res["status"] in ("IN_HOUSE", "CHECKED_OUT") else None
    cur.execute(
        """
        SELECT stay_date, room_type_code, rooms, rate_minor FROM reservation_nights
        WHERE reservation_id = %s AND (%s::date IS NULL OR stay_date < %s) ORDER BY stay_date, room_seq
        """,
        (reservation_id, end, end),
    )
    return [
        tax.StayLine(stay_date=r["stay_date"], room_type_code=r["room_type_code"] or res["room_type_code"],
                     rooms=int(r["rooms"]), rate_minor=int(r["rate_minor"]))
        for r in cur.fetchall()
    ]


def reservation_breakdown(cur: Any, reservation_id: int) -> tax.StayTaxBreakdown:
    """core.tax.compose_stay over the reservation's nights (04 spec D3):
    StayTaxBreakdown{room, sc, mf, vat, td, td_nights_charged, td_units,
    currency, convention, fee_rules, notices, ...}. 404 for an unknown id."""
    res = get_reservation(cur, reservation_id)
    if res is None:
        raise ClefsError(404, "reservation not found", "reservation_not_found")
    lines = stay_lines(cur, reservation_id)
    # rate_minor is in the reservation's currency (an OTA's USD booking), so
    # the breakdown is too: compose_stay converts the fixed TD and labels it.
    return tax.compose_stay(cur, res["hotel_slug"], lines=lines, prior_chain_nights=prior_chain_nights(cur, res),
                            currency=res.get("currency"))
