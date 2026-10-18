"""
Upsell public API (frozen signatures; other modules import only this file).

Canon 9: menus, outlets, orders and upsell events belong here; concierge tools
call get_menu / quote / create_order.

Money in results: Decimal strings in major units ("45.00") next to `*_minor`
integers and a CHAR(3) currency (Canon 2). Errors are ClefsError with a code:
404 item_not_found / hotel_not_found, 409 item_unavailable /
alcohol_not_allowed / outlet_no_delivery / quote_required / quote_expired /
quote_session_mismatch / ordering_disabled, 429 rate_limited. Callers
apologise and hand off rather than inventing prices.

The quote gate (03 spec 2.4 place_order): an order needs a quote the same
session saw within the quote TTL (10 minutes by default).
  - Frozen path: quote(room, lines) then create_order(room, lines, source)
    with the SAME basket; the room is the session, and only unbound quotes
    (from quote() or the guest page) qualify.
  - Session path (concierge tools): quote_for_session(..., session_ref) then
    confirm_order(quote_id=..., session_ref=...); a quote bound to a session
    cannot be confirmed by another.
Both are idempotent: confirming the same quote twice returns the first order
with deduped=True.
"""

from __future__ import annotations

from typing import Any

from clefs.core import rooms as rooms_mod
from clefs.core import time as ctime
from clefs.core.errors import ClefsError, bad_request
from clefs.core.hotels import find_hotel
from clefs.modules.upsell import menu as menu_mod
from clefs.modules.upsell import orders as orders_mod
from clefs.modules.upsell import pricing
from clefs.modules.upsell import settings as settings_mod

__all__ = [
    "get_menu", "quote", "create_order",
    "quote_for_session", "confirm_order", "record_upsell_event", "order_status_for_guest", "guest_menu",
]


# ─── Helpers ───


def _hotel(cur: Any, hotel_slug: str) -> Any:
    h = find_hotel(cur, hotel_slug)
    if h is None:
        raise ClefsError(404, f"Hotel '{hotel_slug}' not found", "hotel_not_found")
    return h


def check_room(cur: Any, hotel_slug: str, room: str) -> str:
    """400 for a malformed room; 404 when the hotel keeps a room register and
    the room is not in it (the /public/stay rule)."""
    room = str(room or "").strip()
    if not rooms_mod.valid_room(room):
        raise bad_request("A valid room number is required", "invalid_room")
    if rooms_mod.has_register(cur, hotel_slug):
        r = rooms_mod.get_room(cur, hotel_slug, room)
        if r is None or not r.get("is_active", True):
            raise ClefsError(404, "Room not found", "room_not_found")
    return room


# ─── Frozen surface ───


def get_menu(cur: Any, hotel_slug: str, *, outlet: str | None = None, language: str = "en") -> list[dict]:
    """[{outlet, section, item_id, name, description, price, currency, tags}]

    Items orderable NOW (item and outlet hours in the hotel's timezone, active,
    alcohol only when the hotel opted in). Extra keys: price_minor, dietary,
    alcohol, prep_min, image_url, available_now."""
    h = find_hotel(cur, hotel_slug)
    if h is None:
        return []
    s = settings_mod.load(cur, hotel_slug)
    m = menu_mod.guest_menu(cur, h, outlet=outlet, language=language, allow_alcohol=s.alcohol_orders)
    return m["items"]


def quote(cur: Any, hotel_slug: str, *, room: str, lines: list[dict]) -> dict:
    """lines = [{item_id, qty, notes?}] -> {lines, subtotal, taxes[], total, currency}

    Also: quote_id, price_display, service_charge, municipality_fee, vat,
    total_minor, expires_at, suggestions[] (pairings, logged as "shown")."""
    h = _hotel(cur, hotel_slug)
    room = check_room(cur, hotel_slug, room)
    s = settings_mod.load(cur, hotel_slug)
    return orders_mod.create_quote(cur, h, s, room=room, lines=lines)


def create_order(cur: Any, hotel_slug: str, *, room: str, lines: list[dict], source: str) -> dict:
    """Place an order (and its service request). -> {order_id, status, total, currency}

    Needs quote(room, the same lines) within the quote TTL, else 409
    quote_required. Also returns the full order (lines, fee split,
    request_id, tracking_token, deduped)."""
    h = _hotel(cur, hotel_slug)
    room = check_room(cur, hotel_slug, room)
    s = settings_mod.load(cur, hotel_slug)
    return orders_mod.confirm_basket(cur, h, s, room=room, lines=lines, source=source)


# ─── Extensions (not frozen; added for the concierge and guest page) ───


def quote_for_session(
    cur: Any, hotel_slug: str, *, room: str, lines: list[dict], session_ref: str, language: str = "en",
    source: str | None = None,
) -> dict:
    """quote() bound to one session: only that session_ref can confirm it."""
    h = _hotel(cur, hotel_slug)
    room = check_room(cur, hotel_slug, room)
    s = settings_mod.load(cur, hotel_slug)
    return orders_mod.create_quote(cur, h, s, room=room, lines=lines, session_ref=session_ref,
                                   language=language, source=source)


def confirm_order(
    cur: Any,
    hotel_slug: str,
    *,
    quote_id: str,
    room: str,
    source: str,
    session_ref: str | None = None,
    notes: str | None = None,
    verified_room: bool = True,
    language: str | None = None,
) -> dict:
    """place_order(confirm=true): the order + one linked F&B request.
    -> the order (order_id, status, lines, fee split, total, currency,
    request_id, tracking_token, deduped)."""
    h = _hotel(cur, hotel_slug)
    room = check_room(cur, hotel_slug, room)
    s = settings_mod.load(cur, hotel_slug)
    return orders_mod.confirm_quote(cur, h, s, quote_id=quote_id, room=room, source=source,
                                    session_ref=session_ref, notes=notes, verified_room=verified_room,
                                    language=language)


def record_upsell_event(
    cur: Any, hotel_slug: str, *, kind: str, state: str = "shown", item_id: int | None = None,
    source_item_id: int | None = None, session_ref: str | None = None, room: str | None = None,
    value_minor: int | None = None, source: str | None = None,
) -> int | None:
    """Log a funnel step the module did not see itself (a spa or late
    checkout offer shown by the concierge). Returns the event id, or None
    when that (session, item, state) was already logged."""
    h = _hotel(cur, hotel_slug)
    key = orders_mod.session_key(h, session_ref, room) if (session_ref or room) else None
    return orders_mod.record_event(cur, h, kind=kind, state=state, item_id=item_id, source_item_id=source_item_id,
                                   session_ref=key, room=room, value_minor=value_minor, source=source)


def order_status_for_guest(cur: Any, hotel_slug: str, order_id: int, *, room: str) -> dict | None:
    """The guest's own order (status, lines, total); None for another room's."""
    try:
        return orders_mod.guest_order_status(cur, hotel_slug, order_id, room)
    except ClefsError as exc:
        if exc.status == 404:
            return None
        raise


def guest_menu(cur: Any, hotel_slug: str, *, outlet: str | None = None, language: str = "en") -> dict:
    """The whole guest menu page payload: outlets, items (greyed out when out
    of hours: available_now=false), pairs, price_display and fee rates."""
    h = _hotel(cur, hotel_slug)
    s = settings_mod.load(cur, hotel_slug)
    m = menu_mod.guest_menu(cur, h, outlet=outlet, language=language, include_unavailable=True,
                            allow_alcohol=s.alcohol_orders)
    rates = pricing.resolve_rates(cur, h, s, ctime.local_date(h))
    return {**m, "currency": h.currency, "price_display": s.price_display,
            "fees": rates.as_json(), "ordering_enabled": s.ordering_enabled}
