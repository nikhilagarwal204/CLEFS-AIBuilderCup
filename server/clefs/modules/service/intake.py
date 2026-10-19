"""
The intake pipeline (03 spec 1.3): the only writer of new service requests.

Every door files through create_request(): the legacy /requests and /pending
routes, service.api.create_request (concierge tools, WhatsApp, voice,
journeys), the console, the device app and the phone-entry console. The steps
run in order:

  1. normalize    room register -> floor, room type, OOO; stay stage from
                  the guest's dates; shift key from hotel-local time
  2. idempotency  the caller's key (session:call id, WhatsApp message id,
                  device client_event_id) replays; the same room + type open
                  in the last 10 minutes merges instead of duplicating
  3. rate limit   guest channels, per room, counted in the database (Vercel
                  runs many instances, so no in-memory limiter)
  4. translate    non-English text through clefs.ai, time-boxed (2 s); on a
                  timeout staff see the guest's own words, flagged
  5. safety       classify_safety sets sensitivity and can lift severity
  6. priority     the legacy heuristic plus the heat, VIP and L1 rules
  7. resolve      request type -> department -> SLA policy
  8. persist      insert, "created" event, route, guest tracking token
  9. legacy       the SSE events old dashboards listen for, and the audit row
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from clefs import ai, events
from clefs import audit as audit_mod
from clefs.core import clock
from clefs.core import rooms as rooms_mod
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.amenities import api as amenities_api
from clefs.modules.guests import api as guests_api
from clefs.modules.service import config, priority, routing, state
from clefs.modules.service import constants as C
from clefs.modules.service import settings as svc_settings

log = logging.getLogger("clefs.service.intake")

TOKEN_TTL = timedelta(days=30)
RATE_WINDOW_MS = 10 * 60_000
DESCRIPTION_MAX = 2000
# Types that are never merged: every complaint, escalation or emergency is
# its own case, even when the same room raised one a minute ago; and every
# food or drink request is its own priced order (upsell links one request
# per order, and a kitchen that misses the second order is worse than a
# duplicate card).
NEVER_MERGE = frozenset({"other", "direct_conversation", "complaint", "guest_feedback", "emergency", "security",
                         "ird", "breakfast", "drinks"})
# Scripts where plain ASCII text is certainly not the guest's own language,
# so an English description needs no translation call.
NON_LATIN = frozenset({"ar", "ru", "hi", "ur", "zh", "ja", "ko", "fa", "he", "th", "bn", "ta", "ml"})


@dataclass
class IntakeIn:
    """One request as a door hands it over. Only description and source are
    required; everything else is resolved when missing."""

    description: str
    source: str
    room: str | None = None
    type: str | None = None  # a legacy type string ("Towel", "In-Room Dining")
    request_type_key: str | None = None  # a v2 key ("towels"), wins over `type`
    category: str | None = None  # the model's or legacy client's department guess
    subtype: str | None = None
    priority_hint: str | None = None
    language: str | None = None
    guest_ref: str | None = None
    idempotency_key: str | None = None
    channel_ref: str | None = None
    amenity_item_id: int | None = None
    quantity: int | None = None
    location_slug: str | None = None
    scheduled_for: int | None = None  # epoch ms
    timestamp: int | None = None  # epoch ms; the legacy routes pass the client's
    created_by_staff_id: int | None = None
    created_by_user_id: int | None = None
    severity_hint: str | None = None  # L1 | L2 | L3 from a caller that knows
    sensitivity_hint: str | None = None  # a safety level the caller already found
    sensitive_kind_hint: str | None = None
    # The guest's own words when the caller already wrote the English
    # description (the concierge's create_job; translate=False then).
    original_text: str | None = None
    # The guest's mood and frustration (0..1) and the concierge session.
    mood: str | None = None
    frustration: float | None = None
    session_id: str | None = None
    # Door-specific behaviour
    allow_merge: bool = True
    rate_limit: bool | None = None  # None: guest sources only
    translate: bool = True
    announce: bool = True  # new_clefs_request / new_aria_request for the old dashboard
    route: bool = True
    # The legacy POST /requests could file a row already assigned or done.
    legacy_status: str | None = None
    legacy_assigned_to: str | None = None
    legacy_accepted_at: int | None = None
    legacy_resolved_at: int | None = None
    flags: dict = field(default_factory=dict)


@dataclass
class IntakeOut:
    row: dict
    merged: bool = False
    replayed: bool = False
    tracking_token: str | None = None
    routed: dict | None = None


# ─── 1. Normalize ───


def _stay_stage(guest: dict | None, today: date) -> str:
    """arrival | in_stay | departure | unknown, from the guest's own dates."""
    if not guest:
        return "unknown"
    try:
        cin = date.fromisoformat(str(guest.get("checkIn"))[:10])
        cout = date.fromisoformat(str(guest.get("checkOut"))[:10])
    except (TypeError, ValueError):
        return "unknown"
    if today == cin:
        return "arrival"
    if today == cout:
        return "departure"
    if cin < today < cout:
        return "in_stay"
    return "unknown"


def normalize(cur: Any, hotel: Any, p: IntakeIn) -> dict:
    """The room facts the rest of the pipeline and the row need."""
    out: dict[str, Any] = {"room": None, "floor": None, "room_type": None, "verified_room": True, "flags": dict(p.flags)}
    room = (p.room or "").strip()[:64] or None
    out["room"] = room
    guest = None
    if room:
        reg = rooms_mod.get_room(cur, hotel.slug, room)
        if reg is not None:
            out["floor"] = reg.get("floor") or rooms_mod.infer_floor(room)
            out["room_type"] = reg.get("room_type_code")
            if reg.get("service_state") in ("ooo", "oos"):
                out["flags"]["ooo_room"] = reg["service_state"]
        else:
            out["floor"] = rooms_mod.infer_floor(room)
            if rooms_mod.has_register(cur, hotel.slug):
                # Not in the register: filed anyway (never lose a guest's
                # request), marked unverified for the front office.
                out["verified_room"] = False
                out["flags"]["unverified_room"] = True
        # Optional context, never a blocker: inside a savepoint, because a
        # failed query would otherwise abort the caller's whole transaction.
        guest = state.guarded(cur, "guest_context", guests_api.guest_context_for_room, cur, hotel.slug, room)
    out["guest"] = guest
    out["stay_stage"] = _stay_stage(guest, htime.local_date(hotel))
    out["shift_key"] = config.shift_key(cur, hotel, clock.now())
    return out


# ─── 2. Idempotency and semantic merge ───


def _by_key(cur: Any, hotel_slug: str, key: str) -> dict | None:
    cur.execute("SELECT * FROM staff_requests WHERE hotel_slug = %s AND idempotency_key = %s", (hotel_slug, key))
    r = cur.fetchone()
    return dict(r) if r else None


def _by_merged_key(cur: Any, hotel_slug: str, key: str) -> dict | None:
    """A keyed request that merged into another room's open one left its key
    on the "merged" feed event, not on a row. WHY look there: a tool executor
    replaying the same key must get the same request back, and must not
    append its words to the description a second time (0130's partial
    index keeps this a point lookup)."""
    cur.execute(
        """SELECT r.* FROM svc_request_events e JOIN staff_requests r ON r.id = e.request_id
           WHERE e.hotel_slug = %s AND e.kind = 'merged' AND e.payload->>'idempotency_key' = %s
           ORDER BY e.id LIMIT 1""",
        (hotel_slug, key),
    )
    r = cur.fetchone()
    return dict(r) if r else None


def _merge_target(cur: Any, hotel: Any, room: str | None, type_key: str, window_min: int) -> dict | None:
    """The same room and request type, still open, filed in the window."""
    if not room or type_key in NEVER_MERGE or window_min <= 0:
        return None
    since = clock.now_ms() - window_min * 60_000
    cur.execute(
        """
        SELECT * FROM staff_requests
        WHERE hotel_slug = %s AND room = %s AND request_type_key = %s AND "timestamp" >= %s
          AND state = ANY(%s)
        ORDER BY "timestamp" DESC LIMIT 1 FOR UPDATE
        """,
        (hotel.slug, room, type_key, since, list(C.OPEN_STATES)),
    )
    r = cur.fetchone()
    return dict(r) if r else None


def _merge(cur: Any, target: dict, p: IntakeIn, text: str, actor: state.Actor) -> dict:
    """Append the new words and quantity to the open request; the guest
    hears "already on its way" instead of staff getting a duplicate card."""
    note = (text or "").strip()
    desc = target.get("description") or ""
    if note and note.lower() not in desc.lower():
        desc = f"{desc}\n+ {note}"[:DESCRIPTION_MAX]
    qty = target.get("quantity")
    if p.quantity:
        qty = (qty or 0) + int(p.quantity)
    cur.execute(
        """
        UPDATE staff_requests SET description = %s, quantity = %s, merged_count = merged_count + 1,
               version = version + 1
        WHERE id = %s RETURNING *
        """,
        (desc, qty, target["id"]),
    )
    row = dict(cur.fetchone())
    payload: dict[str, Any] = {"note": note[:500], "quantity": p.quantity, "source": p.source}
    if p.idempotency_key:
        payload["idempotency_key"] = p.idempotency_key[:200]  # _by_merged_key finds the replay here
    state.record_event(cur, row, "merged", actor, payload=payload)
    state.publish_changed(cur, row, "merged")
    return row


# ─── 3. Rate limit (in the database) ───


# Stand-ins a channel files when it does not know the room (the phone
# fallback's "unknown"). They pool unrelated callers under one "room", so a
# per-room limit on them would refuse the 6th stranger in 10 minutes (every
# caller during a fire). They are counted like a room-less request: never.
PLACEHOLDER_ROOMS = frozenset({"unknown", "n/a", "na", "none", "null", "-", "?", "tbd"})

# Request types that are life safety whatever their configured severity.
RATE_EXEMPT_TYPES = frozenset({"emergency", "security"})


def check_rate(cur: Any, hotel_slug: str, room: str | None, limit: int) -> None:
    """Guest-channel requests for one room in the last 10 minutes. Counted
    from staff_requests itself, so every instance sees the same number."""
    if not room or room.strip().lower() in PLACEHOLDER_ROOMS:
        return
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM staff_requests
        WHERE hotel_slug = %s AND room = %s AND "timestamp" >= %s AND source = ANY(%s)
        """,
        (hotel_slug, room, clock.now_ms() - RATE_WINDOW_MS, sorted(C.GUEST_SOURCES)),
    )
    if int(cur.fetchone()["n"]) >= limit:
        raise ClefsError(
            429,
            f"Room {room} has sent {limit} requests in the last 10 minutes. Please call the front desk.",
            "rate_limited",
        )


def _rate_exempt(p: IntakeIn, res: dict) -> str | None:
    """Why this request skips the room limit, or None. The limit exists to
    stop a room spamming the desk; it must never refuse an emergency, so a
    caller that already knows it is one (the voice fallback's L1, the
    concierge's raise_emergency) is waved through before any counting."""
    if res["request_type_key"] in RATE_EXEMPT_TYPES:
        return "life_safety_type"
    if res["type_info"].get("default_severity") == "L1" or p.severity_hint == "L1":
        return "l1"
    if p.sensitivity_hint == "emergency":
        return "emergency_hint"
    return None


def _lexicon_emergency(cur: Any, hotel: Any, p: IntakeIn, original: str) -> bool:
    """The deterministic safety lexicons only (no LLM, no translation): the
    last look before a 429, so "my husband collapsed" filed as "other" by a
    room that already sent five requests still reaches a person. Runs only
    when the room is over its limit, so the hot path pays nothing."""
    from clefs.modules.service import api as service_api  # api imports this module

    try:
        verdict = service_api.classify_safety(original, language=p.language, hotel_slug=hotel.slug, use_llm=False, cur=cur)
    except Exception:  # a classifier fault must not turn a 429 into a 500
        log.exception("lexicon check before rate limit failed for %s", hotel.slug)
        return False
    return (verdict or {}).get("level") == "emergency"


# ─── 4. Translate ───


def _needs_translation(text: str, language: str | None) -> bool:
    lang = (language or "en").lower()[:2]
    if lang == "en" or not text.strip():
        return False
    return not (lang in NON_LATIN and text.isascii())


def translate(hotel: Any, text: str, language: str | None, timeout_s: float) -> tuple[str, bool | None]:
    """(english_text, translated). translated is None when no translation
    was needed, False when it failed or timed out (staff then see the
    guest's own words, flagged untranslated)."""
    if not _needs_translation(text, language):
        return text, None
    try:
        out = ai.text(
            "service.translate",
            system=(
                "Translate the hotel guest's request into plain English for hotel staff. "
                "Keep room numbers, quantities and names exactly. Output only the translation."
            ),
            input=text,
            hotel_slug=hotel.slug,
            max_output_tokens=400,
            timeout_s=timeout_s,
        ).strip()
    except Exception as exc:  # AIDisabled, AIError, a timeout: never block a request
        log.info("translation skipped for %s: %s", hotel.slug, type(exc).__name__)
        return text, False
    return (out[:DESCRIPTION_MAX] or text), bool(out)


# ─── 5. Safety ───

_LEVEL_RANK = {lvl: i for i, lvl in enumerate(("emergency", "sensitive", "handoff", "none"))}


def classify(cur: Any, hotel: Any, p: IntakeIn, original: str, text_en: str, type_info: dict, use_llm: bool) -> dict:
    """{level, kind}: the classifier's verdict combined with what the caller
    and the request type already knew. Combining keeps the more severe level
    (never downgrade, 03 spec 0.5)."""
    from clefs.modules.service import api as service_api  # api imports this module

    verdict = {"level": "none", "kind": None}
    try:
        verdict = service_api.classify_safety(
            original, language=p.language, text_en=text_en if text_en != original else None,
            hotel_slug=hotel.slug, use_llm=use_llm, cur=cur,
        ) or verdict
    except Exception:  # the classifier failing must not lose the request
        log.exception("classify_safety failed for %s", hotel.slug)
    candidates = [(verdict.get("level") or "none", verdict.get("kind"))]
    if p.sensitivity_hint in _LEVEL_RANK:
        candidates.append((p.sensitivity_hint, p.sensitive_kind_hint))
    if type_info.get("sensitive"):
        candidates.append(("sensitive", type_info.get("key")))
    if type_info.get("key") == "emergency":
        candidates.append(("emergency", p.sensitive_kind_hint or "emergency"))
    level, kind = min(candidates, key=lambda c: _LEVEL_RANK.get(c[0], 3))
    return {"level": level, "kind": kind if level != "none" else None}


def _severity(type_info: dict, safety: dict, hint: str | None) -> str:
    """Most severe of the type default, the caller's hint and the safety
    verdict (emergency -> L1, sensitive or handoff -> at least L2)."""
    levels = [type_info.get("default_severity") or "L3"]
    if hint in C.SEVERITIES:
        levels.append(hint)
    if safety["level"] == "emergency":
        levels.append("L1")
    elif safety["level"] in ("sensitive", "handoff"):
        levels.append("L2")
    return min(levels)  # "L1" < "L2" < "L3"


# ─── 6-7. Priority and resolution ───


def resolve(cur: Any, hotel: Any, p: IntakeIn, text: str) -> dict:
    """Request type, department, legacy type/category for the old clients."""
    type_key, known = config.resolve_request_type(cur, hotel.slug, type=p.type, description=text, key=p.request_type_key)
    info = config.request_type(cur, hotel.slug, type_key)
    dept = info.get("department_key") if known else None
    if dept is None:
        # An unknown type routes by the model's category, as the legacy
        # auto-assigner did, and lands at the front office last.
        dept = config.department_for(cur, hotel.slug, p.category) or "front_office"
    dep = config.department(cur, hotel.slug, dept)
    category = p.category
    if not category or category in ("Clefs Request", "Aria Request"):
        category = priority.CATEGORY_DEPARTMENT.get(p.type or "") or dep.get("legacy_category") or "General"
    # The legacy `type` column keeps the caller's legacy string ("Towel",
    # "Maintenance" even when refined to ac); a v2 key passed as the type
    # ("make_up_room") is shown as its legacy label ("Cleaning").
    given = (p.type or "").strip()
    legacy_type = given if given and given.lower().replace(" ", "_") not in config.request_types(cur, hotel.slug) else ""
    if given and not legacy_type and given.lower().replace(" ", "_") != type_key:
        legacy_type = config.request_type(cur, hotel.slug, given.lower().replace(" ", "_")).get("legacy_type") or ""
    return {
        "request_type_key": type_key,
        "type_info": {**info, "key": type_key},
        "department_key": dept,
        "legacy_type": (legacy_type or info.get("legacy_type") or given or "Other")[:100],
        "category": category[:100],
    }


def prioritize(hotel: Any, p: IntakeIn, text: str, res: dict, severity: str, guest: dict | None, heat_c: float) -> tuple[str, str, dict]:
    """The legacy heuristic (pinned by the golden contract), the caller's
    hint when higher, then the three v2 rules."""
    tier = (guest or {}).get("loyaltyTier")
    smart = priority.calculate_priority(res["legacy_type"], text, res["category"], tier)
    prio = priority.final_priority(p.priority_hint, smart)
    return priority.apply_rules(
        prio, severity, request_type_key=res["request_type_key"], hotel=hotel, at=clock.now(), guest=guest,
        heat_threshold_c=heat_c,
    )


# ─── 8. Persist, route, token ───


def mint_token(cur: Any, hotel_slug: str, request_id: int) -> str:
    """22 random characters for the guest's status link; only the SHA-256
    is stored, so a database leak cannot be turned into tracker links."""
    token = secrets.token_urlsafe(16)
    cur.execute(
        "INSERT INTO svc_guest_tokens (token_hash, hotel_slug, request_id, expires_at) VALUES (%s, %s, %s, %s)",
        (hashlib.sha256(token.encode()).digest(), hotel_slug, request_id, clock.now() + TOKEN_TTL),
    )
    return token


def token_request(cur: Any, hotel_slug: str, token: str) -> int | None:
    """The request a live tracker token belongs to (None: unknown or expired)."""
    if not token or len(token) > 64:
        return None
    cur.execute(
        "SELECT request_id FROM svc_guest_tokens WHERE token_hash = %s AND hotel_slug = %s AND expires_at > %s",
        (hashlib.sha256(token.encode()).digest(), hotel_slug, clock.now()),
    )
    r = cur.fetchone()
    return int(r["request_id"]) if r else None


_INSERT_COLUMNS = (
    "hotel_slug", "room", "type", "subtype", "description", "category", "priority", "status", "timestamp",
    "assigned_to", "accepted_at", "resolved_at", "intake_version", "state", "source", "channel_ref",
    "idempotency_key", "guest_ref", "language", "original_text", "translated", "floor", "room_type", "stay_stage",
    "location_slug", "department_key", "request_type_key", "amenity_item_id", "quantity", "severity",
    "sensitivity", "sensitive_kind", "assigned_staff_id", "assigned_at", "scheduled_for", "ladder_started_at",
    "ack_due_at", "resolve_due_at", "sla_policy_ref", "shift_key", "created_by_staff_id", "created_by_user_id",
    "completed_by_staff_id", "resolution_code", "verified_room", "flags", "mood", "frustration", "session_id",
    "needs_proof",
)


def _insert(cur: Any, values: dict) -> dict | None:
    """INSERT ... ON CONFLICT DO NOTHING on the idempotency index, so two
    instances racing on one key file one row (None: the other one won)."""
    cols = list(_INSERT_COLUMNS)
    params = [Json(values.get(c)) if c == "flags" else values.get(c) for c in cols]
    cur.execute(
        f"""
        INSERT INTO staff_requests ({', '.join(f'"{c}"' if c == 'timestamp' else c for c in cols)})
        VALUES ({', '.join(['%s'] * len(cols))})
        ON CONFLICT (hotel_slug, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        RETURNING *
        """,
        params,
    )
    r = cur.fetchone()
    return dict(r) if r else None


def _legacy_initial(cur: Any, hotel_slug: str, p: IntakeIn) -> dict:
    """The old POST /requests could file a row already in progress (with a
    typed assignee) or already done: keep that, mapped onto v2 states."""
    status = p.legacy_status or "new"
    if status not in ("new", "progress", "completed"):
        status = "new"
    out: dict[str, Any] = {"status": status, "state": {"new": "new", "progress": "assigned", "completed": "completed"}[status],
                           "assigned_to": p.legacy_assigned_to, "accepted_at": p.legacy_accepted_at,
                           "resolved_at": p.legacy_resolved_at, "assigned_at": p.legacy_accepted_at}
    if p.legacy_assigned_to:
        cur.execute("SELECT id FROM staff_members WHERE hotel_slug = %s AND name = %s", (hotel_slug, p.legacy_assigned_to))
        r = cur.fetchone()
        out["assigned_staff_id"] = r["id"] if r else None
        if status == "completed":
            out["completed_by_staff_id"] = out["assigned_staff_id"]
    return out


# ─── create_request() ───


def _apply_amenity(cur: Any, hotel_slug: str, p: IntakeIn) -> None:
    """Canon 8: an amenity item (amn_items) resolved through amenities.api
    gives the request type and a readable description when the caller sent
    only the item and a quantity. A missing item changes nothing."""
    item = state.guarded(cur, "amenity_item", amenities_api.resolve_item, cur, hotel_slug, p.amenity_item_id)
    if not item:
        return
    p.request_type_key = p.request_type_key or item.get("request_type_key")
    name = item.get("name_en") or item.get("name") or "item"
    if not (p.description or "").strip():
        p.description = f"{p.quantity or 1} x {name}"


def create_request(cur: Any, hotel_slug: str, p: IntakeIn, *, actor: state.Actor | None = None) -> IntakeOut:
    """File one request through every step. Joins the caller's transaction:
    events and SSE go out only if it commits."""
    actor = actor or state.SYSTEM
    hotel = get_hotel(cur, hotel_slug)
    s = svc_settings.load(cur, hotel_slug)
    if p.amenity_item_id:
        _apply_amenity(cur, hotel_slug, p)
    original = (p.description or "").strip()[:DESCRIPTION_MAX] or "Service request"
    norm = normalize(cur, hotel, p)

    # 2. idempotency: a replay returns the first outcome
    if p.idempotency_key:
        key = p.idempotency_key[:200]
        prior = _by_key(cur, hotel_slug, key)
        merged_into = None if prior is not None else _by_merged_key(cur, hotel_slug, key)
        if prior is not None or merged_into is not None:
            hit = prior or merged_into
            return IntakeOut(row=hit, replayed=True, merged=merged_into is not None,
                             tracking_token=mint_token(cur, hotel_slug, hit["id"]))
    res = resolve(cur, hotel, p, original)
    if p.allow_merge:
        target = _merge_target(cur, hotel, norm["room"], res["request_type_key"], s.merge_window_min)
        if target is not None:
            row = _merge(cur, target, p, original, actor)
            return IntakeOut(row=row, merged=True, tracking_token=mint_token(cur, hotel_slug, row["id"]))

    # 3. rate limit. Life safety is never refused: a known emergency skips
    # the count, and an over-limit request gets one lexicon look for an
    # emergency in the guest's words before it is turned away. A request let
    # through over the limit is flagged so the desk can see why.
    limited = p.rate_limit if p.rate_limit is not None else p.source in C.GUEST_SOURCES
    if limited and _rate_exempt(p, res) is None:
        try:
            check_rate(cur, hotel_slug, norm["room"], s.guest_room_limit)
        except ClefsError as exc:
            if exc.code != "rate_limited" or not _lexicon_emergency(cur, hotel, p, original):
                raise
            norm["flags"]["over_rate_limit"] = True

    # 4-6. translate, safety, priority
    text_en, translated = (original, None)
    if p.translate and s.translate:
        text_en, translated = translate(hotel, original, p.language, s.translate_timeout_s)
    if translated is False:
        norm["flags"]["untranslated"] = True
    safety = classify(cur, hotel, p, original, text_en, res["type_info"], s.safety_llm)
    severity = _severity(res["type_info"], safety, p.severity_hint)
    prio, severity, rule_flags = prioritize(hotel, p, text_en, res, severity, norm["guest"], s.heat_threshold_c)
    norm["flags"].update(rule_flags)
    return _persist(cur, hotel, p, actor, norm, res, original, text_en, translated, safety, prio, severity)


def _persist(
    cur: Any, hotel: Any, p: IntakeIn, actor: state.Actor, norm: dict, res: dict, original: str, text_en: str,
    translated: bool | None, safety: dict, prio: str, severity: str,
) -> IntakeOut:
    slug = hotel.slug
    now_ms = clock.now_ms()
    ts = p.timestamp if isinstance(p.timestamp, int) and 0 < p.timestamp < 2**53 else now_ms
    scheduled = p.scheduled_for if (p.scheduled_for or 0) > now_ms else None
    given = (p.original_text or "").strip()[:DESCRIPTION_MAX] or None
    if given is not None and translated is None:
        # The caller translated (create_job): keep the guest's words beside
        # the English the staff app and the console read.
        original, translated = given, (True if given.lower() != text_en.strip().lower() else None)
    frustration = None
    if p.frustration is not None:
        try:
            frustration = min(1.0, max(0.0, float(p.frustration)))
        except (TypeError, ValueError):
            frustration = None
    values: dict[str, Any] = {
        # the legacy column is NOT NULL: a request with no room (a lobby
        # location, a caller that does not know it) is stored as ""
        "hotel_slug": slug, "room": norm["room"] or "", "type": res["legacy_type"],
        "subtype": (p.subtype or None) and p.subtype[:100],
        "description": text_en, "category": res["category"], "priority": prio, "status": "new", "timestamp": ts,
        "intake_version": 2, "state": "scheduled" if scheduled else "new", "source": p.source[:40],
        "channel_ref": (p.channel_ref or None) and p.channel_ref[:200],
        "idempotency_key": (p.idempotency_key or None) and p.idempotency_key[:200], "guest_ref": p.guest_ref,
        "language": (p.language or None) and p.language[:10],
        "original_text": original if translated is not None else None, "translated": translated,
        "floor": norm["floor"], "room_type": norm["room_type"], "stay_stage": norm["stay_stage"],
        "location_slug": p.location_slug, "department_key": res["department_key"],
        "request_type_key": res["request_type_key"], "amenity_item_id": p.amenity_item_id, "quantity": p.quantity,
        "severity": severity, "sensitivity": safety["level"], "sensitive_kind": safety["kind"],
        "scheduled_for": scheduled, "ladder_started_at": scheduled or now_ms, "shift_key": norm["shift_key"],
        "created_by_staff_id": p.created_by_staff_id, "created_by_user_id": p.created_by_user_id,
        "verified_room": norm["verified_room"], "flags": norm["flags"],
        "mood": (p.mood or None) and str(p.mood)[:20], "frustration": frustration,
        "session_id": (p.session_id or None) and str(p.session_id)[:80],
        "needs_proof": bool(res["type_info"].get("needs_proof")),
    }
    legacy_route = p.legacy_status not in (None, "new") or bool(p.legacy_assigned_to)
    if legacy_route:
        values.update(_legacy_initial(cur, slug, p))
    values.update(state.sla_fields(cur, hotel, values, start_ms=scheduled or now_ms))
    row = _insert(cur, values)
    if row is None:  # a concurrent writer filed the same idempotency key first
        prior = _by_key(cur, slug, values["idempotency_key"])
        return IntakeOut(row=prior, replayed=True, tracking_token=mint_token(cur, slug, prior["id"]))
    state.record_event(cur, row, "created", actor, to_state=row["state"],
                       payload={"source": row["source"], "severity": severity, "sensitivity": safety["level"]})
    routed = None
    if p.route and row["state"] == "new" and not legacy_route:
        routed = routing.assign(cur, slug, row["id"], actor=actor)
        if routed is not None:
            row = routed["row"]
    if p.amenity_item_id and p.quantity:
        # the amenity ledger is analytics, never a blocker
        state.guarded(cur, "amenity_link", amenities_api.link_request, cur, slug, request_id=row["id"],
                      amenity_item_id=p.amenity_item_id, qty=p.quantity)
    if safety["level"] in ("emergency", "sensitive"):
        _open_incident(cur, row, safety, actor)
    if severity == "L1":
        state.guarded(cur, "l1_signal", _l1_signal, cur, hotel, row)
    from clefs.modules.service import escalation, sla  # sla reads what intake wrote; imported where it is used

    if escalation.is_urgent(row) and row.get("state") in C.UNACKED_STATES:
        # Safety first (05 spec): an emergency or sensitive request pages
        # Security, the duty manager and (L1) the GM now, in this transaction,
        # not at the next minute's sweep. svc_escalations' unique key keeps
        # the sweep from paging the same step twice.
        if state.guarded(cur, "page_now", sla.page_now, cur, slug, row["id"]) is not None:
            row = state.load(cur, slug, row["id"]) or row
    if row.get("request_type_key") == "complaint":
        # 03 spec 1.5: every complaint opens a LEARN recovery case. Guarded:
        # the case is follow-up work, never a reason to lose the request.
        state.guarded(cur, "recovery_case", _open_recovery_case, cur, row, actor)
    token = mint_token(cur, slug, row["id"])
    _announce(cur, row, p.announce)
    audit_mod.audit(cur, actor.principal, "service.request.create", "staff_request", row["id"],
                    after={k: row.get(k) for k in ("room", "type", "state", "status", "priority", "severity", "source",
                                                  "department_key", "assigned_to")},
                    meta={"sensitivity": safety["level"], "pool": (routed or {}).get("choice", {}).get("pool")},
                    hotel_slug=slug)
    return IntakeOut(row=row, tracking_token=token, routed=(routed or {}).get("choice"))


# ─── 9. Legacy events, incidents, signals ───

LEGACY_KEYS = ("id", "hotel_slug", "room", "type", "subtype", "description", "category", "priority", "status",
               "timestamp", "assigned_to", "accepted_at", "resolved_at")


def legacy_view(row: dict) -> dict:
    """The 13-column shape the old dashboards and the golden contract know."""
    return {k: row.get(k) for k in LEGACY_KEYS}


def _announce(cur: Any, row: dict, assistant_events: bool) -> None:
    slug = row["hotel_slug"]
    events.publish(slug, "requests_changed", {}, cur=cur)
    if assistant_events:
        view = legacy_view(row)
        events.publish(slug, "new_clefs_request", view, cur=cur)
        # Dashboards deploy separately from the API: keep the pre-rebrand name.
        events.publish(slug, "new_aria_request", view, cur=cur)
    # Devices poll svc_request_events (polling first, 03 spec 0.4); no extra
    # SSE event here, so the legacy stream sees exactly the three names the
    # old dashboard knows.


def _open_recovery_case(cur: Any, row: dict, actor: state.Actor) -> None:
    # Lazy: recovery imports roster and labour, which intake never needs.
    from clefs.modules.service import recovery

    recovery.open_case(cur, row["hotel_slug"], actor=actor.principal, request_id=row["id"], reason="complaint",
                       guest_ref=row.get("guest_ref"), staff_id=actor.staff_id, user_id=actor.user_id)


def _open_incident(cur: Any, row: dict, safety: dict, actor: state.Actor) -> None:
    """Emergency and sensitive pathways open an incident beside the request
    (03 spec 1.5); the staff SOS and the incident log read the same table."""
    cur.execute(
        """
        INSERT INTO svc_incidents (hotel_slug, kind, level, severity, room, floor, request_id, summary, opened_by, opened_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (row["hotel_slug"], safety.get("kind") or safety["level"], safety["level"], row.get("severity") or "L1",
         row.get("room"), row.get("floor"), row["id"], (row.get("description") or "")[:500],
         Json({"type": actor.type, "staff_id": actor.staff_id, "user_id": actor.user_id, "device_id": actor.device_id,
               "label": actor.label}), clock.now()),
    )
    incident_id = cur.fetchone()["id"]
    cur.execute("UPDATE staff_requests SET incident_id = %s WHERE id = %s", (incident_id, row["id"]))
    row["incident_id"] = incident_id


def _l1_signal(cur: Any, hotel: Any, row: dict) -> None:
    """service.l1_incident: L1 means respond within 15 minutes and the GM
    hears at creation (the brief's daily money-at-risk section). The title
    names the type, never the guest's words (a sensitive case may be L1)."""
    from clefs.core import signals

    label = C.REQUEST_TYPES.get(row.get("request_type_key") or "", {}).get("label_en") or row.get("type") or "Request"
    signals.upsert_signal(
        cur, hotel.slug,
        signals.SignalIn(
            module="service", kind="service.l1_incident", dedupe_key=f"service.l1_incident:{row['id']}",
            title=f"L1 in room {row.get('room') or '-'}: {label}",
            section="service_risk", cadence="daily", severity=4, confidence=1.0, owner_role="gm",
            subject_type="staff_request", subject_id=str(row["id"]),
            due_at=clock.now() + timedelta(minutes=15),
        ),
    )


def token_lookup(cur: Any, token: str) -> tuple[str, int] | None:
    """(hotel_slug, request_id) for a live tracker token, from the public
    link alone (the URL carries no hotel)."""
    if not token or len(token) > 64:
        return None
    cur.execute(
        "SELECT hotel_slug, request_id FROM svc_guest_tokens WHERE token_hash = %s AND expires_at > %s",
        (hashlib.sha256(token.encode()).digest(), clock.now()),
    )
    r = cur.fetchone()
    return (r["hotel_slug"], int(r["request_id"])) if r else None
