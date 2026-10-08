"""
Per-hotel review settings (hotel_module_settings['reviews'], validated by this
model; GET/PUT /hotels/{slug}/module-settings/reviews).

Every field has a safe default: no third-party fetching until the owner reads
the SerpApi disclosure and switches it on, replies always drafted and never
posted without a person, negative = 3 stars or below (03 spec 4.1).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs.core.hotels import get_module_settings


class ReplyStyle(BaseModel):
    # Who signs the reply ("Sara, Guest Relations Manager"). Empty = the
    # hotel's name only ("The team at <hotel>").
    signer: str = Field("", max_length=80)
    tone: Literal["warm", "formal", "brief"] = "warm"
    # The offline contact every negative reply offers instead of a public
    # remedy ("guestrelations@hotel.ae"). Empty = "contact us directly".
    contact: str = Field("", max_length=120)


class ReviewsSettings(BaseModel):
    reply_style: ReplyStyle = ReplyStyle()
    # Canon 20 / 03 spec 4.1: SerpApi is a vendor risk, so Google Maps
    # fetching through it is off until the owner accepts the disclosure. GBP
    # is preferred once connected (the sync skips SerpApi then).
    serpapi_enabled: bool = False
    serpapi_disclosure_accepted_by: int | None = None
    # Pages of 10-20 reviews per daily SerpApi sync (billed per page).
    sync_max_pages: int = Field(5, ge=1, le=10)
    # A review at or below this (on the /5 scale) is negative, and so is any
    # review the analysis rates high severity.
    negative_threshold: float = Field(3.0, ge=1.0, le=4.5)
    # Draft a reply for every new review (negative ones always get one).
    draft_positive: bool = True
    # Drafts, actions and signals only for reviews posted this recently: a
    # first import of three years of history must not flood the brief.
    act_on_days: int = Field(30, ge=1, le=365)
    # Places competitor widget (live, never stored).
    competitors_enabled: bool = True
    # Forwarded review emails (<alias>+reviews@) are accepted from the
    # hotel's own team (member emails), the review platforms' notification
    # domains, and these extra addresses or @domains (a shared inbox).
    forward_senders: list[str] = Field(default_factory=list, max_length=20)


def get_settings(cur: Any, hotel_slug: str) -> ReviewsSettings:
    return get_module_settings(cur, hotel_slug, "reviews", ReviewsSettings)
