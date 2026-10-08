"""
Quotes, orders and the upsell funnel (03 spec 2.4 place_order, 2.5; Canon 9).

Flow: quote (the server re-prices every line from the DB, applies the
hotel's price display and fee stack, stores a snapshot for 10 minutes and
suggests pairings) -> confirm (needs that quote, from the same session, still
fresh and unused; creates the order, its lines and ONE linked F&B request via
service.api.create_request) -> console status changes (accepted, preparing,
delivered, cancelled, refunded) and payment.

Why the quote gate: a language model must never place an order the guest
did not see priced. Why snapshots: the guest agreed to the quoted figure, so
a confirm never re-prices behind their back; an item that became unavailable
in between is a 409 instead.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta
from typing import Any

from clefs import events, ratelimit
from clefs.core import clock
from clefs.core import time as ctime
from clefs.core.errors import ClefsError, bad_request, conflict, not_found, too_many
from clefs.core.money import money_str
from clefs.db import Json
from clefs.modules.service import api as service_api
from clefs.modules.upsell import menu, pricing
from clefs.modules.upsell.settings import UpsellSettings

log = logging.getLogger("clefs.upsell.orders")

MAX_LINES = 30
MAX_QTY = 50
ORDER_STATUSES = ("placed", "accepted", "preparing", "delivered", "cancelled", "refunded")
OPEN_STATUSES = ("placed", "accepted", "preparing")
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "placed": ("accepted", "preparing", "delivered", "cancelled"),
    "accepted": ("preparing", "delivered", "cancelled"),
    "preparing": ("delivered", "cancelled"),
    "delivered": ("refunded",),
    "cancelled": (),
    "refunded": (),
}
PAYMENTS = ("room_charge", "cash", "card", "complimentary")


# ─── Baskets ───


def normalize_lines(lines: Any) -> list[dict]:
    """[{item_id | menu_item_id, qty=1, notes?}] -> merged, validated lines.
    The same item twice becomes one line (quantities add, notes join)."""
    if not isinstance(lines, list) or not lines:
        raise bad_request("An order needs at least one line", "empty_order")
    merged: dict[int, dict] = {}
    for raw in lines:
        if not isinstance(raw, dict):
            raise bad_request("Each line is an object {item_id, qty}", "invalid_line")
        item_id = raw.get("item_id", raw.get("menu_item_id"))
        try:
            item_id = int(item_id)
            qty = int(raw.get("qty", 1))
        except (TypeError, ValueError):
            raise bad_request("item_id and qty must be integers", "invalid_line") from None
        if qty < 1 or qty > MAX_QTY:
            raise bad_request(f"qty must be 1-{MAX_QTY}", "invalid_qty")
        notes = str(raw.get("notes") or "").strip()[:300] or None
        line = merged.setdefault(item_id, {"item_id": item_id, "qty": 0, "notes": None})
        line["qty"] += qty
        if notes:
            line["notes"] = f"{line['notes']}; {notes}" if line["notes"] else notes
        if line["qty"] > MAX_QTY:
            raise bad_request(f"qty must be 1-{MAX_QTY}", "invalid_qty")
    if len(merged) > MAX_LINES:
        raise bad_request(f"At most {MAX_LINES} different items per order", "too_many_lines")
    return sorted(merged.values(), key=lambda line: line["item_id"])


def basket_hash(norm: list[dict]) -> str:
    """Identity of a basket for the frozen create_order path (items + qty;
    notes do not change the price, so they do not change the basket)."""
    key = ",".join(f"{line['item_id']}x{line['qty']}" for line in norm)
    return hashlib.sha256(key.encode()).hexdigest()


def session_key(hotel: Any, session_ref: str | None, room: str) -> str:
    """The funnel's session: the concierge session, else the room's stay day
    (hotel-local business date), so a room's repeated menu views collapse."""
    if session_ref:
        return session_ref
    return f"room:{room}:{ctime.business_date(hotel).isoformat()}"


# ─── Pricing a basket ───


def price_basket(
    cur: Any, hotel: Any, s: UpsellSettings, norm: list[dict], *, language: str = "en", check_hours: bool = True
) -> dict:
    """Re-price every line from the DB. 404 unknown item, 409 when an item is
    inactive, out of hours, alcoholic without opt-in, or its outlet does not
    deliver to rooms."""
    now = menu.local_now(hotel)
    ids = [line["item_id"] for line in norm]
    cur.execute(
        "SELECT i.*, o.key AS outlet_key, o.name_en AS outlet_name_en, o.name_ar AS outlet_name_ar,"
        " o.hours AS outlet_hours, o.active AS outlet_active, o.delivers_to_room, o.kind AS outlet_kind"
        " FROM ups_menu_items i JOIN ups_outlets o ON o.id = i.outlet_id"
        " WHERE i.hotel_slug = %s AND i.id = ANY(%s)",
        (hotel.slug, ids),
    )
    rows = {r["id"]: dict(r) for r in cur.fetchall()}
    priced: list[dict] = []
    currency = hotel.currency
    for line in norm:
        it = rows.get(line["item_id"])
        if it is None:
            raise not_found(f"Menu item {line['item_id']} not found", "item_not_found")
        outlet = {"hours": it["outlet_hours"], "active": it["outlet_active"]}
        name = menu._lang(it, "name", language)
        if it["alcohol"] and not s.alcohol_orders:
            raise conflict(f"{it['name_en']} cannot be ordered here", "alcohol_not_allowed")
        if not it["delivers_to_room"]:
            raise conflict(f"{it['outlet_name_en']} does not deliver to rooms", "outlet_no_delivery")
        if not (it["active"] and it["outlet_active"]) or (check_hours and not menu.item_available(it, outlet, now)):
            raise ClefsError(409, f"{it['name_en']} is not available right now", "item_unavailable")
        if it["currency"].strip() != currency:
            raise conflict(f"{it['name_en']} is priced in {it['currency']}", "currency_mismatch")
        priced.append({
            "item_id": it["id"], "name": name, "name_en": it["name_en"], "name_ar": it["name_ar"],
            "outlet": it["outlet_key"], "outlet_id": it["outlet_id"], "qty": line["qty"],
            "unit_price_minor": it["price_minor"], "line_total_minor": it["price_minor"] * line["qty"],
            "notes": line["notes"], "prep_min": it["prep_min"],
        })
    subtotal = sum(p["line_total_minor"] for p in priced)
    rates = pricing.resolve_rates(cur, hotel, s, ctime.local_date(hotel))
    if s.price_display == "plus_plus":
        net = subtotal
        sc, mf, vat = pricing.compose(net, rates)
        total = net + sc + mf + vat
    else:
        net, sc, mf, vat = pricing.decompose(subtotal, rates)
        total = subtotal
    return {
        "lines": priced, "subtotal_minor": subtotal, "net_minor": net, "service_charge_minor": sc,
        "municipality_fee_minor": mf, "vat_minor": vat, "total_minor": total, "currency": currency,
        "price_display": s.price_display, "rates": rates,
    }


# ─── Serialisation ───


def _m(minor: int | None, code: str) -> str | None:
    return money_str(minor, code)


def taxes_out(p: dict, rates: dict | pricing.Rates) -> list[dict]:
    """The fee lines a guest sees: included in the price (inclusive) or added."""
    r = rates.as_json() if isinstance(rates, pricing.Rates) else (rates or {})
    code = p["currency"].strip()
    included = p["price_display"] == "inclusive"
    out = []
    for key, label, rate in (
        ("service_charge", "Service charge", r.get("sc")),
        ("municipality_fee", "Municipality fee", r.get("mf")),
        ("vat", "VAT", r.get("vat")),
    ):
        amount = p[f"{key}_minor"]
        if amount or (rate and str(rate) not in ("0", "0.0")):
            out.append({"code": key, "label": label, "rate": str(rate) if rate is not None else None,
                        "amount": _m(amount, code), "amount_minor": amount, "included": included})
    return out


def lines_out(lines: list[dict], code: str) -> list[dict]:
    return [
        {**{k: v for k, v in line.items() if k not in ("outlet_id",)},
         "unit_price": _m(line["unit_price_minor"], code), "line_total": _m(line["line_total_minor"], code)}
        for line in lines
    ]


def quote_out(q: dict, *, suggestions: list[dict] | None = None) -> dict:
    code = q["currency"].strip()
    return {
        "quote_id": str(q["id"]),
        "room": q["room"],
        "lines": lines_out(q["lines"], code),
        "price_display": q["price_display"],
        "subtotal": _m(q["subtotal_minor"], code),
        "service_charge": _m(q["service_charge_minor"], code),
        "municipality_fee": _m(q["municipality_fee_minor"], code),
        "vat": _m(q["vat_minor"], code),
        "taxes": taxes_out(q, q.get("rates") or {}),
        "total": _m(q["total_minor"], code),
        "total_minor": q["total_minor"],
        "currency": code,
        "expires_at": q["expires_at"],
        "suggestions": suggestions or [],
    }


# ─── Suggestions ───


def suggestions_for(
    cur: Any, hotel: Any, s: UpsellSettings, norm: list[dict], *, language: str = "en"
) -> list[dict]:
    """Active pairings of basket items whose suggestion is orderable now and
    not already in the basket, best (lowest sort) first."""
    if s.max_suggestions <= 0:
        return []
    in_basket = {line["item_id"] for line in norm}
    cur.execute(
        "SELECT p.*, i.name_en, i.name_ar, i.price_minor, i.currency, i.alcohol, i.active, i.available_from,"
        " i.available_to, i.days_mask, o.hours, o.active AS outlet_active, o.delivers_to_room"
        " FROM ups_upsell_pairs p JOIN ups_menu_items i ON i.id = p.suggest_item_id"
        " JOIN ups_outlets o ON o.id = i.outlet_id"
        " WHERE p.hotel_slug = %s AND p.active AND p.item_id = ANY(%s) ORDER BY p.sort, p.id",
        (hotel.slug, list(in_basket)),
    )
    now = menu.local_now(hotel)
    out: list[dict] = []
    seen: set[int] = set()
    for p in cur.fetchall():
        sid = p["suggest_item_id"]
        if sid in in_basket or sid in seen or (p["alcohol"] and not s.alcohol_orders) or not p["delivers_to_room"]:
            continue
        if not menu.item_available(dict(p), {"hours": p["hours"], "active": p["outlet_active"]}, now):
            continue
        seen.add(sid)
        ar = (language or "").lower().startswith("ar")
        out.append({
            "item_id": sid, "for_item_id": p["item_id"], "kind": p["kind"],
            "name": p["name_ar"] if ar and p["name_ar"] else p["name_en"],
            "reason": p["reason_ar"] if ar and p["reason_ar"] else p["reason_en"],
            "price": _m(p["price_minor"], p["currency"].strip()), "currency": p["currency"].strip(),
        })
        if len(out) >= s.max_suggestions:
            break
    return out


# ─── Quotes ───


def create_quote(
    cur: Any,
    hotel: Any,
    s: UpsellSettings,
    *,
    room: str,
    lines: Any,
    session_ref: str | None = None,
    language: str = "en",
    source: str | None = None,
) -> dict:
    """Price the basket, store the snapshot, log suggestions as "shown"."""
    if not s.ordering_enabled:
        raise ClefsError(409, "Ordering is switched off for this hotel", "ordering_disabled")
    if not ratelimit.hit(cur, f"ups:quote:{hotel.slug}:{room}", limit=s.quotes_per_room_hour, window_s=3600):
        raise too_many()
    norm = normalize_lines(lines)
    p = price_basket(cur, hotel, s, norm, language=language)
    now = clock.now()
    cur.execute(
        """
        INSERT INTO ups_quotes (hotel_slug, session_ref, room, lines, lines_hash, price_display, convention, rates,
            subtotal_minor, service_charge_minor, municipality_fee_minor, vat_minor, total_minor, currency,
            created_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (hotel.slug, session_ref, room, Json(p["lines"]), basket_hash(norm), p["price_display"],
         p["rates"].convention, Json(p["rates"].as_json()), p["subtotal_minor"], p["service_charge_minor"],
         p["municipality_fee_minor"], p["vat_minor"], p["total_minor"], p["currency"],
         now, now + timedelta(minutes=s.quote_ttl_min)),
    )
    q = dict(cur.fetchone())
    sugg = suggestions_for(cur, hotel, s, norm, language=language)
    key = session_key(hotel, session_ref, room)
    for sg in sugg:
        record_event(cur, hotel, kind=sg["kind"], item_id=sg["item_id"], source_item_id=sg["for_item_id"],
                     session_ref=key, room=room, state="shown", source=source or "quote")
    return quote_out(q, suggestions=sugg)


# ─── Upsell funnel ───

EVENT_KINDS = ("pairing", "upgrade", "late_checkout", "spa", "other")
EVENT_STATES = ("shown", "accepted", "paid", "fulfilled", "refunded")


def record_event(
    cur: Any,
    hotel: Any,
    *,
    kind: str,
    state: str,
    item_id: int | None = None,
    source_item_id: int | None = None,
    session_ref: str | None = None,
    room: str | None = None,
    order_id: int | None = None,
    value_minor: int | None = None,
    source: str | None = None,
) -> int | None:
    """Append one funnel row; a repeat of (session, item, state) is a no-op
    and returns None (re-renders and retries never inflate the funnel)."""
    if kind not in EVENT_KINDS or state not in EVENT_STATES:
        raise bad_request("Unknown upsell event kind or state", "invalid_upsell_event")
    cur.execute(
        """
        INSERT INTO ups_upsell_events (hotel_slug, kind, item_id, source_item_id, session_ref, room, order_id, state,
            value_minor, currency, source, at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, session_ref, item_id, state)
            WHERE session_ref IS NOT NULL AND item_id IS NOT NULL DO NOTHING
        RETURNING id
        """,
        (hotel.slug, kind, item_id, source_item_id, session_ref, room, order_id, state, value_minor,
         hotel.currency if value_minor is not None else None, source, clock.now()),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def _advance_funnel(cur: Any, hotel: Any, order_id: int, state: str) -> int:
    """Copy every accepted upsell of an order forward to `state` (paid,
    fulfilled, refunded), keeping its session and value."""
    cur.execute(
        "SELECT * FROM ups_upsell_events WHERE hotel_slug = %s AND order_id = %s AND state = 'accepted'",
        (hotel.slug, order_id),
    )
    n = 0
    for ev in cur.fetchall():
        if record_event(cur, hotel, kind=ev["kind"], state=state, item_id=ev["item_id"],
                        source_item_id=ev["source_item_id"], session_ref=ev["session_ref"], room=ev["room"],
                        order_id=order_id, value_minor=ev["value_minor"], source=ev["source"]):
            n += 1
    return n


# ─── Confirm ───


def _order_source(source: str) -> str:
    """Service keeps its own list of intake channels; anything else files as web."""
    src = (source or "").strip().lower()
    return src if src in service_api.SOURCES else "web"


def _describe(order: dict, lines: list[dict], outlet_name: str | None, verified_room: bool) -> str:
    code = order["currency"].strip()
    items = ", ".join(f"{line['qty']}x {line['name_en']}" for line in lines)
    text = f"In-room order #{order['id']}" + (f" ({outlet_name})" if outlet_name else "") + f": {items}."
    notes = "; ".join(f"{line['name_en']}: {line['notes']}" for line in lines if line.get("notes"))
    if order.get("notes"):
        notes = f"{notes}; {order['notes']}" if notes else order["notes"]
    if notes:
        text += f" Notes: {notes}."
    text += f" Total {code} {money_str(order['total_minor'], code)} ({order['payment'].replace('_', ' ')})."
    if not verified_room:
        text = "[Room not verified: confirm at the door] " + text
    return text[:2000]


def _lock_quote(cur: Any, hotel_slug: str, quote_id: str) -> dict:
    try:
        qid = str(uuid.UUID(str(quote_id)))
    except ValueError:
        raise not_found("Quote not found", "quote_not_found") from None
    cur.execute("SELECT * FROM ups_quotes WHERE hotel_slug = %s AND id = %s FOR UPDATE", (hotel_slug, qid))
    q = cur.fetchone()
    if q is None:
        raise not_found("Quote not found", "quote_not_found")
    return dict(q)


def confirm_quote(
    cur: Any,
    hotel: Any,
    s: UpsellSettings,
    *,
    quote_id: str,
    room: str,
    source: str,
    session_ref: str | None = None,
    notes: str | None = None,
    verified_room: bool = True,
    language: str | None = None,
    payment: str = "room_charge",
) -> dict:
    """Turn a fresh quote into an order + one F&B request. Idempotent: a
    second confirm of the same quote returns the first order (deduped=True)."""
    if not s.ordering_enabled:
        raise ClefsError(409, "Ordering is switched off for this hotel", "ordering_disabled")
    if not verified_room and not s.allow_unverified_rooms:
        raise ClefsError(403, "Scan the QR code in your room to order", "room_not_verified")
    if payment not in PAYMENTS:
        raise bad_request("Unknown payment method", "invalid_payment")
    q = _lock_quote(cur, hotel.slug, quote_id)
    if q["room"] != room:
        raise conflict("This quote is for another room", "quote_room_mismatch")
    if q["session_ref"] is not None and q["session_ref"] != session_ref:
        raise conflict("This quote belongs to another session", "quote_session_mismatch")
    if q["order_id"] is not None:
        return {**get_order(cur, hotel.slug, q["order_id"]), "deduped": True}
    if q["expires_at"] <= clock.now():
        raise conflict("The quote has expired; ask for a new one", "quote_expired")
    if not ratelimit.hit(cur, f"ups:order:{hotel.slug}:{room}", limit=s.orders_per_room_hour, window_s=3600):
        raise too_many("Too many orders from this room; please call the front desk")
    # Still orderable? Prices stay as quoted (the guest agreed to them).
    norm = [{"item_id": line["item_id"], "qty": line["qty"], "notes": line.get("notes")} for line in q["lines"]]
    price_basket(cur, hotel, s, norm, language=language or "en")
    return _place(cur, hotel, s, q, source=source, session_ref=session_ref, notes=notes,
                  verified_room=verified_room, language=language, payment=payment)


def confirm_basket(cur: Any, hotel: Any, s: UpsellSettings, *, room: str, lines: Any, source: str) -> dict:
    """The frozen api.create_order path, which carries no session or quote id:
    the session is the room, and the basket must match an unbound quote
    (api.quote, or the guest page) that room got within the quote TTL. The
    newest one wins; a used one returns its order. A quote bound to a
    session (concierge, console) is never confirmable here: this path cannot
    prove it is that session."""
    norm = normalize_lines(lines)
    cur.execute(
        """
        SELECT id FROM ups_quotes
        WHERE hotel_slug = %s AND room = %s AND lines_hash = %s AND expires_at > %s AND session_ref IS NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        (hotel.slug, room, basket_hash(norm), clock.now()),
    )
    row = cur.fetchone()
    if row is None:
        raise conflict("Quote the order first and confirm it within 10 minutes", "quote_required")
    # The snapshot carries the line notes, so the call's own notes add nothing.
    return confirm_quote(cur, hotel, s, quote_id=str(row["id"]), room=room, source=source)


def _place(
    cur: Any, hotel: Any, s: UpsellSettings, q: dict, *, source: str, session_ref: str | None, notes: str | None,
    verified_room: bool, language: str | None, payment: str,
) -> dict:
    lines = q["lines"]
    outlet_ids = {line["outlet_id"] for line in lines}
    outlet_id = next(iter(outlet_ids)) if len(outlet_ids) == 1 else None
    cur.execute(
        """
        INSERT INTO ups_orders (hotel_slug, outlet_id, quote_id, session_ref, room, source, status, price_display,
            subtotal_minor, service_charge_minor, municipality_fee_minor, vat_minor, total_minor, currency, payment,
            verified_room, notes, language, status_changed_at, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'placed', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (hotel.slug, outlet_id, q["id"], session_ref or q["session_ref"], q["room"], (source or "web")[:40],
         q["price_display"], q["subtotal_minor"], q["service_charge_minor"], q["municipality_fee_minor"],
         q["vat_minor"], q["total_minor"], q["currency"], payment, verified_room,
         (notes or "").strip()[:500] or None, (language or None), clock.now(), clock.now(), clock.now()),
    )
    order = dict(cur.fetchone())
    cur.execute("UPDATE ups_quotes SET order_id = %s WHERE id = %s", (order["id"], q["id"]))

    # Upsells: an ordered item that was suggested to this session is accepted.
    key = session_key(hotel, q["session_ref"] or session_ref, q["room"])
    cur.execute(
        "SELECT DISTINCT ON (item_id) item_id, kind, source_item_id FROM ups_upsell_events"
        " WHERE hotel_slug = %s AND session_ref = %s AND state = 'shown' AND item_id = ANY(%s)",
        (hotel.slug, key, [line["item_id"] for line in lines]),
    )
    shown = {r["item_id"]: r for r in cur.fetchall()}
    for line in lines:
        ev_id = None
        sh = shown.get(line["item_id"])
        if sh is not None:
            ev_id = record_event(cur, hotel, kind=sh["kind"], state="accepted", item_id=line["item_id"],
                                 source_item_id=sh["source_item_id"], session_ref=key, room=q["room"],
                                 order_id=order["id"], value_minor=line["line_total_minor"], source=source)
        cur.execute(
            """
            INSERT INTO ups_order_items (order_id, hotel_slug, menu_item_id, name_snapshot, name_ar_snapshot,
                unit_price_minor, qty, line_total_minor, currency, notes, upsell_event_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (order["id"], hotel.slug, line["item_id"], line["name_en"], line.get("name_ar"), line["unit_price_minor"],
             line["qty"], line["line_total_minor"], q["currency"], line.get("notes"), ev_id),
        )

    # One linked F&B request. If Service refuses it the whole order rolls
    # back: an order the kitchen never hears about is worse than an error.
    cur.execute("SELECT name_en FROM ups_outlets WHERE id = %s", (outlet_id,))
    orow = cur.fetchone()
    req = service_api.create_request(
        cur, hotel.slug, room=q["room"], type=s.request_type,
        description=_describe(order, lines, orow["name_en"] if orow else None, verified_room),
        source=_order_source(source), subtype=(orow["name_en"] if orow else "In-room dining")[:100],
        language=language,
    )
    cur.execute("UPDATE ups_orders SET request_id = %s WHERE id = %s", (req.get("id"), order["id"]))
    events.publish(hotel.slug, "upsell.order_created",
                   {"id": order["id"], "room": q["room"], "status": "placed", "request_id": req.get("id")}, cur=cur)
    out = get_order(cur, hotel.slug, order["id"])
    return {**out, "tracking_token": req.get("tracking_token"), "deduped": False}


# ─── Reading orders ───


def order_out(o: dict, items: list[dict]) -> dict:
    code = o["currency"].strip()
    return {
        "id": o["id"], "order_id": o["id"], "room": o["room"], "outlet_id": o["outlet_id"], "source": o["source"],
        "status": o["status"], "request_id": o["request_id"], "quote_id": str(o["quote_id"]) if o["quote_id"] else None,
        "price_display": o["price_display"],
        "subtotal": _m(o["subtotal_minor"], code), "service_charge": _m(o["service_charge_minor"], code),
        "municipality_fee": _m(o["municipality_fee_minor"], code), "vat": _m(o["vat_minor"], code),
        "total": _m(o["total_minor"], code), "total_minor": o["total_minor"], "currency": code,
        "payment": o["payment"], "paid_at": o["paid_at"], "verified_room": o["verified_room"],
        "notes": o["notes"], "language": o["language"], "cancel_reason": o["cancel_reason"],
        "status_changed_at": o["status_changed_at"], "created_at": o["created_at"],
        "lines": [
            {"id": i["id"], "item_id": i["menu_item_id"], "name": i["name_snapshot"], "name_ar": i["name_ar_snapshot"],
             "qty": i["qty"], "unit_price": _m(i["unit_price_minor"], code), "line_total": _m(i["line_total_minor"], code),
             "notes": i["notes"], "upsell": i["upsell_event_id"] is not None}
            for i in items
        ],
    }


def get_order(cur: Any, hotel_slug: str, order_id: int) -> dict:
    cur.execute("SELECT * FROM ups_orders WHERE hotel_slug = %s AND id = %s", (hotel_slug, order_id))
    o = cur.fetchone()
    if o is None:
        raise not_found("Order not found", "order_not_found")
    cur.execute("SELECT * FROM ups_order_items WHERE order_id = %s ORDER BY id", (order_id,))
    return order_out(dict(o), [dict(r) for r in cur.fetchall()])


def list_orders(
    cur: Any, hotel_slug: str, *, status: list[str] | None = None, room: str | None = None,
    since: Any = None, limit: int = 100,
) -> list[dict]:
    sql = "SELECT * FROM ups_orders WHERE hotel_slug = %s"
    params: list[Any] = [hotel_slug]
    if status:
        bad = [x for x in status if x not in ORDER_STATUSES]
        if bad:
            raise bad_request(f"Unknown status {bad[0]!r}", "invalid_status")
        sql += " AND status = ANY(%s)"
        params.append(status)
    if room:
        sql += " AND room = %s"
        params.append(room)
    if since is not None:
        sql += " AND created_at >= %s"
        params.append(since)
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
    params.append(max(1, min(int(limit), 500)))
    cur.execute(sql, params)
    orders = [dict(r) for r in cur.fetchall()]
    if not orders:
        return []
    cur.execute("SELECT * FROM ups_order_items WHERE order_id = ANY(%s) ORDER BY id", ([o["id"] for o in orders],))
    by_order: dict[int, list[dict]] = {}
    for i in cur.fetchall():
        by_order.setdefault(i["order_id"], []).append(dict(i))
    return [order_out(o, by_order.get(o["id"], [])) for o in orders]


# ─── Lifecycle (console) ───


def set_status(cur: Any, hotel: Any, order_id: int, status: str, *, reason: str | None = None) -> tuple[dict, dict]:
    """Move an order along TRANSITIONS; the funnel follows (delivered ->
    fulfilled, refunded -> refunded). 409 illegal_transition otherwise."""
    if status not in ORDER_STATUSES:
        raise bad_request(f"Unknown status {status!r}", "invalid_status")
    cur.execute("SELECT * FROM ups_orders WHERE hotel_slug = %s AND id = %s FOR UPDATE", (hotel.slug, order_id))
    o = cur.fetchone()
    if o is None:
        raise not_found("Order not found", "order_not_found")
    before = get_order(cur, hotel.slug, order_id)
    if status == o["status"]:
        return before, before
    if status not in TRANSITIONS[o["status"]]:
        raise conflict(f"An order that is {o['status']} cannot become {status}", "illegal_transition")
    cur.execute(
        "UPDATE ups_orders SET status = %s, status_changed_at = %s, updated_at = %s,"
        " cancel_reason = COALESCE(%s, cancel_reason) WHERE id = %s",
        (status, clock.now(), clock.now(), (reason or "").strip()[:300] or None if status in ("cancelled", "refunded") else None,
         order_id),
    )
    if status == "delivered":
        _advance_funnel(cur, hotel, order_id, "fulfilled")
    elif status == "refunded":
        _advance_funnel(cur, hotel, order_id, "refunded")
    events.publish(hotel.slug, "upsell.order_changed", {"id": order_id, "status": status}, cur=cur)
    return before, get_order(cur, hotel.slug, order_id)


def mark_paid(cur: Any, hotel: Any, order_id: int, *, payment: str | None = None) -> tuple[dict, dict]:
    """Settled on the folio, by card or cash; the funnel records "paid"."""
    if payment is not None and payment not in PAYMENTS:
        raise bad_request("Unknown payment method", "invalid_payment")
    before = get_order(cur, hotel.slug, order_id)
    if before["status"] in ("cancelled", "refunded"):
        raise conflict("A cancelled or refunded order cannot be paid", "illegal_transition")
    cur.execute(
        "UPDATE ups_orders SET paid_at = COALESCE(paid_at, %s), payment = COALESCE(%s, payment), updated_at = %s"
        " WHERE hotel_slug = %s AND id = %s",
        (clock.now(), payment, clock.now(), hotel.slug, order_id),
    )
    _advance_funnel(cur, hotel, order_id, "paid")
    return before, get_order(cur, hotel.slug, order_id)


def guest_order_status(cur: Any, hotel_slug: str, order_id: int, room: str) -> dict:
    """What a guest may see of their own order (no staff, no request ids)."""
    o = get_order(cur, hotel_slug, order_id)
    if o["room"] != room:
        raise not_found("Order not found", "order_not_found")
    keep = ("order_id", "status", "total", "currency", "price_display", "created_at", "status_changed_at")
    return {k: o[k] for k in keep} | {"lines": [{"name": x["name"], "qty": x["qty"]} for x in o["lines"]]}


# ─── Funnel summary (console) ───


def funnel_summary(cur: Any, hotel: Any, start: Any, end: Any) -> dict:
    """shown -> accepted -> paid -> fulfilled (and refunded) for local dates
    [start, end], overall and per suggested item. accept_rate is 0-100."""
    t0, t1 = ctime.local_range(hotel, start, end)
    cur.execute(
        """
        SELECT e.state, e.item_id, MAX(i.name_en) AS name, COUNT(*) AS n, COALESCE(SUM(e.value_minor), 0) AS v
        FROM ups_upsell_events e LEFT JOIN ups_menu_items i ON i.id = e.item_id
        WHERE e.hotel_slug = %s AND e.at >= %s AND e.at < %s
        GROUP BY e.state, e.item_id
        """,
        (hotel.slug, t0, t1),
    )
    code = hotel.currency
    totals = {st: {"count": 0, "value_minor": 0} for st in EVENT_STATES}
    items: dict[Any, dict] = {}
    for r in cur.fetchall():
        totals[r["state"]]["count"] += r["n"]
        totals[r["state"]]["value_minor"] += int(r["v"])
        it = items.setdefault(r["item_id"], {"item_id": r["item_id"], "name": r["name"],
                                             **{st: 0 for st in EVENT_STATES}, "accepted_value_minor": 0})
        it[r["state"]] += r["n"]
        if r["state"] == "accepted":
            it["accepted_value_minor"] += int(r["v"])
    for t in totals.values():
        t["value"] = _m(t["value_minor"], code)
    shown = totals["shown"]["count"]
    rows = sorted(items.values(), key=lambda x: (-x["accepted"], -x["shown"], x["item_id"] or 0))
    for it in rows:
        it["accepted_value"] = _m(it["accepted_value_minor"], code)
        it["accept_rate"] = round(100.0 * it["accepted"] / it["shown"], 1) if it["shown"] else None
    return {
        "start": str(start), "end": str(end), "currency": code, "states": totals,
        "accept_rate": round(100.0 * totals["accepted"]["count"] / shown, 1) if shown else None,
        "items": rows,
    }
