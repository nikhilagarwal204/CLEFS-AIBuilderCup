"""
PredictHQ Features API (02 spec 4.2): POST /v1/features/ with a 10 km
radius around the hotel; one row per day with the attendance sums of
conferences, expos, sports, concerts and festivals, plus the public-holiday
rank level. Stored in dr_demand_features_daily for the demand index.

Needs PREDICTHQ_TOKEN; without it the job reports {"skipped": "no_token"}.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs import db as clefs_db
from clefs.core.time import local_date
from clefs.modules.demand.adapters import vendor_json
from clefs.modules.demand.adapters.phq_events import token

URL = "https://api.predicthq.com/v1/features/"
RADIUS = "10km"
HORIZON_DAYS = 180
ATTENDANCE_FEATURES = tuple(f"phq_attendance_{c}" for c in ("conferences", "expos", "sports", "concerts", "festivals"))
RANK_FEATURES = ("phq_rank_public_holidays",)


# ─── Request and parsing (pure; fixture-tested) ───


def request_body(lat: float, lon: float, start: date, end: date) -> dict:
    body: dict[str, Any] = {
        "active": {"gte": start.isoformat(), "lte": end.isoformat()},
        "location": {"geo": {"lat": round(lat, 5), "lon": round(lon, 5), "radius": RADIUS}},
    }
    for f in ATTENDANCE_FEATURES:
        body[f] = {"stats": ["sum", "count"]}
    for f in RANK_FEATURES:
        body[f] = True
    return body


def parse_rows(payload: dict) -> list[tuple[date, str, float]]:
    """(date, feature, value) triples. Attendance features give their daily
    sum; rank features give the highest PHQ rank level (1-5) present that
    day, 0 when none."""
    out: list[tuple[date, str, float]] = []
    for row in payload.get("results") or []:
        try:
            d = date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        for f in ATTENDANCE_FEATURES:
            stats = ((row.get(f) or {}).get("stats") or {})
            if f in row:
                out.append((d, f, float(stats.get("sum") or 0)))
        for f in RANK_FEATURES:
            levels = (row.get(f) or {}).get("rank_levels") or {}
            if f in row:
                present = [int(k) for k, v in levels.items() if str(k).isdigit() and (v or 0) > 0]
                out.append((d, f, float(max(present) if present else 0)))
    return out


# ─── Sync ───


def store(cur: Any, hotel_slug: str, rows: list[tuple[date, str, float]]) -> int:
    for d, f, v in rows:
        clefs_db.execute(
            "INSERT INTO dr_demand_features_daily (hotel_slug, date, feature, value, source, fetched_at) "
            "VALUES (%s, %s, %s, %s, 'PREDICTHQ', now()) ON CONFLICT (hotel_slug, date, feature) "
            "DO UPDATE SET value = EXCLUDED.value, fetched_at = now()",
            (hotel_slug, d, f, v), cur=cur,
        )
    return len(rows)


def sync_hotel(cur: Any, hotel: Any, *, payload: dict | None = None) -> dict:
    if payload is None and not token():
        return {"skipped": "no_token"}
    if hotel.lat is None or hotel.lng is None:
        return {"skipped": "no_location"}
    today = local_date(hotel)
    if payload is None:
        payload = vendor_json(
            "predicthq", "POST", URL, headers={"Authorization": f"Bearer {token()}", "Accept": "application/json"},
            json=request_body(float(hotel.lat), float(hotel.lng), today, today + timedelta(days=HORIZON_DAYS)),
        )
    rows = parse_rows(payload)
    return {"days": len({d for d, _, _ in rows}), "values": store(cur, hotel.slug, rows)}


def features_for(cur: Any, hotel_slug: str, start: date, end: date) -> dict[str, dict[str, float]]:
    """{iso date: {feature: value}} for the demand index and the day drawer."""
    out: dict[str, dict[str, float]] = {}
    for r in clefs_db.fetch_all(
        "SELECT date, feature, value FROM dr_demand_features_daily WHERE hotel_slug = %s AND date BETWEEN %s AND %s "
        "ORDER BY date, feature", (hotel_slug, start, end), cur=cur,
    ):
        out.setdefault(r["date"].isoformat(), {})[r["feature"]] = float(r["value"])
    return out
