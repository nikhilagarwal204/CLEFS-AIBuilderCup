"""
PredictHQ Events API (02 spec 4.2), run daily and incrementally with
updated.gte = the stored cursor:

GET /v1/events/?within=60km@{lat},{lon}&active.gte={today}&active.lte={+180d}
    &category=conferences,expos,...&state=active,predicted&rank.gte=40&limit=500

Needs PREDICTHQ_TOKEN (Bearer). No token means the job reports
{"skipped": "no_token"} and the curated seed carries the calendar alone.
Coverage of the UAE is UNVERIFIED (research section 1), which is why the
curated seed outranks PredictHQ on dates.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.time import local_date
from clefs.modules.demand import events
from clefs.modules.demand.adapters import vendor_json
from clefs.settings import get_settings

BASE = "https://api.predicthq.com/v1/events/"
CATEGORIES = ("conferences", "expos", "sports", "concerts", "festivals", "performing-arts", "community",
              "public-holidays", "school-holidays")
CATEGORY_MAP = {
    "conferences": "CONFERENCE", "expos": "EXPO", "sports": "SPORTS", "concerts": "CONCERT",
    "festivals": "FESTIVAL", "performing-arts": "CONCERT", "community": "COMMUNITY",
    "public-holidays": "PUBLIC_HOLIDAY", "school-holidays": "SCHOOL_HOLIDAY", "observances": "RELIGIOUS",
}
RADIUS_KM = 60
HORIZON_DAYS = 180
MIN_RANK = 40
PAGE_LIMIT = 500
MAX_PAGES = 10
# Hijri observances follow the moon sighting (Canon: +-1 day window).
_HIJRI = re.compile(r"\b(eid|ramadan|arafat|islamic new year|hijri|muharram|prophet|mawlid|isra)", re.I)


# ─── Parsing (pure; fixture-tested) ───


def _local_day(ts: str | None, tz: ZoneInfo) -> tuple[date | None, bool]:
    """(local date, is exactly local midnight) of an ISO timestamp."""
    if not ts:
        return None, False
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.date(), dt.time() == datetime.min.time()
    loc = dt.astimezone(tz)
    return loc.date(), loc.time() == datetime.min.time()


def _coords(ev: dict) -> tuple[float | None, float | None]:
    geom = ((ev.get("geo") or {}).get("geometry") or {})
    if geom.get("type") == "Point" and len(geom.get("coordinates") or []) == 2:
        lon, lat = geom["coordinates"]
        return float(lat), float(lon)
    loc = ev.get("location") or []
    if len(loc) == 2:
        return float(loc[1]), float(loc[0])
    return None, None


def parse_event(ev: dict, *, default_tz: str = "Asia/Dubai") -> dict | None:
    """One PredictHQ event as an event candidate, or None to skip it
    (duplicate/invalid/archived records, unknown categories)."""
    state = (ev.get("state") or "active").lower()
    if state == "deleted":
        # A cancelled or postponed event is "deleted" with a reason; a
        # duplicate or invalid record is noise and is skipped.
        state = (ev.get("deleted_reason") or "").lower()
    category = CATEGORY_MAP.get(ev.get("category") or "")
    if state not in ("active", "predicted", "cancelled", "postponed") or not category or not ev.get("title"):
        return None
    tz = ZoneInfo(ev.get("timezone") or default_tz)
    start, _ = _local_day(ev.get("start_local") or ev.get("start"), tz)
    end, midnight = _local_day(ev.get("end_local") or ev.get("end") or ev.get("predicted_end"), tz)
    if start is None:
        return None
    if end is None or end < start:
        end = start
    elif midnight and end > start:
        end -= timedelta(days=1)  # an end at local midnight is exclusive
    lat, lon = _coords(ev)
    venue = next((e for e in ev.get("entities") or [] if e.get("type") == "venue"), None)
    spend = ((ev.get("predicted_event_spend_industries") or {}).get("accommodation"))
    moon = bool(_HIJRI.search(ev["title"])) and category in ("PUBLIC_HOLIDAY", "RELIGIOUS")
    return {
        "title": ev["title"], "category": category, "start": start, "end": end,
        "status": {"cancelled": "CANCELLED", "postponed": "POSTPONED"}.get(state),
        "lat": lat, "lon": lon,
        "venue_text": (f"{venue.get('name') or ''} {venue.get('formatted_address') or ''}".strip() or None)
        if venue else None,
        "attendance": int(ev["phq_attendance"]) if ev.get("phq_attendance") else None,
        "phq_rank": ev.get("rank"), "phq_local_rank": ev.get("local_rank"),
        "spend_minor": int(round(float(spend) * 100)) if spend else None,
        "verification": "UNVERIFIED" if state == "predicted" else "VERIFIED",
        "moon_dependent": moon, "uncertainty_days": 1 if moon else 0,
        "description": (ev.get("description") or "")[:1000] or None,
    }


# ─── Fetch and sync ───


def token() -> str | None:
    return get_settings().PREDICTHQ_TOKEN or None


def request_params(lat: float, lon: float, today: date, *, cursor: str | None = None) -> dict:
    params = {
        "within": f"{RADIUS_KM}km@{lat:.5f},{lon:.5f}",
        "active.gte": today.isoformat(), "active.lte": (today + timedelta(days=HORIZON_DAYS)).isoformat(),
        "category": ",".join(CATEGORIES), "state": "active,predicted", "rank.gte": str(MIN_RANK),
        "limit": str(PAGE_LIMIT), "sort": "updated",
    }
    if cursor:
        # Incremental runs also ask for deleted records: that is how a
        # cancellation or postponement arrives (deleted_reason).
        params["updated.gte"] = cursor
        params["state"] = "active,predicted,deleted"
    return params


def fetch(lat: float, lon: float, today: date, *, cursor: str | None = None) -> list[dict]:
    """Every page of results (bounded: MAX_PAGES x PAGE_LIMIT)."""
    headers = {"Authorization": f"Bearer {token()}", "Accept": "application/json"}
    body = vendor_json("predicthq", "GET", BASE, params=request_params(lat, lon, today, cursor=cursor), headers=headers)
    results = list(body.get("results") or [])
    pages = 1
    while body.get("next") and pages < MAX_PAGES:
        if not str(body["next"]).startswith(BASE):
            break  # never send the token to a host the response names
        body = vendor_json("predicthq", "GET", body["next"], headers=headers)
        results.extend(body.get("results") or [])
        pages += 1
    return results


def _scope(lat: float, lon: float) -> str:
    # Hotels on the same block share one cursor (and one fetch per day).
    return f"{lat:.2f},{lon:.2f}"


def sync_hotel(cur: Any, hotel: Any, *, results: list[dict] | None = None) -> dict:
    """Pull the hotel's 60 km radius into the global event store. `results`
    lets a caller (tests, the demo seed) pass a payload instead of fetching."""
    if results is None and not token():
        return {"skipped": "no_token"}
    if hotel.lat is None or hotel.lng is None:
        return {"skipped": "no_location"}
    lat, lon = float(hotel.lat), float(hotel.lng)
    scope = _scope(lat, lon)
    # Hotels on one block run at the same minute: the second waits for the
    # first's transaction, then sees a fresh cursor instead of racing it
    # into duplicate source rows.
    clefs_db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"dr.phq_events:{scope}",), cur=cur)
    state = clefs_db.fetch_one("SELECT cursor, last_run_at FROM dr_sync_state WHERE source = 'PREDICTHQ' AND scope = %s",
                               (scope,), cur=cur)
    started = clock.now()
    if results is None:
        if state and state["last_run_at"] and started - state["last_run_at"] < timedelta(hours=20):
            return {"skipped": "fresh", "scope": scope}
        results = fetch(lat, lon, local_date(hotel), cursor=state["cursor"] if state else None)
    venue_rows = events.venues(cur)
    out = {"fetched": len(results), "ingested": 0, "created": 0, "changed": 0, "skipped_rows": 0}
    newest = state["cursor"] if state else None
    for ev in results:
        cand = parse_event(ev, default_tz=str(hotel.timezone or "Asia/Dubai"))
        if cand is None:
            out["skipped_rows"] += 1
            continue
        res = events.ingest(cur, cand, source="PREDICTHQ", external_id=str(ev["id"]), payload=ev, venue_rows=venue_rows)
        out["ingested"] += 1
        out["created"] += int(res["created"])
        out["changed"] += int(bool(res["changes"]))
        if ev.get("updated") and (newest is None or ev["updated"] > newest):
            newest = ev["updated"]
    clefs_db.execute(
        "INSERT INTO dr_sync_state (source, scope, cursor, last_run_at, last_result) VALUES ('PREDICTHQ', %s, %s, %s, %s) "
        "ON CONFLICT (source, scope) DO UPDATE SET cursor = EXCLUDED.cursor, last_run_at = EXCLUDED.last_run_at, "
        "last_result = EXCLUDED.last_result",
        (scope, newest, started, clefs_db.Json(out)), cur=cur,
    )
    return out
