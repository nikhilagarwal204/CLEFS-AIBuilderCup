"""
Public guest tracker (03 spec 1.7): the link a guest gets after filing a
request. The 22-character token is the only credential (128 random bits,
stored hashed), so there is no hotel in the path and no staff name in any
response.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.core.context import request_info
from clefs.core.errors import ClefsError
from clefs.modules.service import api, intake, routing, state

router = APIRouter(prefix="/public/r", tags=["service-public"])

# Generous: a guest refreshes the page; this only stops token scanning.
IP_LIMIT = 120
IP_WINDOW_S = 600


def _load(cur, token: str) -> tuple[str, dict]:
    if not ratelimit.hit(cur, f"svc:tracker:ip:{request_info().ip or 'unknown'}", limit=IP_LIMIT, window_s=IP_WINDOW_S):
        raise ClefsError(429, "Too many requests, try again shortly", "rate_limited")
    found = intake.token_lookup(cur, token)
    row = state.load(cur, found[0], found[1], lock=False) if found else None
    if row is None:
        raise ClefsError(404, "This link has expired", "not_found")
    return found[0], row


@router.get("/{token}")
def tracker(token: str) -> dict:
    with db.db() as cur:
        _, row = _load(cur, token)
        return api.guest_view(row)


class ConfirmIn(BaseModel):
    resolved: bool
    comment: str | None = Field(None, max_length=1000)


@router.post("/{token}/confirm")
def confirm(token: str, body: ConfirmIn) -> dict:
    """"Was it sorted?" Yes confirms; no reopens the request and routes it
    again (the ack clock restarts from now)."""
    with db.db() as cur:
        slug, row = _load(cur, token)
        if not api.guest_view(row)["can_confirm"]:
            raise ClefsError(409, "This request can no longer be confirmed", "cannot_confirm")
        if body.comment:
            cur.execute("UPDATE staff_requests SET guest_comment = %s WHERE id = %s", (body.comment.strip()[:1000], row["id"]))
        action = "confirm" if body.resolved else "reopen"
        out = state.transition(cur, slug, row["id"], action, state.GUEST, note=body.comment)
        if not body.resolved:
            routing.assign(cur, slug, row["id"], actor=state.GUEST)
        return api.guest_view(state.load(cur, slug, out["row"]["id"]))
