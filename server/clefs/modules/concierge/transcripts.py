"""
Concierge transcripts for the console (03 spec 2.8 and 5.2): what guests
said to the concierge and what it answered, grouped into conversations.

Source: conversation_logs, written only with the guest's recording consent
(turns.store_turn for web, text and WhatsApp; the voice sideband worker for
the phone) and purged after 12 months by Trust. Legacy rows (the old guest
page's own POST) have no stay_session_id and group by their client
session_id like everything else.

Masking is server-side, by role, through clefs.core.pii (field class
"transcript"): owner, gm and front desk get the words, a department head
only their own department's (a conversation belongs to no department, so
none), every other role (finance, revenue, a custom role granted
concierge.view) gets the same rows with the text set to null. The list
preview also shortens phone numbers and emails even for full readers, so a
screen seen over a shoulder never shows a whole number; the words appear in
full only inside one conversation.

Routes (included into router.py, so under /hotels/{slug}/concierge):
  GET /transcripts                  conversations, newest first, keyset paged
  GET /transcripts/{conversation}   one conversation's turns and tool calls
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import pii
from clefs.core.errors import bad_request, not_found

router = APIRouter()
VIEW = require_perm("concierge.view")

LIST_MAX = 100
TURNS_MAX = 1000
TOOLS_MAX = 200
PREVIEW_CHARS = 140
SOURCES = ("web_room", "web_location", "phone", "text_fallback", "whatsapp", "legacy")
# Most severe first (service.api.SAFETY_LEVELS); a conversation shows its worst.
SAFETY_ORDER = ("emergency", "sensitive", "handoff", "none")

LIST_FIELDS = {"conversations.preview": pii.TRANSCRIPT}
DETAIL_FIELDS = {"conversation.preview": pii.TRANSCRIPT, "turns.message": pii.TRANSCRIPT}

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"\+?\d[\d\s-]{7,}\d")


# ─── Helpers ───


def redact_contact(text: str | None) -> str:
    """Shorten emails and phone numbers inside guest-typed text (the list
    preview only): 'jo***@mail.com', '+97******67'."""
    out = EMAIL_RE.sub(lambda m: f"{m.group(0)[:2]}***@{m.group(0).split('@')[1]}", text or "")
    return PHONE_RE.sub(lambda m: f"{m.group(0)[:3]}******{m.group(0)[-2:]}", out)


def at(ms: Any) -> datetime | None:
    """conversation_logs.timestamp (epoch ms, legacy) as a UTC datetime."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def worst(levels: list[str] | None) -> str:
    seen = set(levels or [])
    return next((lvl for lvl in SAFETY_ORDER if lvl in seen), "none")


def encode_cursor(last_ms: int, conversation_id: str) -> str:
    return f"{int(last_ms)}:{conversation_id}"


def decode_cursor(raw: str | None) -> tuple[int, str] | None:
    """'<last_ms>:<conversation id>' (the id may itself hold ':'). A bad
    cursor starts from the top rather than failing the page."""
    ms, sep, cid = (raw or "").partition(":")
    # 18 digits stays inside BIGINT: a forged huge number must not 500.
    if not sep or not ms.isdigit() or len(ms) > 18:
        return None
    return int(ms), cid


def summary(row: dict, preview: str | None) -> dict:
    return {
        "id": row["conversation_id"],
        "session_id": str(row["stay_session_id"]) if row.get("stay_session_id") else None,
        "room": row.get("room") or None,
        "source": row.get("source") or "legacy",
        "language": row.get("language"),
        "started_at": at(row.get("first_ms")),
        "last_at": at(row.get("last_ms")),
        "turns": int(row.get("turns") or 0),
        "guest_turns": int(row.get("guest_turns") or 0),
        "safety_level": worst(row.get("levels")),
        "preview": (preview or "")[:PREVIEW_CHARS] or None,
    }


# ─── Queries ───

GROUP_COLUMNS = """
    session_id AS conversation_id,
    MIN(room_number) AS room,
    MIN(COALESCE(source, 'legacy')) AS source,
    MIN(language) FILTER (WHERE role = 'user') AS language,
    MIN(timestamp) AS first_ms,
    MAX(timestamp) AS last_ms,
    COUNT(*) AS turns,
    COUNT(*) FILTER (WHERE role = 'user') AS guest_turns,
    array_agg(DISTINCT safety_level) FILTER (WHERE safety_level IS NOT NULL) AS levels,
    (array_agg(stay_session_id) FILTER (WHERE stay_session_id IS NOT NULL))[1] AS stay_session_id
"""


def list_conversations(cur: Any, hotel_slug: str, *, room: str | None = None, source: str | None = None,
                       flagged: bool = False, before: str | None = None, limit: int = 50) -> dict:
    """{conversations: [summary], next_before}. Unmasked: the route masks.
    One page is at most LIST_MAX conversations; the preview is each one's
    first guest message with contacts shortened."""
    where, params = ["hotel_slug = %s"], [hotel_slug]
    if room:
        where.append("room_number = %s")
        params.append(room)
    if source:
        where.append("COALESCE(source, 'legacy') = %s")
        params.append(source)
    having, hparams = [], []
    cursor = decode_cursor(before)
    if cursor is not None:
        having.append("(MAX(timestamp), session_id) < (%s, %s)")
        hparams.extend(cursor)
    if flagged:
        having.append("bool_or(safety_level IN ('emergency', 'sensitive', 'handoff'))")
    limit = max(1, min(int(limit), LIST_MAX))
    cur.execute(
        f"""
        SELECT {GROUP_COLUMNS} FROM conversation_logs WHERE {' AND '.join(where)}
        GROUP BY session_id {('HAVING ' + ' AND '.join(having)) if having else ''}
        ORDER BY MAX(timestamp) DESC, session_id DESC LIMIT %s
        """,
        (*params, *hparams, limit + 1),
    )
    rows = [dict(r) for r in cur.fetchall()]
    more = len(rows) > limit
    rows = rows[:limit]
    previews = first_guest_messages(cur, hotel_slug, [r["conversation_id"] for r in rows])
    out = [summary(r, redact_contact(previews.get(r["conversation_id"]))) for r in rows]
    last = rows[-1] if more and rows else None
    return {"conversations": out, "next_before": encode_cursor(last["last_ms"], last["conversation_id"]) if last else None}


def first_guest_messages(cur: Any, hotel_slug: str, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    cur.execute(
        """
        SELECT DISTINCT ON (session_id) session_id, message FROM conversation_logs
        WHERE hotel_slug = %s AND session_id = ANY(%s) AND role = 'user'
        ORDER BY session_id, timestamp, id
        """,
        (hotel_slug, ids),
    )
    return {r["session_id"]: r["message"] for r in cur.fetchall()}


def get_conversation(cur: Any, hotel_slug: str, conversation_id: str) -> dict | None:
    """{conversation, turns, tools} or None. Unmasked: the route masks.
    tools are this session's concierge tool calls (name, outcome and the
    request or order they filed), never their arguments: those hold the
    guest's words too and the turns already show them to who may see."""
    cur.execute(
        f"SELECT {GROUP_COLUMNS} FROM conversation_logs WHERE hotel_slug = %s AND session_id = %s GROUP BY session_id",
        (hotel_slug, conversation_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    row = dict(row)
    cur.execute(
        """
        SELECT id, role, message, language, safety_level, source, call_id AS item_id, timestamp
        FROM conversation_logs WHERE hotel_slug = %s AND session_id = %s ORDER BY timestamp, id LIMIT %s
        """,
        (hotel_slug, conversation_id, TURNS_MAX),
    )
    turns = [{**{k: v for k, v in dict(t).items() if k != "timestamp"}, "at": at(t["timestamp"])} for t in cur.fetchall()]
    first_user = next((t["message"] for t in turns if t["role"] == "user"), None)
    return {"conversation": {**summary(row, first_user), **session_meta(cur, hotel_slug, row.get("stay_session_id"))},
            "turns": turns, "tools": tool_calls(cur, hotel_slug, row.get("stay_session_id"))}


def session_meta(cur: Any, hotel_slug: str, session_id: Any) -> dict:
    """The cc_sessions facts staff need to judge a conversation (surface,
    whether the room was verified, the prompt version it ran on)."""
    empty = {"surface": None, "verified": None, "prompt_version": None, "ended_at": None, "end_reason": None}
    if not session_id:
        return empty
    cur.execute(
        "SELECT surface, verified, prompt_version, ended_at, end_reason FROM cc_sessions WHERE id = %s AND hotel_slug = %s",
        (session_id, hotel_slug),
    )
    found = cur.fetchone()
    return {**empty, **dict(found)} if found else empty


def tool_calls(cur: Any, hotel_slug: str, session_id: Any) -> list[dict]:
    if not session_id:
        return []
    cur.execute(
        """
        SELECT name, status, result->>'status' AS outcome, result->>'request_ref' AS request_ref,
               result->>'order_ref' AS order_ref, at
        FROM cc_tool_calls WHERE session_id = %s AND hotel_slug = %s ORDER BY id LIMIT %s
        """,
        (session_id, hotel_slug, TOOLS_MAX),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── Routes ───


def _visibility(ctx: HotelCtx) -> str:
    """What the viewer may read of the words: the pii matrix cell for a
    conversation, which belongs to no department (so "own department"
    means none). The client shows a banner from this instead of guessing
    the matrix itself."""
    policy = pii.policy_for(pii.TRANSCRIPT, pii.role_for_ctx(ctx), departments=getattr(ctx, "departments", ()) or ())
    return "full" if policy == pii.FULL else "none"


@router.get("/transcripts")
def transcripts_list(
    slug: str,
    room: str | None = Query(None, max_length=40),
    source: str | None = Query(None, max_length=20),
    flagged: bool = False,
    before: str | None = Query(None, max_length=300),
    limit: int = Query(50, ge=1, le=LIST_MAX),
    ctx: HotelCtx = Depends(VIEW),
) -> dict:
    """{conversations: [{id, session_id, room, source, language, started_at,
    last_at, turns, guest_turns, safety_level, preview}], next_before,
    visibility: full|none}. Newest first; pass next_before back as
    `before` for the next page. preview is null for a role the pii matrix
    denies (and for a conversation with no guest turn)."""
    if source is not None and source not in SOURCES:
        raise bad_request(f"source must be one of {', '.join(SOURCES)}", "invalid_source")
    with db.db(readonly=True) as cur:
        page = list_conversations(cur, slug, room=(room or "").strip() or None, source=source, flagged=flagged,
                                  before=before, limit=limit)
    return {**pii.mask_for_ctx(page, ctx, field_classes=LIST_FIELDS), "visibility": _visibility(ctx)}


@router.get("/transcripts/{conversation_id}")
def transcripts_detail(slug: str, conversation_id: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """{conversation: {..summary, surface, verified, prompt_version,
    ended_at, end_reason}, turns: [{id, role, message, language,
    safety_level, source, item_id, at}], tools: [{name, status, outcome,
    request_ref, order_ref, at}], visibility}. Messages are null for a role
    the pii matrix denies. 404 conversation_not_found."""
    with db.db(readonly=True) as cur:
        found = get_conversation(cur, slug, conversation_id[:200])
    if found is None:
        raise not_found("Conversation not found", "conversation_not_found")
    return {**pii.mask_for_ctx(found, ctx, field_classes=DETAIL_FIELDS), "visibility": _visibility(ctx)}
