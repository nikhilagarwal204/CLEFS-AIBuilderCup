"""
Stock locations, par level rows and stock counts (02 spec 5.1).

On hand at a location = the last count + what was issued into it - what was
issued out of it since. With two counts, consumption between them is exact:
opening + received - sent out - closing (02 spec 5.2).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs.core import clock
from clefs.core.errors import ClefsError, bad_request, not_found
from clefs.core.money import D
from clefs.modules.amenities import catalog
from clefs.modules.amenities.constants import PAR_DEFAULTS

LocationKind = Literal["STORE", "FLOOR_CLOSET", "CART", "DEPARTMENT", "MINIBAR"]


# ─── Models ───


class LocationIn(BaseModel):
    kind: LocationKind
    name: str = Field(min_length=1, max_length=80)
    floor: str | None = Field(None, max_length=20)
    active: bool = True


class LocationPatch(BaseModel):
    kind: LocationKind | None = None
    name: str | None = Field(None, min_length=1, max_length=80)
    floor: str | None = Field(None, max_length=20)
    active: bool | None = None


class ParIn(BaseModel):
    location_id: int
    item_id: int
    par_qty: Decimal | None = Field(None, ge=0)             # given = pinned (a manual par sticks)
    days_of_supply: Decimal | None = Field(None, gt=0, le=365)
    safety_stock: Decimal | None = Field(None, ge=0)
    lead_days: Decimal | None = Field(None, gt=0, le=120)
    pinned: bool | None = None


class CountLine(BaseModel):
    location_id: int
    item_id: int
    qty: Decimal = Field(ge=0)
    counted_at: datetime | None = None
    counted_by: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=500)


class CountsIn(BaseModel):
    lines: list[CountLine] = Field(min_length=1, max_length=500)


# ─── Locations ───


def location_out(r: dict) -> dict:
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "floor": r.get("floor"), "active": bool(r["active"])}


def list_locations(cur: Any, hotel_slug: str, *, active_only: bool = False) -> list[dict]:
    cur.execute("SELECT * FROM amn_locations WHERE hotel_slug = %s" + (" AND active" if active_only else "")
                + " ORDER BY kind, floor NULLS FIRST, name", (hotel_slug,))
    return list(cur.fetchall())


def get_location(cur: Any, hotel_slug: str, location_id: int) -> dict:
    cur.execute("SELECT * FROM amn_locations WHERE hotel_slug = %s AND id = %s", (hotel_slug, location_id))
    row = cur.fetchone()
    if not row:
        raise not_found("Stock location not found", "location_not_found")
    return row


def location_by_name(cur: Any, hotel_slug: str, name: str) -> dict | None:
    cur.execute("SELECT * FROM amn_locations WHERE hotel_slug = %s AND lower(name) = lower(%s)", (hotel_slug, name))
    return cur.fetchone()


def create_location(cur: Any, hotel_slug: str, body: LocationIn) -> dict:
    if location_by_name(cur, hotel_slug, body.name.strip()):
        raise ClefsError(409, "A location with that name exists", "location_name_taken")
    cur.execute("INSERT INTO amn_locations (hotel_slug, kind, name, floor, active) VALUES (%s, %s, %s, %s, %s) "
                "RETURNING *", (hotel_slug, body.kind, body.name.strip(), (body.floor or "").strip() or None,
                                body.active))
    return cur.fetchone()


def update_location(cur: Any, hotel_slug: str, location_id: int, body: LocationPatch) -> tuple[dict, dict]:
    before = get_location(cur, hotel_slug, location_id)
    sets = body.model_dump(exclude_unset=True)
    for k in ("kind", "name", "active"):
        if k in sets and sets[k] is None:
            raise bad_request(f"{k} cannot be null", "invalid_field")
    if "name" in sets:
        other = location_by_name(cur, hotel_slug, sets["name"].strip())
        if other and other["id"] != location_id:
            raise ClefsError(409, "A location with that name exists", "location_name_taken")
        sets["name"] = sets["name"].strip()
    if "floor" in sets:
        sets["floor"] = (sets["floor"] or "").strip() or None
    if not sets:
        return before, before
    cols = ", ".join(f"{k} = %s" for k in sets) + ", updated_at = NOW()"
    cur.execute(f"UPDATE amn_locations SET {cols} WHERE hotel_slug = %s AND id = %s RETURNING *",
                (*sets.values(), hotel_slug, location_id))
    return before, cur.fetchone()


def delete_location(cur: Any, hotel_slug: str, location_id: int) -> tuple[dict, bool]:
    """A location the ledger names is deactivated, an unused one deleted."""
    before = get_location(cur, hotel_slug, location_id)
    cur.execute("SELECT 1 FROM amn_issuance WHERE from_location_id = %s OR to_location_id = %s LIMIT 1",
                (location_id, location_id))
    if cur.fetchone():
        cur.execute("UPDATE amn_locations SET active = FALSE, updated_at = NOW() WHERE id = %s", (location_id,))
        return before, False
    cur.execute("DELETE FROM amn_locations WHERE id = %s", (location_id,))
    return before, True


# ─── Par rows ───


def par_out(r: dict) -> dict:
    q = catalog.qty_str
    return {
        "location_id": r["location_id"], "item_id": r["item_id"], "par_qty": q(r["par_qty"]),
        "days_of_supply": q(r["days_of_supply"]), "safety_stock": q(r["safety_stock"]), "lead_days": q(r["lead_days"]),
        "pinned": bool(r["pinned"]), "computed": r.get("computed") or {},
        "computed_at": r["computed_at"].isoformat() if r.get("computed_at") else None,
        "location_name": r.get("location_name"), "location_kind": r.get("location_kind"), "floor": r.get("floor"),
        "item_key": r.get("item_key"), "item_name": r.get("item_name"),
    }


def list_par(cur: Any, hotel_slug: str, *, location_id: int | None = None) -> list[dict]:
    cur.execute(
        """
        SELECT p.*, l.name AS location_name, l.kind AS location_kind, l.floor, i.key AS item_key, i.name_en AS item_name
        FROM amn_par_levels p JOIN amn_locations l ON l.id = p.location_id JOIN amn_items i ON i.id = p.item_id
        WHERE p.hotel_slug = %s AND (%s::bigint IS NULL OR p.location_id = %s)
        ORDER BY l.kind, l.floor NULLS FIRST, l.name, i.sort, i.name_en
        """,
        (hotel_slug, location_id, location_id),
    )
    return list(cur.fetchall())


def upsert_par(cur: Any, hotel_slug: str, body: ParIn) -> dict:
    """Create or edit one par row. A given par_qty pins it (the weekly
    recompute leaves pinned rows alone) unless pinned=false is sent too."""
    loc = get_location(cur, hotel_slug, body.location_id)
    catalog.require_item(cur, hotel_slug, body.item_id)
    dos, lead = PAR_DEFAULTS.get(loc["kind"], (Decimal("2"), Decimal("1")))
    cur.execute("SELECT * FROM amn_par_levels WHERE location_id = %s AND item_id = %s FOR UPDATE",
                (body.location_id, body.item_id))
    cur_row = cur.fetchone() or {}
    pinned = body.pinned if body.pinned is not None else (True if body.par_qty is not None else cur_row.get("pinned", False))
    vals = {
        "par_qty": body.par_qty if body.par_qty is not None else cur_row.get("par_qty", Decimal(0)),
        "days_of_supply": body.days_of_supply or cur_row.get("days_of_supply") or dos,
        "safety_stock": body.safety_stock if body.safety_stock is not None else cur_row.get("safety_stock", Decimal(0)),
        "lead_days": body.lead_days or cur_row.get("lead_days") or lead,
    }
    cur.execute(
        """
        INSERT INTO amn_par_levels (hotel_slug, location_id, item_id, par_qty, days_of_supply, safety_stock, lead_days,
                                    pinned)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (location_id, item_id) DO UPDATE SET par_qty = EXCLUDED.par_qty,
            days_of_supply = EXCLUDED.days_of_supply, safety_stock = EXCLUDED.safety_stock,
            lead_days = EXCLUDED.lead_days, pinned = EXCLUDED.pinned, updated_at = NOW()
        RETURNING *
        """,
        (hotel_slug, body.location_id, body.item_id, vals["par_qty"], vals["days_of_supply"], vals["safety_stock"],
         vals["lead_days"], pinned),
    )
    return cur.fetchone()


def delete_par(cur: Any, hotel_slug: str, location_id: int, item_id: int) -> bool:
    cur.execute("DELETE FROM amn_par_levels WHERE hotel_slug = %s AND location_id = %s AND item_id = %s",
                (hotel_slug, location_id, item_id))
    return cur.rowcount > 0


# ─── Stock counts and on-hand ───


def count_out(r: dict) -> dict:
    return {"id": r["id"], "location_id": r["location_id"], "item_id": r["item_id"],
            "counted_at": r["counted_at"].isoformat(), "qty": catalog.qty_str(r["qty"]),
            "counted_by": r.get("counted_by"), "note": r.get("note")}


def add_counts(cur: Any, hotel_slug: str, body: CountsIn, *, user_id: int | None = None) -> list[dict]:
    now = clock.now()
    out = []
    for ln in body.lines:
        get_location(cur, hotel_slug, ln.location_id)
        catalog.require_item(cur, hotel_slug, ln.item_id)
        at = ln.counted_at or now
        if at.tzinfo is None:
            raise bad_request("counted_at must carry a timezone", "naive_datetime")
        if at > now:
            raise bad_request("A count cannot be in the future", "count_in_future")
        cur.execute(
            "INSERT INTO amn_stock_counts (hotel_slug, location_id, item_id, counted_at, qty, counted_by, "
            "counted_by_user_id, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (hotel_slug, ln.location_id, ln.item_id, at, ln.qty, ln.counted_by, user_id, ln.note),
        )
        out.append(cur.fetchone())
    return out


def list_counts(cur: Any, hotel_slug: str, *, location_id: int | None = None, item_id: int | None = None,
                limit: int = 200) -> list[dict]:
    cur.execute(
        "SELECT * FROM amn_stock_counts WHERE hotel_slug = %s AND (%s::bigint IS NULL OR location_id = %s) "
        "AND (%s::bigint IS NULL OR item_id = %s) ORDER BY counted_at DESC, id DESC LIMIT %s",
        (hotel_slug, location_id, location_id, item_id, item_id, limit),
    )
    return list(cur.fetchall())


def _flow(cur: Any, location_id: int, item_id: int, start: datetime, end: datetime) -> tuple[Decimal, Decimal]:
    """(received, sent out) at a location in (start, end]. A return line has
    negative qty; it flows back the same path with its sign."""
    cur.execute(
        """
        SELECT COALESCE(SUM(qty) FILTER (WHERE to_location_id = %s), 0) AS received,
               COALESCE(SUM(qty) FILTER (WHERE from_location_id = %s), 0) AS sent
        FROM amn_issuance WHERE item_id = %s AND voided_at IS NULL AND issued_at > %s AND issued_at <= %s
          AND (to_location_id = %s OR from_location_id = %s)
        """,
        (location_id, location_id, item_id, start, end, location_id, location_id),
    )
    r = cur.fetchone()
    return D(r["received"]), D(r["sent"])


def on_hand(cur: Any, hotel_slug: str, location_id: int, item_id: int, *, at: datetime | None = None) -> dict | None:
    """Last count + received - sent out since. None without any count."""
    at = at or clock.now()
    cur.execute("SELECT * FROM amn_stock_counts WHERE hotel_slug = %s AND location_id = %s AND item_id = %s "
                "AND counted_at <= %s ORDER BY counted_at DESC, id DESC LIMIT 1", (hotel_slug, location_id, item_id, at))
    last = cur.fetchone()
    if not last:
        return None
    received, sent = _flow(cur, location_id, item_id, last["counted_at"], at)
    return {"qty": D(last["qty"]) + received - sent, "counted_at": last["counted_at"], "counted_qty": D(last["qty"]),
            "received": received, "sent": sent}


def count_consumption(cur: Any, hotel_slug: str, location_id: int, item_id: int, start: datetime,
                      end: datetime) -> list[dict]:
    """Exact consumption between consecutive counts in [start, end]:
    opening + received - sent out - closing, one row per count pair."""
    cur.execute("SELECT counted_at, qty FROM amn_stock_counts WHERE hotel_slug = %s AND location_id = %s "
                "AND item_id = %s AND counted_at >= %s AND counted_at <= %s ORDER BY counted_at",
                (hotel_slug, location_id, item_id, start, end))
    counts = cur.fetchall()
    out = []
    for a, b in zip(counts, counts[1:]):
        received, sent = _flow(cur, location_id, item_id, a["counted_at"], b["counted_at"])
        used = D(a["qty"]) + received - sent - D(b["qty"])
        out.append({"from": a["counted_at"].isoformat(), "to": b["counted_at"].isoformat(),
                    "opening": catalog.qty_str(a["qty"]), "received": catalog.qty_str(received),
                    "sent": catalog.qty_str(sent), "closing": catalog.qty_str(b["qty"]),
                    "consumed": catalog.qty_str(used)})
    return out
