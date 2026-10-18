"""
J7 feedback and review links (04 spec 3.9): no sentiment routing, no
incentives.

- review_links(cur, hotel_slug) reads settings ONLY. It takes no rating, no
  reservation, nothing about the guest, so it cannot route happy guests to
  Google and unhappy ones elsewhere (Google and Tripadvisor both ban that).
- Every guest sees the same links, above the survey and on the completion
  page, with the same props. The submit response is identical for every
  payload: {completion_key: "thanks", review_links}.
- Only AFTER the response is built does a rating of 2 or less, or a negative
  comment, file a follow-up for the GM (service.api.create_followup,
  source j7_feedback). That follow-up never mentions reviews.
- Clicks are counted per day and platform, with no identity.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from clefs.core import clock
from clefs.core.hotels import get_hotel
from clefs.core.time import local_date
from clefs.modules.guests import settings as gsettings
from clefs.modules.service import api as service_api

log = logging.getLogger("clefs.guests.journeys.feedback")

PLATFORMS = ("google", "tripadvisor")
COMPLETION_KEY = "thanks"
LOW_RATING = 2
NEGATIVE_WORDS = ("dirty", "noisy", "rude", "broken", "not working", "cold", "smell", "terrible", "awful", "worst",
                  "وسخ", "مزعج", "لا يعمل", "سيء", "مشكلة")


class ReviewLinks(BaseModel):
    google_url: str | None = None
    tripadvisor_url: str | None = None


def review_links(cur: Any, hotel_slug: str) -> ReviewLinks:
    """The hotel's configured review links. Settings only, by design."""
    cfg = gsettings.load(cur, hotel_slug)
    return ReviewLinks(google_url=cfg.review_links.google_url, tripadvisor_url=cfg.review_links.tripadvisor_url)


class FeedbackIn(BaseModel):
    overall: int | None = Field(default=None, ge=1, le=5)
    went_well: str | None = Field(default=None, max_length=2000)
    improve: str | None = Field(default=None, max_length=2000)
    contact_ok: bool | None = None
    language: str | None = Field(default=None, pattern="^(en|ar)$")


def completion(cur: Any, hotel_slug: str) -> dict:
    """The one response every submission gets."""
    return {"completion_key": COMPLETION_KEY, "review_links": review_links(cur, hotel_slug).model_dump()}


def _negative(text: str | None) -> bool:
    low = (text or "").lower()
    return any(re.search(rf"\b{re.escape(w)}", low) if w.isascii() else w in low for w in NEGATIVE_WORDS)


def submit(cur: Any, token_row: dict, body: FeedbackIn) -> dict:
    """Store the answers once per stay and return the identical completion."""
    slug, rid = token_row["hotel_slug"], token_row["reservation_id"]
    plan_id = (token_row.get("ref") or {}).get("plan_id")
    cur.execute(
        """
        INSERT INTO gst_feedback (hotel_slug, reservation_id, plan_id, overall, went_well, improve, contact_ok, language,
                                  submitted_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, reservation_id) DO NOTHING RETURNING id
        """,
        (slug, rid, plan_id, body.overall, (body.went_well or "").strip() or None, (body.improve or "").strip() or None,
         body.contact_ok, body.language, clock.now()),
    )
    row = cur.fetchone()
    response = completion(cur, slug)
    # After the response exists: a low rating or a negative comment reaches
    # the GM. Nothing about it can change what the guest was shown.
    if row is not None and ((body.overall is not None and body.overall <= LOW_RATING) or _negative(body.improve)):
        try:
            fu = service_api.create_followup(
                cur, slug, kind="guest_feedback",
                summary=f"Stay feedback {body.overall or '-'}/5" + (f": {body.improve.strip()[:200]}" if body.improve else ""),
                guest_ref=rid, owner_role="gm", due_at=None, source="j7_feedback", language=body.language,
            )
            cur.execute("UPDATE gst_feedback SET followup_ref = %s WHERE id = %s", (str(fu.get("id") or ""), row["id"]))
        except Exception:
            log.exception("feedback follow-up failed at %s", slug)
    if row is not None and plan_id:
        cur.execute("UPDATE gst_journey_plans SET status = 'replied', updated_at = %s WHERE id = %s "
                    "AND status IN ('sent', 'delivered', 'read')", (clock.now(), plan_id))
    return response


def click(cur: Any, hotel_slug: str, platform: str) -> str | None:
    """Count one click and return where to send the guest (None when the
    hotel has no link for that platform)."""
    if platform not in PLATFORMS:
        return None
    url = getattr(review_links(cur, hotel_slug), f"{platform}_url")
    if not url:
        return None
    cur.execute(
        """
        INSERT INTO gst_review_link_clicks (hotel_slug, day, platform, clicks) VALUES (%s, %s, %s, 1)
        ON CONFLICT (hotel_slug, day, platform) DO UPDATE SET clicks = gst_review_link_clicks.clicks + 1
        """,
        (hotel_slug, local_date(get_hotel(cur, hotel_slug)), platform),
    )
    return url
