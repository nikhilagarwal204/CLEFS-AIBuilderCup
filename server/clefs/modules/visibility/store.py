"""
Visibility profile, competitor mirror and vendor-cost budget (04 spec 5.1,
5.10). Every other file reads the profile through `get_profile`, so the
defaults (no row yet) live in one place.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from clefs.core import clock
from clefs.core.hotels import Hotel, get_hotel

# ─── Profile ───

PROFILE_COLUMNS = (
    "place_id", "gbp_location", "gh_property_token", "website_url", "booking_engine_host", "brand_aliases",
    "rank_keywords", "area", "landmarks", "room_page_urls", "free_booking_link_pattern", "grid_size",
    "grid_spacing_m", "ai_markets", "monthly_budget_minor", "budget_currency", "engines_enabled", "serpapi_enabled",
)
DEFAULTS: dict[str, Any] = {
    "place_id": None, "gbp_location": None, "gh_property_token": None, "website_url": None,
    "booking_engine_host": None, "brand_aliases": [], "rank_keywords": None, "area": None, "landmarks": [],
    "room_page_urls": [], "free_booking_link_pattern": None, "grid_size": 7, "grid_spacing_m": 800,
    "ai_markets": [], "monthly_budget_minor": 3000, "budget_currency": "USD",
    "engines_enabled": ["gemini_web_search", "serpapi_ai_mode"], "serpapi_enabled": True,
}


class ProfileUpdate(BaseModel):
    """PUT /visibility/settings body: every field optional, only sent keys change."""

    place_id: str | None = Field(None, max_length=300)
    gbp_location: str | None = Field(None, max_length=300)
    gh_property_token: str | None = Field(None, max_length=500)
    website_url: str | None = Field(None, max_length=500)
    booking_engine_host: str | None = Field(None, max_length=300)
    brand_aliases: list[str] | None = Field(None, max_length=20)
    rank_keywords: list[str] | None = Field(None, max_length=5)
    area: str | None = Field(None, max_length=120)
    landmarks: list[str] | None = Field(None, max_length=10)
    room_page_urls: list[str] | None = Field(None, max_length=10)
    free_booking_link_pattern: str | None = Field(None, max_length=120)
    grid_size: int | None = Field(None, ge=3, le=9)
    grid_spacing_m: int | None = Field(None, ge=100, le=5000)
    ai_markets: list[dict] | None = None
    monthly_budget_minor: int | None = Field(None, ge=0, le=100_000_000)
    budget_currency: str | None = Field(None, min_length=3, max_length=3)
    engines_enabled: list[str] | None = None
    serpapi_enabled: bool | None = None

    @field_validator("gbp_location")
    @classmethod
    def _location(cls, v: str | None) -> str | None:
        # The Performance and Lodging APIs take "locations/{id}"; the account
        # listing returns that form, so anything else is a typo.
        if v and not v.startswith("locations/"):
            raise ValueError("gbp_location must look like 'locations/<id>'")
        return v

    @field_validator("website_url", "room_page_urls")
    @classmethod
    def _urls(cls, v: Any) -> Any:
        for u in [v] if isinstance(v, str) else (v or []):
            if u and not u.startswith(("https://", "http://")):
                raise ValueError("URLs must start with https:// or http://")
        return v

    @field_validator("rank_keywords")
    @classmethod
    def _keywords(cls, v: list[str] | None) -> list[str] | None:
        # 3-5 keywords keep the grid near 1,000 free calls a month (5.3).
        if v is not None:
            v = [k.strip() for k in v if k and k.strip()]
            if len(v) > 5:
                raise ValueError("at most 5 rank keywords")
        return v


def get_profile(cur: Any, hotel_slug: str) -> dict:
    """The profile with defaults filled in; `website_url` falls back to the
    hotel's own website so site health works before anyone opens settings."""
    cur.execute(f"SELECT {', '.join(PROFILE_COLUMNS)}, updated_at FROM vis_profile WHERE hotel_slug = %s",
                (hotel_slug,))
    row = cur.fetchone()
    out = dict(DEFAULTS)
    out["updated_at"] = None
    if row:
        out.update({k: v for k, v in dict(row).items() if v is not None})
    for k in ("brand_aliases", "landmarks", "room_page_urls", "ai_markets"):
        out[k] = list(out.get(k) or [])
    if not out.get("website_url"):
        hotel = get_hotel(cur, hotel_slug)
        out["website_url"] = hotel.website or None
    out["exists"] = row is not None
    return out


def update_profile(cur: Any, hotel_slug: str, changes: dict, *, user_id: int | None) -> dict:
    """Upsert only the given columns (a PUT that omits a key keeps it)."""
    from clefs.db import Json

    cols = [k for k in changes if k in PROFILE_COLUMNS]
    vals = [Json(changes[k]) if k == "ai_markets" else changes[k] for k in cols]
    if "budget_currency" in cols:
        vals[cols.index("budget_currency")] = str(changes["budget_currency"]).upper()
    all_cols = ["hotel_slug", *cols, "updated_by", "updated_at"]
    placeholders = ", ".join(["%s"] * len(all_cols))
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*cols, "updated_by", "updated_at"])
    cur.execute(
        f"INSERT INTO vis_profile ({', '.join(all_cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (hotel_slug) DO UPDATE SET {sets}",
        (hotel_slug, *vals, user_id, clock.now()),
    )
    return get_profile(cur, hotel_slug)


# ─── Competitors (mirror of demand.api.comp_set) ───


def sync_competitors(cur: Any, hotel_slug: str) -> dict:
    """Mirror Demand's comp set. Demand owns the list; a comp it dropped is
    deleted here so rank and SoV never report a hotel the owner removed. An
    empty stub answer ([]) leaves the hotel with "other hotels" only."""
    from clefs.modules.demand import api as demand_api
    from clefs.modules.visibility import xmod

    comps = [c for c in (xmod.call(cur, None, demand_api.comp_set, hotel_slug) or []) if c.get("comp_id")]
    now = clock.now()
    for c in comps:
        cur.execute(
            """
            INSERT INTO vis_competitors (hotel_slug, comp_id, name, place_id, property_token, aliases, synced_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, comp_id) DO UPDATE SET name = EXCLUDED.name, place_id = EXCLUDED.place_id,
                property_token = EXCLUDED.property_token, aliases = EXCLUDED.aliases, synced_at = EXCLUDED.synced_at
            """,
            (hotel_slug, str(c["comp_id"]), c.get("name"), c.get("place_id"), c.get("property_token"),
             list(c.get("aliases") or []), now),
        )
    keep = [str(c["comp_id"]) for c in comps]
    cur.execute("DELETE FROM vis_competitors WHERE hotel_slug = %s AND NOT (comp_id = ANY(%s))", (hotel_slug, keep))
    return {"synced": len(keep), "removed": cur.rowcount}


def competitors(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute("SELECT comp_id, name, place_id, property_token, aliases FROM vis_competitors "
                "WHERE hotel_slug = %s ORDER BY comp_id", (hotel_slug,))
    return [dict(r) for r in cur.fetchall()]


# ─── Vendor costs and the monthly budget (5.10) ───

MICROS_PER_MINOR = 10_000  # 1 USD = 100 cents = 1,000,000 micros


def record_cost(cur: Any, hotel_slug: str | None, *, provider: str, purpose: str, cost_micros: int,
                currency: str = "USD", at: datetime | None = None) -> None:
    cur.execute(
        "INSERT INTO vis_api_costs (hotel_slug, provider, purpose, cost_micros, cost_currency, at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (hotel_slug, provider, purpose, int(cost_micros), currency, at or clock.now()),
    )


def _month_start(at: datetime) -> datetime:
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_spend_micros(cur: Any, hotel_slug: str, *, at: datetime | None = None) -> int:
    """Month-to-date USD micros for this hotel: its vis_api_costs plus every
    billed SerpApi call it made (any purpose, so Demand's share of a shared
    call is counted where Demand paid for it, and a cache hit costs 0). The
    month is the UTC month, matching core.serpapi.month_spend_micros."""
    from datetime import UTC

    at = (at or clock.now()).astimezone(UTC)
    start = _month_start(at)
    cur.execute(
        """
        SELECT COALESCE((SELECT SUM(cost_micros) FROM vis_api_costs
                         WHERE hotel_slug = %(h)s AND cost_currency = 'USD' AND at >= %(s)s AND at <= %(a)s), 0)
             + COALESCE((SELECT SUM(cost_micros) FROM serpapi_calls
                         WHERE hotel_slug = %(h)s AND billed AND purpose LIKE 'visibility.%%'
                           AND at >= %(s)s AND at <= %(a)s), 0) AS spend
        """,
        {"h": hotel_slug, "s": start, "a": at},
    )
    return int(cur.fetchone()["spend"] or 0)


def budget_micros(cur: Any, profile: dict, *, on: date | None = None) -> int | None:
    """The monthly budget in USD micros: minor x 10,000 when the budget is in
    USD, else converted with core.money.convert first. None when no rate is
    known (the caller treats that as "no budget check possible", not as 0)."""
    minor = int(profile.get("monthly_budget_minor") or 0)
    ccy = str(profile.get("budget_currency") or "USD").upper()
    if ccy == "USD":
        return minor * MICROS_PER_MINOR
    from clefs.core import money

    try:
        usd = money.convert(cur, money.from_minor(minor, ccy), ccy, "USD", on or clock.now().date())
    except Exception:  # noqa: BLE001  (409 fx_unavailable: degrade, never block the job)
        return None
    return int((Decimal(usd) * 1_000_000).to_integral_value())


def budget_state(cur: Any, hotel_slug: str, *, profile: dict | None = None) -> dict:
    """{spent_micros, budget_micros, remaining_micros, exhausted}."""
    profile = profile or get_profile(cur, hotel_slug)
    spent = month_spend_micros(cur, hotel_slug)
    budget = budget_micros(cur, profile)
    remaining = None if budget is None else budget - spent
    return {"spent_micros": spent, "budget_micros": budget, "remaining_micros": remaining,
            "exhausted": remaining is not None and remaining <= 0}


def hotel_of(cur: Any, hotel_slug: str) -> Hotel:
    return get_hotel(cur, hotel_slug)
