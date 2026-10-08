"""place_order and get_menu (03 spec 2.4): quote then confirm through upsell.api,
the 10-minute window, the session and room binding, and dry runs."""

from __future__ import annotations

from datetime import timedelta

import pytest

from clefs.core import clock as clock_mod
from clefs.core.errors import ClefsError
from tests.modules.concierge.test_tools import call, fake_service, mint  # noqa: F401 (fixture)

MENU = [
    {"outlet": "ird", "section": "Mains", "item_id": 11, "name": "Chicken machboos", "description": "Spiced rice",
     "price": "65.00", "currency": "AED", "tags": [], "dietary": []},
    {"outlet": "ird", "section": "Drinks", "item_id": 12, "name": "Fresh lemon mint", "description": "",
     "price": "25.00", "currency": "AED", "tags": [], "dietary": ["vegan"]},
]
PRICES = {11: 6500, 12: 2500}


class FakeUpsell:
    """upsell.api stand-in with plus-plus pricing (10% service charge, 7%
    municipality fee, 5% VAT on the net plus both fees)."""

    def __init__(self):
        self.quotes: dict[str, dict] = {}
        self.orders: list[dict] = []
        self.menu_calls: list[dict] = []

    def get_menu(self, cur, hotel_slug, *, outlet=None, language="en"):
        self.menu_calls.append({"outlet": outlet, "language": language})
        return [i for i in MENU if outlet in (None, i["outlet"])]

    def quote_for_session(self, cur, hotel_slug, *, room, lines, session_ref, language="en", source=None):
        net = sum(PRICES[x["item_id"]] * x["qty"] for x in lines)
        sc, mf = net // 10, net * 7 // 100
        vat = (net + sc + mf) * 5 // 100
        qid = f"q{len(self.quotes) + 1}"
        q = {"quote_id": qid, "room": room, "session_ref": session_ref, "price_display": "plus_plus",
             "lines": [{"item_id": x["item_id"], "name": f"item {x['item_id']}", "qty": x["qty"]} for x in lines],
             "subtotal": f"{net / 100:.2f}", "total": f"{(net + sc + mf + vat) / 100:.2f}",
             "total_minor": net + sc + mf + vat, "currency": "AED",
             "taxes": [{"label": "Service charge", "amount": f"{sc / 100:.2f}", "included": False}],
             "suggestions": [{"item_id": 12, "name": "Fresh lemon mint", "price": "25.00", "reason": "Cools the spice"}]}
        self.quotes[qid] = q
        return q

    def confirm_order(self, cur, hotel_slug, *, quote_id, room, source, session_ref=None, notes=None,
                      verified_room=True, language=None):
        q = self.quotes[quote_id]
        if q["session_ref"] != session_ref:
            raise ClefsError(409, "other session", "quote_session_mismatch")
        o = {"order_id": 500 + len(self.orders), "status": "placed", "total": q["total"], "total_minor": q["total_minor"],
             "currency": "AED", "request_id": 900, "tracking_token": "ord-tok", "lines": q["lines"], "deduped": False,
             "verified_room": verified_room, "room": room, "source": source}
        self.orders.append(o)
        return o

    def order_status_for_guest(self, cur, hotel_slug, order_id, *, room):
        return next(({"status": o["status"], "lines": o["lines"]} for o in self.orders
                     if o["order_id"] == order_id and o["room"] == room), None)


@pytest.fixture
def fake_upsell(monkeypatch):
    from clefs.modules.upsell import api as upsell_api

    fake = FakeUpsell()
    for name in ("get_menu", "quote_for_session", "confirm_order", "order_status_for_guest"):
        monkeypatch.setattr(upsell_api, name, getattr(fake, name))
    return fake


LINES = [{"menu_item_id": 11, "qty": 2}]


def test_get_menu_filters_by_category(client, hotel_uae, fake_upsell):
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "get_menu", {"category": "drinks"}).json()
    assert out["status"] == "ok" and [i["item_id"] for i in out["items"]] == [12]
    assert out["items"][0]["price"] == "25.00" and out["items"][0]["currency"] == "AED"


def test_quote_then_confirm(client, cur, hotel_uae, fake_upsell, fake_service):
    s = mint(client, hotel_uae.slug)
    quote = call(client, s, "place_order", {"lines": LINES, "confirm": False, "room": "999"}).json()
    assert quote["status"] == "quoted" and quote["total"] == "159.70" and quote["currency"] == "AED"
    assert quote["price_display"] == "plus_plus" and "service charge" in quote["followup_instructions"]
    assert fake_upsell.quotes["q1"]["room"] == "101" and fake_upsell.quotes["q1"]["session_ref"] == s["session_id"]
    placed = call(client, s, "place_order", {"lines": LINES, "confirm": True}, "c2").json()
    assert placed["status"] == "placed" and placed["order_ref"] == "500" and placed["ui"]["kind"] == "order"
    assert placed["upsell"]["item_id"] == 12
    assert fake_upsell.orders[0]["source"] == "text" and fake_upsell.orders[0]["verified_room"] is True
    cur.execute("SELECT orders FROM cc_sessions WHERE id = %s", (s["session_id"],))
    assert cur.fetchone()["orders"] == 1
    status = call(client, s, "get_request_status", {}, "c3").json()
    assert status["requests"][0]["kind"] == "order" and status["requests"][0]["ref"] == "500"


def test_confirm_needs_a_fresh_quote_for_the_same_basket(client, hotel_uae, fake_upsell, fake_service):
    s = mint(client, hotel_uae.slug)
    assert call(client, s, "place_order", {"lines": LINES, "confirm": True}).json()["status"] == "quote_required"
    call(client, s, "place_order", {"lines": LINES, "confirm": False}, "c2")
    other = [{"menu_item_id": 11, "qty": 3}]
    assert call(client, s, "place_order", {"lines": other, "confirm": True}, "c3").json()["status"] == "quote_required"
    clock_mod.freeze(clock_mod.now() + timedelta(minutes=11))
    try:
        assert call(client, s, "place_order", {"lines": LINES, "confirm": True}, "c4").json()["status"] == "quote_required"
    finally:
        clock_mod.unfreeze()
    assert fake_upsell.orders == []


def test_another_session_cannot_confirm_my_quote(client, hotel_uae, fake_upsell, fake_service):
    mine = mint(client, hotel_uae.slug)
    call(client, mine, "place_order", {"lines": LINES, "confirm": False})
    theirs = mint(client, hotel_uae.slug)
    assert call(client, theirs, "place_order", {"lines": LINES, "confirm": True}).json()["status"] == "quote_required"


def test_whatsapp_and_unknown_surfaces_cannot_order(client, cur, hotel_uae, fake_upsell):
    from clefs.modules.concierge import tools

    session = {"id": None, "synthetic": True, "hotel_slug": hotel_uae.slug, "surface": "whatsapp", "room": "101"}
    out = tools.execute(cur, session, "place_order", {"lines": LINES, "confirm": False})
    assert out["status"] == "not_available" and fake_upsell.quotes == {}


def test_dry_run_prices_but_files_nothing(cur, hotel_uae, fake_upsell, fake_service):
    from clefs.modules.concierge import api, tools

    spec = api.build_surface_session(hotel_uae.slug, surface="eval", room="101", cur=cur)
    assert spec.dry_run is True
    session = tools.synthetic_session(spec)
    quote = tools.execute(cur, session, "place_order", {"lines": LINES, "confirm": False})
    assert quote["status"] == "quoted" and quote["total"] == "159.70" and quote["dry_run"] is True
    placed = tools.execute(cur, session, "place_order", {"lines": LINES, "confirm": True})
    assert placed["status"] == "placed" and placed["order_ref"].startswith("DRY-")
    req = tools.execute(cur, session, "notify_staff", {"request_type": "towels", "description_en": "Towels"})
    assert req["status"] == "created" and req["dry_run"] is True
    assert fake_upsell.orders == [] and fake_service.requests == []


def _needs(cur, *tables):
    for t in tables:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (t,))
        if not cur.fetchone()["ok"]:
            pytest.skip(f"{t} not migrated in this run")


@pytest.mark.parametrize("display", ["plus_plus", "inclusive"])
def test_real_upsell_quote_then_confirm_when_installed(client, cur, hotel_uae, display):
    """End to end through upsell.api and Service when both are migrated (the
    full suite): the server re-prices from the database, plus-plus adds the
    fees on top, inclusive shows them inside the price."""
    from clefs.core.hotels import set_module_settings

    _needs(cur, "ups_menu_items", "svc_shift_templates")
    cur.execute("INSERT INTO ups_outlets (hotel_slug, key, name_en) VALUES (%s, 'ird', 'In-Room Dining') RETURNING id",
                (hotel_uae.slug,))
    outlet = cur.fetchone()["id"]
    cur.execute("INSERT INTO ups_menu_items (hotel_slug, outlet_id, name_en, category, price_minor, currency) "
                "VALUES (%s, %s, 'Club Sandwich', 'Mains', 5000, 'AED') RETURNING id", (hotel_uae.slug, outlet))
    item = cur.fetchone()["id"]
    set_module_settings(cur, hotel_uae.slug, "upsell", {"price_display": display}, actor=None)
    s = mint(client, hotel_uae.slug)
    lines = [{"menu_item_id": item, "qty": 2}]
    quote = call(client, s, "place_order", {"lines": lines, "confirm": False}).json()
    assert quote["status"] == "quoted", quote
    assert quote["subtotal"] == "100.00" and quote["currency"] == "AED" and quote["price_display"] == display
    if display == "plus_plus":
        assert float(quote["total"]) > 100.0
    else:
        assert quote["total"] == "100.00"
    placed = call(client, s, "place_order", {"lines": lines, "confirm": True}, "c2").json()
    assert placed["status"] == "placed", placed
    assert placed["total"] == quote["total"] and placed["ui"]["token"]
