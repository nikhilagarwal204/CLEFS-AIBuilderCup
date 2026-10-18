"""
Google Business Profile reviews (03 spec 4.1, 04 spec 5.4).

The OAuth connection is core.oauth provider 'google_business', shared with
Visibility, so an owner connects Google once; the location is the one
Visibility's picker stored (visibility.api.gbp_location). Reviews builds no
picker of its own. Raw GBP content expires after 30 days (store.RAW_TTL).

Replies (updateReply) are posted only on an explicit approval click, or by
the autonomy dial when the source carries auto_reply_consent and the review
is 4-5 stars with no negative theme (replies.py decides; this file only
talks to Google).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clefs.core import oauth
from clefs.modules.reviews import http, store

PROVIDER = "google_business"
API = "https://mybusiness.googleapis.com/v4"
STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
MAX_PAGES = 5


def location_path(cur: Any, hotel_slug: str, source: dict | None = None) -> str | None:
    """'accounts/{a}/locations/{l}' for the v4 reviews API, or None when no
    location is picked. The source config may pin one (tests, a multi-account
    owner); else Visibility's pick, completed with the connection's account."""
    from clefs.modules.visibility import api as visibility_api

    cfg = (source or {}).get("config") or {}
    loc = cfg.get("location_name") or visibility_api.gbp_location(cur, hotel_slug)
    if not loc:
        return None
    loc = str(loc).strip("/")
    if loc.startswith("accounts/"):
        return loc
    account = cfg.get("account") or (oauth.get_connection(cur, hotel_slug, PROVIDER) or {}).get("account_ref")
    if not account:
        return None
    account = str(account).strip("/")
    account = account if account.startswith("accounts/") else f"accounts/{account}"
    return f"{account}/{loc}"


def _headers(cur: Any, hotel_slug: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {oauth.get_access_token(cur, hotel_slug, PROVIDER)}"}


def _parse_ts(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _original(comment: str) -> str:
    """GBP returns '(Translated by Google) ... (Original) ...' for foreign
    reviews: keep the guest's own words."""
    if "(Original)" in comment:
        return comment.split("(Original)", 1)[1].strip()
    return comment.replace("(Translated by Google)", "").strip()


def to_review(r: dict) -> store.ReviewIn:
    reply = r.get("reviewReply") or {}
    reviewer = r.get("reviewer") or {}
    return store.ReviewIn(
        platform="google", external_id=str(r.get("reviewId") or r.get("name")),
        rating=STARS.get(str(r.get("starRating") or "").upper()), rating_scale=5,
        author_display=None if reviewer.get("isAnonymous") else reviewer.get("displayName"),
        text=_original(r.get("comment") or ""), posted_at=_parse_ts(r.get("createTime")),
        has_owner_response=bool(reply), owner_response_at=_parse_ts(reply.get("updateTime")),
        platform_ref=r.get("name"),
    )


def sync(cur: Any, hotel: Any, source: dict, *, max_pages: int = MAX_PAGES) -> dict:
    """Newest reviews until a known one. Raises oauth.NotConnected when the
    Google connection is missing or revoked (the job records it on the
    source and moves on)."""
    loc = location_path(cur, hotel.slug, source)
    if not loc:
        return {"fetched": 0, "created": 0, "error": "gbp_location_missing"}
    headers = _headers(cur, hotel.slug)
    token, fetched, created = None, 0, 0
    for _ in range(max_pages):
        params: dict[str, Any] = {"pageSize": 50, "orderBy": "updateTime desc"}
        if token:
            params["pageToken"] = token
        body = http.request_json("gbp", "GET", f"{API}/{loc}/reviews", params=params, headers=headers)
        items = [to_review(r) for r in body.get("reviews") or []]
        known = store.known_external_ids(cur, hotel.slug, "google", [r.external_id for r in items])
        for r in items:
            fetched += 1
            # Known reviews still refresh (a reply posted on Google directly
            # marks them answered). Ordered by updateTime, everything changed
            # since the last sync sits above the first known review, so the
            # page holding one is the last page needed.
            _, is_new = store.ingest(cur, hotel.slug, source["id"], r)
            created += int(is_new)
        token = body.get("nextPageToken")
        if known or not token:
            break
    return {"fetched": fetched, "created": created}


def post_reply(cur: Any, hotel_slug: str, review_name: str, text: str) -> dict:
    """PUT {review}/reply. Returns {comment, updateTime}."""
    return http.request_json("gbp", "PUT", f"{API}/{review_name.strip('/')}/reply",
                             json={"comment": text}, headers=_headers(cur, hotel_slug))
