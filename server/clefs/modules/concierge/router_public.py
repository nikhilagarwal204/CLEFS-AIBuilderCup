"""
Public concierge routes (guests, no login): the stay context and session
minting (03 spec 2.2, 2.3). The browser calls them through /api/clefs.

Listed in concierge/module.py MODULE.routers after legacy.router (the same
order the routes had when they were chained through it). turns.router rides
on this router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field

from clefs import db, ratelimit
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, not_found, too_many
from clefs.core.hotels import find_hotel
from clefs.modules.concierge import constants as C
from clefs.modules.concierge import context_api, sessions

router = APIRouter(tags=["concierge-public"])

# Keys an old cached guest page posts: the prompt is the server's now.
CLIENT_PROMPT_KEYS = frozenset({"session", "instructions", "tools", "tool_choice"})


class TargetIn(BaseModel):
    room: str | None = Field(default=None, max_length=24)
    location: str | None = Field(default=None, max_length=64)


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    hotel: str | None = Field(default=None, max_length=64, alias="hotel")
    hotel_slug: str | None = Field(default=None, max_length=64, alias="hotelSlug")
    target: TargetIn = TargetIn()
    k: str | None = Field(default=None, max_length=64)
    stay_token: str | None = Field(default=None, max_length=512, alias="stayToken")
    lang: str | None = Field(default=None, max_length=16)
    mode: str = "voice"
    transport: str = "live"
    guest_ref: str | None = Field(default=None, max_length=200, alias="guestRef")
    consents: dict[str, bool] | None = None


class EndIn(BaseModel):
    withdraw_consent: bool = False
    reason: str = Field(default="guest_ended", max_length=40)


def _hotel(cur: Any, slug: str | None):
    hotel = find_hotel(cur, slug or "") if slug else None
    if hotel is None or hotel.status == "archived":
        raise not_found("Hotel not found", "hotel_not_found")
    return hotel


# ─── Enumeration guard ───
# A 404 (unknown room, inactive room, unknown venue) rolls the request's
# transaction back, including any rate-limit rows inserted in it. So misses
# are checked and recorded in their own short transactions: the check runs
# before the lookup, the record commits after it fails.

TARGET_MISSES = ("room_not_found", "location_not_found")


def _miss_key(who: str) -> str:
    return f"cc:miss:ip:{who}"


def _guard_misses(who: str) -> None:
    with db.db() as cur:
        if ratelimit.remaining(cur, _miss_key(who), limit=C.TARGET_MISSES_PER_IP, window_s=C.TARGET_MISS_WINDOW_S) <= 0:
            raise too_many()


def _record_miss(who: str) -> None:
    with db.db() as cur:
        ratelimit.hit(cur, _miss_key(who), limit=C.TARGET_MISSES_PER_IP, window_s=C.TARGET_MISS_WINDOW_S)


def _who(ip: str | None) -> str:
    return sessions.ip_hash(ip or "unknown").hex()[:24]


# ─── Stay context ───


@router.get("/public/stay/{slug}/{target}/context")
def stay_context(
    slug: str,
    target: str,
    k: str | None = None,
    lang: str | None = None,
    kind: str | None = None,
    st: str | None = None,
    x_stay_token: str | None = Header(default=None),
) -> dict:
    """One room's (or venue's) page context; never another room, and the
    guest's first name only for a verified scan. `st` / X-Stay-Token: the
    stay_session_key from an earlier verified visit."""
    if kind not in (None, "room", "location"):
        raise ClefsError(400, "kind must be room or location", "invalid_kind")
    who = _who(request_info().ip)
    _guard_misses(who)
    # Per target (a guest reloading) and per address overall (walking room
    # numbers to learn which exist). Their own transaction, committed before
    # the lookup, so a 404 cannot roll the hits back.
    with db.db() as cur:
        if not (
            ratelimit.hit(cur, f"cc:ctx:ip:{who}", limit=C.CONTEXT_READS_PER_IP, window_s=C.CONTEXT_WINDOW_S)
            and ratelimit.hit(cur, f"cc:ctx:{slug[:64]}:{target[:64]}:{who}", limit=C.CONTEXT_READS_PER_IP_TARGET,
                              window_s=C.CONTEXT_WINDOW_S)
        ):
            raise too_many()
    try:
        with db.db() as cur:
            hotel = _hotel(cur, slug)
            return context_api.stay_context(cur, hotel, target, k=k, lang=lang, stay_token=x_stay_token or st, kind=kind)
    except ClefsError as exc:
        if exc.code in TARGET_MISSES:
            _record_miss(who)
        raise


# ─── Sessions ───


@router.post("/public/concierge/sessions")
def create_session(body: SessionIn) -> dict:
    """Mint a concierge session: bearer session token (2 h) plus, for
    Live voice, a single-use Gemini Live token carrying the server-built prompt."""
    if CLIENT_PROMPT_KEYS & set(body.model_extra or {}):
        raise ClefsError(409, "This page is out of date, please refresh", "client_outdated")
    ip = request_info().ip
    who = _who(ip)
    _guard_misses(who)
    try:
        with db.db() as cur:
            hotel = _hotel(cur, body.hotel or body.hotel_slug)
            return sessions.start_session(
                cur,
                hotel,
                room=body.target.room,
                location=body.target.location,
                k=body.k,
                stay_token=body.stay_token,
                language=body.lang,
                mode=body.mode,
                transport=body.transport,
                guest_ref=body.guest_ref,
                grants=body.consents,
                ip=ip,
            )
    except ClefsError as exc:
        if exc.code in TARGET_MISSES:
            _record_miss(who)
        raise


@router.post("/public/concierge/sessions/{session_id}/end")
def end_session(session_id: str, body: EndIn | None = None, authorization: str | None = Header(default=None)) -> dict:
    """End the session. withdraw_consent=true is the header's Privacy button:
    every consent this guest gave is withdrawn and no transcript is kept."""
    body = body or EndIn()
    with db.db() as cur:
        row = sessions.authenticate(cur, session_id, sessions.bearer(authorization), allow_ended=True)
        reason = "privacy_withdrawn" if body.withdraw_consent else (body.reason or "guest_ended")
        return sessions.end_session(cur, row, reason=reason, withdraw=body.withdraw_consent)


class WithdrawIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    hotel: str = Field(max_length=64)
    guest_ref: str = Field(min_length=8, max_length=200, alias="guestRef")


@router.post("/public/concierge/consent/withdraw")
def withdraw_consent(body: WithdrawIn) -> dict:
    """Session-independent withdrawal for the Privacy button (no live
    session on this page). Answers the same shape for an unknown guest_ref,
    so it reveals nothing; rate-limited per address."""
    who = _who(request_info().ip)
    with db.db() as cur:
        if not ratelimit.hit(cur, f"cc:withdraw:ip:{who}", limit=C.MINT_PER_IP_LIMIT, window_s=C.MINT_PER_IP_WINDOW_S):
            raise too_many()
    with db.db() as cur:
        hotel = _hotel(cur, body.hotel)
        return sessions.withdraw_for_guest_ref(cur, hotel.slug, body.guest_ref)


# ─── Tools, turns and text chat (turns.py) ───
# Included last: turns imports tools and text_chat, which import this
# module's siblings; importing it also registers the WhatsApp chat handler,
# the jobs, signals and metrics of the tools-turns part.
from clefs.modules.concierge import turns as _turns  # noqa: E402

router.include_router(_turns.router)
