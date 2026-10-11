"""
The voice-local tool executor: a guarded fallback only. Phone tools run on
the concierge executor through concierge.api.run_tool; this serves a call
the concierge cannot (the module switched off for the hotel or missing from
the build, or no concierge session bound when the call was answered; see
executor.concierge_available). It speaks the same tool contract as
the concierge executor through other modules' api.py only: requests and
escalations via service.api, menu and orders via upsell.api, hotel answers
via concierge.api.kb_facts. Results keep the concierge shape (status,
followup_instructions, ui) so the model sees one contract either way.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.concierge import api as concierge_api
from clefs.modules.voice import calls

PRIORITIES = ("low", "medium", "high")
STATUS_WINDOW_H = 36
WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def result(status: str, followup: str, **data: Any) -> dict:
    return {"status": status, **{k: v for k, v in data.items() if v is not None}, "followup_instructions": followup}


def _clean(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit]


def _room(call: dict, args: dict) -> str | None:
    """executor.resolve_room already bound or confirmed the room."""
    if call.get("room_confirmed_by") == "extension" and call.get("room"):
        return str(call["room"])
    return _clean(args.get("room"), 24) or call.get("room") or None


def _unverified_note(call: dict) -> str:
    return "" if call.get("room_confirmed_by") == "extension" else " [room given by voice: confirm at the door]"


def execute(cur: Any, call: dict, name: str, args: dict) -> dict:
    fn = {
        "notify_staff": notify_staff,
        "escalate_to_human": escalate_to_human,
        "get_request_status": get_request_status,
        "search_hotel_info": search_hotel_info,
        "get_menu": get_menu,
        "place_order": place_order,
    }.get(name)
    if fn is None:
        return result("unknown_tool", "That tool does not exist. Carry on without it.")
    return fn(cur, call, args)


# ─── Requests and escalations ───


def notify_staff(cur: Any, call: dict, args: dict) -> dict:
    from clefs.modules.service import api as service_api

    room = _room(call, args)
    if not room:
        return result("room_required", "Ask the caller for their room number, then call again with it.")
    sensitivity = _clean(args.get("sensitivity"), 12)
    if sensitivity == "emergency":
        return escalate_to_human(cur, call, {**args, "reason": "emergency"})
    rtype = _clean(args.get("request_type"), 40) or "other"
    desc = _clean(args.get("description_en") or args.get("label") or rtype, 500)
    qty = args.get("quantity")
    if isinstance(qty, int) and 0 < qty <= 20 and str(qty) not in desc:
        desc = f"{desc} (x{qty})"
    if sensitivity == "sensitive":
        out = service_api.escalate_to_human(cur, call["hotel_slug"], room=room, reason=rtype, channel="sip",
                                            sensitive=True, excerpt=None)
        return result("escalated", "Tell the caller a manager has been told and will contact them shortly. Be calm and kind.",
                      request_ref=str(out.get("request_id")), ui={"kind": "request", "token": out.get("tracking_token")})
    priority = _clean(args.get("priority"), 10)
    row = service_api.create_request(
        cur, call["hotel_slug"], room=room, type=rtype, description=f"[phone] {desc}{_unverified_note(call)}", source="sip",
        priority_hint=priority if priority in PRIORITIES else None, language="en",
    )
    merged = bool(row.get("merged"))
    follow = ("Tell the caller this was already requested and is on its way." if merged
              else "Tell the caller the request is on its way. Do not promise an exact time.")
    return result("merged" if merged else "created", follow, request_ref=str(row.get("id")), merged=merged,
                  verified_room=call.get("room_confirmed_by") == "extension",
                  ui={"kind": "request", "token": row.get("tracking_token")})


def escalate_to_human(cur: Any, call: dict, args: dict) -> dict:
    from clefs.modules.service import api as service_api

    room = _room(call, args) or ""
    reason = _clean(args.get("reason"), 30) or "other"
    summary = _clean(args.get("summary_en") or args.get("description_en"), 400)
    if reason == "emergency":
        row = service_api.create_request(
            cur, call["hotel_slug"], room=room or "unknown", type="security", source="sip", priority_hint="L1",
            description=f"EMERGENCY reported on the room phone: {summary or 'caller reported an emergency'}{_unverified_note(call)}",
            language="en",
        )
        # The hotel country's numbers (calls.dial_sequence), never one
        # country's list for every hotel.
        dial = calls.dial_sequence(calls.line_prefix(cur, call), get_hotel(cur, call["hotel_slug"]).country)
        return result("escalated", f"Tell the caller help is on the way, that they can {dial}, and offer to transfer "
                      "them to the front desk.", request_ref=str(row.get("id")), reason=reason,
                      ui={"kind": "emergency", "token": row.get("tracking_token")})
    out = service_api.escalate_to_human(
        cur, call["hotel_slug"], room=room, reason=reason, channel="sip",
        sensitive=reason in ("legal",), excerpt=_clean(args.get("original_text"), 300) or summary or None,
    )
    return result("escalated", "Tell the caller a team member will follow up with them shortly, or offer to transfer "
                  "them to the front desk now.", request_ref=str(out.get("request_id")), reason=reason,
                  ui={"kind": "request", "token": out.get("tracking_token")})


# ─── Status, knowledge, menu, orders ───


def get_request_status(cur: Any, call: dict, args: dict) -> dict:
    """Requests filed from this call, plus (extension-bound calls only) the
    same room's phone requests of the last 36 hours. Guest-safe states only,
    never staff names.

    WHY only extension-bound: a spoken room is whatever the caller said, so
    anyone on an outside DID or an unmapped handset could name a room and
    hear another guest's requests. A spoken-room call sees only its own,
    the same rule as the web path (concierge tools._tracked)."""
    from clefs.modules.service import api as service_api

    params: list[Any] = [call["id"]]
    extra = ""
    if call.get("room") and call.get("room_confirmed_by") == "extension":
        extra = " OR (c.hotel_slug = %s AND c.room = %s AND c.started_at > %s)"
        params += [call["hotel_slug"], call["room"], clock.now() - timedelta(hours=STATUS_WINDOW_H)]
    cur.execute(
        f"""
        SELECT t.result FROM vc_tool_calls t JOIN vc_calls c ON c.id = t.call_row_id
        WHERE (t.call_row_id = %s{extra}) AND t.result ? 'ui' ORDER BY t.id DESC LIMIT 10
        """,
        params,
    )
    items = []
    for row in cur.fetchall():
        token = ((row["result"] or {}).get("ui") or {}).get("token")
        if not token:
            continue
        st = service_api.request_status_for_guest(cur, call["hotel_slug"], token)
        if st:
            items.append({"request_ref": str(st.get("id")), "what": st.get("request_type_label") or st.get("type"),
                          "state": st.get("state")})
    if not items:
        return result("none", "Say you cannot see any open requests from this room, and offer to send one.")
    return result("ok", "Tell the caller the status of each request in a short sentence. Never name staff.", requests=items)


def search_hotel_info(cur: Any, call: dict, args: dict) -> dict:
    query = _clean(args.get("query"), 200).lower()
    facts = concierge_api.kb_facts(cur, call["hotel_slug"], language=(call.get("language") or "en"))
    words = {w for w in WORD_RE.findall(query) if len(w) > 2}
    scored = []
    for f in facts:
        text = str(f.get("text") or "")
        hits = sum(1 for w in words if w in text.lower())
        if hits:
            scored.append((hits, text))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return result("not_found", "Say you do not have that information and offer to ask the front desk "
                      "(escalate_to_human with reason unknown_answer).")
    return result("ok", "Answer only from these results, in one or two short spoken sentences.",
                  results=[t[:600] for _, t in scored[:3]])


def get_menu(cur: Any, call: dict, args: dict) -> dict:
    from clefs.modules.upsell import api as upsell_api

    items = upsell_api.get_menu(cur, call["hotel_slug"], outlet=_clean(args.get("outlet_key"), 60) or None,
                                language=(call.get("language") or "en"))
    if not items:
        return result("unavailable", "Say nothing can be ordered right now and offer to ask the front desk.", items=[])
    view = [{"menu_item_id": i.get("item_id"), "name": i.get("name"), "price": i.get("price"),
             "currency": i.get("currency"), "outlet": i.get("outlet")} for i in items[:40]]
    return result("ok", "Offer a few relevant items with their prices. Use only these prices.", items=view)


def _lines(args: dict) -> list[dict]:
    out = []
    for ln in (args.get("lines") or [])[:20]:
        if not isinstance(ln, dict):
            continue
        item = ln.get("menu_item_id", ln.get("item_id"))
        qty = ln.get("qty", 1)
        if isinstance(item, (int, str)) and str(item).strip() and isinstance(qty, int) and 0 < qty <= 20:
            out.append({"item_id": int(item) if str(item).isdigit() else item, "qty": qty})
    return out


def _basket(lines: list[dict]) -> str:
    return ",".join(f"{ln['item_id']}x{ln['qty']}" for ln in sorted(lines, key=lambda x: str(x["item_id"])))


def _session_ref(call: dict) -> str:
    """The upsell session a phone call's quotes are bound to: only this call
    can confirm them (upsell 409 quote_session_mismatch otherwise)."""
    return f"vc:{call['id']}"


def _stored_quote_id(cur: Any, call: dict, basket: str) -> str | None:
    """The quote this call read to the caller for the same basket. Quotes
    live in the call's own tool ledger (vc_tool_calls.result), so nothing
    new is persisted; upsell enforces the TTL when confirming."""
    cur.execute(
        """
        SELECT result->>'quote_id' AS quote_id FROM vc_tool_calls
        WHERE call_row_id = %s AND name = 'place_order' AND result->>'status' = 'quoted'
          AND result->>'basket' = %s AND result ? 'quote_id'
        ORDER BY id DESC LIMIT 1
        """,
        (call["id"], basket),
    )
    row = cur.fetchone()
    return (row or {}).get("quote_id") or None


def place_order(cur: Any, call: dict, args: dict) -> dict:
    """quote then confirm through upsell's session path, so the order row
    carries verified_room honestly.

    WHY not quote()/create_order(): create_order confirms with the default
    verified_room=True, so a room the caller merely spoke would be charged
    with no door check, the F&B request would lose its "room not verified"
    note and the hotel's allow_unverified_rooms=False setting would be
    bypassed. Only an extension-bound call is a verified room."""
    from clefs.modules.upsell import api as upsell_api

    room = _room(call, args)
    lines = _lines(args)
    if not room:
        return result("room_required", "Ask for the caller's room number, then quote again with it.")
    if not lines:
        return result("invalid", "Ask the caller what they would like, then quote again with the menu item ids.")
    verified = call.get("room_confirmed_by") == "extension"
    basket = _basket(lines)
    language = call.get("language") or "en"
    try:
        if not args.get("confirm"):
            q = upsell_api.quote_for_session(cur, call["hotel_slug"], room=room, lines=lines,
                                             session_ref=_session_ref(call), language=language, source="sip")
            return result("quoted", "Read the items and the total to the caller, and ask them to confirm. Then call "
                          "place_order again with the same lines and confirm true.", total=q.get("total"),
                          currency=q.get("currency"), quote_id=str(q.get("quote_id") or "") or None, basket=basket)
        quote_id = _stored_quote_id(cur, call, basket)
        if quote_id is None:
            raise ClefsError(409, "No quote for this basket on this call", "quote_required")
        order = upsell_api.confirm_order(cur, call["hotel_slug"], quote_id=quote_id, room=room, source="sip",
                                         session_ref=_session_ref(call), verified_room=verified, language=language)
    except ClefsError as exc:
        if exc.code in ("quote_required", "quote_expired"):
            return result("quote_required", "Call place_order with confirm false first, read the total, get a yes, "
                          "then confirm.")
        raise
    follow = "Tell the caller the order is placed and on its way."
    if not verified:
        follow += " Staff will confirm the room at the door."
    return result("placed", follow, order_ref=str(order.get("order_id")), total=order.get("total"),
                  verified_room=verified, ui={"kind": "request", "token": order.get("tracking_token")})
