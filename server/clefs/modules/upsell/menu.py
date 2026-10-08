"""
Outlets, menu items and pairings: storage, validation, availability and the
guest-facing menu (03 spec 2.4 get_menu, 2.5 DDL; Canon 9).

Availability is judged in the hotel's timezone (Canon 3) through
clefs.core.time, never the server clock's zone: a Dubai breakfast menu must
close at 11:00 Dubai time whatever region the API runs in.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, Field, field_validator

from clefs import files
from clefs.core import time as ctime
from clefs.core.errors import ClefsError, bad_request, not_found
from clefs.core.money import money_str, to_minor
from clefs.db import Json

OUTLET_KINDS = ("in_room_dining", "restaurant", "bar", "cafe", "pool", "spa", "minibar", "other")
DIETARY = (
    "vegan", "vegetarian", "gluten_free", "dairy_free", "halal", "spicy",
    "contains_nuts", "contains_pork", "contains_shellfish",
)
ALL_DAYS = 127
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


# ─── Input models ───


class HoursWindow(BaseModel):
    from_: str = Field(alias="from")
    to: str
    days_mask: int = Field(ALL_DAYS, ge=0, le=ALL_DAYS)

    model_config = {"populate_by_name": True}

    @field_validator("from_", "to")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v or ""):
            raise ValueError("use HH:MM (24 h)")
        return v

    def dump(self) -> dict:
        return {"from": self.from_, "to": self.to, "days_mask": self.days_mask}


class OutletIn(BaseModel):
    key: str = Field(max_length=40)
    name_en: str = Field(min_length=1, max_length=120)
    name_ar: str | None = Field(None, max_length=120)
    kind: str = "in_room_dining"
    hours: list[HoursWindow] = Field(default_factory=list, max_length=14)
    delivers_to_room: bool = True
    active: bool = True
    sort: int = 0

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if not _KEY.match(v):
            raise ValueError("lowercase letters, digits, '-' or '_' (max 40)")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in OUTLET_KINDS:
            raise ValueError(f"one of {', '.join(OUTLET_KINDS)}")
        return v


class OutletPatch(BaseModel):
    name_en: str | None = Field(None, min_length=1, max_length=120)
    name_ar: str | None = Field(None, max_length=120)
    kind: str | None = None
    hours: list[HoursWindow] | None = Field(None, max_length=14)
    delivers_to_room: bool | None = None
    active: bool | None = None
    sort: int | None = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        if v is not None and v not in OUTLET_KINDS:
            raise ValueError(f"one of {', '.join(OUTLET_KINDS)}")
        return v


def _dietary(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    out: list[str] = []
    for tag in v:
        t = (tag or "").strip().lower().replace("-", "_").replace(" ", "_")
        if t not in DIETARY:
            raise ValueError(f"unknown dietary tag {tag!r}; use {', '.join(DIETARY)}")
        if t not in out:
            out.append(t)
    return out


def _hhmm_or_none(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    if not _HHMM.match(v):
        raise ValueError("use HH:MM (24 h)")
    return v


class ItemFields(BaseModel):
    """Shared by create and patch; every field optional here."""

    sku: str | None = Field(None, max_length=60)
    category: str | None = Field(None, min_length=1, max_length=80)
    category_ar: str | None = Field(None, max_length=80)
    name_en: str | None = Field(None, min_length=1, max_length=160)
    name_ar: str | None = Field(None, max_length=160)
    desc_en: str | None = Field(None, max_length=1000)
    desc_ar: str | None = Field(None, max_length=1000)
    # Major units as a Decimal string ("45.00"), in the hotel's currency.
    price: str | None = Field(None, max_length=20)
    image_file_id: str | None = None
    available_from: str | None = None
    available_to: str | None = None
    days_mask: int | None = Field(None, ge=0, le=ALL_DAYS)
    dietary: list[str] | None = Field(None, max_length=len(DIETARY))
    alcohol: bool | None = None
    prep_min: int | None = Field(None, ge=0, le=600)
    active: bool | None = None
    sort: int | None = None

    _v_dietary = field_validator("dietary")(classmethod(lambda cls, v: _dietary(v)))
    _v_hours = field_validator("available_from", "available_to")(classmethod(lambda cls, v: _hhmm_or_none(v)))


class ItemIn(ItemFields):
    outlet_id: int
    name_en: str = Field(min_length=1, max_length=160)
    price: str = Field(max_length=20)


class ItemPatch(ItemFields):
    outlet_id: int | None = None


class PairIn(BaseModel):
    item_id: int
    suggest_item_id: int
    kind: str = Field("pairing", pattern="^(pairing|upgrade)$")
    reason_en: str | None = Field(None, max_length=200)
    reason_ar: str | None = Field(None, max_length=200)
    active: bool = True
    sort: int = 0


def price_minor(price: str, currency: str) -> int:
    """'45.5' -> 4550 fils; 400 invalid_price on garbage or a negative."""
    try:
        minor = to_minor(price, currency)
    except (ValueError, ArithmeticError):
        raise bad_request(f"price {price!r} is not an amount", "invalid_price") from None
    if minor < 0:
        raise bad_request("price cannot be negative", "invalid_price")
    return minor


# ─── Availability (hotel-local) ───


def _t(v: Any) -> time:
    return v if isinstance(v, time) else time.fromisoformat(str(v))


def _day_bit(iso_weekday: int) -> int:
    return 1 << (iso_weekday - 1)


def in_window(now: datetime, start: Any, end: Any, days_mask: int) -> bool:
    """Is local `now` inside [start, end) on a day in days_mask? A window whose
    end is at or before its start runs past midnight and belongs to the day
    it opened: Friday 22:00-02:00 covers Saturday 01:00 too."""
    s, e, t = _t(start), _t(end), now.time()
    today, yesterday = _day_bit(now.isoweekday()), _day_bit((now.isoweekday() - 2) % 7 + 1)
    if s < e:
        return bool(days_mask & today) and s <= t < e
    return (bool(days_mask & today) and t >= s) or (bool(days_mask & yesterday) and t < e)


def outlet_open(outlet: dict, now: datetime) -> bool:
    windows = outlet.get("hours") or []
    if not windows:
        return True
    return any(in_window(now, w["from"], w["to"], int(w.get("days_mask", ALL_DAYS))) for w in windows)


def item_available(item: dict, outlet: dict, now: datetime) -> bool:
    if not (item.get("active") and outlet.get("active")):
        return False
    if not outlet_open(outlet, now):
        return False
    mask = int(item.get("days_mask", ALL_DAYS))
    if item.get("available_from") is None or item.get("available_to") is None:
        return bool(mask & _day_bit(now.isoweekday()))
    return in_window(now, item["available_from"], item["available_to"], mask)


def local_now(hotel: Any) -> datetime:
    return ctime.hotel_now(hotel)


# ─── Serialisation ───


def _hhmm(v: Any) -> str | None:
    return None if v is None else _t(v).strftime("%H:%M")


def outlet_out(row: dict) -> dict:
    return {
        "id": row["id"], "key": row["key"], "name_en": row["name_en"], "name_ar": row["name_ar"],
        "kind": row["kind"], "hours": row["hours"] or [], "delivers_to_room": row["delivers_to_room"],
        "active": row["active"], "sort": row["sort"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def item_out(row: dict) -> dict:
    cur_code = row["currency"].strip()
    return {
        "id": row["id"], "outlet_id": row["outlet_id"], "sku": row["sku"],
        "category": row["category"], "category_ar": row["category_ar"],
        "name_en": row["name_en"], "name_ar": row["name_ar"],
        "desc_en": row["desc_en"], "desc_ar": row["desc_ar"],
        "price": money_str(row["price_minor"], cur_code), "price_minor": row["price_minor"], "currency": cur_code,
        "image_file_id": str(row["image_file_id"]) if row["image_file_id"] else None,
        "available_from": _hhmm(row["available_from"]), "available_to": _hhmm(row["available_to"]),
        "days_mask": row["days_mask"], "dietary": list(row["dietary"] or []), "alcohol": row["alcohol"],
        "prep_min": row["prep_min"], "active": row["active"], "sort": row["sort"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


# ─── Outlets ───


def list_outlets(cur: Any, hotel_slug: str, *, active_only: bool = False) -> list[dict]:
    cur.execute(
        "SELECT * FROM ups_outlets WHERE hotel_slug = %s" + (" AND active" if active_only else "")
        + " ORDER BY sort, id",
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_outlet(cur: Any, hotel_slug: str, outlet_id: int) -> dict:
    cur.execute("SELECT * FROM ups_outlets WHERE hotel_slug = %s AND id = %s", (hotel_slug, outlet_id))
    row = cur.fetchone()
    if row is None:
        raise not_found("Outlet not found", "outlet_not_found")
    return dict(row)


def outlet_by_key(cur: Any, hotel_slug: str, key: str) -> dict | None:
    cur.execute("SELECT * FROM ups_outlets WHERE hotel_slug = %s AND key = %s", (hotel_slug, (key or "").strip().lower()))
    row = cur.fetchone()
    return dict(row) if row else None


def create_outlet(cur: Any, hotel_slug: str, body: OutletIn) -> dict:
    if outlet_by_key(cur, hotel_slug, body.key) is not None:
        raise ClefsError(409, f"An outlet with key {body.key!r} exists", "outlet_exists")
    cur.execute(
        """
        INSERT INTO ups_outlets (hotel_slug, key, name_en, name_ar, kind, hours, delivers_to_room, active, sort)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (hotel_slug, body.key, body.name_en, body.name_ar, body.kind, Json([w.dump() for w in body.hours]),
         body.delivers_to_room, body.active, body.sort),
    )
    return dict(cur.fetchone())


def update_outlet(cur: Any, hotel_slug: str, outlet_id: int, patch: OutletPatch) -> tuple[dict, dict]:
    before = get_outlet(cur, hotel_slug, outlet_id)
    changes = patch.model_dump(exclude_unset=True)
    if "hours" in changes:
        changes["hours"] = Json([w.dump() for w in (patch.hours or [])])
    for k in ("name_en", "kind", "delivers_to_room", "active", "sort"):
        if k in changes and changes[k] is None:
            del changes[k]  # NOT NULL columns: an explicit null means "leave it"
    if not changes:
        return before, before
    sets = ", ".join(f"{k} = %({k})s" for k in changes)
    cur.execute(
        f"UPDATE ups_outlets SET {sets}, updated_at = NOW() WHERE hotel_slug = %(h)s AND id = %(id)s RETURNING *",
        {**changes, "h": hotel_slug, "id": outlet_id},
    )
    return before, dict(cur.fetchone())


def delete_outlet(cur: Any, hotel_slug: str, outlet_id: int) -> dict:
    """Hard delete (items and pairings cascade). Orders keep their snapshots:
    ups_orders.outlet_id and ups_order_items.menu_item_id go NULL."""
    before = get_outlet(cur, hotel_slug, outlet_id)
    cur.execute("DELETE FROM ups_outlets WHERE hotel_slug = %s AND id = %s", (hotel_slug, outlet_id))
    return before


# ─── Items ───

_ITEM_COLS = (
    "outlet_id", "sku", "category", "category_ar", "name_en", "name_ar", "desc_en", "desc_ar",
    "price_minor", "image_file_id", "available_from", "available_to", "days_mask", "dietary",
    "alcohol", "prep_min", "active", "sort",
)
_ITEM_NOT_NULL = {"outlet_id", "category", "name_en", "price_minor", "days_mask", "dietary", "alcohol", "active", "sort"}


def list_items(cur: Any, hotel_slug: str, *, outlet_id: int | None = None, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM ups_menu_items WHERE hotel_slug = %s"
    params: list[Any] = [hotel_slug]
    if outlet_id is not None:
        sql += " AND outlet_id = %s"
        params.append(outlet_id)
    if active_only:
        sql += " AND active"
    cur.execute(sql + " ORDER BY outlet_id, sort, category, id", params)
    return [dict(r) for r in cur.fetchall()]


def get_item(cur: Any, hotel_slug: str, item_id: int) -> dict:
    cur.execute("SELECT * FROM ups_menu_items WHERE hotel_slug = %s AND id = %s", (hotel_slug, item_id))
    row = cur.fetchone()
    if row is None:
        raise not_found("Menu item not found", "item_not_found")
    return dict(row)


def _item_values(cur: Any, hotel_slug: str, currency: str, fields: dict) -> dict:
    """Validated column values from ItemFields.model_dump(exclude_unset=True)."""
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if k == "price":
            if v is not None:
                out["price_minor"] = price_minor(v, currency)
        elif k == "outlet_id":
            if v is not None:
                get_outlet(cur, hotel_slug, v)  # 404 for another hotel's outlet
                out[k] = v
        elif k == "image_file_id":
            if v:
                out[k] = files.file_row(cur, hotel_slug, v)["id"]  # 404 unless this hotel's file
            else:
                out[k] = None
        elif k in _ITEM_COLS:
            if v is None and k in _ITEM_NOT_NULL:
                continue
            out[k] = v
    return out


def create_item(cur: Any, hotel_slug: str, currency: str, body: ItemIn) -> dict:
    vals = _item_values(cur, hotel_slug, currency, body.model_dump(exclude_unset=False))
    vals.setdefault("category", "Other")
    if (vals.get("available_from") is None) != (vals.get("available_to") is None):
        raise bad_request("available_from and available_to go together", "invalid_hours")
    cols = [c for c in _ITEM_COLS if c in vals]
    cur.execute(
        f"INSERT INTO ups_menu_items (hotel_slug, currency, {', '.join(cols)}) "
        f"VALUES (%(h)s, %(ccy)s, {', '.join(f'%({c})s' for c in cols)}) RETURNING *",
        {**vals, "h": hotel_slug, "ccy": currency},
    )
    return dict(cur.fetchone())


def update_item(cur: Any, hotel_slug: str, currency: str, item_id: int, patch: ItemPatch) -> tuple[dict, dict]:
    before = get_item(cur, hotel_slug, item_id)
    vals = _item_values(cur, hotel_slug, currency, patch.model_dump(exclude_unset=True))
    frm = vals.get("available_from", before["available_from"])
    to = vals.get("available_to", before["available_to"])
    if (frm is None) != (to is None):
        raise bad_request("available_from and available_to go together", "invalid_hours")
    if not vals:
        return before, before
    if "price_minor" in vals:
        vals["currency"] = currency  # a repriced item follows the hotel's currency
    sets = ", ".join(f"{k} = %({k})s" for k in vals)
    cur.execute(
        f"UPDATE ups_menu_items SET {sets}, updated_at = NOW() WHERE hotel_slug = %(h)s AND id = %(id)s RETURNING *",
        {**vals, "h": hotel_slug, "id": item_id},
    )
    return before, dict(cur.fetchone())


def delete_item(cur: Any, hotel_slug: str, item_id: int) -> dict:
    before = get_item(cur, hotel_slug, item_id)
    cur.execute("DELETE FROM ups_menu_items WHERE hotel_slug = %s AND id = %s", (hotel_slug, item_id))
    return before


# ─── Pairings ───


def list_pairs(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute("SELECT * FROM ups_upsell_pairs WHERE hotel_slug = %s ORDER BY item_id, sort, id", (hotel_slug,))
    return [dict(r) for r in cur.fetchall()]


def upsert_pair(cur: Any, hotel_slug: str, body: PairIn) -> dict:
    if body.item_id == body.suggest_item_id:
        raise bad_request("An item cannot be paired with itself", "invalid_pair")
    get_item(cur, hotel_slug, body.item_id)
    get_item(cur, hotel_slug, body.suggest_item_id)
    cur.execute(
        """
        INSERT INTO ups_upsell_pairs (hotel_slug, item_id, suggest_item_id, kind, reason_en, reason_ar, active, sort)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (item_id, suggest_item_id) DO UPDATE SET kind = EXCLUDED.kind, reason_en = EXCLUDED.reason_en,
            reason_ar = EXCLUDED.reason_ar, active = EXCLUDED.active, sort = EXCLUDED.sort
        RETURNING *
        """,
        (hotel_slug, body.item_id, body.suggest_item_id, body.kind, body.reason_en, body.reason_ar, body.active, body.sort),
    )
    return dict(cur.fetchone())


def delete_pair(cur: Any, hotel_slug: str, pair_id: int) -> dict:
    cur.execute("DELETE FROM ups_upsell_pairs WHERE hotel_slug = %s AND id = %s RETURNING *", (hotel_slug, pair_id))
    row = cur.fetchone()
    if row is None:
        raise not_found("Pairing not found", "pair_not_found")
    return dict(row)


# ─── Guest-facing menu ───

IMAGE_TTL_S = 3600


def _lang(row: dict, field: str, language: str) -> str | None:
    """Arabic text when asked for and present, else English (the KB rule)."""
    if (language or "en").lower().startswith("ar") and row.get(f"{field}_ar"):
        return row[f"{field}_ar"]
    return row.get(f"{field}_en") if f"{field}_en" in row else row.get(field)


def tags_for(item: dict) -> list[str]:
    return list(item.get("dietary") or []) + (["alcohol"] if item.get("alcohol") else [])


def guest_menu(
    cur: Any,
    hotel: Any,
    *,
    outlet: str | None = None,
    language: str = "en",
    now: datetime | None = None,
    include_unavailable: bool = False,
    allow_alcohol: bool = False,
) -> dict:
    """What a guest may order: {outlets[], items[], pairs{item_id: [...]}}.

    Items outside their hours are dropped unless include_unavailable (the
    guest page greys them out; the concierge only offers what is orderable).
    Alcohol is hidden unless the hotel opted in (settings.alcohol_orders).
    """
    now = now or local_now(hotel)
    outlets = {o["id"]: o for o in list_outlets(cur, hotel.slug, active_only=True)}
    if outlet:
        outlets = {i: o for i, o in outlets.items() if o["key"] == outlet.strip().lower()}
    items_out: list[dict] = []
    for it in list_items(cur, hotel.slug, active_only=True):
        o = outlets.get(it["outlet_id"])
        if o is None or (it["alcohol"] and not allow_alcohol):
            continue
        available = item_available(it, o, now)
        if not available and not include_unavailable:
            continue
        code = it["currency"].strip()
        items_out.append({
            "item_id": it["id"],
            "outlet": o["key"],
            "section": (it["category_ar"] if language.lower().startswith("ar") and it["category_ar"] else it["category"]),
            "name": _lang(it, "name", language),
            "description": _lang(it, "desc", language),
            "price": money_str(it["price_minor"], code),
            "price_minor": it["price_minor"],
            "currency": code,
            "tags": tags_for(it),
            "dietary": list(it["dietary"] or []),
            "alcohol": it["alcohol"],
            "prep_min": it["prep_min"],
            "image_url": files.signed_path(hotel.slug, str(it["image_file_id"]), ttl_s=IMAGE_TTL_S)
            if it["image_file_id"] else None,
            "available_now": available,
        })
    offered = {i["item_id"] for i in items_out if i["available_now"]}
    pairs: dict[int, list[dict]] = {}
    for p in list_pairs(cur, hotel.slug):
        if p["active"] and p["item_id"] in offered and p["suggest_item_id"] in offered:
            reason = p["reason_ar"] if language.lower().startswith("ar") and p["reason_ar"] else p["reason_en"]
            pairs.setdefault(p["item_id"], []).append(
                {"suggest_item_id": p["suggest_item_id"], "kind": p["kind"], "reason": reason}
            )
    return {
        "outlets": [
            {"key": o["key"], "name": _lang(o, "name", language), "kind": o["kind"], "hours": o["hours"] or [],
             "delivers_to_room": o["delivers_to_room"], "open_now": outlet_open(o, now)}
            for o in outlets.values()
        ],
        "items": items_out,
        "pairs": pairs,
    }
