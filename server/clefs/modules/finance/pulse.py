"""
Weekly pulse: NRevPAR, OTA cost per occupied room, cancellation leakage and
recovery cost, with no P&L needed (04 spec 6.1).

Inputs come from core stays (occupancy_daily for ARN and OccRN, stayed
reservation_nights for rooms revenue) and from Profit Guard and Service
through ports.py. The week is an ISO week of hotel-local dates.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from clefs.core import money
from clefs.db import Json
from clefs.core.hotels import Hotel, get_hotel
from clefs.modules.finance import calc, ports

# ─── Inputs ───


def _occupancy(cur: Any, hotel: Hotel, start: date, end: date) -> tuple[int | None, int | None, int]:
    """(ARN, OccRN, occupancy room revenue). ARN None when no day is stored."""
    cur.execute(
        """SELECT COUNT(*) AS days, COALESCE(SUM(rooms_available), 0) AS arn,
                  COALESCE(SUM(rooms_occupied), 0) AS occ, COALESCE(SUM(room_revenue_minor), 0) AS rev
           FROM occupancy_daily WHERE hotel_slug = %s AND date BETWEEN %s AND %s""",
        (hotel.slug, start, end),
    )
    r = cur.fetchone()
    if not r or int(r["days"]) == 0:
        return None, None, 0
    return int(r["arn"]), int(r["occ"]), int(r["rev"])


def _convert(cur: Any, amount: int, src: str, dst: str, on: date) -> int:
    if src == dst or amount == 0:
        return amount
    try:
        return int(money.convert_minor(cur, amount, src, dst, on))
    except Exception:  # noqa: BLE001  (no rate: the amount cannot be counted in the hotel's currency)
        return 0


def rooms_revenue(cur: Any, hotel: Hotel, start: date, end: date, *, sc_as_revenue: bool) -> tuple[int, dict]:
    """(RoomsRev minor, room nights by channel). Stayed nights only; the
    service charge joins revenue only when Profit Guard says so; pass-through
    taxes (MF, TF, VAT, TD) never do."""
    cur.execute(
        """SELECT n.currency, COALESCE(r.channel_code, 'UNKNOWN') AS channel_code,
                  COALESCE(SUM(n.rate_minor * n.rooms), 0) AS rate, COALESCE(SUM(n.sc_minor), 0) AS sc,
                  COALESCE(SUM(n.rooms), 0) AS rn
           FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
           WHERE n.hotel_slug = %s AND n.stayed AND n.stay_date BETWEEN %s AND %s
           GROUP BY n.currency, COALESCE(r.channel_code, 'UNKNOWN')""",
        (hotel.slug, start, end),
    )
    total = 0
    rn_by_channel: dict[str, int] = defaultdict(int)
    for r in cur.fetchall():
        amt = int(r["rate"]) + (int(r["sc"]) if sc_as_revenue else 0)
        total += _convert(cur, amt, r["currency"].strip(), hotel.currency, start)
        rn_by_channel[r["channel_code"]] += int(r["rn"])
    return total, dict(rn_by_channel)


def _dist(cur: Any, hotel: Hotel, rows: list[dict], start: date) -> tuple[int, int, dict[str, dict]]:
    """(Dist, OTA part, per channel {is_ota, cost_minor})."""
    total = ota = 0
    by_channel: dict[str, dict] = {}
    for r in rows:
        cost = int(r.get("commission_minor") or 0) + int(r.get("payment_fee_minor") or 0) \
            + int(r.get("booking_engine_fee_minor") or 0)
        cost = _convert(cur, cost, str(r.get("currency") or hotel.currency).strip(), hotel.currency, start)
        total += cost
        if r.get("is_ota"):
            ota += cost
        ch = by_channel.setdefault(str(r.get("channel_code") or "UNKNOWN"),
                                   {"is_ota": bool(r.get("is_ota")), "cost_minor": 0})
        ch["cost_minor"] += cost
    return total, ota, by_channel


# ─── Compute ───


def compute_week(cur: Any, hotel_slug: str, week_start: date) -> dict:
    """The pulse for the ISO week starting `week_start` (a Monday is not
    enforced: the range is simply 7 hotel-local days). Minor units, with
    None for every figure whose input or denominator is missing."""
    hotel = get_hotel(cur, hotel_slug)
    start, end = week_start, week_start + timedelta(days=6)
    arn, occ, occ_rev = _occupancy(cur, hotel, start, end)
    sc = ports.treat_sc_as_revenue(cur, hotel_slug)
    rev, rn_by_channel = rooms_revenue(cur, hotel, start, end, sc_as_revenue=sc)
    if rev == 0 and not rn_by_channel and occ_rev:
        # A hotel that sends only occupancy (no reservations) still gets a
        # rooms revenue: the occupancy feed's own figure.
        rev = occ_rev
    dist_rows, dist_ok = ports.distribution_by_night(cur, hotel_slug, start, end)
    dist, ota, by_channel = _dist(cur, hotel, dist_rows, start) if dist_ok else (None, None, {})
    cxl_rows, cxl_ok = ports.cancellation_costs(cur, hotel_slug, start, end)
    leakage = sum(_convert(cur, int(r.get("cost_minor") or 0), str(r.get("currency") or hotel.currency).strip(),
                           hotel.currency, start) for r in cxl_rows) if cxl_ok else None
    recovery = ports.recovery_cost(cur, hotel_slug, start, end)
    channels = []
    for code in sorted(set(by_channel) | set(rn_by_channel)):
        ch = by_channel.get(code, {"is_ota": False, "cost_minor": 0})
        rn = rn_by_channel.get(code, 0)
        channels.append({"channel_code": code, "is_ota": ch["is_ota"], "cost_minor": ch["cost_minor"],
                         "room_nights": rn, "cost_per_rn_minor": calc.div_minor(ch["cost_minor"], rn)})
    return {
        "week_start": start, "week_end": end, "currency": hotel.currency,
        "available_rn": arn, "occupied_rn": occ, "rooms_revenue_minor": rev,
        "distribution_cost_minor": dist, "ota_cost_minor": ota,
        "direct_cost_minor": None if dist is None else dist - (ota or 0),
        "nrevpar_minor": None if dist is None else calc.div_minor(rev - dist, arn),
        "revpar_minor": calc.div_minor(rev, arn),
        "ota_cost_por_minor": None if ota is None else calc.div_minor(ota, occ),
        "cancellation_leakage_minor": leakage,
        "recovery_cost_minor": recovery,
        "sc_as_revenue": sc,
        "channels": channels,
    }


# ─── Store and read ───

_STORED = ("available_rn", "occupied_rn", "rooms_revenue_minor", "distribution_cost_minor", "nrevpar_minor",
           "ota_cost_por_minor", "cancellation_leakage_minor", "recovery_cost_minor")


def store_week(cur: Any, hotel_slug: str, p: dict) -> None:
    detail = {k: p[k] for k in ("ota_cost_minor", "direct_cost_minor", "revpar_minor", "sc_as_revenue", "channels")}
    cur.execute(
        f"""INSERT INTO fin_weekly (hotel_slug, week_start, {", ".join(_STORED)}, currency, detail, computed_at)
            VALUES (%s, %s, {", ".join(["%s"] * len(_STORED))}, %s, %s, now())
            ON CONFLICT (hotel_slug, week_start) DO UPDATE SET
              {", ".join(f"{c} = EXCLUDED.{c}" for c in _STORED)},
              currency = EXCLUDED.currency, detail = EXCLUDED.detail, computed_at = now()""",
        (hotel_slug, p["week_start"], *[p[c] for c in _STORED], p["currency"], Json(detail)),
    )


def to_api(p: dict) -> dict:
    """Minor units plus Decimal strings for every money figure (n/a = None)."""
    ccy = p["currency"]
    out = {
        "week_start": p["week_start"].isoformat(),
        "week_end": (p["week_start"] + timedelta(days=6)).isoformat(),
        "currency": ccy,
        "available_rn": p.get("available_rn"),
        "occupied_rn": p.get("occupied_rn"),
    }
    for k in ("rooms_revenue_minor", "distribution_cost_minor", "ota_cost_minor", "direct_cost_minor",
              "nrevpar_minor", "revpar_minor", "ota_cost_por_minor", "cancellation_leakage_minor",
              "recovery_cost_minor"):
        v = p.get(k)
        out[k] = v
        out[k.removesuffix("_minor")] = calc.money_or_none(v, ccy)
    out["channels"] = [{**c, "cost": calc.money_or_none(c["cost_minor"], ccy),
                        "cost_per_rn": calc.money_or_none(c.get("cost_per_rn_minor"), ccy)}
                       for c in p.get("channels") or []]
    out["sc_as_revenue"] = p.get("sc_as_revenue")
    return out


def row_to_pulse(r: dict) -> dict:
    detail = r.get("detail") or {}
    return {**{k: r[k] for k in _STORED}, "week_start": r["week_start"], "currency": r["currency"].strip(),
            "ota_cost_minor": detail.get("ota_cost_minor"), "direct_cost_minor": detail.get("direct_cost_minor"),
            "revpar_minor": detail.get("revpar_minor"), "sc_as_revenue": detail.get("sc_as_revenue"),
            "channels": detail.get("channels") or []}


def stored_week(cur: Any, hotel_slug: str, week_start: date) -> dict | None:
    cur.execute("SELECT * FROM fin_weekly WHERE hotel_slug = %s AND week_start = %s", (hotel_slug, week_start))
    r = cur.fetchone()
    return row_to_pulse(r) if r else None


def list_weeks(cur: Any, hotel_slug: str, start: date, end: date) -> list[dict]:
    cur.execute("""SELECT * FROM fin_weekly WHERE hotel_slug = %s AND week_start BETWEEN %s AND %s
                   ORDER BY week_start""", (hotel_slug, start, end))
    return [row_to_pulse(r) for r in cur.fetchall()]
