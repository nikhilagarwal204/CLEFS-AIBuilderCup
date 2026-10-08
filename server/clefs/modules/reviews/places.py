"""
Competitor ratings widget (03 spec 4.1, 04 spec 5.4): a live server-side
proxy to the Google Places API (New). Only place ids are stored
(rev_competitors); rating and userRatingCount are fetched on every view and
never written anywhere (Places terms), not even to a cache.

There is no competitor "unanswered" figure: Places returns at most 5
reviews and no owner replies (04 spec 5.4).
"""

from __future__ import annotations

import re
from typing import Any

from clefs.core.errors import ClefsError, NotConfigured
from clefs.modules.reviews import http
from clefs.settings import get_settings

API = "https://places.googleapis.com/v1/places"
FIELDS = "id,displayName,rating,userRatingCount,googleMapsUri"
MAX_PLACES = 10
_PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{10,300}$")


def valid_place_id(pid: str) -> bool:
    return bool(_PLACE_ID.match(pid or ""))


def get_set(cur: Any, hotel_slug: str) -> dict:
    cur.execute("SELECT place_ids, own_place_id, updated_at FROM rev_competitors WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    return dict(row) if row else {"place_ids": [], "own_place_id": None, "updated_at": None}


def put_set(cur: Any, hotel_slug: str, place_ids: list[str], own_place_id: str | None, *, user_id: int | None) -> dict:
    ids = list(dict.fromkeys(p.strip() for p in place_ids if p and p.strip()))
    if len(ids) > MAX_PLACES:
        raise ClefsError(400, f"At most {MAX_PLACES} competitors", "too_many_places")
    bad = [p for p in ids + ([own_place_id] if own_place_id else []) if not valid_place_id(p)]
    if bad:
        raise ClefsError(400, "Not a Google place id: " + bad[0][:40], "invalid_place_id")
    cur.execute(
        """INSERT INTO rev_competitors (hotel_slug, place_ids, own_place_id, updated_by) VALUES (%s, %s, %s, %s)
           ON CONFLICT (hotel_slug) DO UPDATE SET place_ids = EXCLUDED.place_ids, own_place_id = EXCLUDED.own_place_id,
               updated_by = EXCLUDED.updated_by, updated_at = now()
           RETURNING place_ids, own_place_id, updated_at""",
        (hotel_slug, ids, own_place_id, user_id),
    )
    return dict(cur.fetchone())


def _own_place(cur: Any, hotel_slug: str, own: str | None) -> str | None:
    """The hotel's own place: set explicitly, else what the SerpApi source
    resolved when it first found the hotel."""
    if own:
        return own
    cur.execute("SELECT config->>'place_id' AS pid FROM rev_sources WHERE hotel_slug = %s AND kind = 'serpapi_google'",
                (hotel_slug,))
    row = cur.fetchone()
    return row["pid"] if row and row["pid"] else None


def fetch_place(place_id: str) -> dict:
    key = get_settings().GOOGLE_API_KEY
    if not key:
        raise NotConfigured("Google Places (GOOGLE_API_KEY)")
    body = http.request_json("places", "GET", f"{API}/{place_id}",
                             headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS})
    return {
        "place_id": place_id,
        "name": (body.get("displayName") or {}).get("text"),
        "rating": body.get("rating"),
        "user_rating_count": body.get("userRatingCount"),
        "maps_url": body.get("googleMapsUri"),
    }


def live(cur: Any, hotel_slug: str) -> dict:
    """{places[{place_id, name, rating, user_rating_count, maps_url, own,
    error?}], attribution}. One failed place does not hide the others."""
    s = get_set(cur, hotel_slug)
    own = _own_place(cur, hotel_slug, s["own_place_id"])
    ids = ([own] if own else []) + [p for p in s["place_ids"] if p != own]
    if ids and not get_settings().GOOGLE_API_KEY:
        raise NotConfigured("Google Places (GOOGLE_API_KEY)")
    out = []
    for pid in ids:
        try:
            out.append({**fetch_place(pid), "own": pid == own})
        except http.VendorError as exc:
            out.append({"place_id": pid, "own": pid == own, "error": exc.code,
                        "name": None, "rating": None, "user_rating_count": None, "maps_url": None})
    return {"places": out, "attribution": "Google", "stored": False}
