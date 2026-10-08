"""
Hotels and per-module settings.

    hotel = get_hotel(cur, slug)                        # 60 s in-process cache
    s = get_module_settings(cur, slug, "demand", DemandSettings)
    set_module_settings(cur, slug, "demand", s, actor=ctx)   # audited
    module_enabled(cur, slug, "demand")

Modules never ALTER hotels. A module's knobs live in
hotel_module_settings.settings and are validated by the module's own Pydantic
model, so adding a setting never needs a migration.

Projections matter: GET /hotels/{slug} is public (guest QR pages read it), so it
returns LEGACY_PUBLIC_FIELDS only. The old SELECT * would now leak tax_profile,
WhatsApp ids and the legacy_open_access flag.
"""

from __future__ import annotations

import logging
import re
import threading
import time as _time
from datetime import datetime, time
from decimal import Decimal
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from clefs.core.errors import ClefsError

log = logging.getLogger("clefs.hotels")

T = TypeVar("T", bound=BaseModel)

CACHE_TTL_S = 60.0
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

# What GET /hotels/{slug} returned before the refactor (minus nothing): the
# concierge prompt reads knowledge_base, star_rating, rooms_count, category and
# website, so all of these stay public.
LEGACY_PUBLIC_FIELDS: tuple[str, ...] = (
    "id",
    "slug",
    "name",
    "star_rating",
    "rooms_count",
    "category",
    "website",
    "city",
    "created_at",
    "knowledge_base",
    "assistant_name",
)

# GET /hotels/{slug}/context -> hotel (members, devices, legacy-open visitors).
CONTEXT_FIELDS: tuple[str, ...] = (
    "slug",
    "name",
    "city",
    "country",
    "emirate",
    "timezone",
    "currency",
    "locale",
    "guest_languages",
    "weekend_days",
    "brief_time_local",
    "star_rating",
    "rooms_count",
    "assistant_name",
    "status",
)

# GET/PATCH /hotels/{slug}/settings (core.settings.*): everything editable.
SETTINGS_FIELDS: tuple[str, ...] = CONTEXT_FIELDS + (
    "category",
    "website",
    "lat",
    "lng",
    "address",
    "business_day_cutoff",
    "tax_profile",
    "whatsapp_phone_number_id",
    "whatsapp_display_number",
    "front_desk_phone",
    "inbound_email_alias",
    "knowledge_base",
    "legacy_open_access",
)

# Guest-facing minimal hotel block for /public/stay.
PUBLIC_STAY_FIELDS: tuple[str, ...] = (
    "slug",
    "name",
    "city",
    "star_rating",
    "category",
    "assistant_name",
    "locale",
    "guest_languages",
)


class Hotel(BaseModel):
    id: int
    slug: str
    name: str
    star_rating: int | None = None
    rooms_count: int | None = None
    category: str | None = None
    website: str | None = None
    city: str | None = None
    knowledge_base: str | None = None
    assistant_name: str | None = None
    created_at: datetime | None = None
    country: str = "IN"
    emirate: str | None = None
    timezone: str = "Asia/Kolkata"
    currency: str = "INR"
    locale: str = "en-IN"
    guest_languages: list[str] = ["en"]
    weekend_days: list[int] = [6, 7]
    lat: Decimal | None = None
    lng: Decimal | None = None
    address: str | None = None
    business_day_cutoff: time = time(4, 0)
    brief_time_local: time = time(7, 0)
    tax_profile: dict[str, Any] = {}
    whatsapp_phone_number_id: str | None = None
    whatsapp_display_number: str | None = None
    front_desk_phone: str | None = None
    inbound_email_alias: str | None = None
    status: str = "active"
    legacy_open_access: bool = False
    updated_at: datetime | None = None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def project(self, fields: tuple[str, ...]) -> dict[str, Any]:
        """JSON-ready dict of exactly these fields (TIME as HH:MM, CHAR trimmed)."""
        out: dict[str, Any] = {}
        for f in fields:
            v = getattr(self, f)
            if isinstance(v, time):
                v = v.strftime("%H:%M")
            out[f] = v
        return out


# ─── Cache ───

_cache: dict[str, tuple[float, Hotel]] = {}
_cache_lock = threading.Lock()


def invalidate(slug: str) -> None:
    with _cache_lock:
        _cache.pop(slug, None)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _row_to_hotel(row: dict) -> Hotel:
    data = dict(row)
    for key in ("country", "currency"):
        if isinstance(data.get(key), str):
            data[key] = data[key].strip()  # CHAR(n) pads
    data["guest_languages"] = list(data.get("guest_languages") or ["en"])
    # Only a missing value defaults to Sat-Sun. An empty array is a real choice
    # (a hotel with no weekend pricing) and must read back as saved.
    wd = data.get("weekend_days")
    data["weekend_days"] = [6, 7] if wd is None else [int(x) for x in wd]
    data["tax_profile"] = data.get("tax_profile") or {}
    return Hotel.model_validate({k: v for k, v in data.items() if k in Hotel.model_fields})


def find_hotel(cur: Any, slug: str, *, fresh: bool = False) -> Hotel | None:
    if not fresh:
        with _cache_lock:
            hit = _cache.get(slug)
        if hit and hit[0] > _time.monotonic():
            return hit[1]
    cur.execute("SELECT * FROM hotels WHERE slug = %s", (slug,))
    row = cur.fetchone()
    if row is None:
        return None
    hotel = _row_to_hotel(row)
    with _cache_lock:
        _cache[slug] = (_time.monotonic() + CACHE_TTL_S, hotel)
    return hotel


def get_hotel(cur: Any, slug: str, *, fresh: bool = False) -> Hotel:
    """The hotel or a 404 ClefsError (same wording the legacy route used)."""
    hotel = find_hotel(cur, slug, fresh=fresh)
    if hotel is None:
        raise ClefsError(404, f"Hotel '{slug}' not found", "hotel_not_found")
    return hotel


def list_hotels(cur: Any, *, include_archived: bool = False) -> list[Hotel]:
    cur.execute(
        "SELECT * FROM hotels WHERE %s OR status <> 'archived' ORDER BY slug",
        (include_archived,),
    )
    return [_row_to_hotel(r) for r in cur.fetchall()]


def create_hotel(
    cur: Any,
    *,
    slug: str,
    name: str,
    country: str = "AE",
    emirate: str | None = "dubai",
    timezone: str = "Asia/Dubai",
    currency: str = "AED",
    locale: str = "en-AE",
    city: str | None = None,
    guest_languages: list[str] | None = None,
    weekend_days: list[int] | None = None,
    star_rating: int | None = None,
    rooms_count: int | None = None,
    status: str = "active",
    legacy_open_access: bool = False,
    **extra: Any,
) -> Hotel:
    """Insert a new hotel (UAE defaults). New hotels are locked by default."""
    if not SLUG_RE.match(slug):
        raise ClefsError(400, "slug must be 3-64 chars of a-z, 0-9 and '-'", "invalid_slug")
    ZoneInfo(timezone)  # raises on an unknown zone
    cols = {
        "slug": slug,
        "name": name,
        "country": country.upper(),
        "emirate": emirate,
        "timezone": timezone,
        "currency": currency.upper(),
        "locale": locale,
        "city": city,
        "guest_languages": guest_languages or ["en", "ar"],
        "weekend_days": [6, 7] if weekend_days is None else list(weekend_days),
        "star_rating": star_rating,
        "rooms_count": rooms_count,
        "status": status,
        "legacy_open_access": legacy_open_access,
        **extra,
    }
    names = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    cur.execute(
        f"INSERT INTO hotels ({names}) VALUES ({placeholders}) RETURNING *",
        tuple(_adapt(v) for v in cols.values()),
    )
    hotel = _row_to_hotel(cur.fetchone())
    invalidate(slug)
    return hotel


def _adapt(v: Any) -> Any:
    from clefs.db import Json

    return Json(v) if isinstance(v, dict) else v


def update_hotel(cur: Any, slug: str, changes: dict[str, Any]) -> tuple[Hotel, Hotel]:
    """Apply validated column changes. Returns (before, after). Caller audits."""
    before = get_hotel(cur, slug, fresh=True)
    if not changes:
        return before, before
    unknown = set(changes) - set(SETTINGS_FIELDS)
    if unknown:
        raise ClefsError(400, f"not editable: {', '.join(sorted(unknown))}", "invalid_field")
    sets = ", ".join(f"{k} = %s" for k in changes)
    cur.execute(
        f"UPDATE hotels SET {sets}, updated_at = NOW() WHERE slug = %s RETURNING *",
        tuple(_adapt(v) for v in changes.values()) + (slug,),
    )
    after = _row_to_hotel(cur.fetchone())
    invalidate(slug)
    return before, after


# ─── Module settings ───


def module_settings_row(cur: Any, slug: str, module: str) -> dict | None:
    cur.execute(
        "SELECT hotel_slug, module, enabled, settings, updated_by, updated_at "
        "FROM hotel_module_settings WHERE hotel_slug = %s AND module = %s",
        (slug, module),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def get_module_settings(cur: Any, slug: str, module: str, model: type[T]) -> T:
    """The module's settings, validated, with the model's defaults applied.
    Stored keys the model no longer knows are ignored; a stored value that no
    longer validates falls back to defaults (logged) rather than breaking the
    module's endpoints."""
    row = module_settings_row(cur, slug, module)
    raw = (row or {}).get("settings") or {}
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        log.warning("invalid %s settings for %s, using defaults: %s", module, slug, exc)
        return model()


def set_module_settings(
    cur: Any,
    slug: str,
    module: str,
    data: BaseModel | dict[str, Any],
    *,
    actor: Any,
    enabled: bool | None = None,
) -> dict:
    """Upsert (and audit) a module's settings. `actor` is a HotelCtx or Principal."""
    from clefs.audit import audit
    from clefs.db import Json

    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else dict(data)
    before = module_settings_row(cur, slug, module)
    user_id = getattr(getattr(actor, "principal", actor), "user_id", None)
    cur.execute(
        """
        INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings, updated_by, updated_at)
        VALUES (%s, %s, COALESCE(%s, TRUE), %s, %s, NOW())
        ON CONFLICT (hotel_slug, module) DO UPDATE SET
            enabled = COALESCE(%s, hotel_module_settings.enabled),
            settings = EXCLUDED.settings,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        RETURNING hotel_slug, module, enabled, settings, updated_by, updated_at
        """,
        (slug, module, enabled, Json(payload), user_id, enabled),
    )
    after = dict(cur.fetchone())
    audit(
        cur,
        actor,
        "core.module_settings.update",
        "hotel_module_settings",
        f"{slug}:{module}",
        before={"enabled": before["enabled"], "settings": before["settings"]} if before else None,
        after={"enabled": after["enabled"], "settings": after["settings"]},
        hotel_slug=slug,
    )
    return after


def module_enabled(cur: Any, slug: str, module: str) -> bool:
    """Loaded in this process (registry), the hotel is not archived, and the
    module is not switched off for it (no hotel_module_settings row means on;
    legacy hotels carry explicit off rows for the owner modules, migration
    1900). "core" is never switched off. The scheduler applies the same rule
    in bulk (clefs.jobs._target_hotels), one query per tick.

    One query: the archived check rides on the settings lookup, so the hot
    public paths that call this pay nothing extra. An unknown slug keeps the
    old answer (no row: on); callers resolve the hotel first anyway."""
    from clefs.registry import enabled_modules

    if module != "core" and module not in enabled_modules():
        return False
    cur.execute(
        """
        SELECT h.status, s.enabled
        FROM (SELECT %s::text AS slug) k
        LEFT JOIN hotels h ON h.slug = k.slug
        LEFT JOIN hotel_module_settings s ON s.hotel_slug = k.slug AND s.module = %s
        """,
        (slug, module),
    )
    row = cur.fetchone()
    if row is None:
        return True
    if row["status"] == "archived":
        return False
    return module == "core" or row["enabled"] is None or bool(row["enabled"])


def enabled_modules_for(cur: Any, slug: str, loaded: list[str]) -> list[str]:
    cur.execute(
        "SELECT module FROM hotel_module_settings WHERE hotel_slug = %s AND NOT enabled",
        (slug,),
    )
    off = {r["module"] for r in cur.fetchall()}
    return [m for m in loaded if m not in off]
