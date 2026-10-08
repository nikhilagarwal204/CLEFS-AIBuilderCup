"""
The Owner Brief on WhatsApp (02 spec 6.2, Canon: WhatsApp).

Everything goes out through clefs.notify (its outbox paces 6 s per recipient,
enforces the 24 h window, dedupes webhooks and handles Meta's error codes);
Brief owns only the conversation:

    07:00  owner_brief_v1 (UTILITY template, from the platform number)
           header "Clefs brief · 1 Oct", body: count, money at stake, 5 lines
           buttons  [Approve #1]  [Review items]  [Open brief -> /brief/{token}]
    tap    opens the 24 h window, so the replies are session messages:
           Review items -> a list (<= 5 rows: act:{id}:open)
           a row        -> reply buttons Approve / Assign / More
           More         -> a list: Adjust, Dispute, Remind, Snooze 1d/7d, Ignore
           Adjust       -> "Reply with a rate between AED 380 and 520" (session)
           Dispute      -> the packet PDF as a DOCUMENT message

Every button id is clefs.auth.tokens.sign_ref("<ref>") around a recipient-bound
nonce (nonce.py); state-changing taps are single-use through core
actions.decide(nonce=...). Decisions run as the tapping member's HotelCtx,
so role permissions and the autonomy dial apply exactly as in the console
(via="whatsapp"). Each tap gets at most one reply (service messages are
billable from 2026-10-01; 3 or fewer per interaction).

Opt-in: brf_wa_contacts. A member who opted out (console, or STOP on
WhatsApp) gets the brief by email; START resumes it.

Numbers: users.phone_e164 is a global identity (magic links, inbound sender
mapping), so Brief never stores a typed number. A member proves their own
by sending "CLEFS <code>" from it (brf_phone_verifications); a manager can
never give anyone a number.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from clefs import db, notify
from clefs.audit import audit
from clefs.auth import tokens
from clefs.auth.deps import HotelCtx, Principal, require_perm, resolve_ctx
from clefs.core import actions as core_actions
from clefs.core import clock, money
from clefs.core.errors import ClefsError
from clefs.core.hotels import Hotel, get_hotel
from clefs.modules.brief import brief, nonce, render, templates

log = logging.getLogger("clefs.brief.whatsapp")

PURPOSE_REPLY = "brief.wa_reply"
SESSION_TTL = timedelta(minutes=30)
LIST_ROWS = 5  # 02 spec 6.2: up to 5 rows in the "Review items" list
ROW_TITLE_MAX = 24
ROW_DESC_MAX = 72
BUTTON_TITLE_MAX = 20
STOP_WORDS = frozenset({"STOP", "UNSUBSCRIBE", "STOP BRIEF"})
START_WORDS = frozenset({"START", "SUBSCRIBE", "START BRIEF"})
IGNORE_REASONS: tuple[tuple[str, str], ...] = (
    ("wrong", "Not accurate"),
    ("done", "Already handled"),
    ("small", "Not worth it"),
    ("other", "Other reason"),
)
MORE_MENU: tuple[tuple[str, str, str], ...] = (
    ("adjust", "Adjust", "Change the amount or rate, then approve"),
    ("dispute", "Dispute", "Open a dispute and get the evidence pack"),
    ("remind", "Remind", "Nudge whoever holds it"),
    ("snooze1", "Snooze 1 day", "Bring it back tomorrow"),
    ("snooze7", "Snooze 7 days", "Bring it back next week"),
    ("ignore", "Ignore", "Dismiss it with a reason"),
)


# ─── Contacts and opt-in ───
# The number is the member's own account phone (users.phone_e164): the
# foundation's inbound resolver maps a WhatsApp sender to a user by it, so a
# brief sent anywhere else could not be answered. A member with no row is
# treated as opted in by their account (they gave Clefs the number and the
# hotel's brief settings name WhatsApp); an explicit opt-out always wins.

BRIEF_ROLES = ("owner", "gm", "finance", "revenue", "dept_head")


def _contact_api(r: dict) -> dict:
    # An explicit opt-out outranks everything; otherwise no number means nothing can be sent.
    if r.get("opted_out_at"):
        state = "opted_out"
    elif not r.get("phone_e164"):
        state = "no_phone"
    else:
        state = "opted_in" if r.get("opted_in_at") else "implicit"
    return {
        "user_id": r["user_id"], "name": r.get("name"), "role": r.get("role"),
        "phone_masked": _mask(r.get("phone_e164")), "has_phone": bool(r.get("phone_e164")), "state": state,
        "opted_in_at": r["opted_in_at"].isoformat() if r.get("opted_in_at") else None,
        "opt_in_method": r.get("opt_in_method"),
        "opted_out_at": r["opted_out_at"].isoformat() if r.get("opted_out_at") else None,
        "opt_out_method": r.get("opt_out_method"),
    }


def _mask(phone: str | None) -> str | None:
    from clefs.core import privacy

    return privacy.mask_phone(phone) if phone else None


def list_contacts(cur: Any, hotel_slug: str) -> list[dict]:
    """Every active member who can receive the brief, with their WhatsApp state."""
    cur.execute(
        """
        SELECT m.user_id, u.name, m.role, u.phone_e164, c.opted_in_at, c.opt_in_method, c.opted_out_at, c.opt_out_method
        FROM hotel_memberships m JOIN users u ON u.id = m.user_id
        LEFT JOIN brf_wa_contacts c ON c.hotel_slug = m.hotel_slug AND c.user_id = m.user_id
        WHERE m.hotel_slug = %s AND m.is_active AND u.is_active AND m.role = ANY(%s)
        ORDER BY m.id
        """,
        (hotel_slug, list(BRIEF_ROLES)),
    )
    return [_contact_api(dict(r)) for r in cur.fetchall()]


def get_contact(cur: Any, hotel_slug: str, user_id: int) -> dict | None:
    cur.execute("SELECT * FROM brf_wa_contacts WHERE hotel_slug = %s AND user_id = %s", (hotel_slug, user_id))
    row = cur.fetchone()
    return dict(row) if row else None


def whatsapp_allowed(cur: Any, hotel_slug: str, user_id: int) -> bool:
    c = get_contact(cur, hotel_slug, user_id)
    return not (c and c.get("opted_out_at"))


def set_opt_in(cur: Any, hotel_slug: str, user_id: int, *, opted_in: bool, method: str, actor: Any,
               phone: str | None = None) -> dict:
    """Record an opt-in or opt-out. It NEVER writes users.phone_e164: that
    number is a global identity (magic links are minted by it, inbound
    WhatsApp senders map to a user by it), so storing an unproven one would
    let whoever typed it sign in and tap buttons as that member at every
    hotel. `phone` only confirms: the account's own number passes, another
    is 409 phone_mismatch, and an account with no number is 409
    phone_unverified (the member proves one with start_verification)."""
    from clefs.core.phone import normalize_phone

    cur.execute("SELECT u.id, u.phone_e164 FROM users u JOIN hotel_memberships m ON m.user_id = u.id "
                "WHERE u.id = %s AND m.hotel_slug = %s AND m.is_active", (user_id, hotel_slug))
    user = cur.fetchone()
    if user is None:
        raise ClefsError(404, "No such member at this hotel", "member_not_found")
    number = user["phone_e164"]
    if phone:
        wanted = normalize_phone(phone)
        if not number:
            raise ClefsError(409, "Confirm the number from its WhatsApp first", "phone_unverified")
        if wanted != number:
            raise ClefsError(409, "That is not the number on this member's account", "phone_mismatch")
    if opted_in and not number:
        raise ClefsError(400, "Add a WhatsApp number first", "no_phone")
    before = get_contact(cur, hotel_slug, user_id)
    now = clock.now()
    cur.execute(
        """
        INSERT INTO brf_wa_contacts (hotel_slug, user_id, phone_e164, opted_in_at, opt_in_method, opted_out_at,
                                     opt_out_method, updated_at)
        VALUES (%(h)s, %(u)s, %(p)s, %(in_at)s, %(in_m)s, %(out_at)s, %(out_m)s, %(now)s)
        ON CONFLICT (hotel_slug, user_id) DO UPDATE SET
            phone_e164 = EXCLUDED.phone_e164,
            opted_in_at = COALESCE(EXCLUDED.opted_in_at, brf_wa_contacts.opted_in_at),
            opt_in_method = COALESCE(EXCLUDED.opt_in_method, brf_wa_contacts.opt_in_method),
            opted_out_at = EXCLUDED.opted_out_at, opt_out_method = EXCLUDED.opt_out_method, updated_at = EXCLUDED.updated_at
        """,
        {"h": hotel_slug, "u": user_id, "p": number or "", "now": now,
         "in_at": now if opted_in else None, "in_m": method if opted_in else None,
         "out_at": None if opted_in else now, "out_m": None if opted_in else method},
    )
    after = get_contact(cur, hotel_slug, user_id)
    audit(cur, actor, "brief.wa.opt_in" if opted_in else "brief.wa.opt_out", "brf_wa_contact", after["id"],
          before={k: str(v) for k, v in (before or {}).items() if k in ("opted_in_at", "opted_out_at")},
          after={"method": method}, hotel_slug=hotel_slug)
    return after


# ─── Number confirmation ───
# The only way Brief ever stores users.phone_e164. The member asks for a
# code for the number they typed (their own account only, never a manager
# for someone else), the console shows "CLEFS 123456", and they send it
# from that WhatsApp to the platform number. Meta signs the webhook and
# names the sender, so the matching message is proof they hold the number;
# no template or SMS is needed, and the message opens the 24 h window the
# brief's buttons use. Only the code's hash is stored; a wrong code from
# that number counts, and five end the request.

VERIFY_TTL = timedelta(minutes=30)
VERIFY_MAX_ATTEMPTS = 5
VERIFY_RE = re.compile(r"^\s*CLEFS[\s:#-]*(\d{6})\s*$", re.IGNORECASE)


def _code_hash(code: str) -> str:
    import hashlib

    return hashlib.sha256(f"brf-verify:{code}".encode()).hexdigest()


def platform_display_number() -> str | None:
    """Where members send the code. The foundation has no setting for the
    platform number's display form yet (FOUNDATION-REQUEST: Settings
    WA_PLATFORM_DISPLAY_NUMBER); until it does the console says "the Clefs
    WhatsApp number" and gives no wa.me link."""
    from clefs.settings import get_settings

    raw = getattr(get_settings(), "WA_PLATFORM_DISPLAY_NUMBER", None)
    return str(raw).strip() or None if raw else None


def _verification_api(row: dict | None, *, code: str | None = None) -> dict:
    if row is None:
        return {"status": "none"}
    if row.get("verified_at"):
        status = "verified"
    elif row.get("cancelled_at"):
        status = "cancelled"
    elif row["expires_at"] <= clock.now():
        status = "expired"
    else:
        status = "pending"
    out: dict[str, Any] = {"status": status, "phone_masked": _mask(row["phone_e164"]),
                           "expires_at": row["expires_at"].isoformat()}
    if code is not None:
        send_to = platform_display_number()
        digits = re.sub(r"\D", "", send_to or "")
        out.update(code=code, message=f"CLEFS {code}", send_to=send_to,
                   wa_link=f"https://wa.me/{digits}?text=CLEFS%20{code}" if digits else None)
    return out


def verification_state(cur: Any, user_id: int) -> dict:
    cur.execute("SELECT * FROM brf_phone_verifications WHERE user_id = %s ORDER BY id DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    return _verification_api(dict(row) if row else None)


def start_verification(cur: Any, hotel_slug: str, user_id: int, phone: str, *, actor: Any) -> dict:
    """A fresh code for the caller's own number (routes pass the caller's
    user_id only). Refuses a number another account holds, and a different
    number than the account already has (that is a profile change, which
    only the person or a Clefs admin makes through the team settings)."""
    import secrets

    from clefs.core.phone import normalize_phone

    wanted = normalize_phone(phone)
    cur.execute("SELECT u.id, u.phone_e164 FROM users u JOIN hotel_memberships m ON m.user_id = u.id "
                "WHERE u.id = %s AND m.hotel_slug = %s AND m.is_active AND u.is_active", (user_id, hotel_slug))
    user = cur.fetchone()
    if user is None:
        raise ClefsError(404, "No such member at this hotel", "member_not_found")
    if user["phone_e164"]:
        code = "already_verified" if user["phone_e164"] == wanted else "phone_mismatch"
        raise ClefsError(409, "Your account already has a WhatsApp number", code)
    cur.execute("SELECT 1 FROM users WHERE phone_e164 = %s AND id <> %s", (wanted, user_id))
    if cur.fetchone():
        raise ClefsError(409, "Another account uses that number", "phone_taken")
    now = clock.now()
    cur.execute("UPDATE brf_phone_verifications SET cancelled_at = %s "
                "WHERE user_id = %s AND verified_at IS NULL AND cancelled_at IS NULL", (now, user_id))
    code = f"{secrets.randbelow(1_000_000):06d}"
    cur.execute(
        """
        INSERT INTO brf_phone_verifications (hotel_slug, user_id, phone_e164, code_hash, expires_at, created_at)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (hotel_slug, user_id, wanted, _code_hash(code), now + VERIFY_TTL, now),
    )
    row = dict(cur.fetchone())
    audit(cur, actor, "brief.wa.verify_start", "brf_phone_verification", row["id"],
          after={"phone": _mask(wanted)}, hotel_slug=hotel_slug)
    return _verification_api(row, code=code)


def _open_verifications(cur: Any, sender: str) -> list[dict]:
    cur.execute(
        """
        SELECT * FROM brf_phone_verifications
        WHERE phone_e164 = %s AND verified_at IS NULL AND cancelled_at IS NULL AND expires_at > %s
        ORDER BY id DESC
        """,
        (sender, clock.now()),
    )
    return [dict(r) for r in cur.fetchall()]


def _verify_match(ctx: notify.InboundCtx) -> bool:
    """Read-only: "CLEFS <6 digits>" from a number no member holds yet, with
    an open request for exactly that number."""
    if ctx.user_id is not None or not ctx.text or not ctx.sender:
        return False
    return bool(VERIFY_RE.match(ctx.text)) and bool(_open_verifications(ctx.cur, ctx.sender))


# ─── Billing metric (02 spec 6.2: service messages are billable from 2026-10-01) ───

from clefs.core.metrics import Dimension, Metric, register_metric  # noqa: E402

register_metric(Metric(
    key="wa.service_msgs_month", label="WhatsApp service messages",
    description="Free-form WhatsApp replies Brief sent (lists, buttons, confirmations, documents): each is a "
                "billable service message from 2026-10-01; keep each tap to one reply",
    unit="count", source="FROM outbound_messages o", hotel_column="o.hotel_slug", time_column="o.created_at",
    time_kind="timestamptz", measure="COUNT(*)",
    where="o.channel = 'whatsapp' AND o.category = 'service' AND left(o.purpose, 6) = 'brief.' "
          "AND o.status IN ('sent', 'delivered', 'read')",
    dimensions=(Dimension("kind", "Message kind", "o.kind"),), permission="brief.view", higher_is_better=False,
    synonyms=("whatsapp replies", "service messages", "whatsapp billing"),
))


# ─── Channel: the daily template ───
# Registered as brief's "whatsapp" channel: fn(cur, hotel, run, rcpt, link)
# -> {ok, status, outbox_id?, error?}. A non-ok answer moves delivery on to
# the next channel (email) immediately; an accepted send is watched by
# fallback.py (no DELIVERED within 30 minutes, or a failure, -> email).


def _link_token(link: str | None) -> str:
    """The magic-link token at the end of brief's link URL: the template's
    URL button appends it to {web}/brief/, the mobile brief page."""
    if not link:
        return "expired"
    return link.rstrip("/").rsplit("/", 1)[-1] or "expired"


def _item_by_no(run: dict, item_no: int) -> dict | None:
    for it in list(run.get("items") or []) + list(run.get("overdue") or []):
        if int(it.get("item_no") or 0) == int(item_no):
            return it
    return None


def brief_buttons(cur: Any, hotel: Hotel, run: dict, user_id: int, link: str | None) -> list[dict]:
    """owner_brief_v1's three buttons, in template order. "Approve #1"
    carries item 1's action only while it still waits for a decision;
    otherwise the tap opens the list instead."""
    day = run["brief_date"]
    first = _item_by_no(run, 1)
    aid = first.get("action_id") if first and first.get("action_status") == "proposed" else None
    n_approve = nonce.mint(cur, hotel_slug=hotel.slug, user_id=user_id, verb="approve", run_id=run["id"],
                           action_id=aid, item_no=1)
    n_list = nonce.mint(cur, hotel_slug=hotel.slug, user_id=user_id, verb="list", run_id=run["id"])
    return [
        {"type": "quick_reply", "payload": tokens.sign_ref(f"brief:{day}:1:approve:{n_approve}")},
        {"type": "quick_reply", "payload": tokens.sign_ref(f"brief:{day}:list:{n_list}")},
        {"type": "url", "text": _link_token(link)},
    ]


def send_brief(cur: Any, hotel: Hotel, run: dict, rcpt: dict, link: str | None) -> dict:
    from clefs.modules.brief import api as brief_api  # module attribute: tests and other doors patch it

    phone = rcpt.get("phone_e164")
    if not phone:
        return {"ok": False, "status": "no_address"}
    if not whatsapp_allowed(cur, hotel.slug, rcpt["id"]):
        return {"ok": False, "status": "opted_out"}
    info = brief_api.wa_template_info(cur, hotel.slug, name=templates.OWNER_BRIEF, language=templates.LANG)
    if not info or info.get("status") != "APPROVED":
        return {"ok": False, "status": "template_not_approved"}
    if (info.get("category") or "UTILITY") != "UTILITY":
        # Meta filed the brief as MARKETING: sending it would bill and gate it
        # as marketing (Canon: the brief is a utility template). Email instead.
        return {"ok": False, "status": "template_recategorised"}
    v = templates.owner_brief_variables(run, hotel)
    res = notify.send_whatsapp_template(
        phone, templates.OWNER_BRIEF, templates.LANG, v["params"], hotel_slug=hotel.slug,
        purpose=f"brief.{run['cadence']}", sender="platform", header=v["header"],
        buttons=brief_buttons(cur, hotel, run, rcpt["id"], link), category="utility",
        idempotency_key=f"brief:{run['id']}:{rcpt['id']}:whatsapp", cur=cur,
    )
    if res.ok:
        cur.execute(
            """
            INSERT INTO brf_deliveries (run_id, hotel_slug, user_id, channel, outbox_id, created_at)
            VALUES (%s, %s, %s, 'whatsapp', %s, %s) ON CONFLICT (run_id, user_id, channel) DO NOTHING
            """,
            (run["id"], hotel.slug, rcpt["id"], res.outbox_id, clock.now()),
        )
        if run.get("cadence") == "weekly":
            send_weekly_report(cur, hotel, run, rcpt, link)
    return {"ok": bool(res.ok), "status": res.status, "outbox_id": res.outbox_id, "error": res.error}


brief.register_channel("whatsapp", send_brief)


# ─── Inbound: taps and replies ───
# Refs (inside sign_ref; the foundation hands over verified refs only):
#   brief:{date}:{item_no}:approve:{nonce}     template "Approve #1"
#   brief:{date}:list:{nonce}                  template "Review items"
#   act:{action_id}:{verb}:{nonce}             list rows and reply buttons
# verbs: open, more, approve, assign, assign-u{user}, assign-s{staff}, adjust,
# dispute, remind, snooze1, snooze7, ignore, ignore-{reason}.

NONCE_TEXT = {
    "unknown": "This button is no longer valid. Open today's brief in Clefs.",
    "other_recipient": "This button was sent to someone else, so it cannot be used from this number.",
    "expired": "This button has expired. Open today's brief in Clefs.",
    "mismatch": "This button is no longer valid. Open today's brief in Clefs.",
    "replayed": "Already done: this button was used.",
}


def parse_ref(ref: str | None) -> dict | None:
    parts = (ref or "").split(":")
    if len(parts) == 5 and parts[0] == "brief" and parts[2].isdigit():
        return {"kind": "brief", "day": parts[1], "item_no": int(parts[2]), "verb": parts[3], "nonce": parts[4]}
    if len(parts) == 4 and parts[0] == "brief" and parts[2] == "list":
        return {"kind": "brief", "day": parts[1], "item_no": None, "verb": "list", "nonce": parts[3]}
    if len(parts) == 4 and parts[0] == "act" and parts[1].isdigit():
        return {"kind": "act", "action_id": int(parts[1]), "verb": parts[2], "nonce": parts[3]}
    return None


def actor_for(cur: Any, user_id: int, hotel_slug: str) -> HotelCtx:
    """The tapping member as a HotelCtx at the nonce's hotel. Clefs admin
    powers are never carried over WhatsApp: a phone number is weaker proof
    than a console session."""
    cur.execute("SELECT id, name, email FROM users WHERE id = %s AND is_active", (user_id,))
    u = cur.fetchone()
    if u is None:
        raise ClefsError(403, "This account is not active", "forbidden")
    principal = Principal(kind="user", user_id=u["id"], label=u["name"], email=u["email"], session_kind="whatsapp")
    return resolve_ctx(principal, hotel_slug, cur=cur)


def _reply(ctx: notify.InboundCtx, hotel_slug: str | None, body: str, key: str = "r") -> Any:
    return notify.send_whatsapp_text(
        ctx.sender, body[:4096], hotel_slug=hotel_slug, purpose=PURPOSE_REPLY, sender="platform",
        idempotency_key=f"brief:wa:{ctx.provider_id}:{key}", cur=ctx.cur,
    )


def _interactive(ctx: notify.InboundCtx, hotel_slug: str, body: str, *, key: str = "i", **kw: Any) -> Any:
    return notify.send_whatsapp_interactive(
        ctx.sender, body[:1024], hotel_slug=hotel_slug, purpose=PURPOSE_REPLY, sender="platform",
        idempotency_key=f"brief:wa:{ctx.provider_id}:{key}", cur=ctx.cur, **kw,
    )


@notify.on_whatsapp("staff", priority=20, button_prefix="brief:")
def on_brief_tap(ctx: notify.InboundCtx) -> dict:
    return handle_tap(ctx)


@notify.on_whatsapp("staff", priority=20, button_prefix="act:")
def on_action_tap(ctx: notify.InboundCtx) -> dict:
    return handle_tap(ctx)


def handle_tap(ctx: notify.InboundCtx) -> dict:
    tap = parse_ref(ctx.button_ref)
    if tap is None:
        return {"handled": False}
    try:
        row = nonce.verify(ctx.cur, tap["nonce"], user_id=ctx.user_id, verb=tap["verb"],
                           action_id=tap.get("action_id"))
    except nonce.NonceError as exc:
        _reply(ctx, ctx.hotel_slug, NONCE_TEXT.get(exc.code, NONCE_TEXT["unknown"]))
        return {"handled": True, "outcome": exc.code}
    ctx.cur.execute("SELECT 1 FROM hotel_memberships WHERE user_id = %s AND hotel_slug = %s AND is_active",
                    (row.user_id, row.hotel_slug))
    if ctx.cur.fetchone() is None:
        # Left the hotel since the brief went out: its buttons read nothing more.
        _reply(ctx, None, "You no longer have access to this hotel's brief.")
        return {"handled": True, "outcome": "no_access"}
    hotel = get_hotel(ctx.cur, row.hotel_slug)
    verb = row.verb
    if verb == "list" or (verb == "approve" and row.action_id is None):
        return flow_list(ctx, hotel, row)
    flow = _FLOWS.get(nonce.base_verb(verb))
    if flow is None:
        return {"handled": True, "outcome": "unknown_verb"}
    return flow(ctx, hotel, row)


# ─── Flows ───


def _action(cur: Any, hotel_slug: str, action_id: int | None) -> dict | None:
    if action_id is None:
        return None
    cur.execute("SELECT * FROM actions WHERE id = %s AND hotel_slug = %s", (action_id, hotel_slug))
    row = cur.fetchone()
    return dict(row) if row else None


def _btn(cur: Any, hotel: Hotel, row: nonce.Nonce, action_id: int, verb: str) -> str:
    n = nonce.mint(cur, hotel_slug=hotel.slug, user_id=row.user_id, verb=verb, run_id=row.run_id,
                   action_id=action_id, item_no=row.item_no)
    return tokens.sign_ref(f"act:{action_id}:{verb}:{n}")


def _item_desc(item: dict, hotel: Hotel) -> str:
    parts = []
    if item.get("impact_minor") and item.get("currency"):
        parts.append(templates._money(int(item["impact_minor"]), item["currency"], hotel.locale))
    if item.get("confidence") is not None:
        parts.append(f"{round(float(item['confidence']) * 100)}%")
    dl = render._fmt_deadline(item.get("deadline_at"), hotel.timezone)
    if dl:
        parts.append(dl)
    if item.get("responsible_label"):
        parts.append(str(item["responsible_label"]))
    return render.lint(" · ".join(parts))


def flow_list(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    """"Review items": a list of the run's open decisions (<= 5 rows)."""
    run = brief.get_run(ctx.cur, hotel.slug, row.run_id) if row.run_id else None
    if run is None:
        _reply(ctx, hotel.slug, "This brief is no longer available. Open Clefs for today's.")
        return {"handled": True, "outcome": "no_run"}
    rows, seen = [], set()
    for it in list(run.get("items") or []) + list(run.get("overdue") or []):
        if not it.get("action_id") or it.get("action_status") in core_actions.TERMINAL or it["action_id"] in seen:
            continue
        seen.add(it["action_id"])  # list row ids must be unique
        n = nonce.mint(ctx.cur, hotel_slug=hotel.slug, user_id=row.user_id, verb="open", run_id=run["id"],
                       action_id=it["action_id"], item_no=it.get("item_no"))
        rows.append({
            "id": tokens.sign_ref(f"act:{it['action_id']}:open:{n}"),
            "title": render.lint(f"#{it.get('item_no')} {it.get('title') or ''}")[:ROW_TITLE_MAX],
            "description": _item_desc(it, hotel)[:ROW_DESC_MAX],
        })
        if len(rows) >= LIST_ROWS:
            break
    if not rows:
        _reply(ctx, hotel.slug, "Nothing in this brief is waiting for a decision.")
        return {"handled": True, "outcome": "empty"}
    body = render.lint(str(run.get("headline") or "Your brief")) + "\nPick an item to decide it."
    _interactive(ctx, hotel.slug, body, sections=[{"title": "Decisions", "rows": rows}], button_text="View items",
                 header=f"Clefs brief · {run.get('brief_date')}"[:60])
    return {"handled": True, "outcome": "list", "rows": len(rows)}


def flow_open(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    """A list row: the item, then reply buttons Approve / Assign / More."""
    a = _action(ctx.cur, hotel.slug, row.action_id)
    if a is None:
        _reply(ctx, hotel.slug, "That item is no longer available.")
        return {"handled": True, "outcome": "not_found"}
    item = _run_item(ctx.cur, row) or {}
    lines = [render.lint(str(a["title"]))]
    desc = _item_desc(item, hotel) if item else ""
    if desc:
        lines.append(desc)
    lines.append(f"Status: {str(a['status']).replace('_', ' ')}")
    buttons = [(_btn(ctx.cur, hotel, row, a["id"], "approve"), "Approve"),
               (_btn(ctx.cur, hotel, row, a["id"], "assign"), "Assign"),
               (_btn(ctx.cur, hotel, row, a["id"], "more"), "More")]
    _interactive(ctx, hotel.slug, "\n".join(lines), buttons=buttons)
    return {"handled": True, "outcome": "open"}


def _run_item(cur: Any, row: nonce.Nonce) -> dict | None:
    if not row.run_id or not row.action_id:
        return None
    cur.execute(
        "SELECT * FROM brf_items WHERE run_id = %s AND action_id = %s ORDER BY item_no LIMIT 1",
        (row.run_id, row.action_id),
    )
    r = cur.fetchone()
    if r is None:
        return None
    out = dict(r)
    out["deadline_at"] = out["deadline_at"].isoformat() if out.get("deadline_at") else None
    return out


def flow_more(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    rows = [{"id": _btn(ctx.cur, hotel, row, row.action_id, verb), "title": title[:ROW_TITLE_MAX],
             "description": desc[:ROW_DESC_MAX]} for verb, title, desc in MORE_MENU]
    _interactive(ctx, hotel.slug, "What would you like to do?", sections=[{"title": "Options", "rows": rows}],
                 button_text="Options")
    return {"handled": True, "outcome": "more"}


def _decide_error(exc: ClefsError, action: dict | None) -> str:
    title = render.lint(str((action or {}).get("title") or "This item"))
    if exc.code == "nonce_replayed":
        return NONCE_TEXT["replayed"]
    if exc.code == "illegal_transition" and action:
        return f"{title} is already {str(action['status']).replace('_', ' ')}."
    if exc.code in ("forbidden", "not_surfaced", "unauthorized"):
        return "Your role cannot make this decision. Ask the owner or the GM."
    if exc.code == "action_expired":
        return f"{title} has expired."
    return f"That did not work: {exc.detail}"[:300]


def decide(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce, decision: str, **kw: Any) -> tuple[dict | None, str | None]:
    """core actions.decide as the tapping member, single-use on the nonce.
    -> (action, None) or (None, the reason text to send back)."""
    cur = ctx.cur
    before = _action(cur, hotel.slug, row.action_id)
    cur.execute("SAVEPOINT brf_wa_decide")
    try:
        actor = actor_for(cur, int(ctx.user_id), hotel.slug)
        out = core_actions.decide(cur, int(row.action_id), decision, actor=actor, via="whatsapp", nonce=row.nonce,
                                  hotel_slug=hotel.slug, **kw)
        cur.execute("RELEASE SAVEPOINT brf_wa_decide")
    except ClefsError as exc:
        cur.execute("ROLLBACK TO SAVEPOINT brf_wa_decide")
        return None, _decide_error(exc, before)
    nonce.mark_used(cur, row.nonce)
    return out, None


def _done(ctx: notify.InboundCtx, hotel: Hotel, text: str, outcome: str) -> dict:
    _reply(ctx, hotel.slug, text)
    return {"handled": True, "outcome": outcome}


def flow_approve(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    a, err = decide(ctx, hotel, row, "approve")
    if err:
        return _done(ctx, hotel, err, "refused")
    label = f"#{row.item_no} " if row.item_no else ""
    return _done(ctx, hotel, f"Approved {label}{render.lint(str(a['title']))}. Status: {a['status'].replace('_', ' ')}.",
                 "approved")


def flow_snooze(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    days = 7 if row.verb.endswith("7") else 1
    a, err = decide(ctx, hotel, row, "snooze", snooze_until=clock.now() + timedelta(days=days))
    if err:
        return _done(ctx, hotel, err, "refused")
    return _done(ctx, hotel, f"Snoozed for {days} day{'s' if days > 1 else ''}: {render.lint(str(a['title']))}.", "snoozed")


def flow_ignore(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    """"Ignore" asks why first (a reason sub-list); the reason tap decides."""
    if row.verb == "ignore":
        rows = [{"id": _btn(ctx.cur, hotel, row, row.action_id, f"ignore-{code}"), "title": label}
                for code, label in IGNORE_REASONS]
        _interactive(ctx, hotel.slug, "Why ignore it? This helps Clefs learn.",
                     sections=[{"title": "Reason", "rows": rows}], button_text="Reason")
        return {"handled": True, "outcome": "ignore_reasons"}
    code = row.verb.split("-", 1)[1] if "-" in row.verb else "other"
    reason = dict(IGNORE_REASONS).get(code, "Other reason")
    a, err = decide(ctx, hotel, row, "ignore", note=reason)
    if err:
        return _done(ctx, hotel, err, "refused")
    return _done(ctx, hotel, f"Ignored ({reason.lower()}): {render.lint(str(a['title']))}.", "ignored")


def _assignable(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(
        """
        SELECT u.id, u.name, m.role FROM hotel_memberships m JOIN users u ON u.id = m.user_id
        WHERE m.hotel_slug = %s AND m.is_active AND u.is_active
        ORDER BY CASE m.role WHEN 'gm' THEN 0 WHEN 'dept_head' THEN 1 WHEN 'finance' THEN 2 WHEN 'revenue' THEN 3
                 WHEN 'owner' THEN 4 ELSE 5 END, u.name
        LIMIT 10
        """,
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def flow_assign(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    """"Assign" lists up to 10 members; picking one assigns the action."""
    if row.verb == "assign":
        people = _assignable(ctx.cur, hotel.slug)
        if not people:
            return _done(ctx, hotel, "There is nobody to assign this to yet. Add your team in Clefs.", "no_people")
        rows = [{"id": _btn(ctx.cur, hotel, row, row.action_id, f"assign-u{p['id']}"),
                 "title": str(p["name"] or "Member")[:ROW_TITLE_MAX], "description": str(p["role"]).replace("_", " ")}
                for p in people]
        _interactive(ctx, hotel.slug, "Who should handle it?", sections=[{"title": "Team", "rows": rows}],
                     button_text="Choose")
        return {"handled": True, "outcome": "assign_menu"}
    m = re.fullmatch(r"assign-([us])(\d+)", row.verb)
    if not m:
        return {"handled": True, "outcome": "unknown_verb"}
    kw = {"assignee_user_id": int(m.group(2))} if m.group(1) == "u" else {"assignee_staff_id": int(m.group(2))}
    a, err = decide(ctx, hotel, row, "assign", **kw)
    if err:
        return _done(ctx, hotel, err, "refused")
    who = next((p["name"] for p in _assignable(ctx.cur, hotel.slug) if p["id"] == kw.get("assignee_user_id")), None)
    return _done(ctx, hotel, f"Assigned to {who or 'the team'}: {render.lint(str(a['title']))}.", "assigned")


def flow_remind(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    """Remind: no state change (core logs it); Brief nudges whoever holds it,
    with the escalation ladder's level-1 audience and words (no links)."""
    from clefs.modules.brief import escalate

    a, err = decide(ctx, hotel, row, "remind")
    if err:
        return _done(ctx, hotel, err, "refused")
    people = [p for p in escalate.recipients_for(ctx.cur, hotel.slug, a, 1) if p.get("id") != ctx.user_id]
    title = str(a["title"])[:120]
    body = f"Clefs reminder from {_name(ctx.cur, ctx.user_id)}: \"{title}\" is waiting on you. Please update it in Clefs."
    for p in people:
        who = f"u{p['id']}" if p.get("id") else f"s{p.get('staff_id')}"
        notify.notify(
            notify.Recipient(phone=p.get("phone_e164"), email=p.get("email"), name=p.get("name"), user_id=p.get("id")),
            notify.Message(purpose="brief.remind", hotel_slug=hotel.slug, category="utility", whatsapp_text=body,
                           sms_text=body, email_subject=f"Reminder: {title}", email_text=body,
                           email_html=f"<p>{escalate.notify_escape(body)}</p>",
                           idempotency_key=f"brief:remind:{a['id']}:{row.nonce}:{who}"),
            cur=ctx.cur,
        )
    names = ", ".join(str(p.get("name") or "the team") for p in people[:3]) or "nobody (no one holds it yet)"
    return _done(ctx, hotel, f"Reminder sent to {names}.", "reminded")


def _name(cur: Any, user_id: int | None) -> str:
    cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))
    r = cur.fetchone()
    return str(r["name"]) if r and r["name"] else "your manager"


# ─── Adjust sessions ───
# "Adjust" asks one question and waits for the next text (AWAITING_ADJUST).
# Bounds come from the action's params (the proposing module sets them):
#   params.adjust = {field, min_minor, max_minor, label?}          explicit
#   params[<f>_minor] with min_<f>_minor / max_<f>_minor            per field
#   params[<f>_minor] with floor_minor / ceiling_minor              rate recs
# The reply is checked against those bounds here, and again by the action
# type's own validate_params inside core decide (a type may be stricter).
# The editable field is the proposal, never the baseline: dr.rate_change
# carries current_bar_minor before recommended_minor, and picking the first
# key in dict order made "Adjust" edit the current BAR, so the owner's rate
# was stored where nothing reads it (QA OWN-01). Proposal-named fields win,
# baseline-named ones (current_, old_, previous_, base_) are never offered.
# client brief/pure.ts adjustBounds mirrors this order.

ADJUST_PREFERRED = ("recommended_", "proposed_", "suggested_", "target_", "new_", "decided_")
ADJUST_BASELINE = ("current_", "old_", "previous_", "prev_", "base_", "baseline_")


def adjust_fields(params: dict) -> list[str]:
    """Candidate `<f>_minor` keys, proposal-named first, baselines dropped."""
    keys = [f for f in params if f.endswith("_minor") and not f.startswith(("min_", "max_"))
            and f not in ("floor_minor", "ceiling_minor") and not f.startswith(ADJUST_BASELINE)]
    return sorted(keys, key=lambda f: (not f.startswith(ADJUST_PREFERRED), keys.index(f)))


def adjust_bounds(action: dict, currency: str) -> dict | None:
    p = action.get("params") or {}
    ccy = action.get("currency") or currency
    exp = p.get("adjust")
    if isinstance(exp, dict) and exp.get("field") and exp.get("min_minor") is not None and exp.get("max_minor") is not None:
        return {"field": str(exp["field"]), "min_minor": int(exp["min_minor"]), "max_minor": int(exp["max_minor"]),
                "currency": ccy, "label": str(exp.get("label") or exp["field"].removesuffix("_minor").replace("_", " "))}
    for f in adjust_fields(p):
        v = p[f]
        lo = p.get(f"min_{f}", p.get("floor_minor"))
        hi = p.get(f"max_{f}", p.get("ceiling_minor"))
        if isinstance(v, int) and lo is not None and hi is not None:
            return {"field": f, "min_minor": int(lo), "max_minor": int(hi), "currency": ccy,
                    "label": f.removesuffix("_minor").replace("_", " ")}
    return None


def _bounds_text(b: dict, locale: str | None) -> str:
    lo = templates._money(b["min_minor"], b["currency"], locale)
    hi = templates._money(b["max_minor"], b["currency"], locale).split(" ", 1)[-1]
    return f"{lo} and {hi}"


def active_session(cur: Any, user_id: int | None) -> dict | None:
    if user_id is None:
        return None
    cur.execute("SELECT * FROM brf_wa_sessions WHERE user_id = %s AND expires_at > %s", (user_id, clock.now()))
    row = cur.fetchone()
    return dict(row) if row else None


def flow_adjust(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    a = _action(ctx.cur, hotel.slug, row.action_id)
    if a is None or a["status"] not in ("proposed", "approved"):
        state = str((a or {}).get("status") or "gone").replace("_", " ")
        return _done(ctx, hotel, f"This item cannot be adjusted now (it is {state}).", "refused")
    b = adjust_bounds(a, hotel.currency)
    if b is None:
        from clefs.settings import get_settings

        url = f"{get_settings().web_base_url}/app/{hotel.slug}/brief"
        return _done(ctx, hotel, f"This one is adjusted in Clefs: {url}", "adjust_console")
    data = {**b, "action_id": a["id"], "nonce": row.nonce, "item_no": row.item_no, "run_id": row.run_id}
    ctx.cur.execute(
        """
        INSERT INTO brf_wa_sessions (user_id, hotel_slug, state, data, expires_at)
        VALUES (%s, %s, 'awaiting_adjust', %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET hotel_slug = EXCLUDED.hotel_slug, state = EXCLUDED.state,
            data = EXCLUDED.data, expires_at = EXCLUDED.expires_at, created_at = NOW()
        """,
        (int(ctx.user_id), hotel.slug, db.Json(data), clock.now() + SESSION_TTL),
    )
    return _done(ctx, hotel, f"Reply with a {b['label']} between {_bounds_text(b, hotel.locale)}.", "awaiting_adjust")


def parse_amount(text: str | None) -> Decimal | None:
    """"450", "AED 450", "450.50", "1,020" -> Decimal; anything else None."""
    s = re.sub(r"[^\d.,]", "", text or "")
    if not s or not re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d+)?|\d+(\.\d+)?", s):
        return None
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def handle_adjust_reply(ctx: notify.InboundCtx, sess: dict) -> dict:
    cur, data = ctx.cur, dict(sess["data"])
    hotel = get_hotel(cur, sess["hotel_slug"])
    amount = parse_amount(ctx.text)
    minor = money.to_minor(amount, data["currency"]) if amount is not None else None
    if minor is None or not (int(data["min_minor"]) <= minor <= int(data["max_minor"])):
        return _done(ctx, hotel, f"Please reply with a number between {_bounds_text(data, hotel.locale)}.", "out_of_bounds")
    cur.execute("DELETE FROM brf_wa_sessions WHERE user_id = %s", (int(ctx.user_id),))
    try:
        row = nonce.verify(cur, data["nonce"], user_id=ctx.user_id, verb="adjust", action_id=data["action_id"])
    except nonce.NonceError as exc:
        return _done(ctx, hotel, NONCE_TEXT.get(exc.code, NONCE_TEXT["unknown"]), exc.code)
    a, err = decide(ctx, hotel, row, "adjust", params_patch={data["field"]: minor})
    if err:
        return _done(ctx, hotel, err, "refused")
    shown = templates._money(minor, data["currency"], hotel.locale)
    return _done(ctx, hotel, f"Adjusted {data['label']} to {shown} and approved: {render.lint(str(a['title']))}.",
                 "adjusted")


def _is_brief_member(cur: Any, user_id: int) -> bool:
    cur.execute("SELECT 1 FROM hotel_memberships WHERE user_id = %s AND is_active AND role = ANY(%s) LIMIT 1",
                (user_id, list(BRIEF_ROLES)))
    return cur.fetchone() is not None


def _text_match(ctx: notify.InboundCtx) -> bool:
    """Read-only (the foundation's match rule): an open Adjust question, or
    STOP / START from a member who receives briefs. Everything else passes
    on (Ask Clefs answers owners' free text)."""
    if ctx.user_id is None or not ctx.text:
        return False
    word = ctx.text.strip().upper()
    if word in STOP_WORDS or word in START_WORDS:
        return _is_brief_member(ctx.cur, int(ctx.user_id))
    return active_session(ctx.cur, ctx.user_id) is not None


@notify.on_whatsapp("staff", priority=40, kind="text", match=_text_match)
def on_text(ctx: notify.InboundCtx) -> dict:
    word = (ctx.text or "").strip().upper()
    if word in STOP_WORDS or word in START_WORDS:
        return handle_stop_start(ctx, opted_in=word in START_WORDS)
    sess = active_session(ctx.cur, ctx.user_id)
    if sess is None:
        return {"handled": False}
    return handle_adjust_reply(ctx, sess)


def handle_stop_start(ctx: notify.InboundCtx, *, opted_in: bool) -> dict:
    cur, uid = ctx.cur, int(ctx.user_id)
    cur.execute("SELECT hotel_slug FROM hotel_memberships WHERE user_id = %s AND is_active AND role = ANY(%s)",
                (uid, list(BRIEF_ROLES)))
    slugs = [r["hotel_slug"] for r in cur.fetchall()]
    for slug in slugs:
        set_opt_in(cur, slug, uid, opted_in=opted_in, method="whatsapp" if opted_in else "whatsapp_stop",
                   actor=actor_for(cur, uid, slug))
    text = ("WhatsApp briefs are back on. Reply STOP to pause them." if opted_in else
            "WhatsApp briefs are paused; you will get them by email. Reply START to resume.")
    _reply(ctx, slugs[0] if slugs else None, text)
    return {"handled": True, "outcome": "opted_in" if opted_in else "opted_out", "hotels": slugs}


# ─── Number confirmation: the inbound code ───


def _cancel(cur: Any, ids: list[int]) -> None:
    if ids:
        cur.execute("UPDATE brf_phone_verifications SET cancelled_at = %s WHERE id = ANY(%s) AND verified_at IS NULL",
                    (clock.now(), ids))


def confirm_verification(cur: Any, row: dict, sender: str) -> dict:
    """Store the proven number on the account and turn the brief on. The
    account must still have no number, and nobody else may hold it now."""
    from clefs.auth import sessions

    uid = int(row["user_id"])
    cur.execute("SELECT id, phone_e164 FROM users WHERE id = %s AND is_active FOR UPDATE", (uid,))
    user = cur.fetchone()
    cur.execute("SELECT 1 FROM users WHERE phone_e164 = %s AND id <> %s", (sender, uid))
    taken = cur.fetchone() is not None
    if user is None or user["phone_e164"] or taken:
        _cancel(cur, [row["id"]])
        return {"ok": False, "reason": "phone_taken" if taken else "account_changed"}
    now = clock.now()
    cur.execute("UPDATE users SET phone_e164 = %s WHERE id = %s", (sender, uid))
    cur.execute("UPDATE brf_phone_verifications SET verified_at = %s WHERE id = %s", (now, row["id"]))
    # Anyone else's open request for this number can never succeed now.
    cur.execute("SELECT id FROM brf_phone_verifications WHERE phone_e164 = %s AND id <> %s "
                "AND verified_at IS NULL AND cancelled_at IS NULL", (sender, row["id"]))
    _cancel(cur, [r["id"] for r in cur.fetchall()])
    sessions.evict_user(uid)  # cached sessions carry the old (empty) phone
    actor = None
    try:
        actor = actor_for(cur, uid, row["hotel_slug"])
    except ClefsError:  # membership ended since the request: the number stays proven, no opt-in here
        pass
    if actor is not None:
        set_opt_in(cur, row["hotel_slug"], uid, opted_in=True, method="whatsapp_verify", actor=actor)
    audit(cur, actor, "brief.wa.verify_confirm", "brf_phone_verification", row["id"],
          after={"phone": _mask(sender)}, hotel_slug=row["hotel_slug"])
    return {"ok": True, "user_id": uid, "hotel_slug": row["hotel_slug"]}


@notify.on_whatsapp(("unknown", "staff"), priority=20, kind="text", match=_verify_match)
def on_verify_code(ctx: notify.InboundCtx) -> dict:
    import hmac

    m = VERIFY_RE.match(ctx.text or "")
    rows = _open_verifications(ctx.cur, ctx.sender) if m else []
    if not rows:
        return {"handled": False}
    digest = _code_hash(m.group(1))
    hit = next((r for r in rows if hmac.compare_digest(r["code_hash"], digest)), None)
    if hit is None:
        # Count the miss on every open request for this number; five end them.
        for r in rows:
            ctx.cur.execute("UPDATE brf_phone_verifications SET attempts = attempts + 1 WHERE id = %s "
                            "RETURNING attempts", (r["id"],))
            if int(ctx.cur.fetchone()["attempts"]) >= VERIFY_MAX_ATTEMPTS:
                _cancel(ctx.cur, [r["id"]])
        _reply(ctx, rows[0]["hotel_slug"], "That code does not match. Check the code in Clefs and send it again.")
        return {"handled": True, "outcome": "wrong_code"}
    res = confirm_verification(ctx.cur, hit, ctx.sender)
    text = ("Number confirmed. Your Clefs brief will come here; reply STOP to pause it." if res["ok"] else
            "This number could not be added. Open Clefs and try again.")
    _reply(ctx, hit["hotel_slug"], text)
    return {"handled": True, "outcome": "verified" if res["ok"] else res["reason"]}


# ─── Dispute packet (DOCUMENT) ───
# Profit Guard owns real dispute packets (02 spec 3.7). The packet comes
# from, in order: profit_guard.api.dispute_packet (called only if that door
# exists), a file the action already carries (params/result packet_file_id),
# else a one-page evidence summary built here from the action and its signal.

PACKET_RETENTION_DAYS = 400  # OTA disputes run to the next statements and beyond


def _packet_from_profit_guard(cur: Any, hotel: Hotel, action: dict) -> dict | None:
    try:
        from clefs.modules.profit_guard import api as pg_api
    except ImportError:
        return None
    fn = getattr(pg_api, "dispute_packet", None)
    if fn is None:
        return None
    cur.execute("SAVEPOINT brf_pg_packet")
    try:
        res = fn(cur, hotel.slug, action_id=action["id"])
        cur.execute("RELEASE SAVEPOINT brf_pg_packet")
    except Exception:  # a refusal, or Profit Guard not installed here: the summary packet stands in
        cur.execute("ROLLBACK TO SAVEPOINT brf_pg_packet")
        log.warning("brief: profit guard dispute packet unavailable for action %s", action.get("id"), exc_info=True)
        return None
    if isinstance(res, dict) and res.get("file_id"):
        return {"file_id": str(res["file_id"]), "filename": str(res.get("filename") or "dispute-packet.pdf")}
    return None


def _packet_on_action(cur: Any, hotel: Hotel, action: dict) -> dict | None:
    from clefs import files

    for src in (action.get("params") or {}, action.get("result") or {}):
        fid = src.get("packet_file_id") if isinstance(src, dict) else None
        if fid:
            try:
                ref = files.file_row(cur, hotel.slug, str(fid))
            except ClefsError:
                continue
            if ref:
                return {"file_id": str(fid), "filename": str(ref.get("filename") or "dispute-packet.pdf")}
    return None


def _summary_pdf(cur: Any, hotel: Hotel, action: dict) -> bytes:
    from fpdf import FPDF

    from clefs.modules.brief.weekly import _latin1

    sig = None
    if action.get("signal_id"):
        cur.execute("SELECT title, summary, evidence, impact_minor, currency FROM signals WHERE id = %s",
                    (action["signal_id"],))
        sig = cur.fetchone()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.multi_cell(0, 9, _latin1(f"{hotel.name}: dispute evidence"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    rows = [("Item", str(action["title"])), ("Reference", f"Clefs action #{action['id']}")]
    amount = action.get("expected_minor") or (sig or {}).get("impact_minor")
    if amount:
        rows.append(("Amount", templates._money(int(amount), action.get("currency") or hotel.currency, hotel.locale)))
    if action.get("due_at"):
        rows.append(("Deadline", action["due_at"].astimezone(hotel.tz).strftime("%d %b %Y %H:%M")))
    for k, v in rows:
        pdf.multi_cell(0, 7, _latin1(f"{k}: {v}"), new_x="LMARGIN", new_y="NEXT")
    if sig and sig.get("summary"):
        pdf.ln(2)
        pdf.multi_cell(0, 6, _latin1(str(sig["summary"])), new_x="LMARGIN", new_y="NEXT")
    ev = list((sig or {}).get("evidence") or [])
    if ev:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Evidence", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for e in ev[:40]:
            label = str(e.get("label") or e.get("kind") or "")
            val = e.get("value")
            pdf.multi_cell(0, 6, _latin1(f"- {label}{': ' + str(val) if val not in (None, '') else ''}"),
                           new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def dispute_packet(cur: Any, hotel: Hotel, action: dict) -> dict | None:
    """-> {file_id, filename} of the packet to send, or None."""
    from clefs import files

    found = _packet_from_profit_guard(cur, hotel, action) or _packet_on_action(cur, hotel, action)
    if found:
        return found
    name = f"dispute-{action['id']}.pdf"
    ref = files.put_file(cur, hotel.slug, _summary_pdf(cur, hotel, action), filename=name,
                         content_type="application/pdf", purpose="brief.dispute_packet", source="generated",
                         retention_days=PACKET_RETENTION_DAYS)
    return {"file_id": str(ref.id), "filename": name}


def flow_dispute(ctx: notify.InboundCtx, hotel: Hotel, row: nonce.Nonce) -> dict:
    a, err = decide(ctx, hotel, row, "dispute")
    if err:
        return _done(ctx, hotel, err, "refused")
    title = render.lint(str(a["title"]))
    cur = ctx.cur
    cur.execute("SAVEPOINT brf_wa_packet")
    try:
        pk = dispute_packet(cur, hotel, a)
        cur.execute("RELEASE SAVEPOINT brf_wa_packet")
    except Exception:  # noqa: BLE001 - the decision stands even if the PDF cannot be built
        cur.execute("ROLLBACK TO SAVEPOINT brf_wa_packet")
        log.warning("dispute packet failed for action %s", a["id"], exc_info=True)
        pk = None
    if pk is None:
        return _done(ctx, hotel, f"Dispute opened: {title}. The evidence pack is in Clefs.", "disputed")
    notify.send_whatsapp_document(
        ctx.sender, pk["file_id"], pk["filename"], caption=f"Dispute opened: {title}"[:1024], hotel_slug=hotel.slug,
        purpose=PURPOSE_REPLY, sender="platform", idempotency_key=f"brief:wa:{ctx.provider_id}:doc", cur=cur,
        action_id=a["id"],
    )
    return {"handled": True, "outcome": "disputed", "file_id": pk["file_id"]}


_FLOWS = {
    "open": flow_open, "more": flow_more, "approve": flow_approve, "assign": flow_assign, "adjust": flow_adjust,
    "dispute": flow_dispute, "remind": flow_remind, "snooze": flow_snooze, "ignore": flow_ignore,
}


# ─── Weekly report (DOCUMENT) ───
# 02 spec 6.4: the weekly report goes out as weekly_report_v1, a UTILITY
# template with a DOCUMENT header (the fpdf2 PDF weekly.py stored). Inside an
# open window the PDF goes as a plain document message instead (no template
# needed, and notify signs a fresh 1 h link at delivery time).

REPORT_LINK_TTL_S = 6 * 3600  # Meta fetches the header media when the send is processed


def send_weekly_report(cur: Any, hotel: Hotel, run: dict, rcpt: dict, link: str | None) -> dict:
    from clefs import files
    from clefs.modules.brief import api as brief_api
    from clefs.modules.brief import weekly
    from clefs.settings import get_settings

    block = (run.get("blocks") or {}).get("report") or {}
    phone = rcpt.get("phone_e164")
    if not block.get("pdf_file_id") or not phone:
        return {"ok": False, "status": "no_report"}
    report = weekly.get_report(cur, hotel.slug, block["period_key"]) or {**block, "hotel_slug": hotel.slug}
    file_id, name = str(block["pdf_file_id"]), f"clefs-weekly-{block['period_key']}.pdf"
    key = f"brief:{run['id']}:{rcpt['id']}:weekly_report"
    if notify.window_open(cur, notify.platform_sender_id(), phone):
        res = notify.send_whatsapp_document(
            phone, file_id, name, caption=f"Weekly report {block['period_key']}", hotel_slug=hotel.slug,
            purpose="brief.weekly_report", sender="platform", idempotency_key=key, cur=cur,
        )
        return {"ok": bool(res.ok), "status": res.status, "outbox_id": res.outbox_id}
    info = brief_api.wa_template_info(cur, hotel.slug, name=templates.WEEKLY_REPORT, language=templates.LANG)
    if not templates.is_sendable(info):
        return {"ok": False, "status": "template_not_approved"}
    doc_link = get_settings().PUBLIC_API_ORIGIN.rstrip("/") + files.signed_path(hotel.slug, file_id, ttl_s=REPORT_LINK_TTL_S)
    res = notify.send_whatsapp_template(
        phone, templates.WEEKLY_REPORT, templates.LANG, templates.weekly_report_variables(report, hotel),
        hotel_slug=hotel.slug, purpose="brief.weekly_report", sender="platform",
        header={"type": "document", "link": doc_link, "filename": name},
        buttons=[{"type": "url", "text": _link_token(link)}], category="utility", idempotency_key=key, cur=cur,
    )
    return {"ok": bool(res.ok), "status": res.status, "outbox_id": res.outbox_id}


# ─── Routes: /hotels/{slug}/brief/wa/* ───

router = APIRouter(prefix="/hotels/{slug}/brief/wa", tags=["brief"])
VIEW = require_perm("brief.view")
MANAGE = require_perm("brief.manage")


class ContactIn(BaseModel):
    user_id: int | None = None  # default: the caller
    phone: str | None = Field(default=None, max_length=32)
    opted_in: bool = True


@router.get("/contacts")
def get_contacts(ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return list_contacts(cur, ctx.hotel.slug)


@router.post("/contacts")
def post_contact(body: ContactIn, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Opt-in capture. A member records their own choice with brief.view;
    recording someone else's needs brief.manage. A number is never stored
    here: a manager cannot give someone else one (409 no_phone), and a
    member proves their own through POST /contacts/verify."""
    uid = body.user_id or ctx.principal.user_id
    if uid is None:
        raise ClefsError(400, "user_id is required", "user_required")
    if uid != ctx.principal.user_id and not ctx.can("brief.manage"):
        raise ClefsError(403, "Only a manager can record someone else's opt-in", "forbidden")
    if body.phone and uid != ctx.principal.user_id:
        raise ClefsError(409, "Only they can add their WhatsApp number: ask them to add and confirm it in Clefs",
                         "no_phone")
    with db.db() as cur:
        set_opt_in(cur, ctx.hotel.slug, uid, opted_in=body.opted_in, method="console", actor=ctx, phone=body.phone)
        # A member outside the brief roles still gets their row back.
        return next((c for c in list_contacts(cur, ctx.hotel.slug) if c["user_id"] == uid), {"user_id": uid})


class VerifyIn(BaseModel):
    phone: str = Field(min_length=4, max_length=32)


@router.post("/contacts/verify")
def post_verify(body: VerifyIn, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """A code for the caller's own number (never anyone else's): the answer
    carries the code to send from that WhatsApp, once."""
    uid = ctx.principal.user_id
    if uid is None:
        raise ClefsError(403, "Only a signed-in member can add a number", "forbidden")
    with db.db() as cur:
        return start_verification(cur, ctx.hotel.slug, uid, body.phone, actor=ctx)


@router.get("/contacts/verify")
def get_verify(ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The caller's latest request (the console polls it after showing the code)."""
    uid = ctx.principal.user_id
    if uid is None:
        return {"status": "none"}
    with db.db(readonly=True) as cur:
        return verification_state(cur, uid)


@router.get("/templates")
def get_templates(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return {
            "templates": templates.list_templates(cur, ctx.hotel.slug),
            "brief_templates": [{"name": n, "language": d["language"], "category": d["category"]}
                                for n, d in templates.definitions().items()],
        }


class SubmitIn(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    language: str = Field(default="en", min_length=2, max_length=15)
    category: str = "UTILITY"
    components: list[dict] | None = None  # None: Brief's own definition of `name`
    scope: str = Field(default="hotel", pattern="^(hotel|platform)$")


@router.post("/templates/submit")
def post_submit(body: SubmitIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Submit a template to Meta for the hotel's WABA, or (Clefs admins)
    one of Brief's platform templates."""
    platform = body.scope == "platform"
    if platform and not ctx.principal.is_clefs_admin:
        raise ClefsError(403, "Platform templates are managed by Clefs", "forbidden")
    comps = body.components
    if comps is None:
        d = templates.definitions().get(body.name)
        if d is None:
            raise ClefsError(400, "components are required for this template", "invalid_template")
        comps = d["components"]
    with db.db() as cur:
        out = templates.submit_template(cur, None if platform else ctx.hotel.slug, name=body.name,
                                        language=body.language, category=body.category, components=comps)
        audit(cur, ctx, "brief.wa.template_submit", "brf_wa_template", f"{body.name}/{body.language}",
              after={**out, "scope": body.scope})
        return out


@router.post("/templates/sync")
def post_sync(ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        out = {"hotel": templates.sync(cur, ctx.hotel.slug)}
        if ctx.principal.is_clefs_admin:
            templates.ensure_platform_templates(cur)
            out["platform"] = templates.sync(cur, None)
        return out


class AccountIn(BaseModel):
    waba_id: str = Field(min_length=3, max_length=64, pattern=r"^\d+$")
    phone_number_id: str | None = Field(default=None, max_length=64, pattern=r"^\d+$")
    display_phone: str | None = Field(default=None, max_length=32)
    billing_ready: bool | None = None  # owner attestation (04 spec 3.5) until Meta's status is read


def _account(cur: Any, hotel_slug: str) -> dict | None:
    cur.execute("SELECT * FROM brf_wa_accounts WHERE hotel_slug = %s", (hotel_slug,))
    r = cur.fetchone()
    if r is None:
        return None
    out = dict(r)
    for k in ("billing_checked_at", "connected_at", "updated_at"):
        out[k] = out[k].isoformat() if out.get(k) else None
    return out


@router.get("/account")
def get_account(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return {"account": _account(cur, ctx.hotel.slug)}


@router.put("/account")
def put_account(body: AccountIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before = _account(cur, ctx.hotel.slug)
        now = clock.now()
        cur.execute(
            """
            INSERT INTO brf_wa_accounts (hotel_slug, waba_id, phone_number_id, display_phone, billing_ready,
                                         billing_checked_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug) DO UPDATE SET waba_id = EXCLUDED.waba_id,
                phone_number_id = EXCLUDED.phone_number_id, display_phone = EXCLUDED.display_phone,
                billing_ready = EXCLUDED.billing_ready, billing_checked_at = EXCLUDED.billing_checked_at,
                updated_at = EXCLUDED.updated_at
            """,
            (ctx.hotel.slug, body.waba_id, body.phone_number_id, body.display_phone, body.billing_ready,
             now if body.billing_ready is not None else None, now),
        )
        after = _account(cur, ctx.hotel.slug)
        audit(cur, ctx, "brief.wa.account", "brf_wa_account", ctx.hotel.slug, before=before, after=after)
        return {"account": after}
