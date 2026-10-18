"""
Ticketmaster Discovery API for the UAE (02 spec 4.2): weekly, countryCode=AE,
low trust. Most UAE ticketing runs through Platinumlist or venue sites, so
coverage is thin (research section 1, UNVERIFIED); Ticketmaster dates rank
below every source except AI discovery.

GET https://app.ticketmaster.com/discovery/v2/events.json?apikey=..&countryCode=AE
    &startDateTime=..&endDateTime=..&size=200&page=N   (size x page < 1000)

The key is read from TICKETMASTER_API_KEY (not a modelled setting yet:
FOUNDATION-REQUEST: add TICKETMASTER_API_KEY to Settings and .env.example).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.modules.demand import events
from clefs.modules.demand.adapters import vendor_json
from clefs.settings import env

URL = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE = 200
MAX_PAGES = 4  # size x page must stay under 1000
HORIZON_DAYS = 180
SEGMENT_MAP = {"music": "CONCERT", "sports": "SPORTS", "arts & theatre": "CONCERT", "family": "COMMUNITY",
               "miscellaneous": "COMMUNITY"}
STATUS_MAP = {"cancelled": "CANCELLED", "canceled": "CANCELLED", "postponed": "POSTPONED",
              "rescheduled": "RESCHEDULED"}


def api_key() -> str | None:
    return env("TICKETMASTER_API_KEY")


# ─── Parsing (pure; fixture-tested) ───


def _d(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def parse_event(ev: dict) -> dict | None:
    dates = ev.get("dates") or {}
    start = _d((dates.get("start") or {}).get("localDate"))
    if not start or not ev.get("name"):
        return None
    end = _d((dates.get("end") or {}).get("localDate")) or start
    seg = ((((ev.get("classifications") or [{}])[0]).get("segment") or {}).get("name") or "").lower()
    category = SEGMENT_MAP.get(seg)
    if category is None:
        return None  # film and undefined segments do not move hotel demand
    venue = ((ev.get("_embedded") or {}).get("venues") or [{}])[0]
    loc = venue.get("location") or {}
    lat = float(loc["latitude"]) if loc.get("latitude") else None
    lon = float(loc["longitude"]) if loc.get("longitude") else None
    initial = _d((dates.get("initialStartDate") or {}).get("localDate"))
    return {
        "title": ev["name"], "category": category, "start": start, "end": max(end, start),
        "status": STATUS_MAP.get(((dates.get("status") or {}).get("code") or "").lower()),
        "lat": lat, "lon": lon, "venue_text": venue.get("name"),
        "urls": [ev["url"]] if ev.get("url") else [],
        "original_start": initial if initial and initial != start else None,
        "original_end": initial if initial and initial != start else None,
        "verification": "VERIFIED",
    }


def parse_page(payload: dict) -> list[tuple[str, dict, dict]]:
    """[(external id, candidate, raw event)] for one response page."""
    out = []
    for ev in ((payload.get("_embedded") or {}).get("events") or []):
        cand = parse_event(ev)
        if cand and ev.get("id"):
            out.append((str(ev["id"]), cand, ev))
    return out


# ─── Sync (global; one run covers every UAE hotel) ───


def fetch(today: date) -> list[dict]:
    pages: list[dict] = []
    for page in range(MAX_PAGES):
        body = vendor_json("ticketmaster", "GET", URL, params={
            "apikey": api_key(), "countryCode": "AE", "size": str(PAGE_SIZE), "page": str(page), "sort": "date,asc",
            "startDateTime": f"{today.isoformat()}T00:00:00Z",
            "endDateTime": f"{(today + timedelta(days=HORIZON_DAYS)).isoformat()}T23:59:59Z",
        })
        pages.append(body)
        total = int(((body.get("page") or {}).get("totalPages")) or 0)
        if page + 1 >= total:
            break
    return pages


def sync(cur: Any, *, pages: list[dict] | None = None, today: date | None = None) -> dict:
    if pages is None and not api_key():
        return {"skipped": "no_key"}
    today = today or clock.now().date()
    pages = fetch(today) if pages is None else pages
    venue_rows = events.venues(cur)
    out = {"fetched": 0, "ingested": 0, "created": 0, "changed": 0}
    for payload in pages:
        for ext_id, cand, raw in parse_page(payload):
            out["fetched"] += 1
            res = events.ingest(cur, cand, source="TICKETMASTER", external_id=ext_id, payload=raw, venue_rows=venue_rows)
            out["ingested"] += 1
            out["created"] += int(res["created"])
            out["changed"] += int(bool(res["changes"]))
    clefs_db.execute(
        "INSERT INTO dr_sync_state (source, scope, last_run_at, last_result) VALUES ('TICKETMASTER', 'AE', now(), %s) "
        "ON CONFLICT (source, scope) DO UPDATE SET last_run_at = now(), last_result = EXCLUDED.last_result",
        (clefs_db.Json(out),), cur=cur,
    )
    return out
