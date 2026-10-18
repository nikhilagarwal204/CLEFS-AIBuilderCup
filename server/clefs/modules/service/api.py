"""
Service public API (frozen signatures; other modules import only this file).

create_request, escalate_to_human and request_status_for_guest run on the v2
intake pipeline (intake.py), state machine (state.py) and routing
(routing.py). The journey-era functions (create_followup and the stay
queries) keep their working defaults until the Service lane's later parts
replace the bodies, never the signatures (FROZEN_API).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from clefs.auth.tokens import sign_ref, verify_ref
from clefs.core import actions as actions_mod
from clefs.core import clock
from clefs.modules.service import constants as C

# Channels that may file requests. Guest channels are rate limited per room
# (constants.GUEST_SOURCES); staff channels are not. 04 spec 7.2 adds the
# journey and inquiry sources.
SOURCES = (
    "web", "voice", "sip", "whatsapp", "text", "console", "device", "legacy",
    "whatsapp_journey", "phone_desk", "inquiries", "j7_feedback", "journey_gate",
)

# Journeys classify a guest's reply into a department_hint (04 spec 11: one of
# housekeeping, engineering, ird, front_office, concierge, security, general)
# and file it with create_request(type=REQUEST_TYPE_FOR_DEPARTMENT[hint], ...).
# Values are Service request-type keys; intake refines them inside the same
# department from the text (a housekeeping reply about towels files as
# towels, an engineering one about the AC as ac, with the heat rule).
REQUEST_TYPE_FOR_DEPARTMENT: dict[str, str] = {
    "housekeeping": "make_up_room",
    "engineering": "maintenance",
    "ird": "ird",
    "front_office": "front_desk",
    "concierge": "concierge",
    "security": "security",
    "general": "other",
    # Legacy department names (staff_members.department) stay as aliases:
    # callers written against the wave 0b stub keep working, and
    # tests/foundation/test_module_contracts.py pins them.
    "Housekeeping": "make_up_room",
    "Maintenance": "maintenance",
    "Food & Beverage": "ird",
    "Front Desk": "front_desk",
    "Concierge": "concierge",
    "Security": "security",
    "General": "other",
}

FOLLOWUP_KINDS = ("guest_feedback", "post_stay_recovery", "guest_issue", "guest_contact")


def tracking_token_for(hotel_slug: str, request_id: int) -> str:
    """Signed handle for one request's status (the pre-v2 token form, still
    accepted by request_status_for_guest). New requests get a random token
    from create_request instead, of which only the hash is stored."""
    return sign_ref(f"svc:{hotel_slug}:{request_id}")


def create_request(
    cur: Any,
    hotel_slug: str,
    *,
    room: str,
    type: str,
    description: str,
    source: str,
    subtype: str | None = None,
    priority_hint: str | None = None,
    language: str | None = None,
    guest_ref: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """File a guest or staff request through the intake pipeline: dedupe and
    merge, per-room rate limit for guest sources, translation, safety,
    priority (heat, VIP, L1), department and SLA, routing, legacy SSE events
    (delivered when the caller's transaction commits) and the audit row.

    `type` is a request-type key ("towels") or a legacy type ("Towel");
    `priority_hint` is high|medium|low, or a severity L1|L2|L3 (journeys).
    Returns the staff_requests row plus "tracking_token" (a guest status
    link token), "merged" (True: appended to the same room's open request of
    the same type; tell the guest it is already on its way) and "replayed".
    Raises ClefsError 429 rate_limited when a guest channel's room is over
    its limit; life safety never is (an emergency or security type, an L1
    priority_hint, or emergency words in the description), and a placeholder
    room ("unknown") is not counted as a room. A caller that does not know
    the room should pass room="" rather than a placeholder.

    `idempotency_key` (optional, <= 200 chars, unique per hotel; namespace
    it by caller, e.g. "ups:order:812" or "cc:tool:<call_id>") makes the
    call exactly replayable: the same key returns the first outcome's row
    with "replayed": True and changes nothing (no second card, no second
    rate-limit hit, no words appended twice), whatever state the request is
    in now. The first call may still merge into the room's open request of
    the same type; its replay then returns that request with "merged": True
    too. Without a key only the semantic merge dedupes."""
    from clefs.modules.service import intake

    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    key = None if idempotency_key is None else str(idempotency_key).strip()[:200] or None
    severity_hint = None
    if priority_hint in C.SEVERITIES:
        severity_hint = priority_hint
        priority_hint = {"L1": "high", "L2": "medium"}.get(priority_hint)
    out = intake.create_request(
        cur,
        hotel_slug,
        intake.IntakeIn(
            description=description or "",
            source=source,
            room=room,
            type=type,
            subtype=subtype,
            priority_hint=priority_hint,
            severity_hint=severity_hint,
            language=language,
            guest_ref=None if guest_ref is None else str(guest_ref)[:200],
            idempotency_key=key,
        ),
    )
    return {**out.row, "tracking_token": out.tracking_token, "merged": out.merged, "replayed": out.replayed}


def escalate_to_human(
    cur: Any,
    hotel_slug: str,
    *,
    room: str,
    reason: str,
    channel: str,
    sensitive: bool,
    excerpt: str | None,
) -> dict:
    """Hand the guest to a person. A sensitive pathway (harassment, theft,
    a lost passport) routes like L1 (duty manager, then Security, then the
    GM) and the guest's words are NOT copied into the staff-visible
    description; otherwise the front office gets a high-priority handoff.
    -> {ok, request_id, assigned_to, tracking_token}."""
    from clefs.modules.service import intake

    description = f"[{channel}] Guest asked for a person: {reason}"[:600]
    if excerpt and not sensitive:
        description += f' - "{excerpt[:300]}"'
    out = intake.create_request(
        cur,
        hotel_slug,
        intake.IntakeIn(
            description=description,
            source=channel if channel in SOURCES else "web",
            room=room,
            type="Direct Conversation",
            request_type_key="direct_conversation",
            priority_hint="high",
            sensitivity_hint="sensitive" if sensitive else "handoff",
            sensitive_kind_hint="guest_escalation",
            allow_merge=False,
            rate_limit=False,  # a guest asking for a person is never turned away
            translate=False,  # the description is ours; the excerpt stays verbatim
            flags={"escalation": True, "channel": channel},
        ),
    )
    row = out.row
    return {"ok": True, "request_id": row["id"], "assigned_to": row.get("assigned_to"), "tracking_token": out.tracking_token}


def request_status_for_guest(cur: Any, hotel_slug: str, tracking_token: str) -> dict | None:
    """Minimal, guest-safe status for one request (never staff names).

    Accepts the random token from create_request and the older signed form
    (tracking_token_for). -> {id, room, type, status, created_at_ms,
    assigned_at_ms, resolved_at_ms, state: received|assigned|on_the_way|done,
    request_type_label, department_label, updated_at_ms, can_confirm,
    guest_confirmation} or None."""
    from clefs.modules.service import intake

    request_id = intake.token_request(cur, hotel_slug, tracking_token or "")
    if request_id is None:
        ref = verify_ref(tracking_token or "")
        if not ref:
            return None
        try:
            prefix, slug, rid = ref.split(":", 2)
            request_id = int(rid)
        except ValueError:
            return None
        if prefix != "svc" or slug != hotel_slug:
            return None
    cur.execute("SELECT * FROM staff_requests WHERE id = %s AND hotel_slug = %s", (request_id, hotel_slug))
    row = cur.fetchone()
    if row is None:
        return None
    return guest_view(row)


def guest_view(row: dict) -> dict:
    """The tracker's projection of a request row (shared with the public route)."""
    state = row.get("state") or {"progress": "assigned", "completed": "completed"}.get(row.get("status"), "new")
    rtype = C.REQUEST_TYPES.get(row.get("request_type_key") or "", {})
    dept = C.DEPARTMENTS.get(row.get("department_key") or "", {})
    resolved = row.get("resolved_at")
    updated = max(x for x in (row.get("timestamp"), row.get("assigned_at") or row.get("accepted_at"),
                              row.get("acknowledged_at"), row.get("started_at"), resolved, row.get("cancelled_at"),
                              row.get("guest_confirmed_at"), 0) if x is not None)
    # A guest answer belongs to ONE completion. After a "No" (reopen,
    # reassign) and a second completion the guest is asked again; without
    # this the fixed request stayed "not resolved" forever (QA GS-7). The
    # stored answer is kept until they reply (the last thing they told us);
    # "unknown" inside the window can only follow a later completion (the
    # expiry job marks it 24 h after the one before).
    answer = row.get("guest_confirmation")
    answered_at = row.get("guest_confirmed_at")
    awaiting = answer is None or answer == "unknown" or (
        resolved is not None and (answered_at is None or int(answered_at) < int(resolved)))
    can_confirm = (
        state == "completed" and awaiting
        and resolved is not None and clock.now_ms() - int(resolved) < 24 * 3600 * 1000
    )
    return {
        "id": row["id"],
        "room": row["room"],
        "type": row["type"],
        "status": row["status"],
        "created_at_ms": row["timestamp"],
        "assigned_at_ms": row.get("assigned_at") or row.get("accepted_at"),
        "resolved_at_ms": resolved,
        "state": C.TRACKER_STATE.get(state, "received"),
        "request_type_label": rtype.get("label_en") or row.get("type"),
        "request_type_label_ar": rtype.get("label_ar"),
        "department_label": dept.get("name") or row.get("category"),
        "department_label_ar": dept.get("name_ar"),
        "updated_at_ms": updated or row["timestamp"],
        "can_confirm": bool(can_confirm),
        # the answer to the CURRENT completion: null while a new one is
        # awaited, so a tracker that treats an answer as "settled" keeps asking
        "guest_confirmation": None if can_confirm else answer,
        "cancelled": state == "cancelled",
    }


# ─── Follow-ups and stay queries (04 spec 7.2) ───


def create_followup(
    cur: Any,
    hotel_slug: str,
    *,
    kind: str,
    summary: str,
    guest_ref: str | int | None,
    owner_role: str,
    due_at: datetime | None,
    source: str,
    language: str | None = None,
    room: str | None = None,
) -> dict:
    """A follow-up for a guest who has left or has no room: no ack ladder,
    no SLA timers. kind: guest_feedback (J7 low rating), post_stay_recovery
    (J6_post), guest_issue (J5 negative after check-out) or guest_contact
    (Inquiry Guard rerouting a non-sales message).

    Until the Service lane builds its follow-up queue this files a core.task
    action (a human task for `owner_role`, surfaced by the brief), so nothing
    is lost meanwhile. -> {id, action_id, kind, status}; status "suppressed"
    (id None) when the hotel switched core.task's dial off."""
    if kind not in FOLLOWUP_KINDS:
        raise ValueError(f"kind must be one of {FOLLOWUP_KINDS}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    title = f"Guest follow-up: {summary}".strip()
    a = actions_mod.create_action(
        cur,
        hotel_slug,
        type="core.task",
        title=title[:300],
        params={
            "followup_kind": kind,
            "summary": summary,
            "guest_ref": guest_ref,
            "source": source,
            "language": language,
            "room": room,
        },
        owner_role=owner_role,
        due_at=due_at,
        actor=actions_mod.SYSTEM,
    )
    return {"id": a.get("id"), "action_id": a.get("id"), "kind": kind, "status": a["status"]}


def _epoch_ms(dt: datetime) -> int:
    """A naive datetime is read as UTC (never as the server's local zone)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def open_items_for_stay(
    cur: Any, hotel_slug: str, *, room: str, since: datetime, until: datetime | None = None,
    reservation_id: int | None = None,
) -> list[dict]:
    """[{id, request_type, severity: 'L1'|'L2'|'L3', status, created_at}] still
    open for the stay, filed in [since, until), oldest first. `created_at` is
    an aware UTC datetime; `status` is the v2 state. Used by the guests J6
    checkout-recovery gate.

    `reservation_id` (optional) widens the match to the whole stay: requests
    in the reservation's room_number (when `room` is "" or differs) and any
    request filed with guest_ref=str(reservation_id) (journeys file that
    way, so a room move is followed). Its checked_out_at bounds the window
    when `until` is None: a request filed in the room afterwards is the next
    guest's. An unknown reservation adds nothing. No room and no reservation
    -> [].

    WHY "scheduled" is left out: J6 asks "is anything unresolved before this
    guest leaves", and a scheduled item (the checkout luggage pickup, a taxi)
    is future service, not an unresolved issue; it joins the answer once it
    falls due and turns "new". A row with no severity (filed before v2) reads
    as L3, so it can never trip the daily L1 cadence on its own."""
    open_now = [s for s in C.OPEN_STATES if s != "scheduled"]
    rooms = {r for r in [(room or "").strip()] if r}
    ref = None
    if reservation_id is not None:
        cur.execute("SELECT room_number, checked_out_at FROM reservations WHERE id = %s AND hotel_slug = %s",
                    (int(reservation_id), hotel_slug))
        res = cur.fetchone()
        if res is not None:
            ref = str(int(reservation_id))
            if (res["room_number"] or "").strip():
                rooms.add(res["room_number"].strip())
            if until is None and res["checked_out_at"] is not None:
                until = res["checked_out_at"]
    if not rooms and ref is None:
        return []
    since_ms = _epoch_ms(since)
    sql = """
        SELECT id, COALESCE(request_type_key, type) AS request_type, COALESCE(severity, 'L3') AS severity,
               state AS status, "timestamp" AS ts
        FROM staff_requests
        WHERE hotel_slug = %s AND (room = ANY(%s) OR (%s::text IS NOT NULL AND guest_ref = %s))
          AND state = ANY(%s) AND "timestamp" >= %s
    """
    params: list[Any] = [hotel_slug, sorted(rooms), ref, ref, open_now, since_ms]
    if until is not None:
        sql += ' AND "timestamp" < %s'
        params.append(_epoch_ms(until))
    cur.execute(sql + ' ORDER BY "timestamp", id', params)
    return [
        {
            "id": r["id"],
            "request_type": r["request_type"],
            "severity": r["severity"],
            "status": r["status"],
            "created_at": datetime.fromtimestamp(int(r["ts"]) / 1000, tz=timezone.utc),
        }
        for r in cur.fetchall()
    ]


def has_open_complaint(
    cur: Any, hotel_slug: str, *, room: str | None = None, reservation_id: int | None = None, since: datetime
) -> bool:
    """True while the guest has an unresolved complaint (pauses marketing,
    never J7): an open recovery case for the room opened since `since`. A
    reservation_id without a room reads the reservation's room_number."""
    from clefs.modules.service import recovery

    if not room and reservation_id is not None:
        cur.execute("SELECT room_number FROM reservations WHERE id = %s AND hotel_slug = %s", (reservation_id, hotel_slug))
        row = cur.fetchone()
        room = row["room_number"] if row else None
    return recovery.has_open_complaint(cur, hotel_slug, room=room, since=since)


def recovery_cost(cur: Any, hotel_slug: str, *, start: date, end: date) -> int:
    """Service-recovery gestures given in [start, end], minor units of the
    hotel's currency (Finance's weekly pulse)."""
    from clefs.modules.service import recovery

    return recovery.recovery_cost(cur, hotel_slug, start=start, end=end)


def on_duty(cur: Any, hotel_slug: str, *, role: str, at: datetime) -> list[dict]:
    """Who holds `role` (for example duty_manager) at `at`:
    [{user_id, staff_member_id, name}] from the roster (a shift role) or the
    hotel's active memberships (gm and other console roles)."""
    from clefs.modules.service import roster

    return roster.on_duty(cur, hotel_slug, role=role, at=at)


def resolve_finished_signals(cur: Any, hotel_slug: str) -> int:
    """What the minute sweep reconciles: resolve the brief items of finished
    requests and closed incidents (signals.resolve_finished). -> how many."""
    from clefs.modules.service import signals

    return signals.resolve_finished(cur, hotel_slug)


# ─── State subscriptions (Wave 1 gap: Upsell orders follow their request) ───


def on_request_state(fn: Any) -> Any:
    """Subscribe fn(cur, change) to every request state transition.

    Called inside the transition's transaction (after the row, feed event
    and audit row are written), each subscriber in its own savepoint: a
    failing callback is logged and rolled back alone, never the transition.
    A replayed device action changed nothing and notifies nobody.

    `change` = {hotel_slug, request_id, action, from_state, to_state (v2:
    new|assigned|acknowledged|in_progress|completed|cancelled|reopened|
    scheduled), status (legacy: new|progress|completed), version,
    actor_type (user|device|staff|guest|system|legacy_client), event_id,
    room, request_type_key, department_key, severity, source, guest_ref,
    amenity_item_id, quantity, resolution_code, assigned_staff_id}. Never the
    guest's words. Creation is not a transition: the caller of
    create_request already holds the new row.

    Idempotent by identity; returns fn, so it works as a decorator. Register
    at module import (a Module's api.py or module.py)."""
    from clefs.modules.service import hooks

    return hooks.subscribe(fn)


# ─── Safety classification (03 spec 0.5 and 1.5, Wave 1 preflight) ───
# Safety runs twice: the prompt carries the rules, and the server classifies
# every guest utterance whether or not the model called a tool. The
# classifier (lexicons in 8 languages, then an LLM that may only upgrade) is
# Service's safety.py; the concierge's turn logging, the voice sideband worker
# and journey replies reach it only through this function, since they may not
# import Service internals.

# Ordered most to least severe. A caller combining two verdicts (lexicon and
# model, or two turns) keeps the one with the lower index: never downgrade.
SAFETY_LEVELS = ("emergency", "sensitive", "handoff", "none")


def classify_safety(
    text: str,
    *,
    language: str | None = None,
    text_en: str | None = None,
    hotel_slug: str | None = None,
    use_llm: bool = True,
    cur: Any = None,
) -> dict:
    """Classify one guest utterance: {level: one of SAFETY_LEVELS, kind: str
    | None (medical, fire, harassment, refund, ...), source: 'lexicon' |
    'llm' | None, confidence: float 0-1}.

    `text` is the guest's own words; `text_en` an English rendering when the
    caller already has one (skips a translation); `hotel_slug` attributes the
    LLM layer's cost; use_llm=False runs the deterministic lexicons only (hot
    paths with no latency budget); `cur` lets a caller inside a transaction
    share its connection for per-hotel settings instead of opening another.

    Layer 1 is the deterministic lexicons (en, ar, ru, hi, ur, zh, de, fr,
    with negative guards; the script of the text picks extra lexicons when
    the language tag is wrong or missing). Layer 2, an LLM with a 1.5 s
    budget, runs only when layer 1 found nothing urgent and may only raise
    the level (confidence >= 0.6). Never raises: a classifier fault returns
    the lexicon verdict, or {"level": "none"}. Callers must read "none" as
    "the server adds no escalation", never as permission to drop one the
    model or a person already raised."""
    from clefs.modules.service import safety

    try:
        return safety.classify(text or "", language=language, text_en=text_en, hotel_slug=hotel_slug,
                               use_llm=use_llm, cur=cur)
    except Exception:  # safety must never break the caller's turn
        import logging

        logging.getLogger("clefs.service.safety").exception("classify_safety failed")
        try:
            v = safety.lexicon_verdict(text or "", language=language, text_en=text_en)
            return {k: v.get(k) for k in ("level", "kind", "source", "confidence")}
        except Exception:
            return {"level": "none", "kind": None, "source": None, "confidence": 0.0}
