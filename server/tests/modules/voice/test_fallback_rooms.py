"""
Spoken (unverified) rooms on the phone fallback executor:
- place_order confirms through upsell's session path with
  verified_room=False for a spoken room and True for an extension room;
- get_request_status never widens a spoken-room call to other calls'
  requests for the same room (privacy).
"""

from __future__ import annotations

import pytest

from tests.modules.voice.test_mcp import start_call, tool  # noqa: F401  (fixture re-export)


# ─── Upsell stand-in ───


class FakeUpsell:
    """upsell.api's session path, recorded. Voice tests run without the
    upsell module's tables; the api.py contract is what voice depends on."""

    def __init__(self) -> None:
        self.quotes: dict[str, dict] = {}
        self.orders: list[dict] = []

    def quote_for_session(self, cur, hotel_slug, *, room, lines, session_ref, language="en", source=None):
        qid = f"q{len(self.quotes) + 1}"
        self.quotes[qid] = {"room": room, "lines": lines, "session_ref": session_ref}
        return {"quote_id": qid, "total": "45.00", "currency": "AED", "total_minor": 4500}

    def confirm_order(self, cur, hotel_slug, *, quote_id, room, source, session_ref=None, notes=None,
                      verified_room=True, language=None):
        from clefs.core.errors import ClefsError

        q = self.quotes.get(quote_id)
        if q is None:
            raise ClefsError(409, "no quote", "quote_required")
        if q["session_ref"] != session_ref:
            raise ClefsError(409, "other session", "quote_session_mismatch")
        oid = 700 + len(self.orders)
        self.orders.append({"order_id": oid, "quote_id": quote_id, "room": room, "source": source,
                            "verified_room": verified_room, "session_ref": session_ref})
        return {"order_id": oid, "total": "45.00", "currency": "AED", "tracking_token": f"otok{oid}"}

    def create_order(self, *a, **kw):
        raise AssertionError("the phone path must not use create_order (it defaults verified_room=True)")


@pytest.fixture
def ups(monkeypatch):
    from clefs.modules.upsell import api as upsell_api

    fake = FakeUpsell()
    for name in ("quote_for_session", "confirm_order", "create_order"):
        monkeypatch.setattr(upsell_api, name, getattr(fake, name))
    return fake


BASKET = {"lines": [{"menu_item_id": 11, "qty": 2}]}


# ─── place_order ───


def test_spoken_room_order_is_unverified(client, start_call, ups):
    token = start_call("rtc_spoken_order", from_user="777")
    quoted = tool(client, token, "place_order", {**BASKET, "room": "503"})
    assert quoted["status"] == "quoted" and quoted["total"] == "45.00"
    placed = tool(client, token, "place_order", {**BASKET, "confirm": True})
    assert placed["status"] == "placed", placed
    [order] = ups.orders
    assert order["verified_room"] is False and order["room"] == "503" and order["source"] == "sip"
    assert placed.get("verified_room") is False and "confirm the room at the door" in placed["followup_instructions"]


def test_extension_room_order_is_verified(client, start_call, ups):
    token = start_call("rtc_ext_order", from_user="1501")
    tool(client, token, "place_order", BASKET)
    placed = tool(client, token, "place_order", {**BASKET, "confirm": True})
    assert placed["status"] == "placed"
    [order] = ups.orders
    assert order["verified_room"] is True and order["room"] == "501"


def test_confirm_without_quote_is_refused(client, start_call, ups):
    token = start_call("rtc_noquote")
    out = tool(client, token, "place_order", {**BASKET, "confirm": True})
    assert out["status"] == "quote_required" and ups.orders == []


def test_confirm_of_a_different_basket_needs_its_own_quote(client, start_call, ups):
    token = start_call("rtc_otherbasket")
    tool(client, token, "place_order", BASKET)
    out = tool(client, token, "place_order", {"lines": [{"menu_item_id": 12, "qty": 1}], "confirm": True})
    assert out["status"] == "quote_required" and ups.orders == []


# ─── get_request_status ───


def test_spoken_room_status_sees_only_its_own_call(client, start_call, fake_service):
    first = start_call("rtc_guest_503", from_user="778")
    assert tool(client, first, "create_ticket", {"request_type": "towels", "description_en": "towels",
                                                 "room": "503"})["status"] == "created"
    # A second caller names the same room through a side-effect-free path.
    other = start_call("rtc_snoop_503", from_user="779")
    tool(client, other, "escalate_to_human", {"reason": "other", "summary_en": "question", "room": "503"})
    out = tool(client, other, "get_request_status", {})
    seen = {r["request_ref"] for r in out.get("requests") or []}
    towels = str(fake_service.requests[0]["id"])
    assert towels not in seen


def test_extension_room_status_includes_the_rooms_other_calls(client, start_call, fake_service):
    first = start_call("rtc_ext_a", from_user="1501")
    assert tool(client, first, "create_ticket", {"request_type": "towels", "description_en": "towels"})["status"] == "created"
    second = start_call("rtc_ext_b", from_user="501")
    out = tool(client, second, "get_request_status", {})
    assert out["status"] == "ok"
    assert str(fake_service.requests[0]["id"]) in {r["request_ref"] for r in out["requests"]}


# ─── Against the real upsell module (when it is migrated in this run) ───


@pytest.fixture
def real_upsell(cur):
    cur.execute("SELECT to_regclass('ups_quotes') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        pytest.skip("run with CLEFS_ONLY_MODULES including upsell for the integration check")


def test_spoken_room_order_row_is_unverified_in_upsell(client, cur, start_call, real_upsell, hotel_uae, monkeypatch):
    """End to end: the ups_orders row of a spoken-room phone order is
    verified_room=FALSE, and allow_unverified_rooms=False refuses it."""
    from tests.modules.upsell.conftest import _ServiceStub

    from clefs.core.hotels import set_module_settings
    from clefs.modules.service import api as service_api
    from clefs.modules.upsell import menu

    # The outlet and item have no hours: always orderable, so no frozen
    # clock (the call JWT is checked against wall time).
    monkeypatch.setattr(service_api, "create_request", _ServiceStub())
    ird = menu.create_outlet(cur, hotel_uae.slug, menu.OutletIn(key="ird", name_en="In-Room Dining"))
    club = menu.create_item(cur, hotel_uae.slug, "AED", menu.ItemIn(outlet_id=ird["id"], name_en="Club", price="50"))
    basket = {"lines": [{"menu_item_id": club["id"], "qty": 1}]}

    token = start_call("rtc_real_spoken", from_user="780")
    assert tool(client, token, "place_order", {**basket, "room": "503"})["status"] == "quoted"
    placed = tool(client, token, "place_order", {**basket, "confirm": True})
    assert placed["status"] == "placed", placed
    cur.execute("SELECT verified_room, source FROM ups_orders WHERE id = %s", (int(placed["order_ref"]),))
    assert dict(cur.fetchone()) == {"verified_room": False, "source": "sip"}

    set_module_settings(cur, hotel_uae.slug, "upsell", {"allow_unverified_rooms": False}, actor=None)
    other = start_call("rtc_real_spoken2", from_user="781")
    assert tool(client, other, "place_order", {**basket, "room": "503"})["status"] == "quoted"
    refused = tool(client, other, "place_order", {**basket, "confirm": True})
    assert refused["status"] == "error" and refused.get("code") == "room_not_verified", refused
