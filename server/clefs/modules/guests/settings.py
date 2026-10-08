"""
Guests module settings (hotel_module_settings["guests"], validated by
MODULE.settings_model). One flat model: the journeys settings of 04 spec 3.10
(`JourneySettings`) plus the few identity switches of 03 spec 2.7.

Rules the validators enforce, because a bad value here would break the law,
not just the UI:
- The marketing window may be narrowed inside [09:00, 18:00) and never
  widened (Canon WhatsApp; Cabinet Decision 56/2024, 04 spec 0.2 #9).
- Review links are the GBP share links (g.page, maps.app.goo.gl), a
  search.google.com writereview link, or a page on an official Tripadvisor
  country site (04 spec 3.9), matched by exact host. Nothing else,
  so the J7 page can never be pointed at a rating-gated funnel.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

Step = Literal["j1", "j2", "j3", "j4", "j5", "j6_pre", "j6_post", "j7", "j8"]
ALL_STEPS: tuple[str, ...] = ("j1", "j2", "j3", "j4", "j5", "j6_pre", "j6_post", "j7", "j8")

MARKETING_EARLIEST = time(9, 0)
MARKETING_LATEST = time(18, 0)


def parse_hhmm(v: str) -> time:
    try:
        hh, mm = v.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"time must be HH:MM, got {v!r}") from exc


class Window(BaseModel):
    """A daily half-open window [start, end) in the hotel's timezone."""

    start: str = "08:00"
    end: str = "21:00"

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        parse_hhmm(v)
        return v

    @model_validator(mode="after")
    def _order(self) -> "Window":
        if parse_hhmm(self.start) >= parse_hhmm(self.end):
            raise ValueError("window start must be before its end")
        return self

    @property
    def t0(self) -> time:
        return parse_hhmm(self.start)

    @property
    def t1(self) -> time:
        return parse_hhmm(self.end)


# ─── Review links ───
# Exact official hosts only. A "contains tripadvisor" or "ends with a
# tripadvisor label" test lets tripadvisor.review-us.io or
# tripadvisor.com.evil.example through, and such a host could serve a
# rating-gated funnel (happy guests to Google, unhappy ones to a form), which
# Google and Tripadvisor both ban (00-PLAN; 04 spec 3.9).

GOOGLE_REVIEW_HOSTS = frozenset({"g.page", "search.google.com", "maps.app.goo.gl"})

# Tripadvisor's own country sites (the registrable domain, compared whole).
TRIPADVISOR_DOMAINS = frozenset(
    {
        "tripadvisor.com", "tripadvisor.ae", "tripadvisor.co.uk", "tripadvisor.ca", "tripadvisor.com.au",
        "tripadvisor.co.nz", "tripadvisor.ie", "tripadvisor.in", "tripadvisor.com.sg", "tripadvisor.com.my",
        "tripadvisor.com.ph", "tripadvisor.co.id", "tripadvisor.com.hk", "tripadvisor.com.tw", "tripadvisor.jp",
        "tripadvisor.cn", "tripadvisor.co.kr", "tripadvisor.com.vn", "tripadvisor.co.za", "tripadvisor.com.eg",
        "tripadvisor.com.tr", "tripadvisor.co.il", "tripadvisor.de", "tripadvisor.at", "tripadvisor.ch",
        "tripadvisor.fr", "tripadvisor.be", "tripadvisor.nl", "tripadvisor.it", "tripadvisor.es",
        "tripadvisor.pt", "tripadvisor.se", "tripadvisor.dk", "tripadvisor.fi", "tripadvisor.no",
        "tripadvisor.ru", "tripadvisor.com.gr", "tripadvisor.pl", "tripadvisor.cz", "tripadvisor.sk",
        "tripadvisor.com.br", "tripadvisor.com.mx", "tripadvisor.com.ar", "tripadvisor.cl", "tripadvisor.co",
        "tripadvisor.com.pe", "tripadvisor.com.ve",
    }
)
# Subdomains Tripadvisor serves its site on: www, mobile, and language
# editions (ar.tripadvisor.com, nl.tripadvisor.be, fr.tripadvisor.ch).
_TA_SUBDOMAIN = re.compile(r"^(www|m|[a-z]{2})$")


def _https_host(v: str) -> str | None:
    """The lowercased host of a plain https URL, or None. A userinfo part
    (https://tripadvisor.com@evil.example) or an explicit port is refused
    rather than parsed around."""
    u = urlparse(v.strip())
    if u.scheme != "https" or not u.hostname or "@" in u.netloc:
        return None
    try:
        if u.port is not None:
            return None
    except ValueError:
        return None
    return u.hostname.lower().rstrip(".")


def is_google_review_url(v: str) -> bool:
    host = _https_host(v)
    if host not in GOOGLE_REVIEW_HOSTS:
        return False
    if host == "search.google.com":
        # Only the write-review endpoint: search.google.com hosts other tools.
        return urlparse(v.strip()).path.lower().startswith("/local/writereview")
    return True


def is_tripadvisor_url(v: str) -> bool:
    host = _https_host(v)
    if host is None:
        return False
    if host in TRIPADVISOR_DOMAINS:
        return True
    sub, _, rest = host.partition(".")
    return rest in TRIPADVISOR_DOMAINS and bool(_TA_SUBDOMAIN.match(sub))


class ReviewLinksIn(BaseModel):
    google_url: str | None = None
    tripadvisor_url: str | None = None

    @field_validator("google_url")
    @classmethod
    def _google(cls, v: str | None) -> str | None:
        if not v:
            return None
        if not is_google_review_url(v):
            raise ValueError(
                "the Google link must be the g.page or maps.app.goo.gl share link, "
                "or a search.google.com/local/writereview link"
            )
        return v.strip()

    @field_validator("tripadvisor_url")
    @classmethod
    def _tripadvisor(cls, v: str | None) -> str | None:
        if not v:
            return None
        if not is_tripadvisor_url(v):
            raise ValueError("the Tripadvisor link must be on an official Tripadvisor site (tripadvisor.com, tripadvisor.ae, ...)")
        return v.strip()


class GuestsSettings(BaseModel):
    # ─── Journeys (04 spec 3.10 JourneySettings) ───
    enabled_steps: list[Step] = Field(default_factory=lambda: list(ALL_STEPS))
    utility_window: Window = Field(default_factory=lambda: Window(start="08:00", end="21:00"))
    marketing_window: Window = Field(default_factory=lambda: Window(start="09:00", end="18:00"))
    j2_min_lead_days: int = Field(default=10, ge=0, le=60)
    j5_offset_h: int = Field(default=18, ge=1, le=72)
    j7_offset_days: int = Field(default=2, ge=0, le=14)
    j8_offsets: list[int] = Field(default_factory=lambda: [30, 60], min_length=0, max_length=4)
    review_links: ReviewLinksIn = Field(default_factory=ReviewLinksIn)
    # Pending legal review (04 spec 7.7): J7 to Booking/Agoda/Expedia-provided
    # contacts is off until counsel signs off.
    j7_for_ota_provided_contacts: bool = False
    standard_checkin_time: str = "15:00"
    standard_checkout_time: str = "12:00"
    # 3.8 / 7.7: no LLM on a journey reply without transfer_abroad consent
    # until counsel signs off.
    ai_on_unconsented_inbound: bool = False
    # 3.5: owner attestation that the WABA has a Meta payment method on file.
    wa_billing_attested_at: datetime | None = None
    # J8 {{6}}: the booking engine (falls back to the hotel's website).
    booking_engine_url: str | None = None
    # Fallback texts for KB-sourced template variables (J2 {{4}}, J4 {{4}}
    # and {{5}}), used when the concierge KB has no approved fact.
    planning_info: str | None = None
    checkin_bring: str | None = None
    directions: str | None = None

    # ─── Identity (03 spec 2.7) ───
    # Show the returning-guest badge on the queue, device and handover.
    returning_badge: bool = True

    @field_validator("standard_checkin_time", "standard_checkout_time")
    @classmethod
    def _time(cls, v: str) -> str:
        parse_hhmm(v)
        return v

    @field_validator("marketing_window")
    @classmethod
    def _narrow_only(cls, v: Window) -> Window:
        if v.t0 < MARKETING_EARLIEST or v.t1 > MARKETING_LATEST:
            raise ValueError("the marketing window can be narrowed inside 09:00-18:00, never widened")
        return v

    @field_validator("j8_offsets")
    @classmethod
    def _offsets(cls, v: list[int]) -> list[int]:
        if any(d < 7 or d > 365 for d in v):
            raise ValueError("J8 offsets are days after check-out, between 7 and 365")
        return sorted(set(v))

    @field_validator("booking_engine_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if not v:
            return None
        if urlparse(v).scheme not in ("https", "http"):
            raise ValueError("booking_engine_url must be an http(s) URL")
        return v

    @field_validator("planning_info", "checkin_bring", "directions")
    @classmethod
    def _short(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if len(v) > 200:
            raise ValueError("at most 200 characters (a template variable)")
        # Escaped on purpose: the repo convention bans the literal character
        # from every source file, including the check that enforces it.
        if "\u2014" in v:
            raise ValueError("no em dash in guest-facing text")
        return v or None

    @property
    def checkin_t(self) -> time:
        return parse_hhmm(self.standard_checkin_time)

    @property
    def checkout_t(self) -> time:
        return parse_hhmm(self.standard_checkout_time)


def load(cur, hotel_slug: str) -> GuestsSettings:
    from clefs.core.hotels import get_module_settings

    return get_module_settings(cur, hotel_slug, "guests", GuestsSettings)
