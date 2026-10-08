"""reservation_nights: one row per stay date and room line (02 spec 2.1).

rate_minor is R (room revenue net of every fee) per room per night. The fee
columns are the row's totals under the hotel's date-effective fee rules
(core/tax), unless the PMS gave its own split. A multi-room booking whose
revenue is known only as a total gets one row per room (room_seq 1..n,
rooms=1), so the allocation adds up to the minor unit.

`stayed` marks a night that has started and was not left: TD liability and
occupancy only ever count stayed nights.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs.core import tax
from clefs.core.money import allocate, from_minor
from clefs.core.time import business_date

FEE_COLS = ("sc_minor", "mf_minor", "tf_minor", "vat_minor", "td_minor")
NOT_STAYING = ("CANCELLED", "NO_SHOW")

# ─── Stayed ───


def stayed_flag(status: str, stay_date: date, *, check_out: date, actual_check_out: date | None, today: date) -> bool:
    """A night counts once it has started (checked in, not left). A no-show
    never checked in; a CONFIRMED stay has not started."""
    if status in NOT_STAYING or status == "CONFIRMED":
        return False
    end = actual_check_out or check_out
    if stay_date >= end:
        return False
    if status == "IN_HOUSE":
        return stay_date <= today
    return status == "CHECKED_OUT"


# One statement per hotel: the business date moves IN_HOUSE nights into
# `stayed` as days pass (the nightly derivation runs it before occupancy).
_REFRESH = """
UPDATE reservation_nights n SET stayed = x.v
FROM (
    SELECT n2.id,
           CASE
             WHEN r.status IN ('CANCELLED', 'NO_SHOW', 'CONFIRMED') THEN FALSE
             WHEN n2.stay_date >= COALESCE(r.actual_check_out, r.check_out) THEN FALSE
             WHEN r.status = 'IN_HOUSE' THEN n2.stay_date <= %(today)s
             ELSE TRUE
           END AS v
    FROM reservation_nights n2 JOIN reservations r ON r.id = n2.reservation_id
    WHERE n2.hotel_slug = %(hotel)s AND (%(rid)s::bigint IS NULL OR r.id = %(rid)s)
) x
WHERE n.id = x.id AND n.stayed IS DISTINCT FROM x.v
"""


def refresh_stayed(cur: Any, hotel_slug: str, *, today: date, reservation_id: int | None = None) -> int:
    """Re-derive `stayed` for a hotel (or one reservation). Returns rows changed."""
    cur.execute(_REFRESH, {"today": today, "hotel": hotel_slug, "rid": reservation_id})
    return cur.rowcount or 0


# ─── Building rows ───


def _bedrooms(cur: Any, hotel_slug: str, codes: set[str]) -> dict[str, int]:
    if not codes:
        return {}
    cur.execute(
        "SELECT code, bedrooms FROM room_types WHERE hotel_slug = %s AND code = ANY(%s)",
        (hotel_slug, sorted(codes)),
    )
    return {r["code"]: int(r["bedrooms"] or 1) for r in cur.fetchall()}


def _dates(check_in: date, check_out: date) -> list[date]:
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]


def _base_lines(res: dict, nights: list[dict] | None) -> tuple[list[dict], str]:
    """Rows without fees, plus the rate_basis. Explicit PMS nights win; else
    room revenue, then gross (decomposed later), then a zero rate."""
    dates = _dates(res["check_in"], res["check_out"])
    rooms = max(int(res.get("rooms_count") or 1), 1)
    base = {"room_number": res.get("room_number"), "room_type_code": res.get("room_type_code")}
    if nights:
        out = []
        for n in nights:
            row = base | {k: v for k, v in n.items() if v is not None}
            row.setdefault("room_seq", 1)
            row.setdefault("rooms", 1 if "room_seq" in n else rooms)
            row["rate_minor"] = int(row.get("rate_minor") or 0)
            out.append(row)
        return out, "nightly"
    total, basis = None, "none"
    if res.get("room_revenue_minor") is not None:
        total, basis = int(res["room_revenue_minor"]), "room_revenue"
    elif res.get("gross_minor") is not None:
        total, basis = int(res["gross_minor"]), "gross_decomposed"
    if not dates:
        return [], basis
    if total is None:
        return [base | {"stay_date": d, "room_seq": 1, "rooms": rooms, "rate_minor": 0} for d in dates], basis
    seqs = range(1, rooms + 1)
    parts = allocate(total, [1] * (len(dates) * rooms))
    out = []
    for i, d in enumerate(dates):
        for j, seq in enumerate(seqs):
            amount = parts[i * rooms + j]
            key = "gross_minor" if basis == "gross_decomposed" else "rate_minor"
            out.append(base | {"stay_date": d, "room_seq": seq, "rooms": 1, key: amount, "rate_minor": amount})
    return out, basis


def _with_fees(cur: Any, res: dict, rows: list[dict], *, prior_chain_nights: int) -> list[dict]:
    """Fill rate (for gross rows) and the fee columns from the hotel's rules.
    A PMS that sent its own split keeps it. No TD on a night that will not be
    stayed (cancelled, no-show, after an early departure) or past the cap."""
    if not rows:
        return rows
    slug, ccy = res["hotel_slug"], res["currency"]
    dates = sorted({r["stay_date"] for r in rows})
    try:
        ctx = tax.load_context(cur, slug, dates[0], dates[-1])
    except Exception:  # noqa: BLE001 - a hotel with no tax setup still records its stays
        ctx = None
    index = tax.td_night_indexes(dates, prior_nights=prior_chain_nights)
    beds = _bedrooms(cur, slug, {r["room_type_code"] for r in rows if r.get("room_type_code")})
    end = res.get("actual_check_out") or res["check_out"]
    profiles: dict[date, Any] = {}
    out = []
    for r in rows:
        row = dict(r)
        explicit = any(row.get(c) is not None for c in FEE_COLS)
        if ctx is None or explicit:
            for c in FEE_COLS:
                row[c] = int(row.get(c) or 0)
            row.pop("gross_minor", None)
            out.append(row)
            continue
        p = profiles.get(row["stay_date"])
        if p is None:
            # Amounts are in the reservation's currency, the profile in the
            # hotel's: restate it (TD converted on the stay date) so a USD
            # stay is not charged AED 10 of TD as USD 10.
            p = profiles[row["stay_date"]] = tax.profile_in_currency(cur, ctx.profile(row["stay_date"]), ccy,
                                                                     row["stay_date"])
        cap = p.td_cap_nights
        liable = (res["status"] not in NOT_STAYING and row["stay_date"] < end and bool(p.td_rate)
                  and (cap is None or index[row["stay_date"]] <= cap))
        units = 0
        if liable:
            bedrooms = beds.get(row.get("room_type_code") or "", 1) if p.td_per_bedroom else 1
            units = int(row["rooms"]) * max(bedrooms, 1)
        gross = row.pop("gross_minor", None)
        if gross is not None:
            b = tax.decompose(from_minor(int(gross), ccy), units, p).minor()
            row["rate_minor"] = b["room"] // max(int(row["rooms"]), 1)
        else:
            b = tax.compose(from_minor(int(row["rate_minor"]) * int(row["rooms"]), ccy), units, p).minor()
        row.update(sc_minor=b["sc"], mf_minor=b["mf"], tf_minor=b["tf"] + b["dest"], vat_minor=b["vat"],
                   td_minor=b["td"])
        out.append(row)
    return out


def write_nights(
    cur: Any,
    res: dict,
    nights: list[dict] | None = None,
    *,
    prior_chain_nights: int = 0,
    today: date | None = None,
) -> dict:
    """Rebuild a reservation's nights: upsert on (reservation_id, stay_date,
    room_seq) and delete rows outside the new set. `res` is the reservations
    row. Returns {nights, rate_basis, room_revenue_minor}."""
    base, basis = _base_lines(res, nights)
    rows = _with_fees(cur, res, base, prior_chain_nights=prior_chain_nights)
    if today is None:
        from clefs.core.hotels import get_hotel

        today = business_date(get_hotel(cur, res["hotel_slug"]))
    keys = []
    for r in rows:
        flag = stayed_flag(res["status"], r["stay_date"], check_out=res["check_out"],
                           actual_check_out=res.get("actual_check_out"), today=today)
        cur.execute(
            """
            INSERT INTO reservation_nights (hotel_slug, reservation_id, stay_date, room_seq, room_number,
                room_type_code, rooms, rate_minor, sc_minor, mf_minor, tf_minor, vat_minor, td_minor, currency, stayed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (reservation_id, stay_date, room_seq) DO UPDATE SET
                room_number = EXCLUDED.room_number, room_type_code = EXCLUDED.room_type_code,
                rooms = EXCLUDED.rooms, rate_minor = EXCLUDED.rate_minor, sc_minor = EXCLUDED.sc_minor,
                mf_minor = EXCLUDED.mf_minor, tf_minor = EXCLUDED.tf_minor, vat_minor = EXCLUDED.vat_minor,
                td_minor = EXCLUDED.td_minor, currency = EXCLUDED.currency, stayed = EXCLUDED.stayed
            """,
            (res["hotel_slug"], res["id"], r["stay_date"], int(r["room_seq"]), r.get("room_number"),
             r.get("room_type_code"), int(r["rooms"]), int(r["rate_minor"]), r["sc_minor"], r["mf_minor"],
             r["tf_minor"], r["vat_minor"], r["td_minor"], res["currency"], flag),
        )
        keys.append(f"{r['stay_date'].isoformat()}#{int(r['room_seq'])}")
    cur.execute(
        """
        DELETE FROM reservation_nights
        WHERE reservation_id = %s AND NOT ((stay_date::text || '#' || room_seq::text) = ANY(%s))
        """,
        (res["id"], keys),
    )
    room_rev = sum(int(r["rate_minor"]) * int(r["rooms"]) for r in rows)
    return {"nights": len(rows), "rate_basis": basis, "room_revenue_minor": room_rev}
