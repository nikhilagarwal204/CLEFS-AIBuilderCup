"""
Concierge tools: schemas and server-side executors (03 spec 2.4).

One executor serves every surface: the web page posts each function call to
POST /public/concierge/sessions/{sid}/tools/{name}, the text chat and the
WhatsApp handler call execute() in-process, and the phone reaches the same
function through the voice module's MCP server.

Security boundary: the room comes from the session row (bound at mint time
from the printed QR key, a stay token or the PBX extension), never from the
model's arguments. A `room` argument exists only on surfaces with no room of
their own (a venue QR, WhatsApp, a text session without a room), and a room
given that way is always marked unverified, so staff confirm at the door.

Dedupe: the browser, the phone and a retrying model can send one call twice.
cc_tool_calls is UNIQUE (session_id, call_id); the second call returns the
stored first answer and changes nothing.

Every result is a dict with `status` and `followup_instructions` (what the
model should say next, in English; the model answers in the guest's
language) and optionally `ui` (what the client should show: the request
tracker, the emergency card, an order, the "Job sent to staff" card of each
job). Failures in another module (a 429 from Service, an unavailable menu
item) become a result the model can speak, never a 500 in the middle of a
conversation.

Jobs (05 spec 3): notify_staff files one job per call through
service.api.create_job, which routes it to the right free staff member and
estimates the arrival; the result names that person (first name only) and
the minutes, and carries the guest page's card. flag_service_recovery is the
guest saying a job never came or is still not fixed: it reopens the job
(service.api.guest_feedback) or flags it (service.api.flag_recovery) for the
duty manager.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.concierge import constants as C

log = logging.getLogger("clefs.concierge.tools")

# ─── Vocabulary ───

TOOL_NAMES: tuple[str, ...] = (
    "notify_staff",
    "escalate_to_human",
    "get_request_status",
    "flag_service_recovery",
    "get_menu",
    "place_order",
    "search_hotel_info",
)

# Service request-type keys a guest can ask for (service.api.create_request
# takes a key or a legacy type; intake refines within the department).
# "other" is the catch-all: Service routes it from the description.
REQUEST_TYPES: tuple[str, ...] = (
    "towels", "linen", "amenities", "water", "pillows", "extra_bed", "make_up_room", "turndown",
    "ird", "breakfast", "drinks", "luggage", "concierge", "spa", "ac", "plumbing", "electrical",
    "maintenance", "wake_up", "billing", "checkout", "front_desk", "security", "other",
)
# The v1 client's category enum, kept so an old prompt's calls still land.
LEGACY_CATEGORIES: dict[str, str] = {
    "Housekeeping": "make_up_room",
    "Maintenance": "maintenance",
    "Food & Beverage": "ird",
    "Front Desk": "front_desk",
    "Concierge": "concierge",
    "Security": "security",
    "General": "other",
}
ESCALATION_REASONS: tuple[str, ...] = (
    "unknown_answer", "complaint", "refund_or_billing", "legal", "emergency", "asked_for_human", "other",
)
PRIORITIES: tuple[str, ...] = ("low", "medium", "high")
# create_job's priority words; URGENT means high (05 spec decision 11).
JOB_PRIORITY: dict[str, str] = {"low": "low", "medium": "normal", "high": "high"}
SENSITIVITIES: tuple[str, ...] = ("none", "handoff", "sensitive")
# flag_service_recovery: nobody came (or it was never done), or it was done
# and is still wrong.
RECOVERY_PROBLEMS: tuple[str, ...] = ("never_arrived", "not_fixed")
MAX_QUANTITY = 20
MAX_ORDER_LINES = 20
MENU_RESULT_MAX = 60
TEXT_MAX = 600


# ─── Schemas (Live function declarations; text_chat adds strict=False) ───


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
    }


def _room_prop() -> dict:
    return {"type": "string", "description": "The guest's room number, only when they tell you (this session has no room)."}


def _notify_staff(room_arg: bool) -> dict:
    props: dict[str, Any] = {
        "request_type": {"type": "string", "enum": list(REQUEST_TYPES), "description": "What kind of request this is."},
        "label": {"type": "string", "description": "A short label, for example '2 bath towels'."},
        "amenity_item_id": {"type": "integer", "description": "The amenity catalogue id when known."},
        "quantity": {"type": "integer", "minimum": 1, "maximum": MAX_QUANTITY},
        "description_en": {"type": "string", "description": "The request in plain English for staff."},
        "original_text": {"type": "string", "description": "The guest's own words."},
        "language": {"type": "string", "description": "The guest's language code, for example en or ar."},
        "priority": {"type": "string", "enum": list(PRIORITIES)},
        "sensitivity": {"type": "string", "enum": list(SENSITIVITIES)},
        "scheduled_for": {"type": "string", "description": "When the guest wants it, hotel local time (HH:MM or ISO)."},
        "category": {"type": "string", "enum": list(LEGACY_CATEGORIES), "description": "Legacy department (optional)."},
    }
    if room_arg:
        props["room"] = _room_prop()
    return _fn(
        "notify_staff",
        "Send one job to hotel staff. Call it once per distinct ask: 'two towels and some water, and the AC is not "
        "cooling' is three calls, which may go out together.",
        props, ["request_type", "description_en"],
    )


def _escalate() -> dict:
    return _fn(
        "escalate_to_human",
        "Hand the guest to a person: complaints, billing, refunds, legal, emergencies, a request for a human, or a "
        "question the hotel knowledge does not answer.",
        {
            "reason": {"type": "string", "enum": list(ESCALATION_REASONS)},
            "urgency": {"type": "string", "enum": list(PRIORITIES)},
            "summary_en": {"type": "string", "description": "One or two sentences in English for staff."},
            "original_text": {"type": "string", "description": "The guest's own words."},
        },
        ["reason", "summary_en"],
    )


def _status() -> dict:
    return _fn(
        "get_request_status",
        "The status of the guest's own requests and orders from this stay.",
        {"request_ref": {"type": "string", "description": "A specific request reference, if the guest gave one."}},
        [],
    )


def _recovery() -> dict:
    return _fn(
        "flag_service_recovery",
        "The guest says something they asked for never came, was not done, or is still not fixed (for example 'the "
        "towels never came', 'the AC is still broken'). Reopens their job and tells the duty manager.",
        {
            "problem": {"type": "string", "enum": list(RECOVERY_PROBLEMS),
                        "description": "never_arrived: nobody came or it was never done. not_fixed: it was done but is "
                                       "still wrong."},
            "request_type": {"type": "string", "enum": list(REQUEST_TYPES), "description": "What the job was for."},
            "request_ref": {"type": "string", "description": "The request reference, when known."},
            "summary_en": {"type": "string", "description": "What is wrong, in one or two English sentences for the "
                                                            "duty manager."},
            "original_text": {"type": "string", "description": "The guest's own words."},
        },
        ["problem", "summary_en"],
    )


def _menu() -> dict:
    return _fn(
        "get_menu",
        "Food and drink available to order right now, with prices and item ids.",
        {"outlet_key": {"type": "string"}, "category": {"type": "string"}},
        [],
    )


def _order(room_arg: bool) -> dict:
    props: dict[str, Any] = {
        "outlet_key": {"type": "string"},
        "lines": {
            "type": "array",
            "maxItems": MAX_ORDER_LINES,
            "items": {
                "type": "object",
                "properties": {"menu_item_id": {"type": "integer"}, "qty": {"type": "integer", "minimum": 1, "maximum": MAX_QUANTITY}},
                "required": ["menu_item_id", "qty"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
        "confirm": {"type": "boolean", "description": "false: get the priced quote. true: place it after the guest said yes."},
    }
    if room_arg:
        props["room"] = _room_prop()
    return _fn("place_order", "Quote, then place, a food and drink order.", props, ["lines", "confirm"])


def _search() -> dict:
    return _fn(
        "search_hotel_info",
        "Search the hotel's approved information (facilities, policies, hours, local places).",
        {"query": {"type": "string"}},
        ["query"],
    )


def tool_schemas(surface: str, *, search: bool = False, can_order: bool = False) -> list[dict]:
    """The tools a session on `surface` is offered. The room argument is
    only offered where the session has no room of its own."""
    room_arg = surface not in C.ROOM_BOUND_SURFACES
    out = [_notify_staff(room_arg), _escalate(), _status(), _recovery()]
    if can_order and surface in C.ORDER_SURFACES:
        out += [_menu(), _order(room_arg)]
    if search:
        out.append(_search())
    return out


# ─── Session binding and results ───


def _clean(value: Any, limit: int = TEXT_MAX) -> str:
    return " ".join(str(value or "").split())[:limit]


def target_room(session: dict, args: dict) -> tuple[str | None, bool]:
    """(room, verified_room). The session's own room always wins and model
    arguments are ignored; a room typed by the guest on a room-less surface
    is used but never verified."""
    from clefs.core import rooms as rooms_mod

    if session.get("room"):
        return str(session["room"]), bool(session.get("verified"))
    arg = _clean(args.get("room"), 24)
    if arg and rooms_mod.valid_room(arg):
        return arg, False
    return None, False


def source_for(session: dict) -> str:
    """The service.api SOURCES value for this session's surface."""
    return C.SURFACE_SOURCE.get(session.get("surface") or "", "web")


def language_of(session: dict, args: dict | None = None) -> str:
    lang = _clean((args or {}).get("language"), 8) or session.get("language") or "en"
    return lang.split("-")[0].lower() or "en"


def result(status: str, followup: str, *, ui: dict | None = None, **data: Any) -> dict:
    out = {"status": status, **data, "followup_instructions": followup}
    if ui:
        out["ui"] = ui
    return out


def error_result(exc: ClefsError) -> dict:
    """A refusal from another module, phrased for the model."""
    code = exc.code or "error"
    if exc.status == 429:
        return result("rate_limited", "Apologise: too many requests were sent from this room just now. Ask the guest "
                      "to call the front desk if it is urgent.", code=code)
    return result("error", "Apologise that this could not be done right now and offer to connect the guest with the "
                  "front desk (escalate_to_human with reason other) if they want.", code=code, detail=str(exc.detail)[:200])


def add_event(cur: Any, session: dict, kind: str, ref: dict, *, auto: bool = False) -> None:
    """cc_session_events row plus the matching session counter (the
    containment metrics read both). Synthetic eval sessions have no row."""
    if session.get("synthetic"):
        return
    cur.execute(
        "INSERT INTO cc_session_events (session_id, hotel_slug, kind, ref, auto) VALUES (%s, %s, %s, %s, %s)",
        (session["id"], session["hotel_slug"], kind, Json(ref), auto),
    )
    column = {"request": "requests_created", "escalation": "handoffs", "emergency": "emergencies",
              "unknown": "unknowns", "order": "orders"}.get(kind)
    if column:
        cur.execute(f"UPDATE cc_sessions SET {column} = {column} + 1 WHERE id = %s", (session["id"],))


def session_events(cur: Any, session: dict, kinds: tuple[str, ...]) -> list[dict]:
    if session.get("synthetic"):
        return []
    cur.execute(
        "SELECT id, kind, ref, auto, at FROM cc_session_events WHERE session_id = %s AND kind = ANY(%s) ORDER BY at",
        (session["id"], list(kinds)),
    )
    return [dict(r) for r in cur.fetchall()]


def latest_request_id(cur: Any, session: dict) -> int | None:
    """The newest job this session filed, if any (a recovery case links to it)."""
    for ev in reversed(session_events(cur, session, ("request",))):
        rid = (ev.get("ref") or {}).get("request_id")
        if rid is not None:
            try:
                return int(rid)
            except (TypeError, ValueError):
                continue
    return None


def _dry_ref() -> str:
    return "DRY-" + uuid.uuid4().hex[:8].upper()


def synthetic_session(spec: Any) -> dict:
    """A session dict for a spec with no cc_sessions row (Trust's dry-run
    evals): tools answer realistically and write nothing."""
    return {
        "id": None, "synthetic": True, "hotel_slug": spec.hotel_slug, "surface": spec.surface, "room": spec.room,
        "location_slug": spec.location, "verified": spec.verified, "language": spec.language,
        "prompt_version": spec.prompt_version, "consent_ref": None, "consents": {}, "store_transcripts": False,
    }


# ─── notify_staff: one job per call (05 spec 3) ───


def _request_type(args: dict) -> str:
    rtype = _clean(args.get("request_type"), 40)
    if rtype in REQUEST_TYPES:
        return rtype
    return LEGACY_CATEGORIES.get(_clean(args.get("category"), 40), "other")


def _quantity(args: dict) -> int | None:
    qty = args.get("quantity")
    if isinstance(qty, bool):
        return None
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return None
    return qty if 1 <= qty <= MAX_QUANTITY else None


def _describe(session: dict, args: dict, *, room: str | None, verified: bool, location: dict | None) -> str:
    """The staff-facing English text. The guest's own words and the quantity
    go to create_job as fields of their own (staff see the original next to
    their translation), so this adds only what has no field: the model's
    label, the wanted time, the venue and an unverified room."""
    label = _clean(args.get("label"), 120)
    desc = _clean(args.get("description_en")) or label or "Guest request"
    parts = [desc if not label or label.lower() in desc.lower() else f"{label}: {desc}"]
    qty = _quantity(args)
    if qty and qty > 1 and str(qty) not in parts[0]:
        parts.append(f"Quantity: {qty}.")
    when = _clean(args.get("scheduled_for"), 40)
    if when:
        parts.append(f"Wanted at: {when} (hotel time).")
    if location:
        parts.append(f"Guest is at {location.get('name') or location.get('slug')}.")
    if room and not verified:
        # One wording for web and phone: the staff card reads "confirm at the door".
        parts.append("Room not verified: please confirm at the door.")
    return " ".join(parts)[:2000]


def _ms(value: Any) -> int | None:
    """Epoch milliseconds from a time as Service gives it (epoch ms or
    seconds, a datetime, an ISO string), so the guest page reads one form."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value if value > 1e11 else value * 1000)
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(stamp.timestamp() * 1000)
    text = str(value).strip()
    if text.isdigit():
        return _ms(int(text))
    try:
        return _ms(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _minutes(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return minutes if minutes > 0 else None


def job_card(info: dict | None, *, request_type: str | None = None, label: str | None = None,
             quantity: int | None = None, room: str | None = None) -> dict:
    """The guest page's "Job sent to staff" card (05 spec 3) from
    service.api.dispatch_info. First name only: whatever Service sends, a
    surname never reaches the guest."""
    from clefs.core.pii import first_name

    info = info or {}
    qty = info.get("quantity")
    fallback_label = (request_type or "").replace("_", " ").capitalize() or None
    return {
        "item_label": _clean(info.get("item_label"), 120) or label or fallback_label,
        "quantity": qty if isinstance(qty, int) and not isinstance(qty, bool) and qty > 0 else quantity,
        "room": _clean(info.get("room") or room, 24) or None,
        "staff_first_name": first_name(info.get("staff_first_name")),
        "department_label": _clean(info.get("department_label"), 80) or None,
        "eta_minutes": _minutes(info.get("eta_minutes")),
        "created_at": _ms(info.get("created_at")),
        "accepted_at": _ms(info.get("accepted_at")),
        "state": _clean(info.get("state"), 40) or None,
        # Keys, so the guest page can say the item and the team in its own language.
        "request_type": request_type,
        "department_key": _clean(info.get("department_key"), 40) or None,
    }


def dispatch_card(cur: Any, hotel_slug: str, request_id: Any, **card: Any) -> dict | None:
    """job_card() for a request filed another way (a hand-off), read back
    through service.api.dispatch_info. None when Service cannot describe it:
    the tracker link still works without a card."""
    from clefs.modules.service import api as service_api

    if request_id is None:
        return None
    cur.execute("SAVEPOINT cc_card")
    try:
        info = service_api.dispatch_info(cur, hotel_slug, int(request_id))
    except Exception as exc:  # noqa: BLE001 - a card is a nicety, never a failed hand-off
        cur.execute("ROLLBACK TO SAVEPOINT cc_card")
        log.info("no dispatch card for request %s at %s: %s", request_id, hotel_slug, type(exc).__name__)
        return None
    cur.execute("RELEASE SAVEPOINT cc_card")
    return job_card(info, **card) if info else None


def job_followup(card: dict, *, merged: bool, door_check: bool) -> str:
    """What the model says once a job is out: the department, the first name
    of the person on the way and the minutes, when Service knows them ("Of
    course. Housekeeping has been notified. Maria is on her way, about 5
    minutes.")."""
    door = " Say staff will confirm the room at the door." if door_check else ""
    if merged:
        return "Tell the guest this was already requested and is on its way; you have added their note to it." + door
    dept, name, eta = card.get("department_label"), card.get("staff_first_name"), card.get("eta_minutes")
    if dept and name and eta:
        line = (f"say that {dept} has been notified and {name} is on the way, about {eta} minutes (for example: "
                f'"Of course. {dept} has been notified. {name} is on the way, about {eta} minutes.")')
    elif dept and name:
        line = f"say that {dept} has been notified and {name} is on the way"
    elif dept and eta:
        line = f"say that {dept} has been notified and someone will be with the guest in about {eta} minutes"
    elif dept:
        line = f"say that {dept} has been notified and the request is on its way"
    else:
        line = "say the request is on its way"
    rules = f" Name only {name}, by first name." if name else ""
    if not eta:
        rules += " Do not promise a time."
    return f"In one or two short sentences, in the guest's language, {line}.{door}{rules} Promise nothing else."


def _location(cur: Any, session: dict) -> dict | None:
    slug = session.get("location_slug")
    if not slug:
        return None
    cur.execute("SELECT slug, name FROM hotel_locations WHERE hotel_slug = %s AND slug = %s", (session["hotel_slug"], slug))
    row = cur.fetchone()
    return dict(row) if row else {"slug": slug, "name": slug}


def _requests_so_far(cur: Any, session: dict) -> int:
    if session.get("synthetic"):
        return 0
    cur.execute("SELECT requests_created FROM cc_sessions WHERE id = %s", (session["id"],))
    row = cur.fetchone()
    return int(row["requests_created"]) if row else 0


def notify_staff(cur: Any, session: dict, args: dict, *, dry_run: bool = False, call_id: str | None = None) -> dict:
    from clefs.modules.amenities import api as amenities_api
    from clefs.modules.concierge import mood
    from clefs.modules.service import api as service_api

    room, verified = target_room(session, args)
    location = _location(cur, session)
    if not room and not location:
        return result("room_required", "Ask the guest for their room number, then call notify_staff again with it.")
    if _requests_so_far(cur, session) >= C.MAX_REQUESTS_PER_SESSION:
        return result("limit", "Tell the guest you have passed on a lot already and ask them to call the front desk "
                      "for anything more in this conversation.")
    rtype = _request_type(args)
    label = _clean(args.get("label"), 100) or None
    qty = _quantity(args)
    description = _describe(session, args, room=room, verified=verified, location=location)
    if dry_run:
        return result("created", "Tell the guest their request is on its way.", request_ref=_dry_ref(), merged=False,
                      verified_room=verified, dry_run=True, request_type=rtype,
                      ui={"kind": "request", "token": None})
    priority = _clean(args.get("priority"), 10)
    if _clean(args.get("sensitivity"), 12) == "sensitive":
        # A sensitive pathway (harassment, theft): Service routes it like L1
        # and does not copy the guest's words to staff-visible text.
        out = service_api.escalate_to_human(
            cur, session["hotel_slug"], room=room or "", reason=_clean(args.get("label"), 120) or rtype,
            channel=source_for(session), sensitive=True, excerpt=None,
        )
        add_event(cur, session, "escalation", {"request_id": out.get("request_id"), "tracking_token": out.get("tracking_token"),
                                               "reason": "sensitive_request"})
        return result("escalated", "Tell the guest a manager has been told and will contact them shortly. Be calm and kind.",
                      request_ref=str(out.get("request_id")), merged=False, verified_room=verified,
                      ui={"kind": "request", "token": out.get("tracking_token")})
    # The session's mood (mood.py) rides with the job, so Service and the
    # duty manager see an upset guest's request for what it is.
    last_mood, peak_frustration = mood.current(cur, session)
    job = service_api.create_job(
        cur, session["hotel_slug"], room=room or "", request_type=rtype, quantity=qty, description_en=description,
        original_text=_clean(args.get("original_text"), 1000) or None, guest_language=language_of(session, args),
        priority=JOB_PRIORITY.get(priority, "normal"), mood=last_mood, frustration=peak_frustration,
        session_id=str(session["id"]), guest_ref=session.get("consent_ref"), channel=source_for(session),
        idempotency_key=f"cc:{session['id']}:{call_id}"[:200] if call_id else None,
    ) or {}
    request_id = job.get("request_id", job.get("id"))
    merged = bool(job.get("merged"))
    item_id = args.get("amenity_item_id")
    if (isinstance(item_id, int) and request_id is not None and not merged
            and amenities_api.resolve_item(cur, session["hotel_slug"], item_id)):
        amenities_api.link_request(cur, session["hotel_slug"], request_id=int(request_id), amenity_item_id=item_id,
                                   qty=qty or 1)
    add_event(cur, session, "request", {"request_id": request_id, "tracking_token": job.get("tracking_token"),
                                        "type": rtype, "merged": merged, "verified_room": verified})
    card = job_card(job, request_type=rtype, label=label, quantity=qty, room=room)
    follow = job_followup(card, merged=merged, door_check=bool(room) and not verified)
    return result("merged" if merged else "created", follow, request_ref=str(request_id), merged=merged,
                  verified_room=verified, department=card["department_label"], staff_first_name=card["staff_first_name"],
                  eta_minutes=card["eta_minutes"],
                  ui={"kind": "request", "token": job.get("tracking_token"), "card": card})


# ─── escalate_to_human ───


def emergency_numbers(cur: Any, hotel_slug: str, kind: str | None = None) -> list[dict]:
    """The country's numbers, the one for this kind of emergency first
    (an ambulance for a medical emergency, civil defence for a fire)."""
    from clefs.core.hotels import get_hotel

    country = (get_hotel(cur, hotel_slug).country or "").upper()
    numbers = [{"number": n, "kind": k} for n, k in C.EMERGENCY_NUMBERS.get(country, ())]
    first = C.PRIMARY_NUMBER_SERVICE.get(kind or "")
    return sorted(numbers, key=lambda x: x["kind"] != first) if first else numbers


def _numbers_text(numbers: list[dict]) -> str:
    if not numbers:
        return "local emergency services"
    return ", ".join(f"{n['number']} ({n['kind'].replace('_', ' ')})" for n in numbers[:3])


def recent_emergency(cur: Any, session: dict, within_s: int = C.EMERGENCY_DEDUPE_S) -> dict | None:
    """An emergency escalation in this session in the last `within_s`
    seconds (the turn log's double check must not file a second one)."""
    if session.get("synthetic"):
        return None
    cur.execute(
        "SELECT id, ref, auto, at FROM cc_session_events WHERE session_id = %s AND kind = 'emergency' AND at > %s "
        "ORDER BY at DESC LIMIT 1",
        (session["id"], clock.now() - timedelta(seconds=within_s)),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def raise_emergency(cur: Any, session: dict, *, summary: str, original: str | None, kind: str | None = None,
                    auto: bool = False, dry_run: bool = False) -> dict:
    """The 1.5 path: an L1 'emergency' request (Service opens the incident
    and pages the duty manager, then Security, then the GM). Shared by the
    model's escalate_to_human and the turn log's automatic escalation."""
    from clefs.modules.service import api as service_api

    numbers = emergency_numbers(cur, session["hotel_slug"], kind)
    ui = {"kind": "emergency", "numbers": numbers}
    follow = (f"Tell the guest help is on the way. If anyone is in danger they should call {_numbers_text(numbers)} "
              "now. Stay calm, keep them talking, and do not end the conversation.")
    existing = recent_emergency(cur, session)
    if existing is not None:
        return result("escalated", follow, request_ref=str(existing["ref"].get("request_id")), deduped=True, ui=ui)
    if dry_run:
        return result("escalated", follow, request_ref=_dry_ref(), dry_run=True, ui=ui)
    room, _verified = target_room(session, {})
    location = _location(cur, session)
    where = f" at {location['name']}" if location else ""
    text = f"EMERGENCY{where}: {summary}"[:1500]
    if original and original.lower() != summary.lower():
        text += f' Guest said: "{original[:300]}"'
    row = service_api.create_request(
        cur, session["hotel_slug"], room=room or "", type="emergency", description=text, source=source_for(session),
        priority_hint="L1", language="en", guest_ref=session.get("consent_ref"),
    )
    add_event(cur, session, "emergency", {"request_id": row["id"], "tracking_token": row.get("tracking_token"),
                                          "kind": kind}, auto=auto)
    return result("escalated", follow, request_ref=str(row["id"]), ui=ui)


def escalate_to_human(cur: Any, session: dict, args: dict, *, dry_run: bool = False, call_id: str | None = None) -> dict:
    from clefs.modules.concierge import kb
    from clefs.modules.service import api as service_api

    reason = _clean(args.get("reason"), 40)
    reason = reason if reason in ESCALATION_REASONS else "other"
    summary = _clean(args.get("summary_en")) or reason.replace("_", " ")
    original = _clean(args.get("original_text"), 1000) or None
    if reason == "emergency":
        return raise_emergency(cur, session, summary=summary, original=original, dry_run=dry_run)
    if reason == "unknown_answer" and not dry_run:
        # The question goes to the owner's "asked but not answered" queue.
        unknown = kb.record_unknown(cur, session["hotel_slug"], text=original or summary,
                                    language=language_of(session), session_id=session.get("id"))
        add_event(cur, session, "unknown", {"unknown_id": unknown.get("id")})
    if dry_run:
        return result("escalated", "Tell the guest a team member will follow up with them shortly.",
                      request_ref=_dry_ref(), reason=reason, dry_run=True, ui={"kind": "handoff"})
    room, _verified = target_room(session, args)
    out = service_api.escalate_to_human(
        cur, session["hotel_slug"], room=room or "", reason=f"{reason}: {summary}"[:300], channel=source_for(session),
        # Legal matters are handled discreetly: staff see the reason, not the words.
        sensitive=reason == "legal", excerpt=original,
    )
    add_event(cur, session, "escalation", {"request_id": out.get("request_id"), "tracking_token": out.get("tracking_token"),
                                           "reason": reason})
    # The guest page shows the hand-off as a job card too (who has it, how
    # fast it was picked up), when Service can describe it.
    card = dispatch_card(cur, session["hotel_slug"], out.get("request_id"), request_type="front_desk", label="Front desk",
                         room=room)
    who, team = (card or {}).get("staff_first_name"), (card or {}).get("department_label") or "the front desk"
    follow = {
        "unknown_answer": "Say you do not have that information and that a team member will get back to the guest.",
        "complaint": "Apologise sincerely, without blaming anyone, and say a manager will contact them shortly.",
        "refund_or_billing": "Say the front office will look into it and contact them; do not promise any refund.",
    }.get(reason, f"Tell the guest {who} from {team} will contact them shortly. Name only {who}, by first name." if who
          else "Tell the guest a team member will contact them shortly.")
    ui = {"kind": "handoff", "token": out.get("tracking_token")}
    if card:
        ui["card"] = card
    return result("escalated", follow, request_ref=str(out.get("request_id")), reason=reason, ui=ui)


# ─── get_request_status ───

STATUS_WINDOW_H = 36  # a verified room sees requests from its other sessions this far back
STATE_TEXT = {
    "received": "received, waiting for a team member",
    "assigned": "assigned to a team member",
    "on_the_way": "on the way",
    "done": "done",
}


def _tracked(cur: Any, session: dict, room: str | None) -> list[dict]:
    """Requests and orders this guest may ask about: this session's own, plus
    those of the same room's other sessions in the last 36 hours when the
    room is verified. An unverified or room-less session sees only its own,
    so typing another room number reveals nothing."""
    if session.get("synthetic"):
        return []
    if room and session.get("verified"):
        cur.execute(
            """
            SELECT ev.kind, ev.ref, ev.at FROM cc_session_events ev JOIN cc_sessions s ON s.id = ev.session_id
            WHERE s.hotel_slug = %s AND s.room = %s AND s.verified AND ev.at > %s
              AND ev.kind IN ('request', 'escalation', 'emergency', 'order')
            ORDER BY ev.at DESC LIMIT 20
            """,
            (session["hotel_slug"], room, clock.now() - timedelta(hours=STATUS_WINDOW_H)),
        )
        return [dict(r) for r in cur.fetchall()]
    return list(reversed(session_events(cur, session, ("request", "escalation", "emergency", "order"))))[:20]


def get_request_status(cur: Any, session: dict, args: dict, *, dry_run: bool = False, call_id: str | None = None) -> dict:
    from clefs.core.pii import first_name
    from clefs.modules.service import api as service_api
    from clefs.modules.upsell import api as upsell_api

    room, _verified = target_room(session, {})
    wanted = _clean(args.get("request_ref"), 40).removeprefix("#")
    items: list[dict] = []
    seen: set[str] = set()
    for ev in _tracked(cur, session, room):
        ref = ev["ref"] or {}
        if ev["kind"] == "order" and ref.get("order_id") and room:
            key = f"order:{ref['order_id']}"
            if key in seen or (wanted and wanted not in (str(ref["order_id"]), str(ref.get("request_id")))):
                continue
            seen.add(key)
            o = upsell_api.order_status_for_guest(cur, session["hotel_slug"], int(ref["order_id"]), room=room)
            if o:
                # order_ref / request_ref repeat `ref` in the key every other
                # tool result uses, so a phone model reading "request_ref"
                # from create_ticket finds the same key here.
                items.append({"kind": "order", "ref": str(ref["order_id"]), "order_ref": str(ref["order_id"]),
                              "status": o.get("status"),
                              "items": [f"{x.get('qty')} x {x.get('name')}" for x in o.get("lines") or []][:10]})
            continue
        token = ref.get("tracking_token")
        rid = str(ref.get("request_id") or "")
        if not token or rid in seen or (wanted and wanted != rid):
            continue
        seen.add(rid)
        view = service_api.request_status_for_guest(cur, session["hotel_slug"], token)
        if view:
            item = {"kind": ev["kind"], "ref": rid, "request_ref": rid, "label": view.get("request_type_label"),
                    "state": view.get("state"), "state_text": STATE_TEXT.get(view.get("state"), view.get("state")),
                    "cancelled": bool(view.get("cancelled")), "token": token}
            # Who is on it and how long, when Service shares it with guests
            # (05 spec 3); a first name only.
            name, eta = first_name(view.get("staff_first_name")), _minutes(view.get("eta_minutes"))
            if name:
                item["staff_first_name"] = name
            if eta and view.get("state") != "done":
                item["eta_minutes"] = eta
            items.append(item)
    if not items:
        return result("none", "Say you cannot see any open requests from this conversation, and offer to send one.",
                      requests=[])
    return result("ok", "Tell the guest the status of each request in a short sentence. Where a request has a "
                  "staff_first_name, you may say that person is on it, and the minutes when given. Never say any other "
                  "name.", requests=items, ui={"kind": "tracker", "token": items[0].get("token")})


# ─── flag_service_recovery ───


def _recovery_target(cur: Any, session: dict, room: str | None, args: dict) -> dict | None:
    """The guest's own job this is about: the one they named, else the
    newest of that type, else their newest. Only jobs this guest may ask
    about (_tracked), so naming another room's job finds nothing."""
    from clefs.modules.service import api as service_api

    wanted = _clean(args.get("request_ref"), 40).removeprefix("#")
    rtype = _clean(args.get("request_type"), 40)
    jobs = [ev["ref"] or {} for ev in _tracked(cur, session, room) if ev["kind"] == "request"]
    jobs = [j for j in jobs if j.get("tracking_token") and j.get("request_id") is not None]
    if wanted:
        pick = next((j for j in jobs if str(j["request_id"]) == wanted), None)
    elif rtype in REQUEST_TYPES and rtype != "other":
        pick = next((j for j in jobs if j.get("type") == rtype), None)
    else:
        pick = jobs[0] if jobs else None
    if pick is None:
        return None
    view = service_api.request_status_for_guest(cur, session["hotel_slug"], pick["tracking_token"]) or {}
    return {"request_id": pick["request_id"], "token": pick["tracking_token"], "type": pick.get("type"),
            "state": view.get("state")}


def flag_service_recovery(cur: Any, session: dict, args: dict, *, dry_run: bool = False,
                          call_id: str | None = None) -> dict:
    """The guest says a job never came or is still not fixed. A job marked
    done reopens with a recovery case (service.api.guest_feedback
    fixed=False); one still open, or none found, is flagged to the duty
    manager (service.api.flag_recovery never_arrived or not_fixed)."""
    from clefs.modules.service import api as service_api

    problem = _clean(args.get("problem"), 20)
    problem = problem if problem in RECOVERY_PROBLEMS else "not_fixed"
    summary = _clean(args.get("summary_en")) or ("The guest says their request never came." if problem == "never_arrived"
                                                 else "The guest says it is still not fixed.")
    original = _clean(args.get("original_text"), 1000) or None
    follow = ("Apologise sincerely, without blaming anyone, and say a manager has been told and is on it now. Do not "
              "promise compensation, a refund or a time, and do not file the request again.")
    if dry_run:
        return result("flagged", follow, problem=problem, dry_run=True, ui={"kind": "recovery", "token": None})
    room, _verified = target_room(session, {})
    job = _recovery_target(cur, session, room, args)
    text = summary if not original or original.lower() == summary.lower() else f'{summary} Guest said: "{original[:300]}"'
    outcome: dict | None = None
    how = "flagged"
    if job and job["state"] == "done":
        # Marked done, and the guest says otherwise: reopen it with a case
        # for the manager (05 spec decision 8, "not fixed").
        cur.execute("SAVEPOINT cc_feedback")
        try:
            outcome = service_api.guest_feedback(cur, session["hotel_slug"], job["token"], fixed=False, comment=text) or {}
        except Exception as exc:  # noqa: BLE001 - too late to reopen, or refused: the duty manager still hears of it
            cur.execute("ROLLBACK TO SAVEPOINT cc_feedback")
            log.info("guest_feedback refused for request %s at %s: %s", job["request_id"], session["hotel_slug"],
                     getattr(exc, "detail", None) or type(exc).__name__)
        else:
            cur.execute("RELEASE SAVEPOINT cc_feedback")
            how = "reopened"
    if outcome is None:
        kind = "never_arrived" if problem == "never_arrived" and not (job and job["state"] == "done") else "not_fixed"
        outcome = service_api.flag_recovery(
            cur, session["hotel_slug"], kind=kind, room=room, summary=text,
            request_id=int(job["request_id"]) if job else None, session_id=str(session["id"]),
            guest_language=language_of(session, args),
        ) or {}
    add_event(cur, session, "recovery", {
        "kind": problem, "how": how, "request_id": job["request_id"] if job else None,
        "tracking_token": job["token"] if job else None, "case_id": outcome.get("case_id") or outcome.get("id"),
    })
    return result("flagged", follow, problem=problem, reopened=how == "reopened",
                  request_ref=str(job["request_id"]) if job else None,
                  ui={"kind": "recovery", "token": job["token"] if job else None})


# ─── get_menu and search_hotel_info ───


def get_menu(cur: Any, session: dict, args: dict, *, dry_run: bool = False, call_id: str | None = None) -> dict:
    from clefs.modules.upsell import api as upsell_api

    outlet = _clean(args.get("outlet_key"), 64) or None
    category = _clean(args.get("category"), 64).lower()
    items = upsell_api.get_menu(cur, session["hotel_slug"], outlet=outlet, language=language_of(session)) or []
    if category:
        items = [i for i in items if category in str(i.get("section") or "").lower()] or items
    if not items:
        return result("unavailable", "Say nothing can be ordered right now and offer to ask the front desk.", items=[])
    out = [
        {"item_id": i.get("item_id"), "name": i.get("name"), "section": i.get("section"), "outlet": i.get("outlet"),
         "price": i.get("price"), "currency": i.get("currency"), "dietary": i.get("dietary") or [],
         "description": _clean(i.get("description"), 140) or None}
        for i in items[:MENU_RESULT_MAX]
    ]
    return result("ok", "Offer a few relevant items with their prices. Use only these prices. To order, call "
                  "place_order with confirm=false first.", items=out, truncated=len(items) > MENU_RESULT_MAX)


def search_hotel_info(cur: Any, session: dict, args: dict, *, dry_run: bool = False, call_id: str | None = None) -> dict:
    from clefs.modules.concierge import kb

    query = _clean(args.get("query"), 200)
    hits = kb.search_approved(cur, session["hotel_slug"], query, language=language_of(session)) if query else []
    if not hits:
        return result("not_found", "Say you do not have that information. If the guest wants, call escalate_to_human "
                      "with reason unknown_answer.", results=[])
    return result("ok", "Answer only from these results.", results=[{"kb_ref": h["kb_ref"], "text": h["text"]} for h in hits])


# ─── place_order: quote, then confirm (upsell.api) ───


def order_lines(args: dict) -> list[dict]:
    """[{item_id, qty}] merged by item and sorted, so the quote and the
    confirm of one basket compare equal whatever order the model used."""
    merged: dict[int, int] = {}
    for line in (args.get("lines") or [])[:MAX_ORDER_LINES]:
        if not isinstance(line, dict):
            continue
        item, qty = line.get("menu_item_id", line.get("item_id")), line.get("qty", 1)
        try:
            item, qty = int(item), int(qty)
        except (TypeError, ValueError):
            continue
        if item > 0 and qty > 0:
            merged[item] = min(MAX_QUANTITY, merged.get(item, 0) + qty)
    return [{"item_id": i, "qty": q} for i, q in sorted(merged.items())]


def lines_key(lines: list[dict]) -> str:
    return ",".join(f"{line['item_id']}x{line['qty']}" for line in lines)


def _quote_view(q: dict) -> dict:
    return {
        "quote_id": q.get("quote_id"),
        "lines": [{"item_id": x.get("item_id"), "name": x.get("name"), "qty": x.get("qty"), "unit_price": x.get("unit_price"),
                   "line_total": x.get("line_total")} for x in q.get("lines") or []],
        "price_display": q.get("price_display"),
        "subtotal": q.get("subtotal"),
        "taxes": [{"label": t.get("label"), "amount": t.get("amount"), "included": t.get("included")} for t in q.get("taxes") or []],
        "total": q.get("total"),
        "currency": q.get("currency"),
        "suggestions": [{"item_id": s.get("item_id"), "name": s.get("name"), "price": s.get("price"),
                         "reason": s.get("reason")} for s in (q.get("suggestions") or [])[:2]],
    }


def _quote(cur: Any, session: dict, room: str, lines: list[dict], *, dry_run: bool) -> dict:
    from clefs.modules.upsell import api as upsell_api

    call = lambda: upsell_api.quote_for_session(  # noqa: E731
        cur, session["hotel_slug"], room=room, lines=lines, session_ref=str(session.get("id") or "eval"),
        language=language_of(session), source=source_for(session),
    )
    if not dry_run:
        return call()
    # A dry run prices the basket for real, then rolls the quote row and its
    # "shown" funnel events back: realistic numbers, nothing persisted.
    cur.execute("SAVEPOINT cc_dry_quote")
    try:
        return call()
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT cc_dry_quote")


def _store_quote(cur: Any, session: dict, room: str, lines: list[dict], q: dict) -> None:
    if session.get("synthetic"):
        return
    now = clock.now()
    cur.execute(
        """
        INSERT INTO cc_order_quotes (session_id, hotel_slug, room, lines, lines_key, quote, created_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (session["id"], session["hotel_slug"], room, Json(lines), lines_key(lines), Json(json.loads(json.dumps(q, default=str))),
         now, now + timedelta(seconds=C.QUOTE_TTL_S)),
    )


def _fresh_quote(cur: Any, session: dict, lines: list[dict]) -> dict | None:
    if session.get("synthetic"):
        return None
    cur.execute(
        """
        SELECT id, room, quote, confirmed_at, order_ref FROM cc_order_quotes
        WHERE session_id = %s AND lines_key = %s AND expires_at > %s
        ORDER BY created_at DESC LIMIT 1 FOR UPDATE
        """,
        (session["id"], lines_key(lines), clock.now()),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def place_order(cur: Any, session: dict, args: dict, *, dry_run: bool = False, call_id: str | None = None) -> dict:
    """confirm=false: a priced quote re-priced from the database by
    upsell.api (price display, the plus-plus fees from the hotel's tax
    settings). confirm=true: needs a quote for the same basket from this
    session in the last 10 minutes, then upsell.api.confirm_order files the
    order and its one F&B request, and the answer carries an upsell idea."""
    from clefs.modules.upsell import api as upsell_api

    if session.get("surface") not in C.ORDER_SURFACES:
        return result("not_available", "Say ordering is not available here and offer to pass a request to staff instead.")
    lines = order_lines(args)
    if not lines:
        return result("invalid", "Ask the guest what they would like, call get_menu for the item ids, then quote again.")
    room, verified = target_room(session, args)
    if not room:
        return result("room_required", "Ask for the guest's room number, then quote again with it.")
    if not args.get("confirm"):
        q = _quote(cur, session, room, lines, dry_run=dry_run)
        view = _quote_view(q)
        if not dry_run:
            _store_quote(cur, session, room, lines, q)
            add_event(cur, session, "quote", {"quote_id": q.get("quote_id"), "total_minor": q.get("total_minor"),
                                              "currency": q.get("currency"),
                                              "suggested": [s.get("item_id") for s in q.get("suggestions") or []]})
        follow = (f"Read back the items and the total of {view['currency']} {view['total']}"
                  + (" (service charge, municipality fee and VAT are added as shown)" if view["price_display"] == "plus_plus" else "")
                  + ". Ask the guest to confirm. Only after a clear yes, call place_order again with the same lines and "
                  "confirm=true.")
        if view["suggestions"]:
            follow += f" You may suggest once: {view['suggestions'][0]['name']}."
        return result("quoted", follow, dry_run=dry_run or None, **view)
    if dry_run:
        q = _quote(cur, session, room, lines, dry_run=True)
        return result("placed", "Tell the guest the order is placed.", order_ref=_dry_ref(), total=q.get("total"),
                      currency=q.get("currency"), dry_run=True, ui={"kind": "order", "token": None})
    stored = _fresh_quote(cur, session, lines)
    if stored is None:
        return result("quote_required", "Call place_order with confirm=false first, read the total to the guest and get "
                      "their yes.")
    quote = stored["quote"] or {}
    order = upsell_api.confirm_order(
        cur, session["hotel_slug"], quote_id=str(quote.get("quote_id")), room=stored["room"], source=source_for(session),
        session_ref=str(session["id"]), notes=_clean(args.get("notes"), 300) or None, verified_room=verified,
        language=language_of(session),
    )
    ref = {"order_id": order.get("order_id"), "request_id": order.get("request_id"), "tracking_token": order.get("tracking_token"),
           "total_minor": order.get("total_minor"), "currency": order.get("currency"),
           "item_ids": [x.get("item_id") for x in order.get("lines") or []]}
    if stored["confirmed_at"] is None:
        cur.execute("UPDATE cc_order_quotes SET confirmed_at = %s, order_ref = %s WHERE id = %s",
                    (clock.now(), Json(json.loads(json.dumps(ref, default=str))), stored["id"]))
        add_event(cur, session, "order", ref)
    ordered = set(ref["item_ids"])
    upsell = next((s for s in quote.get("suggestions") or [] if s.get("item_id") not in ordered), None)
    follow = "Tell the guest the order is placed and will be delivered to their room."
    if not verified:
        follow += " Staff will confirm the room at the door."
    if upsell:
        follow += f" If it fits, mention once that {upsell.get('name')} goes well with it."
    return result("placed", follow, order_ref=str(order.get("order_id")), total=order.get("total"),
                  currency=order.get("currency"), deduped=bool(order.get("deduped")) or None, verified_room=verified,
                  upsell={"item_id": upsell.get("item_id"), "name": upsell.get("name"), "price": upsell.get("price")} if upsell else None,
                  ui={"kind": "order", "token": order.get("tracking_token"), "order_id": order.get("order_id")})


# ─── Dispatch with dedupe ───

EXECUTORS: dict[str, Callable[..., dict]] = {
    "notify_staff": notify_staff,
    "escalate_to_human": escalate_to_human,
    "get_request_status": get_request_status,
    "flag_service_recovery": flag_service_recovery,
    "get_menu": get_menu,
    "place_order": place_order,
    "search_hotel_info": search_hotel_info,
}


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _claim(cur: Any, session: dict, call_id: str, name: str, args: dict) -> dict | None:
    """Insert the call row first. A concurrent duplicate blocks on the unique
    index until this transaction ends, then finds the stored answer. Returns
    the stored answer of an earlier call with the same id, else None."""
    cur.execute(
        """
        INSERT INTO cc_tool_calls (session_id, hotel_slug, call_id, name, arguments, status)
        VALUES (%s, %s, %s, %s, %s, 'running')
        ON CONFLICT (session_id, call_id) DO NOTHING RETURNING id
        """,
        (session["id"], session["hotel_slug"], call_id, name, Json(_jsonable(args))),
    )
    if cur.fetchone() is not None:
        return None
    cur.execute("SELECT result, status FROM cc_tool_calls WHERE session_id = %s AND call_id = %s", (session["id"], call_id))
    row = cur.fetchone()
    stored = dict((row or {}).get("result") or {}) or result("in_progress", "Wait for the earlier answer to this call.")
    return {**stored, "deduped": True}


def execute(cur: Any, session: dict, name: str, args: dict | None, *, call_id: str | None = None,
            dry_run: bool | None = None) -> dict:
    """Run one tool for `session` (a cc_sessions row, or synthetic_session()
    for a dry-run eval). dry_run defaults to the session being an eval."""
    args = args if isinstance(args, dict) else {}
    dry = bool(dry_run) if dry_run is not None else (session.get("surface") == "eval" or bool(session.get("synthetic")))
    fn = EXECUTORS.get(name)
    if fn is None:
        return result("unknown_tool", "That tool does not exist. Carry on without it.")
    stored = not session.get("synthetic")
    call_id = (_clean(call_id, 120) or f"srv_{uuid.uuid4().hex}") if stored else None
    if stored:
        earlier = _claim(cur, session, call_id, name, args)
        if earlier is not None:
            return earlier
    t0 = time.monotonic()
    # Each executor runs in a savepoint: a refusal from Service or Upsell
    # (or a bug) rolls back its own writes and becomes a result the model
    # can speak, while the call row and the rest of the turn survive.
    cur.execute("SAVEPOINT cc_tool")
    try:
        out = fn(cur, session, args, dry_run=dry, call_id=call_id)
    except ClefsError as exc:
        cur.execute("ROLLBACK TO SAVEPOINT cc_tool")
        out, status = error_result(exc), "refused"
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT cc_tool")
        log.exception("concierge tool %s failed for %s", name, session.get("hotel_slug"))
        out, status = result("error", "Apologise that something went wrong and offer to connect the guest with the front "
                             "desk."), "failed"
    else:
        cur.execute("RELEASE SAVEPOINT cc_tool")
        status = "ok"
    out = {k: v for k, v in _jsonable(out).items() if v is not None}
    if stored:
        cur.execute(
            "UPDATE cc_tool_calls SET result = %s, status = %s, latency_ms = %s WHERE session_id = %s AND call_id = %s",
            (Json(out), status, int((time.monotonic() - t0) * 1000), session["id"], call_id),
        )
        cur.execute("UPDATE cc_sessions SET tool_calls = tool_calls + 1 WHERE id = %s", (session["id"],))
    return out


def execute_for_session(cur: Any, session_id: str, name: str, args: dict | None, *, call_id: str | None = None,
                        hotel_slug: str | None = None) -> dict:
    """execute() by session id, for callers that hold no session row (the
    phone's MCP server and sideband worker through api.run_tool). An unknown
    or ended session gets a speakable refusal, never an exception mid-call.
    hotel_slug, when given, must be the session's hotel: a session id leaked
    or mistyped across tenants answers exactly like an unknown one, so the
    reply says nothing about another hotel's sessions."""
    from clefs.modules.concierge import sessions

    session = sessions.get_session(cur, session_id)
    if (session is None or session.get("ended_at") is not None or session["expires_at"] <= clock.now()
            or (hotel_slug is not None and session.get("hotel_slug") != hotel_slug)):
        return result("session_ended", "Apologise and suggest the guest calls the front desk directly.")
    session.pop("token_hash", None)
    return execute(cur, session, name, args, call_id=call_id)
