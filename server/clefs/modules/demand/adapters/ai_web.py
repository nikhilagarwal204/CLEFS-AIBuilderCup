"""
AI web discovery (02 spec 4.2): a weekly Responses API call with the
web_search tool (clefs.ai.web_search) asking for upcoming events near the
hotel. Output schema [{title, venue, start, end, url, category, confidence}].

Guard rails, because a model can invent an expo:
- The answer must carry at least one url citation, or nothing is kept.
- Every event needs its own http(s) URL; one whose domain none of the
  citations names is kept at confidence 0.3 (uncited claim).
- Results are always UNVERIFIED with confidence capped at 0.6 (events.ingest
  enforces both), hotel-scoped, and may drive a recommendation only once
  another source corroborates them or the owner confirms them.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel

from clefs import ai
from clefs.core.time import local_date
from clefs.modules.demand import dedup, events

TASK = "demand.ai_discovery"
HORIZON_DAYS = 120
UNCITED_CONFIDENCE = 0.3
CATEGORY_MAP = {"conference": "CONFERENCE", "expo": "EXPO", "exhibition": "EXPO", "sports": "SPORTS",
                "concert": "CONCERT", "festival": "FESTIVAL", "community": "COMMUNITY"}


class DiscoveredEvent(BaseModel):
    title: str
    venue: str
    start: str  # YYYY-MM-DD
    end: str  # YYYY-MM-DD ("" when single day)
    url: str
    category: str  # conference | expo | sports | concert | festival | community
    confidence: float


class Discovery(BaseModel):
    events: list[DiscoveredEvent]


SYSTEM = (
    "You list real, scheduled events from current web results only. Never guess dates: omit an event whose "
    "dates you cannot find on a web page. Give the official event URL. confidence is 0-1: how sure the dates "
    "are right (a past edition's dates are not this year's)."
)


def query_for(hotel: Any, today: date) -> str:
    city = (hotel.city or "").strip() or "Dubai"
    return (
        f"Upcoming conferences, trade exhibitions, concerts, sports events and festivals in {city}, United Arab "
        f"Emirates between {today.isoformat()} and {(today + timedelta(days=HORIZON_DAYS)).isoformat()} that draw "
        "visitors who need hotel rooms. For each: title, venue, start and end date (YYYY-MM-DD), the official URL, "
        "a category (conference, expo, sports, concert, festival or community) and your confidence."
    )


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def candidates(result: Any, today: date) -> list[dict]:
    """Event candidates from a web_search SearchResult (pure; tested with
    FakeBackend answers)."""
    cited = {_domain(c.url) for c in (result.citations or []) if getattr(c, "url", None)}
    if not cited:
        return []
    out = []
    for e in result.data.events:
        try:
            start = date.fromisoformat(e.start.strip()[:10])
            end = date.fromisoformat(e.end.strip()[:10]) if e.end.strip() else start
        except ValueError:
            continue
        url = e.url.strip()
        if not url.startswith(("http://", "https://")) or end < today or end < start:
            continue
        if (end - start).days > 60:
            continue  # a "two-month expo" is a season listing, not an event
        conf = min(max(float(e.confidence or 0), 0.0), dedup.AI_CONFIDENCE_CAP)
        if _domain(url) not in cited:
            conf = min(conf, UNCITED_CONFIDENCE)
        urls = [url] + [c.url for c in result.citations if _domain(c.url) == _domain(url) and c.url != url]
        out.append({
            "title": e.title, "category": CATEGORY_MAP.get(e.category.strip().lower(), "COMMUNITY"),
            "venue_text": e.venue, "start": start, "end": end, "urls": urls[:5],
            "confidence": conf, "verification": "UNVERIFIED",
        })
    return out


def external_id(hotel_slug: str, cand: dict) -> str:
    key = f"{dedup.norm_title(cand['title'])}|{cand['start'].isoformat()}"
    return f"{hotel_slug}:{hashlib.sha1(key.encode()).hexdigest()[:16]}"


def discover(cur: Any, hotel: Any) -> dict:
    today = local_date(hotel)
    try:
        result = ai.web_search(TASK, query=query_for(hotel, today), schema=Discovery, system=SYSTEM,
                               hotel_slug=hotel.slug)
    except ai.AIDisabled:
        return {"skipped": "ai_disabled"}
    cands = candidates(result, today)
    if not result.citations:
        return {"skipped": "no_citations", "returned": len(result.data.events)}
    venue_rows = events.venues(cur)
    out = {"returned": len(result.data.events), "kept": len(cands), "created": 0, "corroborated": 0}
    for c in cands:
        res = events.ingest(cur, c, source="AI_WEB", external_id=external_id(hotel.slug, c), hotel_slug=hotel.slug,
                            payload={"title": c["title"], "urls": c["urls"]}, venue_rows=venue_rows)
        out["created"] += int(res["created"])
        out["corroborated"] += int(res["matched"])
    return out
