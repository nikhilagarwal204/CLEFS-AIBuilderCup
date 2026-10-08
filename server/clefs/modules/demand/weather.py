"""
Demand Radar weather: the Open-Meteo hourly cache behind
demand.api.outdoor_temp_c (Canon 12).

- Job dr.weather_fetch (every 3 hours, per hotel) pulls yesterday plus a
  7-day hourly forecast from Open-Meteo (no key) at the hotel's coordinates,
  else its emirate's centroid, and upserts dr_weather by hour (UTC).
- outdoor_temp_c(hotel, at) never calls the network: it runs on Service's
  request intake (AC above 40 C becomes L1), so it reads the cache only,
  memoised per hotel and hour for 10 minutes. No reading within 90 minutes
  of `at` returns None and Service falls back to its June-September
  11:00-17:00 heuristic.
- Licence: the free Open-Meteo API is for non-commercial use. Setting
  OPEN_METEO_API_KEY switches to the commercial customer endpoint with the
  same response shape.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from clefs import db as clefs_db
from clefs import jobs
from clefs.core import clock
from clefs.core.time import local_range, tz_of
from clefs.modules.demand.adapters import VendorError, vendor_json
from clefs.settings import env

log = logging.getLogger("clefs.demand.weather")

FREE_URL = "https://api.open-meteo.com/v1/forecast"
CUSTOMER_URL = "https://customer-api.open-meteo.com/v1/forecast"
HOURLY = ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation", "weather_code",
          "wind_speed_10m")
PAST_DAYS = 1
FORECAST_DAYS = 7
MATCH_WINDOW = timedelta(minutes=90)
KEEP_PAST = timedelta(days=14)
MEMO_TTL_S = 600
# Emirate centroids for a hotel without coordinates (approximate; weather is
# regional enough for the 40 C rule).
EMIRATE_CENTROIDS = {
    "dubai": (25.2048, 55.2708), "abu_dhabi": (24.4539, 54.3773), "sharjah": (25.3463, 55.4209),
    "ajman": (25.4052, 55.5136), "ras_al_khaimah": (25.8007, 55.9762), "fujairah": (25.1288, 56.3265),
    "umm_al_quwain": (25.5647, 55.5552),
}

# slug -> (loaded at (monotonic), window start, window end, {hour: temp_c}).
# One query loads a day and a half of hours, so the intake path touches the
# database about once per hotel per MEMO_TTL_S, not once per request.
_memo: dict[str, tuple[float, datetime, datetime, dict[datetime, float]]] = {}
MEMO_BEFORE = timedelta(hours=6)
MEMO_AFTER = timedelta(hours=30)
_memo_lock = threading.Lock()


# ─── Fetch and parse ───


def coords(hotel: Any) -> tuple[float, float] | None:
    lat, lng = getattr(hotel, "lat", None), getattr(hotel, "lng", None)
    if lat is not None and lng is not None:
        return float(lat), float(lng)
    emirate = str(getattr(hotel, "emirate", None) or "").lower().replace(" ", "_").replace("-", "_")
    return EMIRATE_CENTROIDS.get(emirate)


def parse_hourly(body: dict) -> list[dict]:
    """Open-Meteo `hourly` arrays (timeformat=unixtime, UTC) -> rows. Hours
    with no temperature are dropped."""
    h = body.get("hourly") if isinstance(body, dict) else None
    if not isinstance(h, dict) or not isinstance(h.get("time"), list):
        return []
    out = []

    def col(name: str, i: int) -> Any:
        vals = h.get(name) or []
        return vals[i] if i < len(vals) else None

    for i, t in enumerate(h["time"]):
        temp = col("temperature_2m", i)
        if temp is None or not isinstance(t, (int, float)):
            continue
        out.append({
            "at_hour": datetime.fromtimestamp(int(t), tz=timezone.utc),
            "temp_c": Decimal(str(temp)).quantize(Decimal("0.1")),
            "apparent_c": _dec(col("apparent_temperature", i)),
            "humidity_pct": _int(col("relative_humidity_2m", i)),
            "precip_mm": _dec(col("precipitation", i), "0.01"),
            "weather_code": _int(col("weather_code", i)),
            "wind_kmh": _dec(col("wind_speed_10m", i)),
        })
    return out


def _dec(v: Any, q: str = "0.1") -> Decimal | None:
    return Decimal(str(v)).quantize(Decimal(q)) if isinstance(v, (int, float)) else None


def _int(v: Any) -> int | None:
    return int(round(v)) if isinstance(v, (int, float)) else None


def fetch(lat: float, lon: float) -> list[dict]:
    key = (env("OPEN_METEO_API_KEY", "") or "").strip()
    params: dict[str, Any] = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(HOURLY),
                              "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS, "timezone": "GMT",
                              "timeformat": "unixtime"}
    if key:
        params["apikey"] = key
    return parse_hourly(vendor_json("open-meteo", "GET", CUSTOMER_URL if key else FREE_URL, params=params))


# ─── Store ───


def store(cur: Any, hotel_slug: str, rows: list[dict], *, fetched_at: datetime) -> int:
    """Upsert by hour. Hours before the fetch time are observed (Open-Meteo
    backfills them from its models), later ones forecast."""
    for r in rows:
        cur.execute(
            """INSERT INTO dr_weather (hotel_slug, at_hour, temp_c, apparent_c, humidity_pct, precip_mm,
                                       weather_code, wind_kmh, is_forecast, fetched_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (hotel_slug, at_hour) DO UPDATE SET
                   temp_c = EXCLUDED.temp_c, apparent_c = EXCLUDED.apparent_c,
                   humidity_pct = EXCLUDED.humidity_pct, precip_mm = EXCLUDED.precip_mm,
                   weather_code = EXCLUDED.weather_code, wind_kmh = EXCLUDED.wind_kmh,
                   is_forecast = EXCLUDED.is_forecast, fetched_at = EXCLUDED.fetched_at""",
            (hotel_slug, r["at_hour"], r["temp_c"], r["apparent_c"], r["humidity_pct"], r["precip_mm"],
             r["weather_code"], r["wind_kmh"], r["at_hour"] > fetched_at, fetched_at))
    _memo_clear(hotel_slug)
    return len(rows)


def sync_hotel(cur: Any, hotel: Any) -> dict:
    where = coords(hotel)
    if where is None:
        return {"skipped": "no_coordinates"}
    rows = fetch(*where)
    if not rows:
        return {"skipped": "empty_answer"}
    return {"hours": store(cur, hotel.slug, rows, fetched_at=clock.now()), "lat": where[0], "lon": where[1]}


def purge_old(cur: Any, *, now: datetime) -> int:
    cur.execute("DELETE FROM dr_weather WHERE at_hour < %s", (now - KEEP_PAST,))
    return cur.rowcount


# ─── Reads ───


def _memo_clear(hotel_slug: str | None = None) -> None:
    with _memo_lock:
        for k in [k for k in _memo if hotel_slug is None or k == hotel_slug]:
            _memo.pop(k, None)


def reading_at(cur: Any, hotel_slug: str, at: datetime) -> dict | None:
    """The cached hour nearest `at`, within MATCH_WINDOW."""
    return clefs_db.fetch_one(
        """SELECT * FROM dr_weather WHERE hotel_slug = %s AND at_hour BETWEEN %s AND %s
            ORDER BY abs(extract(epoch FROM at_hour - %s::timestamptz)) LIMIT 1""",
        (hotel_slug, at - MATCH_WINDOW, at + MATCH_WINDOW, at), cur=cur)


def outdoor_temp_c(hotel: Any, at: datetime) -> float | None:
    """demand.api.outdoor_temp_c: cache only, never the network (it sits on
    the request-intake path). Any failure is None: the caller's heuristic
    is the fallback, never an exception."""
    slug = hotel if isinstance(hotel, str) else getattr(hotel, "slug", None)
    if not slug or not isinstance(at, datetime):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    at = at.astimezone(timezone.utc)
    now_m = time.monotonic()
    with _memo_lock:
        hit = _memo.get(slug)
    if hit is None or now_m - hit[0] >= MEMO_TTL_S or not hit[1] <= at <= hit[2]:
        t0, t1 = at - MEMO_BEFORE, at + MEMO_AFTER
        try:
            with clefs_db.db(readonly=True) as cur:
                rows = clefs_db.fetch_all(
                    "SELECT at_hour, temp_c FROM dr_weather WHERE hotel_slug = %s AND at_hour BETWEEN %s AND %s "
                    "AND temp_c IS NOT NULL", (slug, t0 - MATCH_WINDOW, t1 + MATCH_WINDOW), cur=cur)
        except Exception:
            log.exception("weather cache read failed for %s", slug)
            return None
        hit = (now_m, t0, t1, {r["at_hour"]: float(r["temp_c"]) for r in rows})
        with _memo_lock:
            if len(_memo) > 2000:
                _memo.clear()
            _memo[slug] = hit
    near = [(abs(h - at), t) for h, t in hit[3].items() if abs(h - at) <= MATCH_WINDOW]
    return min(near, key=lambda x: x[0])[1] if near else None

def daily(cur: Any, hotel: Any, start: date, end: date) -> list[dict]:
    """Per hotel-local day in [start, end]: max / min temperature, total
    precipitation and the hours covered (the calendar's weather strip)."""
    t0, t1 = local_range(hotel, start, end)
    rows = clefs_db.fetch_all(
        """SELECT (at_hour AT TIME ZONE %s)::date AS day, max(temp_c) AS max_c, min(temp_c) AS min_c,
                  sum(precip_mm) AS precip_mm, max(apparent_c) AS max_apparent_c, count(*) AS hours,
                  bool_and(is_forecast) AS forecast
             FROM dr_weather WHERE hotel_slug = %s AND at_hour >= %s AND at_hour < %s
            GROUP BY 1 ORDER BY 1""",
        (str(tz_of(hotel)), hotel.slug, t0, t1), cur=cur)
    return [{"date": r["day"].isoformat(), "max_c": _f(r["max_c"]), "min_c": _f(r["min_c"]),
             "max_apparent_c": _f(r["max_apparent_c"]), "precip_mm": _f(r["precip_mm"]),
             "hours": r["hours"], "forecast": r["forecast"]} for r in rows]


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def status(cur: Any, hotel: Any) -> dict:
    r = clefs_db.fetch_one("SELECT max(fetched_at) AS at, count(*) AS n FROM dr_weather WHERE hotel_slug = %s",
                           (hotel.slug,), cur=cur)
    where = coords(hotel)
    return {"last_fetched_at": r["at"].isoformat() if r and r["at"] else None, "hours_cached": r["n"] if r else 0,
            "coordinates": {"lat": where[0], "lon": where[1]} if where else None,
            "from_hotel": getattr(hotel, "lat", None) is not None and getattr(hotel, "lng", None) is not None,
            "provider": "open-meteo", "commercial_key": bool((env("OPEN_METEO_API_KEY", "") or "").strip())}


# ─── Jobs ───


@jobs.scheduled("dr.weather_fetch", jobs.every(hours=3), timeout_s=60)
def _weather_fetch(ctx: jobs.JobCtx) -> dict:
    try:
        return sync_hotel(ctx.cur, ctx.hotel)
    except VendorError as exc:
        if exc.transient:
            raise
        return {"skipped": "vendor_error", "status": exc.status}

