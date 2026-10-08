"""
The issuance log (02 spec 5.1): every unit that leaves the store or a floor
closet, entered on the mobile quick-entry grid, posted by an API, or imported
from a CSV through clefs.core.ingest (commit handler ISSUANCE).

Returns are negative lines. Each line snapshots the unit cost (incl. excise)
of its business date, so a later price change never rewrites what a day
cost. The business date comes from clefs.core.time (the night audit cutoff),
and a missing shift is inferred from the hotel-local hour.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from clefs import jobs
from clefs.core import clock
from clefs.core import time as ctime
from clefs.core.errors import ClefsError, bad_request, not_found
from clefs.core.hotels import get_hotel
from clefs.core.ingest import register_commit_handler
from clefs.core.money import D, money_str
from clefs.modules.amenities import catalog, stock
from clefs.modules.amenities.constants import REASONS, SHIFTS

Shift = Literal["MORNING", "EVENING", "NIGHT"]
Reason = Literal["REPLENISH", "GUEST_REQUEST", "VIP", "DAMAGE", "RETURN"]
MAX_LINES = 500


# ─── Models ───


class IssueLine(BaseModel):
    item_id: int
    qty: Decimal = Field(ge=-9999, le=9999)
    issued_at: datetime | None = None
    shift: Shift | None = None
    from_location_id: int | None = None
    to_location_id: int | None = None
    to_floor: str | None = Field(None, max_length=20)
    staff_member_id: int | None = None
    staff_name: str | None = Field(None, max_length=80)
    reason: Reason = "REPLENISH"
    request_id: int | None = None
    note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _signs(self) -> "IssueLine":
        if self.qty == 0:
            raise ValueError("qty cannot be 0")
        # A RETURN is stock coming back: always negative, whatever was typed.
        if self.reason == "RETURN" and self.qty > 0:
            self.qty = -self.qty
        return self


class IssueIn(BaseModel):
    """Single line (the fields of IssueLine) or bulk ({lines: [...]})."""
    lines: list[IssueLine] = Field(min_length=1, max_length=MAX_LINES)


# ─── Helpers ───


def infer_shift(local: datetime) -> str:
    """07:00-15:00 morning, 15:00-23:00 evening, else night (hotel-local)."""
    t = local.time()
    if time(7) <= t < time(15):
        return "MORNING"
    if time(15) <= t < time(23):
        return "EVENING"
    return "NIGHT"


def issuance_out(r: dict) -> dict:
    ccy = r["currency"].strip()
    cost = r.get("unit_cost_minor")
    return {
        "id": r["id"], "issued_at": r["issued_at"].isoformat(), "business_date": r["business_date"].isoformat(),
        "shift": r.get("shift"), "item_id": r["item_id"], "item_key": r.get("item_key"),
        "item_name": r.get("item_name"), "qty": catalog.qty_str(r["qty"]),
        "from_location_id": r.get("from_location_id"), "to_location_id": r.get("to_location_id"),
        "to_floor": r.get("to_floor"), "staff_member_id": r.get("staff_member_id"), "staff_name": r.get("staff_name"),
        "reason": r["reason"], "request_id": r.get("request_id"), "source": r["source"],
        "import_batch_id": r.get("import_batch_id"),
        "unit_cost": money_str(cost, ccy) if cost is not None else None,
        "line_cost": money_str(int((D(cost) * D(r["qty"])).to_integral_value()), ccy) if cost is not None else None,
        "currency": ccy, "note": r.get("note"),
        "voided_at": r["voided_at"].isoformat() if r.get("voided_at") else None,
    }


# ─── Writing lines ───


def _staff_name(cur: Any, hotel_slug: str, staff_id: int | None) -> str | None:
    # staff_members is the legacy baseline table (0001); read-only here.
    if not staff_id:
        return None
    cur.execute("SELECT name FROM staff_members WHERE hotel_slug = %s AND id = %s", (hotel_slug, staff_id))
    r = cur.fetchone()
    return r["name"] if r else None


def insert_line(cur: Any, hotel: Any, ln: IssueLine, *, source: str = "MANUAL", user_id: int | None = None,
                import_batch_id: int | None = None, import_row_no: int | None = None,
                costs: catalog.CostBook | None = None, _loc_cache: dict | None = None) -> dict:
    """Validate and write one line. Floor comes from the line, else from the
    destination location (a floor closet), else stays NULL (store level)."""
    slug = hotel.slug
    item = catalog.require_item(cur, slug, ln.item_id)
    now = clock.now()
    at = ln.issued_at or now
    if at.tzinfo is None:
        at = at.replace(tzinfo=hotel.tz)
    if (at - now).total_seconds() > 300:                  # five minutes of device clock skew
        raise bad_request("An issuance cannot be dated in the future", "issued_in_future")
    cache = _loc_cache if _loc_cache is not None else {}
    floor = (ln.to_floor or "").strip() or None
    for lid in (ln.from_location_id, ln.to_location_id):
        if lid and lid not in cache:
            cache[lid] = stock.get_location(cur, slug, lid)
    if not floor and ln.to_location_id:
        floor = cache[ln.to_location_id].get("floor")
    bdate = ctime.business_date(hotel, at)
    shift = ln.shift or infer_shift(at.astimezone(hotel.tz))
    book = costs or catalog.CostBook(cur, slug)
    staff_name = (ln.staff_name or "").strip() or _staff_name(cur, slug, ln.staff_member_id)
    cur.execute(
        """
        INSERT INTO amn_issuance (hotel_slug, issued_at, business_date, shift, item_id, qty, from_location_id,
            to_location_id, to_floor, staff_member_id, staff_name, reason, request_id, source, import_batch_id,
            import_row_no, unit_cost_minor, currency, note, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, import_batch_id, import_row_no) WHERE import_batch_id IS NOT NULL
        DO UPDATE SET issued_at = EXCLUDED.issued_at, business_date = EXCLUDED.business_date,
            shift = EXCLUDED.shift, item_id = EXCLUDED.item_id, qty = EXCLUDED.qty, to_floor = EXCLUDED.to_floor,
            from_location_id = EXCLUDED.from_location_id, to_location_id = EXCLUDED.to_location_id,
            staff_name = EXCLUDED.staff_name, reason = EXCLUDED.reason, request_id = EXCLUDED.request_id,
            unit_cost_minor = EXCLUDED.unit_cost_minor, note = EXCLUDED.note, voided_at = NULL
        RETURNING *
        """,
        (slug, at, bdate, shift, item["id"], ln.qty, ln.from_location_id, ln.to_location_id, floor,
         ln.staff_member_id, staff_name, ln.reason, ln.request_id, source, import_batch_id, import_row_no,
         book.on(item["id"], bdate), item["currency"], ln.note, user_id),
    )
    row = cur.fetchone()
    row["item_key"], row["item_name"] = item["key"], item["name_en"]
    return row


def schedule_rollup(cur: Any, hotel: Any, dates: list[date]) -> None:
    """Lines dated before today change closed days: re-roll them soon
    (am.rollup_range) instead of waiting for the nightly window, which only
    looks back 14 days."""
    closed = [d for d in dates if d < ctime.local_date(hotel)]
    if closed:
        jobs.enqueue("am.rollup_range", {"start": min(closed).isoformat(), "end": max(closed).isoformat()},
                     hotel_slug=hotel.slug, dedupe_key=f"am.rollup_range:{hotel.slug}:{min(closed)}:{max(closed)}",
                     cur=cur)


def create_lines(cur: Any, hotel_slug: str, body: IssueIn, *, source: str = "MANUAL",
                 user_id: int | None = None) -> list[dict]:
    hotel = get_hotel(cur, hotel_slug)
    book = catalog.CostBook(cur, hotel_slug)
    cache: dict = {}
    rows = [insert_line(cur, hotel, ln, source=source, user_id=user_id, costs=book, _loc_cache=cache)
            for ln in body.lines]
    schedule_rollup(cur, hotel, [r["business_date"] for r in rows])
    return rows


# ─── Reading and voiding ───


def list_lines(cur: Any, hotel_slug: str, *, start: date, end: date, item_id: int | None = None,
               staff: str | None = None, include_voided: bool = False, limit: int = 500) -> list[dict]:
    cur.execute(
        f"""
        SELECT s.*, i.key AS item_key, i.name_en AS item_name FROM amn_issuance s JOIN amn_items i ON i.id = s.item_id
        WHERE s.hotel_slug = %s AND s.business_date BETWEEN %s AND %s
          AND (%s::bigint IS NULL OR s.item_id = %s) AND (%s::text IS NULL OR s.staff_name = %s)
          {'' if include_voided else 'AND s.voided_at IS NULL'}
        ORDER BY s.issued_at DESC, s.id DESC LIMIT %s
        """,
        (hotel_slug, start, end, item_id, item_id, staff, staff, limit),
    )
    return list(cur.fetchall())


def void_line(cur: Any, hotel_slug: str, line_id: int) -> dict:
    """A mistaken line is voided, never deleted: the log stays an audit trail."""
    cur.execute("UPDATE amn_issuance SET voided_at = NOW() WHERE hotel_slug = %s AND id = %s AND voided_at IS NULL "
                "RETURNING *", (hotel_slug, line_id))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT 1 FROM amn_issuance WHERE hotel_slug = %s AND id = %s", (hotel_slug, line_id))
        if cur.fetchone():
            raise ClefsError(409, "That line is already voided", "already_voided")
        raise not_found("Issuance line not found", "issuance_not_found")
    return row


# ─── CSV import (clefs.core.ingest kind ISSUANCE) ───
# Canonical fields (core/ingest/kinds.py): issued_at! item! qty! floor shift
# staff_name reason location request_ref. The item column may hold a key, a
# SKU, a name or any alias; a row naming no catalog item is skipped and
# reported in commit_result (one typo must not block a month of lines). A
# corrected re-upload supersedes its batch: that batch's lines are removed
# before the new ones go in, so nothing is counted twice.

_REASON_WORDS = {
    "replenish": "REPLENISH", "restock": "REPLENISH", "refill": "REPLENISH", "guest": "GUEST_REQUEST",
    "guest_request": "GUEST_REQUEST", "request": "GUEST_REQUEST", "vip": "VIP", "damage": "DAMAGE",
    "damaged": "DAMAGE", "write off": "DAMAGE", "return": "RETURN", "returned": "RETURN",
}


def _resolve_item_ref(ref: str, items: list[dict], by_key: dict, by_sku: dict, by_name: dict) -> dict | None:
    r = (ref or "").strip()
    if not r:
        return None
    hit = by_key.get(r.upper()) or by_sku.get(r.lower()) or by_name.get(catalog.normalize_text(r))
    if hit:
        return hit
    matches = catalog.match_items(r, items)
    return matches[0]["item"] if len(matches) == 1 else None


def _reason(raw: Any) -> str:
    t = " ".join(str(raw or "").replace("-", " ").split()).lower()
    if not t:
        return "REPLENISH"
    return _REASON_WORDS.get(t) or (t.upper() if t.upper() in REASONS else "REPLENISH")


def _shift(raw: Any) -> str | None:
    t = str(raw or "").strip().upper()
    for s in SHIFTS:
        if t and (t == s or t == s[0] or t.startswith(s[:3])):
            return s
    return None


def commit_issuance(cur: Any, batch: dict, rows: list[dict]) -> dict:
    slug = batch["hotel_slug"]
    hotel = get_hotel(cur, slug)
    items = catalog.list_items(cur, slug)
    by_key = {i["key"]: i for i in items}
    by_sku = {i["sku"].lower(): i for i in items if i.get("sku")}
    by_name = {catalog.normalize_text(i["name_en"]): i for i in items}
    by_name.update({catalog.normalize_text(i["name_ar"]): i for i in items if i.get("name_ar")})
    if batch.get("supersedes_batch_id"):
        cur.execute("DELETE FROM amn_issuance WHERE hotel_slug = %s AND import_batch_id = %s",
                    (slug, batch["supersedes_batch_id"]))
    book = catalog.CostBook(cur, slug)
    cache: dict = {}
    written, skipped, dates = 0, [], set()
    for r in rows:
        item = _resolve_item_ref(r.get("item") or "", items, by_key, by_sku, by_name)
        loc = stock.location_by_name(cur, slug, str(r["location"]).strip()) if r.get("location") else None
        try:
            qty = D(r.get("qty"))
            at = datetime.fromisoformat(str(r["issued_at"])) if r.get("issued_at") else None
        except Exception:                                   # noqa: BLE001  a malformed cell skips the row
            qty, at = Decimal(0), None
        if not item or qty == 0 or at is None:
            skipped.append({"row_no": r.get("_row_no"), "item": r.get("item"),
                            "reason": "unknown_item" if not item else "invalid_row"})
            continue
        req = str(r.get("request_ref") or "").strip()
        ln = IssueLine(item_id=item["id"], qty=qty, issued_at=at, shift=_shift(r.get("shift")),
                       to_location_id=loc["id"] if loc else None, to_floor=(str(r["floor"]).strip() if r.get("floor")
                                                                             else None),
                       staff_name=(str(r["staff_name"]).strip()[:80] if r.get("staff_name") else None),
                       reason=_reason(r.get("reason")), request_id=int(req) if req.isdigit() else None)
        row = insert_line(cur, hotel, ln, source="CSV", import_batch_id=batch["id"], import_row_no=r.get("_row_no"),
                          costs=book, _loc_cache=cache)
        dates.add(row["business_date"])
        written += 1
    if not written and rows:
        raise ClefsError(422, "No row names an item in the amenity catalog; add the items or their aliases first",
                         "no_known_items")
    schedule_rollup(cur, hotel, sorted(dates))
    return {"written": written, "skipped": len(skipped), "unmatched_rows": skipped[:50]}


register_commit_handler("ISSUANCE", commit_issuance)


# ─── Supplier invoices (kind SUPPLIER_INVOICE) -> unit cost history ───
# UAE excise is paid before the goods reach the hotel, so an invoice price
# already includes it. The excise part is estimated from the ad valorem rate
# (50% sweetened, 100% energy: excise = price x rate / (1 + rate)) and kept
# next to the cost, which is what variance multiplies by. Several lines for
# one item on one invoice date become one weighted average cost.

_EXCISE_RATE = {"SWEETENED": Decimal("0.5"), "ENERGY": Decimal("1")}


def commit_supplier_invoice(cur: Any, batch: dict, rows: list[dict]) -> dict:
    slug = batch["hotel_slug"]
    items = catalog.list_items(cur, slug)
    by_key = {i["key"]: i for i in items}
    by_sku = {i["sku"].lower(): i for i in items if i.get("sku")}
    by_name = {catalog.normalize_text(i["name_en"]): i for i in items}
    acc: dict[tuple[int, date], list] = {}
    skipped = []
    for r in rows:
        item = _resolve_item_ref(r.get("item") or "", items, by_key, by_sku, by_name)
        qty = D(r.get("qty") or 1)
        amount = r.get("amount_minor")
        unit = r.get("unit_cost_minor")
        if not item or not r.get("invoice_date") or qty <= 0 or (amount is None and unit is None):
            skipped.append({"row_no": r.get("_row_no"), "item": r.get("item")})
            continue
        total = D(amount) if amount is not None else D(unit) * qty
        key = (item["id"], date.fromisoformat(str(r["invoice_date"])[:10]))
        a = acc.setdefault(key, [Decimal(0), Decimal(0), item, r.get("invoice_no")])
        a[0] += total
        a[1] += qty
    for (item_id, d), (total, qty, item, inv) in acc.items():
        cost = int((total / qty).to_integral_value())
        rate = _EXCISE_RATE.get(item["excise_category"])
        excise = int((cost * rate / (1 + rate)).to_integral_value()) if rate else 0
        catalog.add_cost(cur, slug, item_id, effective_from=d, unit_cost_minor=cost, excise_minor=excise,
                         source="SUPPLIER_INVOICE", invoice_ref=(str(inv)[:80] if inv else None),
                         import_batch_id=batch["id"])
    return {"costs_written": len(acc), "skipped": len(skipped), "unmatched_rows": skipped[:50]}


register_commit_handler("SUPPLIER_INVOICE", commit_supplier_invoice)
