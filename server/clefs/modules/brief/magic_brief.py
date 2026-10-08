"""
The magic-link mobile brief (02 spec 6.4 public API; Canon 14 `/brief/[token]`).

    GET  /public/brief/{token}                           the brief the link was minted for
    POST /public/brief/{token}/actions/{id}/decide       one-tap decisions on its surfaced actions

The token is the raw clefs.auth.magic token that brief.mint_link stored
(72 h, scope {"action_ids": [...]}, also recorded in brf_links). These routes
read it WITHOUT consuming a use: POST /auth/magic/exchange (the foundation)
is what turns a use into a 12 h session cookie for the full console, and
link previewers must never burn a use. A GET here only reads.

Authority is exactly the magic session's (CONTRACTS Part 2, magic sessions):
the decision runs as a HotelCtx for a `magic` principal scoped to the link's
hotel and action ids, so core actions.decide applies its own rule (the
surfaced ids only, with the member's full role; anything else 403
not_surfaced). An expired link, an inactive member or a member who left the
hotel is 401 magic_link_invalid, the same answer as an unknown token.

Every response is no-store, noindex and no-referrer: the token is in the
path, so nothing may cache it or leak it onward.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.auth import magic, tokens
from clefs.auth import permissions as perms_mod
from clefs.auth.deps import HotelCtx, Principal, resolve_ctx
from clefs.core import actions as core_actions
from clefs.core import clock, money
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, too_many
from clefs.core.hotels import get_hotel
from clefs.modules.brief import brief

router = APIRouter(prefix="/public/brief", tags=["brief-public"])

INVALID = "This link has expired or is not valid"
RATE_LIMIT = 120  # requests per IP per 10 minutes (a morning of taps, never a token scan)
RATE_WINDOW_S = 600
NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
Decision = Literal["approve", "adjust", "assign", "dispute", "remind", "snooze", "ignore"]


# ─── Link resolution ───


def load_link(cur: Any, token: str) -> dict:
    """The live brief link behind a raw token, or 401 magic_link_invalid."""
    if not token or len(token) > 200:
        raise ClefsError(401, INVALID, "magic_link_invalid")
    cur.execute(
        """
        SELECT ml.id, ml.user_id, ml.hotel_slug, ml.scope, ml.expires_at, bl.run_id, u.name, u.is_active
        FROM magic_links ml
        JOIN brf_links bl ON bl.token_hash = ml.token_hash
        JOIN users u ON u.id = ml.user_id
        JOIN hotel_memberships m ON m.user_id = ml.user_id AND m.hotel_slug = ml.hotel_slug AND m.is_active
        WHERE ml.token_hash = %s AND ml.purpose = 'brief'
        """,
        (tokens.hash_token(token),),
    )
    link = cur.fetchone()
    if link is None or not link["is_active"] or link["expires_at"] <= clock.now():
        raise ClefsError(401, INVALID, "magic_link_invalid")
    return dict(link)


def surfaced_ids(link: dict) -> set[int]:
    raw = (link.get("scope") or {}).get(core_actions.MAGIC_SCOPE_ACTION_IDS) or []
    out: set[int] = set()
    for x in raw if isinstance(raw, list) else []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def ctx_for_link(cur: Any, link: dict) -> HotelCtx:
    """The same principal POST /auth/magic/exchange would create, without
    creating a session: magic, one hotel, the brief purposes, these ids."""
    scope = {**(link.get("scope") or {}), "hotel": link["hotel_slug"], "purposes": list(magic.SESSION_PURPOSES),
             "magic_link_id": link["id"]}
    principal = Principal(kind="user", user_id=link["user_id"], label=link["name"], session_kind="magic", scope=scope)
    return resolve_ctx(principal, link["hotel_slug"], cur=cur)


def _limit() -> None:
    """Its own transaction: a 401 rolls the request back, and a token scan
    must still be counted."""
    ip = request_info().ip or "unknown"
    with db.db() as cur:
        allowed = ratelimit.hit(cur, f"brief:public:{ip}", limit=RATE_LIMIT, window_s=RATE_WINDOW_S)
    if not allowed:
        raise too_many()


# ─── View ───

# What the one-tap page offers per state (core decide still has the last word).
_BY_STATUS: dict[str, tuple[str, ...]] = {
    "proposed": ("approve", "adjust", "assign", "dispute", "snooze", "ignore"),
    "approved": ("assign", "remind", "snooze"),
    "assigned": ("assign", "remind", "snooze"),
    "in_progress": ("remind",),
    "snoozed": ("approve", "assign", "remind"),
}


def _action_view(a: dict, hotel_ccy: str, role_perms: frozenset[str]) -> dict:
    from clefs.modules.brief import whatsapp  # the Adjust bounds convention lives with the WhatsApp flow

    t = core_actions.get_action_type(a["type"])
    perm = t.decide_perm if t else "core.actions.decide"
    bounds = whatsapp.adjust_bounds(a, hotel_ccy)
    decisions = [d for d in _BY_STATUS.get(a["status"], ())
                 if (d != "adjust" or bounds) and (d != "dispute" or str(a["type"]).startswith(("pg.", "profit")))]
    can = perm in role_perms
    return {
        "id": a["id"], "type": a["type"], "label": t.label if t else a["type"], "status": a["status"],
        "can_decide": can, "decisions": decisions if can else [],
        "adjust": None if bounds is None else {
            "field": bounds["field"], "label": bounds["label"], "currency": bounds["currency"],
            "min": money.money_str(bounds["min_minor"], bounds["currency"]),
            "max": money.money_str(bounds["max_minor"], bounds["currency"]),
        },
    }


def brief_view(cur: Any, link: dict) -> dict:
    hotel = get_hotel(cur, link["hotel_slug"])
    run = brief.get_run(cur, hotel.slug, link["run_id"])
    if run is None:
        raise ClefsError(401, INVALID, "magic_link_invalid")
    ids = surfaced_ids(link)
    cur.execute("SELECT m.role FROM hotel_memberships m WHERE m.user_id = %s AND m.hotel_slug = %s AND m.is_active",
                (link["user_id"], hotel.slug))
    role = (cur.fetchone() or {}).get("role")
    perms = perms_mod.permissions_for_role(role)
    cur.execute("SELECT * FROM actions WHERE hotel_slug = %s AND id = ANY(%s)", (hotel.slug, list(ids) or [0]))
    actions = {r["id"]: _action_view(dict(r), hotel.currency, perms) for r in cur.fetchall()}
    keep = ("item_no", "kind", "section", "section_label", "title", "impact_minor", "impact_value", "impact_estimated", "currency",
            "confidence", "urgency", "deadline_at", "owner_role", "responsible_label", "recommended_action", "action_id")

    def _item(i: dict) -> dict:
        out = {k: i.get(k) for k in keep}
        out["action"] = actions.get(i.get("action_id")) if i.get("action_id") in ids else None
        return out

    return {
        "hotel": {"slug": hotel.slug, "name": hotel.name, "timezone": hotel.timezone, "currency": hotel.currency,
                  "locale": hotel.locale},
        "user": {"name": link["name"], "role": role},
        "expires_at": link["expires_at"].isoformat(),
        "run": {
            "id": run["id"], "cadence": run["cadence"], "brief_date": run["brief_date"], "headline": run["headline"],
            "currency": run["currency"], "at_stake_minor": run.get("at_stake_minor"),
            "at_stake_value": run.get("at_stake_value"), "verified": run.get("verified"),
            "items": [_item(i) for i in run.get("items") or []],
            "overdue": [_item(i) for i in run.get("overdue") or []],
        },
    }


# ─── Routes ───


@router.get("/{token}")
def get_brief(token: str, response: Response) -> dict:
    response.headers.update(NO_STORE)
    _limit()
    with db.db() as cur:
        return brief_view(cur, load_link(cur, token))


class DecideIn(BaseModel):
    decision: Decision
    params: dict[str, Any] | None = None  # adjust: {<field>: <amount in major units>}
    note: str | None = Field(default=None, max_length=500)
    snooze_days: int | None = Field(default=None, ge=1, le=30)
    snooze_until: datetime | None = None
    assignee_user_id: int | None = None
    assignee_staff_id: int | None = None


def _adjust_patch(a: dict, hotel_ccy: str, params: dict | None) -> dict:
    """Adjust takes the one bounded field in major units (what the page
    shows); anything else is refused, so the link cannot rewrite params."""
    from decimal import Decimal, InvalidOperation

    from clefs.modules.brief import whatsapp

    b = whatsapp.adjust_bounds(a, hotel_ccy)
    if b is None:
        raise ClefsError(409, "This item is adjusted in the console", "adjust_unavailable")
    raw = (params or {}).get(b["field"], (params or {}).get("amount"))
    try:
        minor = money.to_minor(Decimal(str(raw)), b["currency"])
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ClefsError(400, f"Send {b['field']} as an amount", "invalid_amount") from exc
    if not (b["min_minor"] <= minor <= b["max_minor"]):
        raise ClefsError(400, f"{b['label'].capitalize()} must be between "
                              f"{money.money_str(b['min_minor'], b['currency'])} and "
                              f"{money.money_str(b['max_minor'], b['currency'])}", "out_of_bounds")
    return {b["field"]: minor}


@router.post("/{token}/actions/{action_id}/decide")
def post_decide(token: str, action_id: int, body: DecideIn, response: Response) -> dict:
    response.headers.update(NO_STORE)
    _limit()
    with db.db() as cur:
        link = load_link(cur, token)
        if action_id not in surfaced_ids(link):
            raise ClefsError(403, "This link does not cover that action", "not_surfaced")
        ctx = ctx_for_link(cur, link)
        kw: dict[str, Any] = {"note": body.note}
        if body.decision == "adjust":
            cur.execute("SELECT * FROM actions WHERE id = %s AND hotel_slug = %s", (action_id, link["hotel_slug"]))
            a = cur.fetchone()
            if a is None:
                raise ClefsError(404, "Action not found", "not_found")
            kw["params_patch"] = _adjust_patch(dict(a), ctx.hotel.currency, body.params)
        if body.decision == "snooze":
            if body.snooze_until is not None and body.snooze_until.tzinfo is None:
                raise ClefsError(400, "snooze_until needs a timezone offset", "invalid_snooze_until")
            kw["snooze_until"] = body.snooze_until or clock.now() + timedelta(days=body.snooze_days or 1)
        if body.decision == "assign":
            kw.update(assignee_user_id=body.assignee_user_id, assignee_staff_id=body.assignee_staff_id)
        out = core_actions.decide(cur, action_id, body.decision, actor=ctx, via="console",
                                  hotel_slug=link["hotel_slug"], **kw)
        view = _action_view(out, ctx.hotel.currency, perms_mod.permissions_for_role(ctx.role))
        return {"action": core_actions.to_api(out), "view": view}
