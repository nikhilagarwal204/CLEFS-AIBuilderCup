"""
Guest menu page routes under /public/menu/{slug} (Canon 13): the menu, a
priced quote, placing the quoted order, tracking it, and the "shown" beacon
for pairing suggestions (03 spec 2.6 FoodMenu.tsx).

Room identity follows 03 spec 2.3: `k` is the per-room QR key. A valid key
makes the order verified_room=true; a missing or rotated key still orders
(unless the hotel turned that off) but staff confirm at the door. Nothing
here returns another room's data, and every write is rate-limited per IP and
per room in the database (clefs.ratelimit), so a scripted client cannot
flood the kitchen.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.auth.tokens import sign_ref, verify_ref
from clefs.core import rooms as rooms_mod
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, too_many
from clefs.core.hotels import find_hotel, module_enabled
from clefs.modules.upsell import api, orders
from clefs.modules.upsell import settings as settings_mod

router = APIRouter(prefix="/public/menu/{slug}", tags=["upsell-public"])

WINDOW_S = 600
MENU_READS_PER_IP = 120
BEACONS_PER_IP = 120
WRITES_PER_IP = 60


def _ip() -> str:
    return request_info().ip or "unknown"


def _limit(cur, key: str, limit: int) -> None:
    if not ratelimit.hit(cur, key, limit=limit, window_s=WINDOW_S):
        raise too_many()


def _open(cur, slug: str):
    """The hotel, 404 when unknown or when it switched the menu module off."""
    h = find_hotel(cur, slug)
    if h is None or not module_enabled(cur, slug, "upsell"):
        raise ClefsError(404, "Menu not available", "menu_unavailable")
    return h


def key_status(cur, slug: str, room: str, k: str | None) -> str:
    """valid | invalid (wrong or rotated key) | absent (legacy printed QR)."""
    if not k:
        return "absent"
    r = rooms_mod.get_room(cur, slug, room)
    version = r["qr_key_version"] if r else 1
    return "valid" if rooms_mod.verify_qr_key(slug, room, version, k) else "invalid"


def order_token(slug: str, order_id: int) -> str:
    return sign_ref(f"ups:{slug}:{order_id}")


# ─── Menu ───


@router.get("")
def public_menu(slug: str, room: str | None = None, k: str | None = None, lang: str = "en",
                outlet: str | None = None) -> dict:
    """The guest menu: outlets (open_now), items (available_now), pairs,
    price_display, fee rates and whether this room's key checked out."""
    with db.db() as cur:
        _open(cur, slug)
        _limit(cur, f"ups:menu:{slug}:{_ip()}", MENU_READS_PER_IP)
        payload = api.guest_menu(cur, slug, outlet=outlet, language=lang[:12])
        status = None
        if room:
            room = api.check_room(cur, slug, room)
            status = key_status(cur, slug, room, k)
    return {**payload, "room": room, "key_status": status}


# ─── Quote and order ───


class PublicQuoteBody(BaseModel):
    room: str = Field(max_length=32)
    k: str | None = Field(None, max_length=64)
    lines: list[dict] = Field(max_length=orders.MAX_LINES * 2)
    lang: str = Field("en", max_length=12)


class PublicOrderBody(BaseModel):
    room: str = Field(max_length=32)
    k: str | None = Field(None, max_length=64)
    quote_id: str = Field(max_length=64)
    notes: str | None = Field(None, max_length=500)
    lang: str = Field("en", max_length=12)


@router.post("/quote")
def public_quote(slug: str, body: PublicQuoteBody) -> dict:
    with db.db() as cur:
        h = _open(cur, slug)
        _limit(cur, f"ups:pubw:{slug}:{_ip()}", WRITES_PER_IP)
        room = api.check_room(cur, slug, body.room)
        # Not session-bound: the quote id itself is the page's capability,
        # and confirm still checks the room and the 10-minute window.
        q = orders.create_quote(cur, h, settings_mod.load(cur, slug), room=room, lines=body.lines,
                                language=body.lang, source="web")
        return {**q, "key_status": key_status(cur, slug, room, body.k)}


@router.post("/orders", status_code=201)
def public_order(slug: str, body: PublicOrderBody) -> dict:
    """Confirm a quote shown on the page (10-minute window). The response
    carries order_token for GET .../orders/{order_token}."""
    with db.db() as cur:
        _open(cur, slug)
        _limit(cur, f"ups:pubw:{slug}:{_ip()}", WRITES_PER_IP)
        room = api.check_room(cur, slug, body.room)
        verified = key_status(cur, slug, room, body.k) == "valid"
        o = api.confirm_order(cur, slug, quote_id=body.quote_id, room=room, source="web", notes=body.notes,
                              verified_room=verified, language=body.lang)
        keep = ("order_id", "status", "lines", "subtotal", "service_charge", "municipality_fee", "vat", "total",
                "currency", "price_display", "verified_room", "created_at", "deduped", "tracking_token")
        return {k: o.get(k) for k in keep} | {"order_token": order_token(slug, o["order_id"])}


@router.get("/orders/{token}")
def public_order_status(slug: str, token: str) -> dict:
    ref = verify_ref(token)
    parts = (ref or "").split(":")
    if len(parts) != 3 or parts[0] != "ups" or parts[1] != slug or not parts[2].isdigit():
        raise ClefsError(404, "Order not found", "order_not_found")
    with db.db(readonly=True) as cur:
        _open(cur, slug)
        cur.execute("SELECT room FROM ups_orders WHERE hotel_slug = %s AND id = %s", (slug, int(parts[2])))
        row = cur.fetchone()
        if row is None:
            raise ClefsError(404, "Order not found", "order_not_found")
        out = api.order_status_for_guest(cur, slug, int(parts[2]), room=row["room"])
    if out is None:
        raise ClefsError(404, "Order not found", "order_not_found")
    return out


# ─── Funnel beacon ───


class BeaconBody(BaseModel):
    room: str = Field(max_length=32)
    item_id: int
    source_item_id: int | None = None
    kind: str = Field("pairing", pattern="^(pairing|upgrade)$")


@router.post("/events", status_code=204)
def public_beacon(slug: str, body: BeaconBody) -> Response:
    """The page showed a pairing suggestion ("shown" only; a guest cannot
    claim an acceptance or a payment). Repeats in one stay day collapse."""
    with db.db() as cur:
        _open(cur, slug)
        _limit(cur, f"ups:beacon:{slug}:{_ip()}", BEACONS_PER_IP)
        room = api.check_room(cur, slug, body.room)
        ids = [i for i in (body.item_id, body.source_item_id) if i is not None]
        cur.execute("SELECT id FROM ups_menu_items WHERE hotel_slug = %s AND id = ANY(%s)", (slug, ids))
        own = {r["id"] for r in cur.fetchall()}
        if body.item_id not in own:
            raise ClefsError(404, "Menu item not found", "item_not_found")
        api.record_upsell_event(cur, slug, kind=body.kind, state="shown", item_id=body.item_id,
                                source_item_id=body.source_item_id if body.source_item_id in own else None,
                                room=room, source="web")
    return Response(status_code=204)
