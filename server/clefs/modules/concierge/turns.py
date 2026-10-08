"""
Concierge turn log, the guest-session HTTP routes, and the module's jobs,
signals and metrics for turns (03 spec 2.4 "server double check", 2.8).

Every guest utterance reaches the server, whether or not the model called a
tool: the web client posts each turn to POST .../turns (it replaces the old
fire-and-forget logConversation), the text chat and WhatsApp record theirs
in-process. Each turn:
  - is stored in conversation_logs with its prompt_version, language and the
    consent in force, but only when the guest allowed recording
    (cc_sessions.store_transcripts); otherwise only the counters move;
  - runs service.api.classify_safety synchronously on guest words. An
    emergency with no emergency escalation in this session in the last 30 s
    makes the server file one itself (tools.raise_emergency, the same L1
    path the model uses) and answer ui:{kind:'emergency'} with the numbers,
    so a model that missed it cannot leave the guest alone.

Routes (public, bearer session token from POST /public/concierge/sessions):
  POST /public/concierge/sessions/{sid}/tools/{name}   run one tool call
  POST /public/concierge/sessions/{sid}/turns          log one turn
  POST /public/concierge/sessions/{sid}/chat           HTTP text chat
They are included by router_public (module.py lists only two routers).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.core import clock
from clefs.core.errors import ClefsError, not_found, too_many
from clefs.db import Json
from clefs.modules.concierge import sessions, tools

log = logging.getLogger("clefs.concierge.turns")

router = APIRouter(tags=["concierge-public"])

# ─── Limits ───
TURN_TEXT_MAX = 8000
TOOL_CALLS_PER_SESSION = 120      # per 10 minutes; a real conversation makes a handful
TURNS_PER_SESSION = 400           # per 10 minutes (voice logs both sides of every exchange)
CHATS_PER_SESSION = 60            # per 10 minutes
WINDOW_S = 600
# The classifier's LLM layer only ever upgrades a lexicon verdict; on guest
# turns it is worth its latency (a missed emergency is the failure mode).
TURN_SAFETY_USE_LLM = True
ROLES = {"user": "user", "guest": "user", "assistant": "assistant", "agent": "assistant"}
SAFETY_RANK = {"emergency": 0, "sensitive": 1, "handoff": 2, "none": 3}


# ─── Turn log ───


def classify(cur: Any, session: dict, text: str, language: str | None) -> dict:
    """service.api.classify_safety in a savepoint: the classifier failing
    must never lose the turn (the prompt's own rules still apply)."""
    from clefs.modules.service import api as service_api

    none = {"level": "none", "kind": None, "source": None, "confidence": 0.0}
    cur.execute("SAVEPOINT cc_safety")
    try:
        verdict = service_api.classify_safety(
            text, language=language, hotel_slug=session["hotel_slug"], use_llm=TURN_SAFETY_USE_LLM, cur=cur,
        ) or none
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT cc_safety")
        log.warning("classify_safety failed for %s: %s", session["hotel_slug"], type(exc).__name__)
        return none
    cur.execute("RELEASE SAVEPOINT cc_safety")
    return verdict if verdict.get("level") in SAFETY_RANK else none


def store_turn(cur: Any, session: dict, *, role: str, text: str, item_id: str | None, language: str | None,
               safety_level: str | None) -> bool:
    """One conversation_logs row, only with the recording consent. The legacy
    columns stay filled so the old console transcript view keeps working."""
    if not session.get("store_transcripts") or session.get("synthetic"):
        return False
    if item_id:
        cur.execute("SELECT 1 FROM conversation_logs WHERE stay_session_id = %s AND call_id = %s AND role = %s LIMIT 1",
                    (session["id"], item_id, role))
        if cur.fetchone() is not None:
            return True  # the client retried this item
    now = clock.now()
    cur.execute(
        """
        INSERT INTO conversation_logs (hotel_slug, room_number, session_id, role, message, timestamp, stay_session_id,
                                       source, call_id, language, prompt_version, safety_level, consent_ref, consents,
                                       created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session["hotel_slug"], session.get("room") or session.get("location_slug") or "", str(session["id"]), role,
            text, clock.now_ms(), session["id"], session.get("surface"), item_id, language, session.get("prompt_version"),
            safety_level, session.get("consent_ref"), Json(session.get("consents") or {}), now,
        ),
    )
    return True


def record_turn(cur: Any, session: dict, *, role: str, text: str, item_id: str | None = None,
                language: str | None = None) -> dict:
    """Log one turn and, for the guest's words, run the safety double check.
    -> {ok, stored, safety:{level, kind}, escalated, ui?, followup_instructions?}"""
    role = ROLES.get((role or "").lower(), "")
    if not role:
        raise ClefsError(400, "role must be user or assistant", "invalid_role")
    text = (text or "").strip()[:TURN_TEXT_MAX]
    lang = (language or session.get("language") or "")[:8] or None
    out: dict[str, Any] = {"ok": True, "stored": False, "safety": {"level": "none", "kind": None}, "escalated": False}
    if not text:
        return out
    verdict = classify(cur, session, text, lang) if role == "user" else None
    level = (verdict or {}).get("level") or None
    out["stored"] = store_turn(cur, session, role=role, text=text, item_id=(item_id or "")[:128] or None,
                               language=lang, safety_level=level)
    if not session.get("synthetic"):
        cur.execute(
            "UPDATE cc_sessions SET turn_count = turn_count + 1, last_seen_at = %s, "
            "last_safety_level = COALESCE(%s, last_safety_level) WHERE id = %s",
            (clock.now(), level if level and level != "none" else None, session["id"]),
        )
    if verdict is None:
        return out
    out["safety"] = {"level": verdict.get("level"), "kind": verdict.get("kind")}
    if verdict.get("level") == "emergency":
        esc = tools.raise_emergency(
            cur, session, summary=f"Safety check flagged a possible {verdict.get('kind') or 'emergency'} in the guest's words.",
            original=text, kind=verdict.get("kind"), auto=True, dry_run=session.get("surface") == "eval",
        )
        # deduped: the model (or an earlier turn) escalated within 30 s, so
        # nothing new was filed, but the card is still the right thing to show.
        out["escalated"] = not esc.get("deduped")
        out["ui"] = esc.get("ui")
        out["followup_instructions"] = esc.get("followup_instructions")
        out["request_ref"] = esc.get("request_ref")
    return out


def record_for_session(cur: Any, session_id: str, *, role: str, text: str, item_id: str | None = None,
                       language: str | None = None) -> dict:
    """record_turn() by session id (the phone's sideband worker through
    api.py). An unknown session logs nothing: {ok: False}."""
    session = sessions.get_session(cur, session_id)
    if session is None:
        return {"ok": False, "stored": False, "safety": {"level": "none", "kind": None}, "escalated": False}
    session.pop("token_hash", None)
    return record_turn(cur, session, role=role, text=text, item_id=item_id, language=language)


# ─── Routes ───


class ToolIn(BaseModel):
    call_id: str | None = Field(default=None, max_length=120)
    # The Live API hands the client an object; a JSON string is taken too.
    arguments: dict | str | None = None


class TurnIn(BaseModel):
    role: str = Field(max_length=20)
    text: str = Field(default="", max_length=TURN_TEXT_MAX * 2)
    item_id: str | None = Field(default=None, max_length=128)
    language: str | None = Field(default=None, max_length=16)


class ChatIn(BaseModel):
    message: str = Field(max_length=4000)
    language: str | None = Field(default=None, max_length=16)
    stream: bool = False


def parse_arguments(raw: dict | str | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ClefsError(400, "arguments must be a JSON object", "invalid_arguments") from None
    if not isinstance(value, dict):
        raise ClefsError(400, "arguments must be a JSON object", "invalid_arguments")
    return value


def _limit(cur: Any, key: str, limit: int) -> None:
    if not ratelimit.hit(cur, key, limit=limit, window_s=WINDOW_S):
        raise too_many()


@router.post("/public/concierge/sessions/{session_id}/tools/{name}")
def run_tool(session_id: str, name: str, body: ToolIn, authorization: str | None = Header(default=None)) -> dict:
    """Execute one function call for the session. The room is the session's;
    a repeated call_id returns the first answer (deduped: true)."""
    if name not in tools.TOOL_NAMES:
        raise not_found("Unknown tool", "unknown_tool")
    args = parse_arguments(body.arguments)
    with db.db() as cur:
        session = sessions.authenticate(cur, session_id, sessions.bearer(authorization))
        _limit(cur, f"cc:tool:{session['id']}", TOOL_CALLS_PER_SESSION)
        return tools.execute(cur, session, name, args, call_id=body.call_id)


@router.post("/public/concierge/sessions/{session_id}/turns")
def log_turn(session_id: str, body: TurnIn, authorization: str | None = Header(default=None)) -> dict:
    """Store one turn (with the recording consent) and run the safety double
    check on the guest's words."""
    with db.db() as cur:
        session = sessions.authenticate(cur, session_id, sessions.bearer(authorization))
        _limit(cur, f"cc:turn:{session['id']}", TURNS_PER_SESSION)
        return record_turn(cur, session, role=body.role, text=body.text, item_id=body.item_id, language=body.language)


STREAM_CHUNK = 48  # characters per SSE delta


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str, ensure_ascii=False)}\n\n"


def chunk_text(text: str, size: int = STREAM_CHUNK) -> list[str]:
    """Split at word boundaries into pieces of about `size` characters."""
    out, cur = [], ""
    for word in (text or "").split(" "):
        piece = word if not cur else " " + word
        if cur and len(cur) + len(piece) > size:
            out.append(cur)
            piece = " " + word
            cur = ""
        cur += piece
    if cur:
        out.append(cur)
    return out


@router.post("/public/concierge/sessions/{session_id}/chat")
def chat(session_id: str, body: ChatIn, request: Request, authorization: str | None = Header(default=None)):
    """HTTP text chat (the Live voice fallback). JSON by default; with
    stream=true or Accept: text/event-stream the reply comes as SSE:
    {type:'delta', text} pieces, then {type:'done', ...the JSON reply}.
    Tools run server-side before the first piece, so the stream is chunked
    delivery of a finished answer (a slow network shows text early)."""
    from clefs.modules.concierge import text_chat

    with db.db() as cur:
        session = sessions.authenticate(cur, session_id, sessions.bearer(authorization))
        _limit(cur, f"cc:chat:{session['id']}", CHATS_PER_SESSION)
    reply = text_chat.chat_turn(session, body.message, language=body.language)
    wants_stream = body.stream or "text/event-stream" in (request.headers.get("accept") or "")
    if not wants_stream:
        return reply

    def events():
        for piece in chunk_text(reply["text"]):
            yield _sse({"type": "delta", "text": piece})
        yield _sse({"type": "done", **reply})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


# ─── Job: concierge.unknowns_cluster (hourly) ───
# Unknown questions are clustered on insert (kb.record_unknown, fuzzy match
# against recent open clusters). This pass merges what that missed: two
# clusters opened at once, or one question asked in different words. The
# fuzzy pass always runs; when the AI is on, one structured call groups the
# remaining open titles by meaning. Merging re-points the events, so the
# 7-day count behind concierge.kb_gap is the whole cluster's.

CLUSTER_OPEN_MAX = 60


class _Groups(BaseModel):
    groups: list[list[int]] = Field(default_factory=list)


def _merge_unknowns(cur: Any, hotel_slug: str, survivor: int, others: list[int]) -> int:
    """Fold `others` into `survivor`: counts add up, samples and languages
    are unioned (5 samples at most), the events move over."""
    ids = [survivor] + [o for o in others if o != survivor]
    if len(ids) < 2:
        return 0
    cur.execute("SELECT id, count, samples, languages, first_at, last_at FROM cc_kb_unknowns "
                "WHERE hotel_slug = %s AND id = ANY(%s) AND status = 'open' FOR UPDATE", (hotel_slug, ids))
    rows = {r["id"]: dict(r) for r in cur.fetchall()}
    if survivor not in rows or len(rows) < 2:
        return 0
    merged = [rows[i] for i in ids if i in rows]
    samples: list = []
    for r in merged:
        samples += [x for x in (r["samples"] or []) if x not in samples]
    languages = sorted({lang for r in merged for lang in (r["languages"] or [])})
    cur.execute(
        "UPDATE cc_kb_unknowns SET count = %s, samples = %s, languages = %s, first_at = %s, last_at = %s WHERE id = %s",
        (sum(r["count"] for r in merged), Json(samples[:5]), languages, min(r["first_at"] for r in merged),
         max(r["last_at"] for r in merged), survivor),
    )
    gone = [r["id"] for r in merged if r["id"] != survivor]
    cur.execute("UPDATE cc_kb_unknown_events SET unknown_id = %s WHERE hotel_slug = %s AND unknown_id = ANY(%s)",
                (survivor, hotel_slug, gone))
    cur.execute("UPDATE cc_kb_unknowns SET status = 'merged', merged_into = %s WHERE id = ANY(%s)", (survivor, gone))
    return len(gone)


def _open_unknowns(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(
        "SELECT id, cluster_key, title, count FROM cc_kb_unknowns WHERE hotel_slug = %s AND status = 'open' "
        "ORDER BY count DESC, last_at DESC LIMIT %s",
        (hotel_slug, CLUSTER_OPEN_MAX),
    )
    return [dict(r) for r in cur.fetchall()]


def _ai_groups(hotel_slug: str, rows: list[dict]) -> list[list[int]]:
    from clefs import ai

    if len(rows) < 2:
        return []
    listing = "\n".join(f"{r['id']}: {r['title'][:160]}" for r in rows)
    try:
        out = ai.structured(
            "concierge.unknowns_cluster",
            system="Hotel guests asked these questions. Group the ids of questions that ask the same thing (the same "
            "answer would satisfy all of them). Leave a question out if it has no duplicate. Only use the ids given.",
            input=listing, schema=_Groups, hotel_slug=hotel_slug, max_output_tokens=800,
        )
    except Exception as exc:  # AIDisabled, AIError: the fuzzy pass stands alone
        log.info("unknowns AI clustering skipped for %s: %s", hotel_slug, type(exc).__name__)
        return []
    valid = {r["id"] for r in rows}
    return [[i for i in g if i in valid] for g in out.groups if len([i for i in g if i in valid]) > 1]


def cluster_unknowns(cur: Any, hotel_slug: str, *, use_ai: bool = True) -> dict:
    """Merge duplicate open clusters; the largest cluster of a group
    survives. Then re-check concierge.kb_gap on the survivors."""
    from rapidfuzz import fuzz

    from clefs.modules.concierge import kb

    rows = _open_unknowns(cur, hotel_slug)
    merged, survivors = 0, set()
    alive = list(rows)
    for i, a in enumerate(rows):
        if a not in alive:
            continue
        dupes = [b for b in rows[i + 1:]
                 if b in alive and fuzz.token_set_ratio(a["cluster_key"], b["cluster_key"]) >= kb.UNKNOWN_MATCH_SCORE]
        if dupes:
            merged += _merge_unknowns(cur, hotel_slug, a["id"], [b["id"] for b in dupes])
            survivors.add(a["id"])
            alive = [x for x in alive if x not in dupes]
    if use_ai:
        by_id = {r["id"]: r for r in alive}
        for group in _ai_groups(hotel_slug, alive):
            keep = max(group, key=lambda g: (by_id[g]["count"], -g))
            merged += _merge_unknowns(cur, hotel_slug, keep, group)
            survivors.add(keep)
    for uid in survivors:
        kb._maybe_kb_gap(cur, hotel_slug, uid)
    return {"open": len(rows), "merged": merged}


# ─── Job: history GC ───


def clear_chat_history(cur: Any) -> int:
    """Working memory never outlives its session (see migration 0203)."""
    cur.execute("UPDATE cc_sessions SET chat_history = NULL WHERE chat_history IS NOT NULL AND ended_at IS NOT NULL")
    return cur.rowcount


def _register_jobs() -> None:
    from clefs import jobs

    @jobs.scheduled("concierge.unknowns_cluster", jobs.every(hours=1), per_hotel=True, timeout_s=120)
    def unknowns_cluster(ctx: Any) -> dict:
        return cluster_unknowns(ctx.cur, ctx.hotel_slug)

    @jobs.scheduled("concierge.chat_history_gc", jobs.every(minutes=30), per_hotel=False, timeout_s=60)
    def chat_history_gc(ctx: Any) -> dict:
        return {"cleared": clear_chat_history(ctx.cur)}


_register_jobs()


# ─── Metrics (03 spec 2.8) ───
# Engaged guest sessions only: evals and prewarms are not guests, and a
# session nobody spoke in contains nothing. Percent metrics are 0-100.
# upsell_accept_rate and upsell_revenue are Upsell's (upsell.upsell_*),
# which owns the funnel; concierge.order_revenue is what the concierge's
# own tool calls placed.


def _register_metrics() -> None:
    from clefs.core.metrics import Dimension, Metric, register_metric

    sessions_src = "FROM cc_sessions s"
    engaged = "s.surface <> 'eval' AND COALESCE(s.mode, '') <> 'prewarm' AND (s.turn_count > 0 OR s.tool_calls > 0)"
    dims = (
        Dimension("surface", "Surface", "s.surface"),
        Dimension("language", "Language", "COALESCE(s.language, 'unknown')"),
        Dimension("verified", "Room verified", "CASE WHEN s.verified THEN 'verified' ELSE 'limited' END"),
    )
    common = dict(source=sessions_src, hotel_column="s.hotel_slug", time_column="s.created_at", time_kind="timestamptz",
                  where=engaged, dimensions=dims, permission="concierge.view")
    register_metric(Metric(key="concierge.sessions", label="Concierge conversations",
                           description="Guest conversations with the AI concierge", unit="count", measure="COUNT(*)",
                           higher_is_better=True, synonyms=("concierge chats", "ai conversations"), **common))
    register_metric(Metric(
        key="concierge.containment_rate", label="Containment rate",
        description="Conversations the concierge finished without a staff request or a hand-off", unit="percent",
        measure="100.0 * COUNT(*) FILTER (WHERE s.requests_created = 0 AND s.handoffs = 0 AND s.emergencies = 0 "
                "AND s.orders = 0) / NULLIF(COUNT(*), 0)",
        higher_is_better=True, synonyms=("self service rate", "resolved by ai"), **common))
    register_metric(Metric(
        key="concierge.unknown_rate", label="Unknown answer rate",
        description="Conversations with a question the approved knowledge could not answer", unit="percent",
        measure="100.0 * COUNT(*) FILTER (WHERE s.unknowns > 0) / NULLIF(COUNT(*), 0)",
        higher_is_better=False, synonyms=("unanswered questions",), **common))
    register_metric(Metric(
        key="concierge.handoff_rate", label="Hand-off rate",
        description="Conversations handed to a person (complaints, billing, emergencies, asked for a human)",
        unit="percent", measure="100.0 * COUNT(*) FILTER (WHERE s.handoffs > 0 OR s.emergencies > 0) / NULLIF(COUNT(*), 0)",
        higher_is_better=False, synonyms=("escalation rate",), **common))
    orders = dict(source="FROM cc_session_events ev JOIN cc_sessions s ON s.id = ev.session_id",
                  hotel_column="ev.hotel_slug", time_column="ev.at", time_kind="timestamptz", where="ev.kind = 'order'",
                  dimensions=dims[:2], permission="concierge.view")
    register_metric(Metric(key="concierge.orders", label="Concierge orders",
                           description="Food and drink orders the concierge placed", unit="count", measure="COUNT(*)",
                           higher_is_better=True, **orders))
    register_metric(Metric(
        key="concierge.order_revenue", label="Concierge order revenue",
        description="Order totals (fees included) placed through the concierge, at the time of ordering",
        unit="money", measure="SUM((ev.ref->>'total_minor')::bigint)", currency="hotel", higher_is_better=True,
        synonyms=("ai order revenue",), **orders))


_register_metrics()


# ─── Signal: concierge.upsell_performance (weekly) ───
# From the concierge's own tool events: pairings the quote suggested, and
# orders in the same session that then included one. A quiet week raises
# nothing; a week with enough suggestions and almost no takers says the
# pairings (or how they are offered) need a look.

UPSELL_KIND = "concierge.upsell_performance"
UPSELL_MIN_SHOWN = 20
UPSELL_LOW_RATE = 0.05


def upsell_week(cur: Any, hotel_slug: str, *, days: int = 7) -> dict:
    from datetime import timedelta

    since = clock.now() - timedelta(days=days)
    cur.execute(
        """
        SELECT ev.session_id, ev.kind, ev.ref FROM cc_session_events ev
        WHERE ev.hotel_slug = %s AND ev.at > %s AND ev.kind IN ('quote', 'order') ORDER BY ev.at
        """,
        (hotel_slug, since),
    )
    shown: dict[Any, set] = {}
    stats = {"shown": 0, "accepted": 0, "orders": 0, "order_minor": 0, "currency": None}
    for r in cur.fetchall():
        ref = r["ref"] or {}
        if r["kind"] == "quote":
            new = {i for i in ref.get("suggested") or [] if i} - shown.setdefault(r["session_id"], set())
            shown[r["session_id"]] |= new
            stats["shown"] += len(new)
        else:
            stats["orders"] += 1
            stats["order_minor"] += int(ref.get("total_minor") or 0)
            stats["currency"] = stats["currency"] or ref.get("currency")
            taken = shown.get(r["session_id"], set()) & set(ref.get("item_ids") or [])
            stats["accepted"] += len(taken)
            shown[r["session_id"]] = shown.get(r["session_id"], set()) - taken
    stats["rate"] = round(stats["accepted"] / stats["shown"], 4) if stats["shown"] else None
    return stats


def upsell_signal(cur: Any, hotel_slug: str) -> dict:
    from clefs.core import signals
    from clefs.core import time as hotel_time
    from clefs.core.hotels import get_hotel

    stats = upsell_week(cur, hotel_slug)
    if stats["shown"] < UPSELL_MIN_SHOWN or (stats["rate"] or 0) >= UPSELL_LOW_RATE:
        return {**stats, "signal": False}
    week = hotel_time.local_date(get_hotel(cur, hotel_slug)).isocalendar()
    signals.upsert_signal(cur, hotel_slug, signals.SignalIn(
        module="concierge", kind=UPSELL_KIND, dedupe_key=f"{UPSELL_KIND}:{week.year}-W{week.week:02d}",
        title=f"Guests took {stats['accepted']} of {stats['shown']} concierge pairing suggestions this week",
        summary="The concierge suggests a pairing with each order quote. Almost none were taken: review the pairings "
                "on the menu page (price, relevance, availability).",
        section="money_available", cadence="weekly", severity=2, confidence=0.7,
        evidence=[signals.Evidence(kind="metric", label="Suggestions shown", value=stats["shown"]),
                  signals.Evidence(kind="metric", label="Suggestions taken", value=stats["accepted"]),
                  signals.Evidence(kind="metric", label="Concierge orders", value=stats["orders"])],
    ))
    return {**stats, "signal": True}


def _register_signals() -> None:
    from clefs import jobs
    from clefs.core import signals

    signals.register_signal_kind(UPSELL_KIND, label="Concierge upsell performance", default_owner_role="gm",
                                 section="money_available", cadence="weekly")

    @jobs.scheduled(UPSELL_KIND, jobs.weekly_at(1, "07:00"), per_hotel=True, timeout_s=60)
    def upsell_performance(ctx: Any) -> dict:
        return upsell_signal(ctx.cur, ctx.hotel_slug)


_register_signals()


# ─── Personal data (Trust's retention and DSAR, 03 spec 5.3) ───
# Tool arguments carry the guest's own words (original_text, notes) and a
# typed room; unknown questions are verbatim guest text. Both follow the
# transcript class (12 months). Sessions keep counters, a hashed IP and a
# consent ref, and their working memory is cleared when they end (above).


def _register_pii() -> None:
    from clefs.core.privacy import register_pii

    register_pii("concierge", "cc_tool_calls", subject_columns={"arguments": "free_text"}, ts_column="at",
                 retention_days=365, strategy="delete")
    register_pii("concierge", "cc_kb_unknown_events", subject_columns={"text": "free_text"}, ts_column="at",
                 retention_days=365, strategy="delete")
    register_pii("concierge", "cc_sessions", subject_columns={"room": "room", "consent_ref": "subject_ref"},
                 ts_column="created_at", retention_days=365, strategy="delete")


_register_pii()

# Importing turns (router_public does) registers the WhatsApp handlers and
# their reply task too.
from clefs.modules.concierge import whatsapp as _whatsapp  # noqa: E402,F401
