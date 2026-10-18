"""
Google Maps reviews through SerpApi (03 spec 4.1; ported from
review-intel/hotel_review_intel.py find_hotel_on_google_maps and
fetch_google_reviews).

Every call goes through core.serpapi.search with purpose 'reviews.sync'
(Canon K3, 04 spec F5): one ledger, one global budget. The sync is
incremental: newest first, and it stops at the first review already stored
(at most `max_pages` pages a day), so a quiet hotel costs one search a day.
Vendor risk (Canon 20): off until the owner accepts the disclosure
(settings.serpapi_enabled), and skipped once GBP is connected.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clefs.core import serpapi
from clefs.modules.reviews import store

PURPOSE = "reviews.sync"


# ─── Step 1: find the hotel on Google Maps ───


def find_place(hotel_name: str, location: str, *, hotel_slug: str) -> dict | None:
    """{title, place_id, data_id, address, rating, reviews_count} or None."""
    res = serpapi.search("google_maps", {"q": f"{hotel_name} {location}".strip(), "type": "search", "hl": "en"},
                         hotel_slug=hotel_slug, purpose=PURPOSE)
    place = res.get("place_results") or (res.get("local_results") or [None])[0]
    if not place:
        return None
    return {
        "title": place.get("title"),
        "place_id": place.get("place_id"),
        "data_id": place.get("data_id"),
        "address": place.get("address"),
        "rating": place.get("rating"),
        "reviews_count": place.get("reviews"),
    }


# ─── Step 2: fetch reviews, newest first ───


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def to_review(r: dict) -> store.ReviewIn:
    """One SerpApi google_maps_reviews item -> ReviewIn. The original-language
    text is preferred over Google's translation: replies go out in the
    guest's language."""
    extracted = r.get("extracted_snippet") or {}
    text = extracted.get("original") or r.get("snippet") or ""
    resp = r.get("response") or {}
    author = (r.get("user") or {}).get("name")
    posted = _parse_iso(r.get("iso_date"))
    ext = r.get("review_id") or store.synthetic_id(author, r.get("iso_date"), text[:80])
    return store.ReviewIn(
        platform="google", external_id=str(ext), rating=r.get("rating"), rating_scale=5,
        author_display=author, text=text, posted_at=posted,
        has_owner_response=bool(resp), owner_response_at=_parse_iso(resp.get("iso_date")) if resp else None,
        platform_ref=r.get("link"),
    )


def fetch_new(data_id: str, *, hotel_slug: str, known: Any, max_pages: int = 5) -> list[store.ReviewIn]:
    """Pages of newest-first reviews until a known one (known(ids) -> set)
    or `max_pages`. The first page is cached for 20 h by core.serpapi, so a
    second sync the same day is free."""
    out: list[store.ReviewIn] = []
    token: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {"data_id": data_id, "sort_by": "newestFirst", "hl": "en"}
        if token:
            params["next_page_token"] = token
            params["num"] = 20  # SerpApi accepts num only with a page token
        res = serpapi.search("google_maps_reviews", params, hotel_slug=hotel_slug, purpose=PURPOSE)
        page = [to_review(r) for r in (res.get("reviews") or [])]
        if not page:
            break
        seen = known([r.external_id for r in page])
        for r in page:
            if r.external_id in seen:
                return out
            out.append(r)
        token = (res.get("serpapi_pagination") or {}).get("next_page_token")
        if not token:
            break
    return out


# ─── Sync one source ───


def sync(cur: Any, hotel: Any, source: dict, *, max_pages: int) -> dict:
    """Resolve the place once (stored in config), then fetch new reviews."""
    config = dict(source.get("config") or {})
    if not config.get("data_id"):
        place = find_place(config.get("query") or hotel.name, config.get("location") or hotel.city or "",
                           hotel_slug=hotel.slug)
        if not place or not place.get("data_id"):
            return {"fetched": 0, "created": 0, "error": "place_not_found"}
        config.update({k: place[k] for k in ("title", "place_id", "data_id", "address") if place.get(k)})
        store.update_source(cur, hotel.slug, source["id"], config=config)
    items = fetch_new(config["data_id"], hotel_slug=hotel.slug, max_pages=max_pages,
                      known=lambda ids: store.known_external_ids(cur, hotel.slug, "google", ids))
    created = 0
    for r in items:
        _, is_new = store.ingest(cur, hotel.slug, source["id"], r)
        created += int(is_new)
    return {"fetched": len(items), "created": created, "place_id": config.get("place_id")}
