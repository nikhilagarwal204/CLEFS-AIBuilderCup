"""
Concierge public API (frozen signatures; other modules import only this file).

build_session() is the one place a guest-channel session (web Live voice, SIP
phone, WhatsApp, text, Trust evals) gets its instructions and tools, so the
prompt has a single source of truth on the server (03 spec 2.1). The frozen
signature takes a channel; build_surface_session() takes the richer 03 spec
arguments (surface, verified, guest, dry_run) and is what the mint route,
the phone and the eval runner use.

KB functions read the structured bilingual KB (cc_kb_* tables): only
approved entries ever reach a guest or another module.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from clefs import db
from clefs.core.errors import bad_request

Channel = Literal["web", "sip", "whatsapp", "text"]
DEFAULT_ASSISTANT_NAME = "Clefs"
HANDOFF_TEXT = "Let me connect you with our front desk team, who can help with that."


# ─── Session spec ───


class SessionSpec(BaseModel):
    hotel_slug: str
    channel: Channel
    room: str | None = None
    location: str | None = None
    language: str | None = None
    assistant_name: str
    model: str
    instructions: str
    tools: list[dict[str, Any]] = []
    voice: str | None = None
    # Added by the Concierge lane (additive; the fields above are frozen).
    # prompt_version: sha256 of the ordered prompt blocks, stored on the
    # session, the conversation turns and Trust's eval runs.
    prompt_version: str = ""
    surface: str = "web_room"
    verified: bool = False
    tool_choice: str = "auto"
    greeting_text: str = ""
    kb_mode: str = "structured"
    # dry_run: Trust's eval runner. The tool executor returns realistic
    # outputs without filing requests, orders or escalations.
    dry_run: bool = False
    kb_entry_ids: list[int] = []


class TextTurn(BaseModel):
    text: str
    tool_calls: list[dict[str, Any]] = []
    handoff: bool = False
    implemented: bool = True


# ─── Knowledge base ───


def kb_facts(cur: Any, hotel_slug: str, *, language: str = "en", keyed_only: bool = False) -> list[dict]:
    """Approved knowledge as [{source, text, fact_key, value, entry_id,
    approved_at}], in section order.

    keyed_only=True gives the approved structured facts (fact_key set:
    check-in time, parking, ...) that Visibility diffs against the web and
    Google; `value` is the normalised value ("15:00", "paid", "true"). In
    kb_mode legacy_text the hotel's old free text comes first as paragraphs
    (source "knowledge_base", no key, entry or approval). `language` picks
    the Arabic text of an entry when it has one."""
    from clefs.modules.concierge import kb

    return kb.facts(cur, hotel_slug, language=language or "en", keyed_only=keyed_only)


def propose_kb_entry(
    cur: Any,
    hotel_slug: str,
    *,
    question_en: str,
    answer_en: str,
    question_ar: str | None = None,
    answer_ar: str | None = None,
    fact_key: str | None = None,
    source: str,
    actor: Any,
) -> int:
    """Queue a KB entry for the owner's approval (Visibility's add_kb_faq).
    Returns the entry id; the entry is pending until a person approves it in
    the console. A fact_key proposal carries its value in answer_en (the
    normaliser reads "15:00", "3 pm", "paid", "yes"); an unparseable value
    raises 400 invalid_fact_value."""
    from clefs.modules.concierge import kb

    if not (answer_en or "").strip():
        raise bad_request("answer_en is required", "answer_required")
    data = {
        "kind": "fact" if fact_key else "faq",
        "question_en": question_en,
        "answer_en": answer_en,
        "question_ar": question_ar,
        "answer_ar": answer_ar,
        "fact_key": fact_key,
        "fact_value": answer_en if fact_key else None,
    }
    row = kb.create_entry(cur, hotel_slug, data, source=(source or "proposal"), actor=actor)
    return int(row["id"])


def kb_entry_status(cur: Any, hotel_slug: str, entry_id: int) -> dict | None:
    """{entry_id, status: pending|approved|rejected|archived, approved_at}
    or None when the entry is not this hotel's."""
    from clefs.modules.concierge import kb

    row = kb.get_entry(cur, hotel_slug, int(entry_id))
    if row is None:
        return None
    return {"entry_id": row["id"], "status": row["status"], "approved_at": row["approved_at"]}


# ─── Sessions ───


def build_session(
    hotel_slug: str,
    *,
    channel: Channel,
    room: str | None = None,
    location: str | None = None,
    language: str | None = None,
) -> SessionSpec:
    """The session for a channel (frozen signature). web maps to web_room,
    or web_location when only a location is given; sip to phone; text to
    text_fallback; whatsapp to whatsapp. Never personalised (verified is
    False): a caller that verified the guest uses build_surface_session."""
    from clefs.modules.concierge.constants import CHANNEL_SURFACE

    if channel not in CHANNEL_SURFACE:
        raise bad_request(f"unknown channel {channel!r}", "invalid_channel")
    surface = "web_location" if channel == "web" and location and not room else CHANNEL_SURFACE[channel]
    return build_surface_session(hotel_slug, surface=surface, room=room, location=location, language=language)


def build_surface_session(
    hotel_slug: str,
    *,
    surface: str,
    room: str | None = None,
    location: str | None = None,
    language: str | None = None,
    verified: bool = False,
    guest: dict | None = None,
    dry_run: bool = False,
    cur: Any = None,
) -> SessionSpec:
    """The 03 spec 2.1 build_session. surface is one of web_room,
    web_location, phone, text_fallback, eval, whatsapp. `guest` (the
    allowlisted guests.api.guest_context_for_room dict) personalises only a
    verified session. surface='eval' (Trust's runner) always gives a dry-run
    spec: the same prompt and tools, with dry_run=True for the executor.
    Pass `cur` to join the caller's transaction."""
    from clefs.modules.concierge import prompt

    kw = dict(surface=surface, room=room, location=location, language=language, verified=verified, guest=guest, dry_run=dry_run)
    if cur is not None:
        return prompt.build_spec(cur, hotel_slug, **kw)
    with db.db() as own:
        return prompt.build_spec(own, hotel_slug, **kw)


def open_session(
    cur: Any,
    spec: SessionSpec,
    *,
    key_status: str = "extension",
    call_ref: str | None = None,
    transport: str = "sip",
    consents: dict[str, bool] | None = None,
    with_token: bool = False,
) -> dict:
    """Store a cc_sessions row for a spec built outside the guest mint: a
    phone call (voice binds it to the call; transport sip, key_status
    extension) or a Trust eval run (surface eval, transport none). Tool
    calls, quotes and dedupe then work as on the web. Returns {session_id,
    expires_at, prompt_version, session_token (only with_token)}."""
    from clefs.modules.concierge import live, sessions

    if transport not in ("live", "http", "sip", "none"):
        raise bad_request("transport must be live, http, sip or none", "invalid_transport")
    if transport == "sip" and not spec.dry_run:
        # The phone's session mint: the same readiness gate as the web mint
        # (409 concierge_not_live). Voice checks is_live() before answering,
        # but the gate belongs to the concierge, so a caller that skips that
        # check (or a switch-off racing the call) still cannot bind a live
        # phone session. Evals (transport none, dry run) never need it.
        live.require_live(cur, spec.hotel_slug)
    row, token = sessions.create_session(
        cur, spec, key_status=key_status, mode="voice" if transport == "sip" else "text", transport=transport,
        consents=consents, call_ref=call_ref, with_token=with_token,
    )
    return {"session_id": str(row["id"]), "expires_at": row["expires_at"], "prompt_version": row["prompt_version"], "session_token": token}


# ─── Tool execution (the one executor, 03 spec 0.3) ───


def run_tool(cur: Any, hotel_slug: str, *, session_ctx: dict, name: str, args: dict | str | None) -> dict:
    """Run one guest tool through the concierge executor (tools.py), the
    same code the web page's tool route, the text chat and WhatsApp use, so
    a phone call (voice MCP server or sideband worker) files, dedupes and
    refuses exactly like the web.

    session_ctx:
      session_id  the cc_sessions id bound at mint or by open_session
                  (required unless `spec` is given). The room, its
                  verification and the surface come from that row, never
                  from `args`: a `room` argument counts only on a session
                  with no room of its own, and is then unverified.
      call_id     idempotency key (the Live tool call id, or the voice ledger's
                  key). The same key on the same session returns the stored
                  first answer with deduped=True and changes nothing.
      spec        a dry-run SessionSpec (Trust's evals) with no stored
                  session: realistic answers, nothing filed.

    Returns the speakable result dict {status, followup_instructions, ui?,
    deduped?, ...}. Never raises for a bad session, a foreign hotel, an
    unknown tool or another module's refusal: each becomes a status the
    model can say (session_ended, unknown_tool, error, rate_limited)."""
    import json

    from clefs.modules.concierge import tools

    ctx = session_ctx if isinstance(session_ctx, dict) else {}
    if isinstance(args, str):
        # A model's raw arguments string: bad JSON runs with no arguments,
        # so the executor's own "room is missing" style answer is spoken.
        try:
            args = json.loads(args or "{}")
        except ValueError:
            args = {}
    args = args if isinstance(args, dict) else {}
    spec = ctx.get("spec")
    if spec is not None and not ctx.get("session_id"):
        if not getattr(spec, "dry_run", False) or getattr(spec, "hotel_slug", None) != hotel_slug:
            # A live spec with no session row would file requests nobody can
            # trace back to a session: only Trust's dry run may skip the row.
            return tools.result("session_ended", "Apologise and suggest the guest calls the front desk directly.")
        return tools.execute(cur, tools.synthetic_session(spec), name, args, dry_run=True)
    session_id = str(ctx.get("session_id") or "")
    call_id = ctx.get("call_id")
    return tools.execute_for_session(cur, session_id, name, args, call_id=str(call_id) if call_id else None,
                                     hotel_slug=hotel_slug)


# ─── Text turns ───


def respond_text(spec: SessionSpec, messages: list[dict]) -> TextTurn:
    """One text turn (WhatsApp, text fallback, Trust evals). The Concierge
    lane's text part implements text_chat.respond_text; until it exists (or
    if it is absent in this build) every turn hands off to a person."""
    import importlib

    try:
        text_chat = importlib.import_module("clefs.modules.concierge.text_chat")
    except ModuleNotFoundError as exc:
        if exc.name != "clefs.modules.concierge.text_chat":
            raise
        return TextTurn(text=HANDOFF_TEXT, handoff=True, implemented=False)
    fn = getattr(text_chat, "respond_text", None)
    if fn is None:
        return TextTurn(text=HANDOFF_TEXT, handoff=True, implemented=False)
    return fn(spec, messages)


# ─── Go-live (readiness gate) ───


def is_live(cur: Any, hotel_slug: str) -> bool:
    """Whether guest conversations may start for this hotel (concierge.live,
    gated by trust.api.readiness when switched on; see live.py for how
    hotels that were already serving guests are grandfathered). The phone
    webhook and WhatsApp check it before taking a conversation."""
    from clefs.modules.concierge import live

    return live.is_live(cur, hotel_slug)


def go_live_demo(cur: Any, hotel_slug: str, *, actor: Any) -> dict:
    """Switch the concierge ON for a synthetic demo hotel (hotels.status =
    'demo') without Trust's readiness gate, for the demo seed. Returns
    {live, source: "demo", overridden: False}; raises 409 not_demo_hotel for
    any other hotel, which must go live through the gated PUT /live."""
    from clefs.modules.concierge import live

    return live.set_demo_live(cur, hotel_slug, actor=actor)


# ─── Emergency numbers ───


def emergency_numbers(country: str | None) -> list[dict]:
    """The public emergency numbers of a country (ISO code, any case), most
    used first, as [{number, kind}]: constants.EMERGENCY_NUMBERS, the list
    the guest prompt and the EmergencyCard read (JP: 119 fire_ambulance,
    110 police, 118 coast_guard; AE: 999 police, 998 ambulance, 997
    civil_defence, 996 coast_guard, ...). An unknown country has none: the
    caller then says "the local emergency services". Voice reads its dial
    sequence from here, so a phone call names the same numbers as the web."""
    from clefs.modules.concierge import constants as C

    return [{"number": n, "kind": k} for n, k in C.EMERGENCY_NUMBERS.get((country or "").strip().upper(), ())]
