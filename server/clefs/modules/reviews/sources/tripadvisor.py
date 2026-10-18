"""
Tripadvisor Content API (03 spec 4.1): the 5 most recent reviews per call,
polled daily and accumulated in rev_reviews (the API never pages further, so
history builds up day by day).

Key: TRIPADVISOR_API_KEY (a Settings field and in .env.example; read here
through settings.env so a test's monkeypatched environment is seen without
resetting the cached Settings). Source config: {location_id, language?}.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clefs.core.errors import NotConfigured
from clefs.modules.reviews import http, store
from clefs.settings import env

API = "https://api.content.tripadvisor.com/api/v1"


def _parse_ts(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def to_review(r: dict) -> store.ReviewIn:
    user = r.get("user") or {}
    return store.ReviewIn(
        platform="tripadvisor", external_id=str(r.get("id")), rating=r.get("rating"), rating_scale=5,
        author_display=user.get("username"), title=r.get("title"), text=r.get("text"),
        language=r.get("lang"), posted_at=_parse_ts(r.get("published_date")),
        has_owner_response=bool(r.get("owner_response")), platform_ref=r.get("url"),
        stay_date_guess=_stay_month(r.get("travel_date")),
    )


def _stay_month(s: Any) -> Any:
    """'2026-08' (travel month) -> the 1st of that month."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:7], "%Y-%m").date()
    except ValueError:
        return None


def sync(cur: Any, hotel: Any, source: dict, **_: Any) -> dict:
    key = env("TRIPADVISOR_API_KEY")
    if not key:
        raise NotConfigured("Tripadvisor (TRIPADVISOR_API_KEY)")
    cfg = source.get("config") or {}
    loc = str(cfg.get("location_id") or "").strip()
    if not loc.isdigit():
        return {"fetched": 0, "created": 0, "error": "location_id_missing"}
    body = http.request_json("tripadvisor", "GET", f"{API}/location/{loc}/reviews",
                             params={"key": key, "language": cfg.get("language") or "en"},
                             headers={"accept": "application/json"})
    created = 0
    items = [to_review(r) for r in body.get("data") or []]
    for r in items:
        _, is_new = store.ingest(cur, hotel.slug, source["id"], r)
        created += int(is_new)
    return {"fetched": len(items), "created": created}
