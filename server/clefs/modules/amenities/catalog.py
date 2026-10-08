"""
The amenity catalog (Canon 8: amn_items): items, unit cost history (costs
include excise), alias matching and the UAE default seed.

Money crosses the API as Decimal strings in the hotel currency (Canon 2);
storage is BIGINT minor units. Every function takes the caller's cursor and
never opens its own transaction.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from clefs.core.errors import ClefsError, bad_request, not_found
from clefs.core.money import D, money_str, to_minor
from clefs.modules.amenities import seed as seed_mod

Category = Literal["BATH", "DENTAL", "SHAVING", "SLIPPERS", "WATER", "TEA_COFFEE", "MINIBAR", "OTHER"]
Basis = Literal["PER_OCCUPIED_ROOM_NIGHT", "PER_ARRIVAL", "PER_GUEST_NIGHT", "ON_REQUEST"]
Excise = Literal["NONE", "SWEETENED", "ENERGY"]

_KEY_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{0,47}$")
ITEM_COLUMNS = (
    "key", "sku", "name_en", "name_ar", "category", "uom", "basis", "std_qty", "unit_cost_minor",
    "excise_category", "is_single_use_plastic", "sup_ban_ref", "aliases", "request_type_key", "max_qty",
    "chargeable", "price_minor", "needs_review", "source", "source_url", "active", "sort",
)


# ─── Models ───


def _clean_aliases(v: list[str] | None) -> list[str]:
    out: list[str] = []
    for a in v or []:
        a = " ".join(str(a).split())[:60]
        if a and a.lower() not in {x.lower() for x in out}:
            out.append(a)
    return out[:40]


class ItemIn(BaseModel):
    key: str | None = Field(None, max_length=48)
    sku: str | None = Field(None, max_length=60)
    name_en: str = Field(min_length=1, max_length=120)
    name_ar: str | None = Field(None, max_length=120)
    category: Category = "OTHER"
    uom: str = Field("unit", max_length=20)
    basis: Basis = "ON_REQUEST"
    std_qty: Decimal = Field(Decimal("0"), ge=0, le=999)
    unit_cost: Decimal = Field(Decimal("0"), ge=0)          # major units, incl. excise
    excise_category: Excise = "NONE"
    is_single_use_plastic: bool = False
    sup_ban_ref: str | None = Field(None, max_length=40)
    aliases: list[str] = Field(default_factory=list)
    request_type_key: str | None = Field(None, max_length=60)
    max_qty: int = Field(4, ge=1, le=50)
    chargeable: bool = False
    price: Decimal | None = Field(None, ge=0)
    active: bool = True
    sort: int = 0

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        return _clean_aliases(v)


class ItemPatch(BaseModel):
    sku: str | None = Field(None, max_length=60)
    name_en: str | None = Field(None, min_length=1, max_length=120)
    name_ar: str | None = Field(None, max_length=120)
    category: Category | None = None
    uom: str | None = Field(None, max_length=20)
    basis: Basis | None = None
    std_qty: Decimal | None = Field(None, ge=0, le=999)
    unit_cost: Decimal | None = Field(None, ge=0)
    excise_category: Excise | None = None
    is_single_use_plastic: bool | None = None
    sup_ban_ref: str | None = Field(None, max_length=40)
    aliases: list[str] | None = None
    request_type_key: str | None = Field(None, max_length=60)
    max_qty: int | None = Field(None, ge=1, le=50)
    chargeable: bool | None = None
    price: Decimal | None = Field(None, ge=0)
    needs_review: bool | None = None
    active: bool | None = None
    sort: int | None = None


class CostIn(BaseModel):
    effective_from: date
    unit_cost: Decimal = Field(ge=0)                        # incl. excise, major units
    excise: Decimal = Field(Decimal("0"), ge=0)             # the excise part of unit_cost
    source: Literal["SUPPLIER_INVOICE", "MANUAL"] = "MANUAL"
    invoice_ref: str | None = Field(None, max_length=80)


# ─── Serialization ───


def qty_str(v: Any) -> str:
    """A quantity as a plain decimal string without trailing zeros ("10", "0.5")."""
    d = D(v or 0)
    return format(d.normalize(), "f") if d else "0"



def item_out(row: dict, *, language: str = "en") -> dict:
    """API shape. Includes the frozen amenities.api fields (id, key, name,
    request_type, max_qty, chargeable, price, currency) and the columns the
    console edits."""
    ccy = (row.get("currency") or "AED").strip()
    name = (row.get("name_ar") if language == "ar" else None) or row["name_en"]
    return {
        "id": row["id"], "key": row["key"], "name": name, "name_en": row["name_en"], "name_ar": row.get("name_ar"),
        "sku": row.get("sku"), "category": row["category"], "uom": row["uom"], "basis": row["basis"],
        "std_qty": qty_str(row["std_qty"]),
        "unit_cost": money_str(row["unit_cost_minor"], ccy), "unit_cost_minor": int(row["unit_cost_minor"]),
        "currency": ccy, "excise_category": row["excise_category"],
        "is_single_use_plastic": bool(row["is_single_use_plastic"]), "sup_ban_ref": row.get("sup_ban_ref"),
        "aliases": list(row.get("aliases") or []), "request_type": row.get("request_type_key"),
        "request_type_key": row.get("request_type_key"), "max_qty": int(row["max_qty"]),
        "chargeable": bool(row["chargeable"]),
        "price": money_str(row["price_minor"], ccy) if row.get("price_minor") is not None else None,
        "price_minor": row.get("price_minor"), "needs_review": bool(row["needs_review"]),
        "source": row["source"], "source_url": row.get("source_url"), "active": bool(row["active"]),
        "sort": int(row["sort"]),
    }


# ─── Items ───


def _slug_key(name: str) -> str:
    """"Dental kit (large)" -> "DENTAL_KIT_LARGE"; non-Latin names fall back to ITEM."""
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    k = re.sub(r"[^A-Z0-9]+", "_", ascii_.upper()).strip("_")[:40]
    return k or "ITEM"


def list_items(cur: Any, hotel_slug: str, *, active_only: bool = False, category: str | None = None) -> list[dict]:
    where, params = ["hotel_slug = %s"], [hotel_slug]
    if active_only:
        where.append("active")
    if category:
        where.append("category = %s")
        params.append(category)
    cur.execute(f"SELECT * FROM amn_items WHERE {' AND '.join(where)} ORDER BY sort, name_en, id", params)
    return list(cur.fetchall())


def get_item(cur: Any, hotel_slug: str, item_id: int, *, for_update: bool = False) -> dict | None:
    cur.execute("SELECT * FROM amn_items WHERE hotel_slug = %s AND id = %s" + (" FOR UPDATE" if for_update else ""),
                (hotel_slug, item_id))
    return cur.fetchone()


def require_item(cur: Any, hotel_slug: str, item_id: int, *, for_update: bool = False) -> dict:
    row = get_item(cur, hotel_slug, item_id, for_update=for_update)
    if not row:
        raise not_found("Amenity item not found", "item_not_found")
    return row


def item_by_key(cur: Any, hotel_slug: str, key: str) -> dict | None:
    cur.execute("SELECT * FROM amn_items WHERE hotel_slug = %s AND key = %s", (hotel_slug, key))
    return cur.fetchone()


def _free_key(cur: Any, hotel_slug: str, base: str) -> str:
    key, n = base, 2
    while item_by_key(cur, hotel_slug, key):
        suffix = f"_{n}"
        key, n = base[: 48 - len(suffix)] + suffix, n + 1
    return key


def create_item(cur: Any, hotel_slug: str, currency: str, body: ItemIn, *, source: str = "MANUAL",
                needs_review: bool = False, source_url: str | None = None) -> dict:
    key = (body.key or "").strip().upper() or _free_key(cur, hotel_slug, _slug_key(body.name_en))
    if not _KEY_RE.match(key):
        raise bad_request("key must be A-Z, 0-9 and _ (max 48)", "invalid_key")
    if item_by_key(cur, hotel_slug, key):
        raise ClefsError(409, f"An item with key {key} already exists", "item_key_taken")
    price_minor = to_minor(body.price, currency) if body.price is not None else None
    cur.execute(
        """
        INSERT INTO amn_items (hotel_slug, key, sku, name_en, name_ar, category, uom, basis, std_qty,
            unit_cost_minor, currency, excise_category, is_single_use_plastic, sup_ban_ref, aliases,
            request_type_key, max_qty, chargeable, price_minor, needs_review, source, source_url, active, sort)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (hotel_slug, key, body.sku, body.name_en.strip(), body.name_ar, body.category, body.uom, body.basis,
         body.std_qty, to_minor(body.unit_cost, currency), currency, body.excise_category,
         body.is_single_use_plastic, body.sup_ban_ref, body.aliases, body.request_type_key, body.max_qty,
         body.chargeable or price_minor is not None, price_minor, needs_review, source, source_url, body.active,
         body.sort),
    )
    return cur.fetchone()


def update_item(cur: Any, hotel_slug: str, item_id: int, body: ItemPatch) -> tuple[dict, dict]:
    before = require_item(cur, hotel_slug, item_id, for_update=True)
    ccy = before["currency"].strip()
    changes = body.model_dump(exclude_unset=True)
    sets: dict[str, Any] = {}
    for k, v in changes.items():
        if k == "unit_cost":
            if v is None:
                raise bad_request("unit_cost cannot be null", "invalid_cost")
            sets["unit_cost_minor"] = to_minor(v, ccy)
        elif k == "price":
            sets["price_minor"] = to_minor(v, ccy) if v is not None else None
        elif k == "aliases":
            sets["aliases"] = _clean_aliases(v)
        elif k in ("name_en", "category", "uom", "basis", "std_qty", "excise_category", "is_single_use_plastic",
                   "max_qty", "chargeable", "needs_review", "active", "sort") and v is None:
            raise bad_request(f"{k} cannot be null", "invalid_field")
        else:
            sets[k] = v
    if not sets:
        return before, before
    # Column names come from the pydantic model's own fields, never the body.
    cols = ", ".join(f"{k} = %s" for k in sets) + ", updated_at = NOW()"
    cur.execute(f"UPDATE amn_items SET {cols} WHERE hotel_slug = %s AND id = %s RETURNING *",
                (*sets.values(), hotel_slug, item_id))
    return before, cur.fetchone()


def delete_item(cur: Any, hotel_slug: str, item_id: int) -> tuple[dict, bool]:
    """Items with ledger rows are deactivated (history must keep its cost);
    an unused item is deleted. Returns (row, deleted)."""
    before = require_item(cur, hotel_slug, item_id, for_update=True)
    cur.execute("SELECT 1 FROM amn_issuance WHERE item_id = %s LIMIT 1", (item_id,))
    used = cur.fetchone() is not None
    if not used:
        cur.execute("SELECT 1 FROM amn_request_lines WHERE item_id = %s LIMIT 1", (item_id,))
        used = cur.fetchone() is not None
    if used:
        cur.execute("UPDATE amn_items SET active = FALSE, updated_at = NOW() WHERE id = %s", (item_id,))
        return before, False
    cur.execute("DELETE FROM amn_items WHERE id = %s", (item_id,))
    return before, True


# ─── Unit cost history ───


def cost_out(row: dict) -> dict:
    ccy = row["currency"].strip()
    return {
        "id": row["id"], "item_id": row["item_id"], "effective_from": row["effective_from"].isoformat(),
        "unit_cost": money_str(row["unit_cost_minor"], ccy), "unit_cost_minor": int(row["unit_cost_minor"]),
        "excise": money_str(row["excise_minor"], ccy), "excise_minor": int(row["excise_minor"]), "currency": ccy,
        "source": row["source"], "invoice_ref": row.get("invoice_ref"), "import_batch_id": row.get("import_batch_id"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def add_cost(cur: Any, hotel_slug: str, item_id: int, *, effective_from: date, unit_cost_minor: int,
             excise_minor: int = 0, source: str = "MANUAL", invoice_ref: str | None = None,
             import_batch_id: int | None = None, user_id: int | None = None) -> dict:
    """Record the unit cost (incl. excise) from a date. Same item and date
    replaces the earlier entry (a corrected invoice). When the entry is the
    newest, amn_items.unit_cost_minor follows it."""
    item = require_item(cur, hotel_slug, item_id)
    if excise_minor > unit_cost_minor:
        raise bad_request("The excise part cannot exceed the unit cost that includes it", "invalid_excise")
    ccy = item["currency"].strip()
    cur.execute(
        """
        INSERT INTO amn_item_costs (hotel_slug, item_id, effective_from, unit_cost_minor, excise_minor, currency,
                                    source, invoice_ref, import_batch_id, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (item_id, effective_from) DO UPDATE SET unit_cost_minor = EXCLUDED.unit_cost_minor,
            excise_minor = EXCLUDED.excise_minor, source = EXCLUDED.source, invoice_ref = EXCLUDED.invoice_ref,
            import_batch_id = EXCLUDED.import_batch_id, created_by = EXCLUDED.created_by, created_at = NOW()
        RETURNING *
        """,
        (hotel_slug, item_id, effective_from, unit_cost_minor, excise_minor, ccy, source, invoice_ref,
         import_batch_id, user_id),
    )
    row = cur.fetchone()
    cur.execute(
        """
        UPDATE amn_items i SET unit_cost_minor = c.unit_cost_minor, updated_at = NOW()
        FROM (SELECT unit_cost_minor FROM amn_item_costs WHERE item_id = %s
              ORDER BY effective_from DESC LIMIT 1) c
        WHERE i.id = %s
        """,
        (item_id, item_id),
    )
    return row


def list_costs(cur: Any, hotel_slug: str, item_id: int) -> list[dict]:
    require_item(cur, hotel_slug, item_id)
    cur.execute("SELECT * FROM amn_item_costs WHERE hotel_slug = %s AND item_id = %s ORDER BY effective_from DESC",
                (hotel_slug, item_id))
    return list(cur.fetchall())


class CostBook:
    """Unit cost (incl. excise) per item and day, from one read of the
    history. Before the first history row the item's current cost applies."""

    def __init__(self, cur: Any, hotel_slug: str, item_ids: list[int] | None = None) -> None:
        cur.execute("SELECT id, unit_cost_minor FROM amn_items WHERE hotel_slug = %s", (hotel_slug,))
        self._current = {r["id"]: int(r["unit_cost_minor"]) for r in cur.fetchall()}
        cur.execute("SELECT item_id, effective_from, unit_cost_minor FROM amn_item_costs WHERE hotel_slug = %s "
                    "ORDER BY item_id, effective_from", (hotel_slug,))
        self._hist: dict[int, list[tuple[date, int]]] = {}
        for r in cur.fetchall():
            self._hist.setdefault(r["item_id"], []).append((r["effective_from"], int(r["unit_cost_minor"])))

    def on(self, item_id: int, d: date) -> int:
        best = None
        for eff, cost in self._hist.get(item_id, ()):
            if eff <= d:
                best = cost
            else:
                break
        if best is None:
            hist = self._hist.get(item_id)
            # Before the first dated cost, the earliest known cost is a better
            # guess than today's (prices rise; today's would overstate).
            best = hist[0][1] if hist else self._current.get(item_id, 0)
        return best


# ─── Alias matching ───
# The second step of request linking (tool value, then alias, then LLM). A
# match needs the whole alias as words ("razor" never matches "razorback"),
# a plural s/es is allowed, and a number just before it is the quantity
# ("2 toothbrushes", "2x razor"). Where two items' aliases overlap in the
# text, the longer alias wins ("shower gel" over "gel").

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_WORD_NUMBERS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "pair": 1}
_NON_WORD = re.compile(r"[^\w؀-ۿ]+", re.UNICODE)


def normalize_text(s: str | None) -> str:
    t = unicodedata.normalize("NFKC", s or "").translate(_ARABIC_DIGITS).casefold()
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c) or "؀" <= c <= "ۿ")
    return " ".join(_NON_WORD.sub(" ", t).split())


def _alias_terms(item: dict) -> list[str]:
    terms = [*(item.get("aliases") or []), item.get("name_en") or "", item.get("name_ar") or ""]
    out = []
    for t in terms:
        n = normalize_text(t)
        if len(n) >= 3 and n not in out:
            out.append(n)
    return out


def match_items(text: str | None, items: list[dict]) -> list[dict]:
    """[{item, qty, alias}] for each catalog item the text names (active items
    only), longest alias first."""
    norm = normalize_text(text)
    if not norm:
        return []
    hits: list[tuple[int, int, dict, int, str]] = []            # (start, end, item, qty, alias)
    for item in items:
        if not item.get("active", True):
            continue
        best = None
        for a in _alias_terms(item):
            pat = r"(?:^|\s)(?:(\d{1,2}|" + "|".join(_WORD_NUMBERS) + r")\s*(?:x\s*)?)?(" + re.escape(a) + r")(?:e?s)?(?=\s|$)"
            m = re.search(pat, norm)
            if m and (best is None or len(a) > len(best[4])):
                qty = 1
                if m.group(1):
                    qty = int(m.group(1)) if m.group(1).isdigit() else _WORD_NUMBERS[m.group(1)]
                else:
                    after = re.match(r"\s*x\s*(\d{1,2})(?=\s|$)", norm[m.end():])
                    qty = int(after.group(1)) if after else 1
                best = (m.start(2), m.end(2), item, qty, a)
        if best:
            hits.append(best)
    kept = []
    for h in sorted(hits, key=lambda h: -(h[1] - h[0])):
        if any(k[0] <= h[0] and h[1] <= k[1] and k[2]["id"] != h[2]["id"] for k in kept):
            continue
        kept.append(h)
    return [{"item": h[2], "qty": max(1, min(h[3], int(h[2].get("max_qty") or 4))), "alias": h[4]} for h in kept]


# ─── UAE default seed ───


def seed_defaults(cur: Any, hotel_slug: str, currency: str, *, force: bool = False) -> dict:
    """Insert the UAE default catalog once per hotel (needs_review = true on
    every row, with its source_url). Existing keys are left alone, so a hotel
    that already built its own catalog only gains the missing items."""
    cur.execute("SELECT version FROM amn_seed_runs WHERE hotel_slug = %s", (hotel_slug,))
    done = cur.fetchone()
    if done and not force:
        return {"seeded": 0, "already": True, "version": done["version"]}
    added = 0
    for r in seed_mod.default_rows(currency):
        if item_by_key(cur, hotel_slug, r["key"]):
            continue
        body = ItemIn(key=r["key"], name_en=r["name_en"], name_ar=r["name_ar"], category=r["category"],
                      basis=r["basis"], std_qty=Decimal(r["std_qty"]), unit_cost=Decimal(0),
                      excise_category=r["excise_category"], is_single_use_plastic=r["is_single_use_plastic"],
                      aliases=r["aliases"], request_type_key=r["request_type_key"], chargeable=r["chargeable"],
                      sort=r["sort"])
        row = create_item(cur, hotel_slug, currency, body, source="SEED", needs_review=True,
                          source_url=r["source_url"])
        cur.execute("UPDATE amn_items SET unit_cost_minor = %s, price_minor = %s WHERE id = %s",
                    (r["unit_cost_minor"], r["price_minor"], row["id"]))
        # A cost row dated far back makes the seeded cost the base of the
        # history, so a later invoice cost never reprices earlier days.
        cur.execute(
            "INSERT INTO amn_item_costs (hotel_slug, item_id, effective_from, unit_cost_minor, excise_minor, currency, "
            "source) VALUES (%s, %s, %s, %s, %s, %s, 'SEED') ON CONFLICT DO NOTHING",
            (hotel_slug, row["id"], date(2000, 1, 1), r["unit_cost_minor"], r["excise_minor"], currency),
        )
        added += 1
    cur.execute(
        "INSERT INTO amn_seed_runs (hotel_slug, version, items) VALUES (%s, %s, %s) "
        "ON CONFLICT (hotel_slug) DO UPDATE SET version = EXCLUDED.version, items = amn_seed_runs.items + "
        "EXCLUDED.items, seeded_at = NOW()",
        (hotel_slug, seed_mod.SEED_VERSION, added),
    )
    return {"seeded": added, "already": False, "version": seed_mod.SEED_VERSION}

