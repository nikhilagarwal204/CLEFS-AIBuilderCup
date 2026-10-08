"""
In-room phone AI console routes (03 spec 3.6). Every path lives under
/hotels/{slug}/voice/ (Canon 13); the webhook and the MCP server are on
their own routers under /webhooks/voice/ (webhook.py, mcp_server.py).

- Lines: CRUD, and the X-Clefs-Line secret, shown once on create/rotate.
- Extensions: single rows, a CSV import, and the "extension = room" rule.
- The setup panel: the last 20 webhook events and a readiness checklist.
- The call log, with transcripts masked by role (pii class transcript).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import hotels
from clefs.core import pii
from clefs.core.errors import bad_request, not_found
from clefs.settings import get_settings
from clefs.modules.voice import calls, lines
from clefs.modules.voice import constants as C

router = APIRouter(prefix="/hotels/{slug}/voice", tags=["voice"])
VIEW = require_perm("voice.view", "voice.manage")
MANAGE = require_perm("voice.manage")
WEBHOOK_PATH = "/webhooks/voice/incoming"


def _obj(body: Any) -> dict:
    if not isinstance(body, dict):
        raise bad_request("body must be a JSON object", "invalid_body")
    return body


# ─── Overview ───


@router.get("/status")
def voice_status(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The setup checklist's server half: what is configured, and the URLs
    the SIP trunk's webhook and the PBX trunk need."""
    s = get_settings()
    origin = s.PUBLIC_API_ORIGIN.rstrip("/")
    with db.db() as cur:
        module_on = hotels.module_enabled(cur, slug, "voice")
        rows = lines.list_lines(cur, slug)
    return {
        "voice_enabled": bool(s.VOICE_ENABLED),
        "module_enabled": module_on,
        "webhook_secret_set": bool(s.VOICE_WEBHOOK_SECRET),
        "api_key_set": bool(s.GEMINI_API_KEY),
        "webhook_url": origin + WEBHOOK_PATH,
        "mcp_url": calls.mcp_server_url(),
        "sip_host": C.BRIDGE_SIP_HOST,
        "reject_code": C.REJECT_CODE,
        "line_header": "X-Clefs-Line",
        "pbx_kinds": list(C.PBX_KINDS),
        "lines": len(rows),
        "lines_enabled": sum(1 for r in rows if r.get("enabled")),
    }


# ─── Lines ───


@router.get("/lines")
def list_lines(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        rows = lines.list_lines(cur, slug)
    return {"lines": [lines.public_line(r, extensions=int(r.pop("extension_count", 0) or 0)) for r in rows]}


@router.post("/lines", status_code=201)
def create_line(slug: str, body: Any = Body(...), ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """201 {line, secret}. The secret is the X-Clefs-Line value; it is shown
    this once and only its sha256 is stored."""
    with db.db() as cur:
        row, secret = lines.create_line(cur, slug, _obj(body))
        out = lines.public_line(row, extensions=0)
        audit(cur, ctx, "voice.line_created", "vc_line", row["id"], after=out, hotel_slug=slug)
    return {"line": out, "secret": secret}


@router.get("/lines/{line_id}")
def get_line(slug: str, line_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        row = lines.get_line(cur, slug, line_id)
        cur.execute("SELECT count(*) AS n FROM vc_extensions WHERE voice_line_id = %s", (line_id,))
        n = int(cur.fetchone()["n"])
    return {"line": lines.public_line(row, extensions=n)}


@router.patch("/lines/{line_id}")
def update_line(slug: str, line_id: int, body: Any = Body(...), ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before, after = lines.update_line(cur, slug, line_id, _obj(body))
        b, a = lines.public_line(before), lines.public_line(after)
        if b != a:
            audit(cur, ctx, "voice.line_updated", "vc_line", line_id, before=b, after=a, hotel_slug=slug)
    return {"line": a}


@router.delete("/lines/{line_id}", status_code=204)
def delete_line(slug: str, line_id: int, ctx: HotelCtx = Depends(MANAGE)) -> None:
    with db.db() as cur:
        row = lines.delete_line(cur, slug, line_id)
        audit(cur, ctx, "voice.line_deleted", "vc_line", line_id, before=lines.public_line(row), hotel_slug=slug)


@router.post("/lines/{line_id}/rotate-secret")
def rotate_secret(slug: str, line_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """{line, secret}. The old secret stops working at once: calls carrying
    it get 486 bad_line_secret until the PBX has the new one."""
    with db.db() as cur:
        row, secret = lines.rotate_secret(cur, slug, line_id)
        audit(cur, ctx, "voice.line_secret_rotated", "vc_line", line_id,
              meta={"hint": row.get("line_secret_hint")}, hotel_slug=slug)
    return {"line": lines.public_line(row), "secret": secret}


# ─── Extensions ───


@router.get("/lines/{line_id}/extensions")
def list_extensions(slug: str, line_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        rows = lines.list_extensions(cur, slug, line_id)
    return {"extensions": rows, "count": len(rows)}


@router.post("/lines/{line_id}/extensions", status_code=201)
def add_extension(slug: str, line_id: int, body: Any = Body(...), ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """{extension, room} or {extension, kind: location, location}. Upserts
    on (line, extension)."""
    with db.db() as cur:
        row = lines.add_extension(cur, slug, line_id, _obj(body))
        audit(cur, ctx, "voice.extension_saved", "vc_extension", row["id"],
              after={k: row.get(k) for k in ("extension", "room", "location_slug", "kind")}, hotel_slug=slug)
    return {"extension": row}


@router.delete("/lines/{line_id}/extensions/{extension_id}", status_code=204)
def delete_extension(slug: str, line_id: int, extension_id: int, ctx: HotelCtx = Depends(MANAGE)) -> None:
    with db.db() as cur:
        row = lines.delete_extension(cur, slug, line_id, extension_id)
        audit(cur, ctx, "voice.extension_deleted", "vc_extension", extension_id,
              before={k: row.get(k) for k in ("extension", "room", "location_slug", "kind")}, hotel_slug=slug)


@router.post("/lines/{line_id}/extensions/import")
def import_extensions(slug: str, line_id: int, body: Any = Body(...), ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """{csv: "extension,room[,kind[,location]]\\n...", replace?: bool}.
    replace drops the line's earlier CSV rows first (manual and rule rows
    stay). {imported, skipped:[{line, extension, reason}], skipped_count}."""
    data = _obj(body)
    text = data.get("csv")
    if not isinstance(text, str) or not text.strip():
        raise bad_request("csv text is required", "csv_required")
    if len(text) > 512 * 1024:
        raise bad_request("csv is larger than 512 KB", "csv_too_large")
    with db.db() as cur:
        out = lines.import_csv(cur, slug, line_id, text, replace=bool(data.get("replace")))
        audit(cur, ctx, "voice.extensions_imported", "vc_line", line_id,
              meta={"imported": out["imported"], "skipped": out["skipped_count"], "replace": bool(data.get("replace"))},
              hotel_slug=slug)
    return out


@router.post("/lines/{line_id}/extensions/rule")
def apply_rule(slug: str, line_id: int, body: Any = Body(...), ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """{prefix?, suffix?, strip_room_prefix?, use_room_extension?, replace?,
    dry_run?}: extension = prefix + room + suffix for every room in the
    register. dry_run returns {rule, rooms, would_map, preview} only."""
    data = _obj(body)
    dry = bool(data.get("dry_run"))
    with db.db() as cur:
        out = lines.apply_rule(cur, slug, line_id, data, replace=bool(data.get("replace")), dry_run=dry)
        if not dry:
            audit(cur, ctx, "voice.extension_rule_applied", "vc_line", line_id,
                  meta={"rule": out["rule"], "created": out["created"], "updated": out["updated"]}, hotel_slug=slug)
    return out


# ─── Webhook event log ───


@router.get("/webhook-events")
def webhook_events(slug: str, limit: int = Query(20, ge=1, le=100), ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The setup panel's "last 20 webhook events": this hotel's receipts,
    plus unclaimed calls to one of its lines' SIP trunks (a PBX that
    dials without the header shows up here as 'unidentified')."""
    with db.db() as cur:
        cur.execute(
            "SELECT DISTINCT trunk_id FROM vc_lines WHERE hotel_slug = %s AND trunk_id IS NOT NULL",
            (slug,),
        )
        projects = [r["trunk_id"] for r in cur.fetchall()]
        cur.execute(
            """
            SELECT webhook_id, event_type, call_id, voice_line_id, project_id, outcome, detail, received_at
            FROM vc_webhook_receipts
            WHERE hotel_slug = %s OR (hotel_slug IS NULL AND project_id = ANY(%s))
            ORDER BY received_at DESC, webhook_id LIMIT %s
            """,
            (slug, projects, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"events": rows}


# ─── Call log ───

CALL_FIELDS = {"sip_headers_redacted": pii.GUEST_CONTACT}
TRANSCRIPT_FIELDS = {"transcript.message": pii.TRANSCRIPT}
CALL_COLUMNS = (
    "id, voice_line_id, provider_call_id, status, reject_code, reject_reason, from_user, extension_matched, room, "
    "location_slug, room_confirmed_by, tool_transport, language, prompt_version, model, started_at, accepted_at, "
    "ended_at, duration_s, end_reason, transferred, transfer_target, requests_created, emergencies, sip_headers_redacted"
)


@router.get("/calls")
def list_calls(
    slug: str,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, ge=1),
    line_id: int | None = Query(None, ge=1),
    status: str | None = Query(None, max_length=20),
    room: str | None = Query(None, max_length=24),
    ctx: HotelCtx = Depends(VIEW),
) -> dict:
    """Newest first, keyset-paged by id ({calls, next_before_id})."""
    where, params = ["hotel_slug = %s"], [slug]
    if before_id:
        where.append("id < %s")
        params.append(before_id)
    if line_id:
        where.append("voice_line_id = %s")
        params.append(line_id)
    if status:
        where.append("status = %s")
        params.append(status)
    if room:
        where.append("room = %s")
        params.append(room.strip())
    with db.db() as cur:
        cur.execute(
            f"SELECT {CALL_COLUMNS} FROM vc_calls WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s",
            (*params, limit + 1),
        )
        rows = [dict(r) for r in cur.fetchall()]
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "calls": [pii.mask_for_ctx(r, ctx, field_classes=CALL_FIELDS) for r in rows],
        "next_before_id": rows[-1]["id"] if more and rows else None,
    }


@router.get("/calls/{call_id}")
def get_call(slug: str, call_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """{call, transcript:[{role, message, safety_level, at}], tools:[...]}.
    The transcript follows the pii matrix: owner, gm and front desk see it,
    everyone else sees the turns with message null."""
    with db.db() as cur:
        cur.execute(f"SELECT {CALL_COLUMNS} FROM vc_calls WHERE id = %s AND hotel_slug = %s", (call_id, slug))
        row = cur.fetchone()
        if row is None:
            raise not_found("Call not found", "call_not_found")
        call = dict(row)
        cur.execute(
            """
            SELECT role, message, safety_level, created_at AS at FROM conversation_logs
            WHERE hotel_slug = %s AND call_id = %s AND source = 'phone' ORDER BY id LIMIT 500
            """,
            (slug, call["provider_call_id"]),
        )
        transcript = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """
            SELECT name, transport, status, result->>'status' AS outcome, result->>'request_ref' AS request_ref, created_at
            FROM vc_tool_calls WHERE call_row_id = %s ORDER BY id LIMIT 200
            """,
            (call_id,),
        )
        tools = [dict(r) for r in cur.fetchall()]
    masked = pii.mask_for_ctx({"call": call, "transcript": transcript}, ctx,
                              field_classes={"call.sip_headers_redacted": pii.GUEST_CONTACT, **TRANSCRIPT_FIELDS})
    return {**masked, "tools": tools}
