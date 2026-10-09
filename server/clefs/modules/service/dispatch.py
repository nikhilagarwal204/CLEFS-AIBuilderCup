"""
Jobs as the guest sees them (05 spec, section 3): the bodies of
service.api.create_job, dispatch_info, guest_feedback and guest_messages,
and the public tracker's view.

create_job files one job through the intake pipeline with the concierge's
English description and the guest's own words and language (no second
translation), routes it to the best free person (routing.free_staff) and
returns dispatch_info: who is coming, in how many minutes, and the guest's
tracker token.

Instants are epoch ms, like every staff_requests instant. accepted_at is the
moment a staff member accepted the job (acknowledged_at): "Time taken" on the
guest card counts from the request to it (decision 13). The legacy
accepted_at column, the deprecated alias of assigned_at, is not it.
"""

from __future__ import annotations

from typing import Any

from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.modules.service import api, config, intake, recovery, routing, staff_lang, state

# create_job's priority -> intake's priority hint ("normal" lets the rules decide).
PRIORITY_HINT = {"normal": None, "medium": "medium", "low": "low", "high": "high", "urgent": "high"}
MOODS = ("calm", "happy", "neutral", "confused", "impatient", "annoyed", "angry", "distressed")


def _first_name(row: dict) -> str | None:
    return routing.first_name(row.get("assigned_to")) if row.get("assigned_staff_id") else None


def info(cur: Any, hotel_slug: str, row: dict, *, token: str | None) -> dict:
    """dispatch_info's shape for one request row."""
    dept = config.department(cur, hotel_slug, row.get("department_key"))
    return {
        "request_id": row["id"],
        "tracking_token": token,
        "item_label": staff_lang.item_label(row),
        "quantity": row.get("quantity"),
        "room": row.get("room"),
        "department_key": row.get("department_key"),
        "department_label": dept.get("name") or row.get("category"),
        "staff_id": row.get("assigned_staff_id"),
        "staff_first_name": _first_name(row),
        "eta_minutes": row.get("eta_minutes"),
        "eta_at": row.get("eta_at"),
        "created_at": row.get("timestamp"),
        "accepted_at": row.get("acknowledged_at"),
        "state": row.get("state"),
        "status": row.get("status"),
        "proof_status": row.get("proof_status"),
        "guest_confirmed_at": row.get("guest_confirmed_at"),
        "closed_at": row.get("closed_at"),
    }


def dispatch_info(cur: Any, hotel_slug: str, request_id: int) -> dict:
    """The request's dispatch facts with a fresh tracker token (only the
    token's hash is stored, so an earlier one cannot be read back)."""
    row = state.load(cur, hotel_slug, int(request_id))
    if row is None:
        raise ClefsError(404, "Request not found", "request_not_found")
    return info(cur, hotel_slug, row, token=intake.mint_token(cur, hotel_slug, row["id"]))


def create_job(cur: Any, hotel_slug: str, *, room: str, request_type: str, quantity: int | None, description_en: str,
               original_text: str | None, guest_language: str | None, priority: str = "normal",
               mood: str | None = None, frustration: float | None = None, session_id: str | None = None,
               guest_ref: str | None = None, channel: str = "concierge", idempotency_key: str | None = None) -> dict:
    if priority not in PRIORITY_HINT:
        raise ValueError(f"priority must be one of {tuple(PRIORITY_HINT)}")
    qty = quantity if isinstance(quantity, int) and not isinstance(quantity, bool) and 0 < quantity <= 99 else None
    key = None if idempotency_key is None else str(idempotency_key).strip()[:200] or None
    lang = staff_lang.norm_lang(guest_language) if guest_language else None
    out = intake.create_request(
        cur,
        hotel_slug,
        intake.IntakeIn(
            description=(description_en or "").strip(),
            # a concierge channel name (web, voice, ...) keeps its guest-channel
            # room limit; "concierge" itself files as web
            source=channel if channel in api.SOURCES else "web",
            room=room,
            type=request_type,
            request_type_key=request_type,
            quantity=qty,
            priority_hint=PRIORITY_HINT[priority],
            language=lang,
            guest_ref=None if guest_ref is None else str(guest_ref)[:200],
            idempotency_key=key,
            translate=False,  # the description is the concierge's English; the guest's words ride along
            original_text=original_text,
            mood=mood if mood in MOODS else None,
            frustration=frustration,
            session_id=session_id,
        ),
    )
    return {**info(cur, hotel_slug, out.row, token=out.tracking_token), "merged": out.merged, "replayed": out.replayed}


# ─── The guest's tracker ───


def tracker_view(cur: Any, hotel_slug: str, row: dict) -> dict:
    """api.guest_view plus what the 05 spec adds for the guest: who is
    coming (first name only), the ETA, when they accepted, the photo check,
    whether the guest is asked "Is everything OK?", and the messages."""
    out = api.guest_view(row)
    out.update({
        "item_label": staff_lang.item_label(row),
        "quantity": row.get("quantity"),
        "staff_first_name": _first_name(row),
        "eta_minutes": row.get("eta_minutes"),
        "eta_at": row.get("eta_at"),
        "accepted_at": row.get("acknowledged_at"),
        "proof_status": row.get("proof_status"),
        "needs_guest_confirmation": bool(out["can_confirm"]) and row.get("closed_at") is None,
        "closed_at": row.get("closed_at"),
        "messages": staff_lang.messages_for(cur, hotel_slug, row),
    })
    return out


def load_by_token(cur: Any, hotel_slug: str, tracking_token: str) -> dict:
    request_id = intake.token_request(cur, hotel_slug, tracking_token or "")
    row = state.load(cur, hotel_slug, request_id) if request_id is not None else None
    if row is None:
        raise ClefsError(404, "This link has expired", "not_found")
    return row


def feedback(cur: Any, hotel_slug: str, row: dict, *, fixed: bool, comment: str | None, checked_out: bool) -> dict:
    """"Is everything OK?" Yes confirms (and closes). "Not fixed" reopens the
    job, routes it again and flags recovery (a case with a suggested gesture
    for the duty manager); after checkout it only flags recovery, since
    nobody can fix a room the guest has left. -> the row afterwards."""
    slug = hotel_slug
    if not api.guest_view(row)["can_confirm"]:
        raise ClefsError(409, "This request can no longer be confirmed", "cannot_confirm")
    comment = (comment or "").strip()[:1000] or None
    if comment:
        cur.execute("UPDATE staff_requests SET guest_comment = %s WHERE id = %s", (comment, row["id"]))
    if fixed:
        state.transition(cur, slug, row["id"], "confirm", state.GUEST, note=comment)
        return state.load(cur, slug, row["id"])
    summary = comment or f"Guest says {staff_lang.item_label(row).lower()} is not fixed."
    if checked_out:
        cur.execute(
            "UPDATE staff_requests SET guest_confirmation = 'no', guest_confirmed_at = %s WHERE id = %s",
            (clock.now_ms(), row["id"]),
        )
        recovery.flag(cur, slug, kind="not_fixed", room=row.get("room"), summary=summary, request_id=row["id"],
                      session_id=row.get("session_id"), guest_language=row.get("language"), post_checkout=True)
        return state.load(cur, slug, row["id"])
    out = state.transition(cur, slug, row["id"], "reopen", state.GUEST, note=comment)
    routing.assign(cur, slug, row["id"], actor=state.GUEST)
    recovery.flag(cur, slug, kind="not_fixed", room=out["row"].get("room"), summary=summary, request_id=row["id"],
                  session_id=row.get("session_id"), guest_language=row.get("language"))
    reopened = state.load(cur, slug, row["id"])
    state.guarded(cur, "status_line", staff_lang.status_line, cur, reopened, "not_fixed")
    return reopened
