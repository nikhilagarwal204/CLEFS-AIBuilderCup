"""
Inbound dispatch: one Meta webhook and one Resend webhook serve every
module (01 spec E6, 04 spec F4).

    @on_whatsapp("staff", button_prefix="brief:")
    def brief_tap(ctx: InboundCtx) -> bool: ...          # ctx.button_ref is the verified ref

    @on_whatsapp("guest", priority=30, match=lambda c: c.hotel_slug is not None, kind="text")
    def concierge_chat(ctx): ...

    @on_inbound_email(purpose="inquiries")
    def inquiry_mail(mail: InboundEmail) -> bool: ...

    @on_outbound_status("guests.journey")
    def journey_status(cur, row, status, error_code): ...

WhatsApp: the signature (X-Hub-Signature-256 = HMAC-SHA256 of the raw body
with the app secret, compared in constant time) is checked by the route before
anything is parsed. Each message is deduplicated on messages[].id (Meta
redelivers), opens the 24 h window, and goes to handlers in ascending
priority; the first handler that returns a truthy value (or {"handled": True})
wins. Statuses update the outbox row and fire on_outbound_status hooks.

Audience: "guest" (someone messaging a hotel's own number), "staff" (a known
user or staff member: owners on the platform number, floor staff), or
"unknown" (a stranger on the platform number). Button and list ids are
trusted only when they carry a valid tokens.sign_ref tag (ctx.button_ref);
button_prefix handlers match verified refs only.

Guest dispatch order is frozen (04 spec F4) in GUEST_PRIORITY: opt-out and
consent (5), journey replies (10), inquiry intake (20), concierge (30),
service fallback (90). Modules register with those numbers, e.g.
@on_whatsapp("guest", priority=GUEST_PRIORITY["inquiries.whatsapp_intake"]).

Every inbound row routed to a hotel stores sender_hmac (core.privacy.
contact_hmac of the phone or email), so modules find a sender's recent
messages and window (outbox.wa_window_open_until) without the raw number.

Email: <alias>+<purpose>[-<ref>]@<INBOUND_EMAIL_DOMAIN, default in.clefs.world>.
The alias maps to hotels.inbound_email_alias; the purpose is the token before
the first "-": "inq" is the inquiries module, "reviews" is reviews, none is
ingest. inbound_address() builds these addresses so no module hand-formats
one. Attachments become files first.

Resend's email.received webhook (Svix-signed, verify_resend_signature)
carries metadata only: process_resend reads the message and its attachments
back from Resend's API (fetch_resend_email), from_resend turns them into our
email payload and process_email routes it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import itertools
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from typing import Any, Callable

from clefs import db
from clefs.auth import tokens
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.notify import outbox
from clefs.notify.transport import HTTP_TIMEOUT_S
from clefs.settings import RESEND_INBOUND_VERIFIER, get_settings

log = logging.getLogger("clefs.notify.inbound")

AUDIENCES = ("guest", "staff", "unknown")

# ─── Frozen guest dispatch order (04 spec F4) ───
# Handlers for audience "guest" run in this order and the first that handles
# the message wins. The numbers are part of the contract: a STOP must reach
# opt-out before a journey reply sees it, a click-to-WhatsApp lead must reach
# inquiries before the concierge answers it, and service only gets leftovers.
GUEST_PRIORITY: dict[str, int] = {
    "guests.optout_consent": 5,  # STOP from any guest; YES only when 3.8 ties it to a journey message
    "guests.journey_replies": 10,  # context.id is a journey wamid, or a point a journey messaged in 72 h
    "inquiries.whatsapp_intake": 20,  # a CTWA referral, or not a known in-house guest
    "concierge.whatsapp_chat": 30,  # a known in-house guest
    "service.whatsapp_fallback": 90,  # anything left
}

EMAIL_DOMAIN_DEFAULT = "in.clefs.world"
# Address token -> handler purpose. "inquiries" and "ingest" are accepted as
# spelled-out synonyms so a hand-typed address still routes.
EMAIL_PURPOSES: dict[str | None, str] = {
    "inq": "inquiries",
    "inquiries": "inquiries",
    "reviews": "reviews",
    "ingest": "ingest",
    None: "ingest",
}
# Handler purpose -> the short token inbound_address() writes.
EMAIL_TOKENS: dict[str, str] = {"inquiries": "inq"}
_order = itertools.count()
_lock = threading.Lock()


# ─── Signature checks ───


def verify_whatsapp_signature(raw_body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: "sha256=<hex>" over the exact raw body."""
    s = get_settings()
    secret = s.WA_APP_SECRET or s.META_APP_SECRET
    if not secret:
        raise ClefsError(503, "WhatsApp webhooks are not configured", "not_configured")
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Bytes: headers arrive latin-1 decoded, and a str compare_digest raises
    # TypeError on any non-ASCII character (a 500 instead of a 401).
    return hmac.compare_digest(expected.encode("ascii"), header[7:].strip().lower().encode("utf-8", "replace"))


def verify_whatsapp_challenge(mode: str | None, token: str | None) -> bool:
    expected = get_settings().WA_VERIFY_TOKEN
    if not expected:
        raise ClefsError(503, "WhatsApp webhooks are not configured", "not_configured")
    return mode == "subscribe" and bool(token) and hmac.compare_digest(token.encode(), expected.encode())


# Svix's own verifiers allow 5 minutes of clock skew either way; so do we, so
# a captured delivery cannot be replayed later.
SVIX_TOLERANCE_S = 5 * 60


def verify_resend_signature(raw_body: bytes, svix_id: str | None, svix_timestamp: str | None,
                            svix_signature: str | None) -> bool:
    """Resend signs its webhooks the Svix way. svix-signature lists
    space-separated "v1,<base64>" entries (two while a secret rotates), each
    an HMAC-SHA256 of "<svix-id>.<svix-timestamp>.<raw body>" keyed with the
    base64-decoded part of RESEND_WEBHOOK_SECRET after "whsec_"; any v1 entry
    may match. A timestamp more than 5 minutes from now, either way, fails."""
    secret = (get_settings().RESEND_WEBHOOK_SECRET or "").strip()
    if not secret:
        raise ClefsError(503, "Inbound email is not configured", "not_configured")
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if not key:
        log.error("RESEND_WEBHOOK_SECRET is not a whsec_<base64> signing secret")
        raise ClefsError(503, "Inbound email is not configured", "not_configured")
    if not svix_id or not svix_timestamp or not svix_signature:
        return False
    if not (svix_timestamp.isascii() and svix_timestamp.isdigit()):
        return False
    if abs(clock.now().timestamp() - int(svix_timestamp)) > SVIX_TOLERANCE_S:
        return False
    signed = f"{svix_id}.{svix_timestamp}.".encode("utf-8", "replace") + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest())
    for entry in svix_signature.split():
        version, _, given = entry.partition(",")
        # Bytes, as for WhatsApp: a str compare_digest raises on non-ASCII.
        if version == "v1" and hmac.compare_digest(expected, given.encode("utf-8", "replace")):
            return True
    return False


# ─── Handler registries ───


@dataclass
class InboundCtx:
    cur: Any
    inbound_id: int
    provider_id: str
    kind: str  # text | button | button_reply | list_reply | image | document | audio | location | ...
    audience: str
    sender: str  # E.164
    recipient_id: str | None  # our phone_number_id
    hotel_slug: str | None
    sender_name: str | None = None
    text: str | None = None
    button_id: str | None = None
    button_ref: str | None = None  # verified sign_ref payload, or None
    button_title: str | None = None
    context_id: str | None = None  # the wamid this message replies to
    referral: dict | None = None  # click-to-WhatsApp ad referral (CTWA)
    user_id: int | None = None
    staff_member_id: int | None = None
    sender_hmac: bytes | None = None
    window_open_until: datetime | None = None
    timestamp: datetime | None = None
    message: dict = field(default_factory=dict)

    def reply_text(self, body: str, *, purpose: str, **kw: Any) -> Any:
        from clefs.notify.send import send_whatsapp_text

        sender = "hotel" if self.audience == "guest" and self.hotel_slug else "platform"
        return send_whatsapp_text(self.sender, body, hotel_slug=self.hotel_slug, purpose=purpose, sender=sender, cur=self.cur, **kw)


@dataclass(frozen=True)
class _WaHandler:
    fn: Callable[[InboundCtx], Any]
    audiences: tuple[str, ...]
    priority: int
    order: int
    match: Callable[[InboundCtx], bool] | None
    button_prefix: str | None
    kinds: tuple[str, ...] | None
    name: str


_wa_handlers: list[_WaHandler] = []


def on_whatsapp(
    audience: str | tuple[str, ...] = "*",
    *,
    priority: int = 50,
    match: Callable[[InboundCtx], bool] | None = None,
    button_prefix: str | None = None,
    kind: str | tuple[str, ...] | None = None,
) -> Callable:
    """Register a WhatsApp handler. Handlers run by ascending priority (then
    registration order), each in a savepoint, and the first that handles the
    message wins: a truthy return, or a dict / object whose `handled` is true.
    `match` narrows further (it must not write); `button_prefix` matches
    verified sign_ref payloads only; `kind` filters on ctx.kind."""
    audiences = (audience,) if isinstance(audience, str) else tuple(audience)
    for a in audiences:
        if a != "*" and a not in AUDIENCES:
            raise ValueError(f"audience must be one of {AUDIENCES} or '*'")
    kinds = (kind,) if isinstance(kind, str) else (tuple(kind) if kind else None)

    def deco(fn: Callable[[InboundCtx], Any]) -> Callable[[InboundCtx], Any]:
        name = f"{fn.__module__}.{fn.__qualname__}"
        if "guest" in audiences:
            _check_guest_priority(fn.__module__, name, priority)
        with _lock:
            _wa_handlers[:] = [h for h in _wa_handlers if h.name != name]
            _wa_handlers.append(
                _WaHandler(fn, audiences, priority, next(_order), match, button_prefix, kinds, name)
            )
        return fn

    return deco


def _check_guest_priority(py_module: str, name: str, priority: int) -> None:
    """A module's guest handler should sit at one of its frozen slots. Logged,
    not raised: a wrong number reorders dispatch (bad) but refusing to import
    the module would drop guest messages altogether (worse)."""
    parts = py_module.split(".")
    if len(parts) < 3 or parts[:2] != ["clefs", "modules"]:
        return
    allowed = sorted(p for key, p in GUEST_PRIORITY.items() if key.split(".", 1)[0] == parts[2])
    if priority not in allowed:
        log.warning(
            "guest WhatsApp handler %s registered at priority %s; the frozen slots for %s are %s (GUEST_PRIORITY)",
            name, priority, parts[2], allowed or "none",
        )


def _handled(result: Any) -> bool:
    """Truthy wins. A dict or result object decides by its `handled` field
    when it has one ({"handled": False, ...} passes the message on)."""
    if isinstance(result, dict):
        return bool(result["handled"]) if "handled" in result else bool(result)
    if result is not None and not isinstance(result, (bool, int, str)) and hasattr(result, "handled"):
        return bool(result.handled)
    return bool(result)


def whatsapp_handlers() -> list[_WaHandler]:
    with _lock:
        return sorted(_wa_handlers, key=lambda h: (h.priority, h.order))


def _wa_matches(h: _WaHandler, ctx: InboundCtx) -> bool:
    if "*" not in h.audiences and ctx.audience not in h.audiences:
        return False
    if h.kinds and ctx.kind not in h.kinds:
        return False
    if h.button_prefix is not None and not (ctx.button_ref and ctx.button_ref.startswith(h.button_prefix)):
        return False
    if h.match is not None:
        try:
            return bool(h.match(ctx))
        except Exception:
            log.exception("match() of %s failed", h.name)
            return False
    return True


@dataclass(frozen=True)
class _StatusHook:
    fn: Callable[[Any, dict, str, str | None], Any]
    purpose_prefix: str
    name: str


_status_hooks: list[_StatusHook] = []


def on_outbound_status(purpose_prefix: str) -> Callable:
    """fn(cur, outbound_row, status, error_code) for rows whose purpose starts
    with the prefix; status is sent | delivered | read | failed.

    Sources: Meta's status webhook (WhatsApp, each distinct status once, in
    arrival order, so "read" may precede "delivered"), and the outbox itself
    for what no webhook reports: a failure the provider returned at send time
    (any channel), a queued session message whose 24 h window closed before
    delivery ("failed" with error_code "outside_window"; row status
    outside_window), and "sent" for email and SMS. The synchronous refusal
    of send_whatsapp_text / _interactive / _document (window already closed
    when called) returns ok=False to the caller and fires no hook. On
    "failed", row["meta"] carries retried_by / fallback_id when the outbox
    is already re-sending (131056, 131049) or sent the fallback template
    (131047, outside_window); that follow-up row reports its own statuses
    under the same purpose. Runs in the caller's transaction inside a
    savepoint; an exception is logged. An empty prefix hears every purpose."""

    def deco(fn: Callable) -> Callable:
        name = f"{fn.__module__}.{fn.__qualname__}"
        with _lock:
            _status_hooks[:] = [h for h in _status_hooks if h.name != name]
            _status_hooks.append(_StatusHook(fn, purpose_prefix, name))
        return fn

    return deco


def fire_status_hooks(cur: Any, row: dict, status: str, error_code: str | None) -> None:
    """Call every on_outbound_status hook whose prefix matches row["purpose"],
    each in its own savepoint so one broken module cannot undo the status
    update or the other hooks."""
    with _lock:
        hooks = list(_status_hooks)
    for hook in hooks:
        if not (row.get("purpose") or "").startswith(hook.purpose_prefix):
            continue
        cur.execute("SAVEPOINT clefs_status_hook")
        try:
            hook.fn(cur, row, status, error_code)
            cur.execute("RELEASE SAVEPOINT clefs_status_hook")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT clefs_status_hook")
            cur.execute("RELEASE SAVEPOINT clefs_status_hook")
            log.exception("status hook %s failed", hook.name)


# ─── Inbound email: which sender verdicts count ───
# Authentication-Results and Received-SPF are ordinary header lines, and the
# sender writes every header of the message they submit. A stranger can add
# "Authentication-Results: x; dmarc=pass header.from=hotel.ae" to their own
# mail, and a dict built from the message's headers used to keep whichever
# copy came last, so ingest auto-committed and reviews accepted forged mail.
#
# The source of truth is Resend's API: its `authentication` object holds the
# SPF, DKIM and DMARC results Resend's receiving server computed, never
# anything read from the message. from_resend drops every AUTH_VERDICT_HEADERS
# line the message carried and writes exactly one Received-SPF
# ("<spf> receiver=inbound.resend.com") and one Authentication-Results
# ("inbound.resend.com; spf=..; dkim=..; dmarc=..") from those results.
# Resend's results name no domain (no header.d=, no smtp.mailfrom=), so only
# dmarc=pass ties a pass to the From domain; ingest and reviews read it so.
#
# Every payload that reaches InboundEmail is then held to the same rule, so
# nothing but a verdict we wrote reaches a module. A verdict counts only when:
#   - it names one of our verifiers: the receiver= host of a Received-SPF is
#     in INBOUND_EMAIL_SPF_VERIFIERS, the authserv-id of an
#     Authentication-Results is in INBOUND_EMAIL_AUTHSERV_IDS (both default to
#     RESEND_INBOUND_VERIFIER, the name from_resend writes), and
#   - exactly one copy names one. We write each verdict once, so a second
#     copy claiming our verifier is a forgery and voids both (fail closed: the
#     batch or review waits for a person). This does not rely on header order.
# Every other header keeps its first copy (case-insensitive).

AUTH_VERDICT_HEADERS = ("authentication-results", "received-spf")


def _host_is(host: str | None, trusted: list[str], *, subdomains: bool) -> bool:
    h = (host or "").strip().strip("\"<>").lower().rstrip(".")
    return bool(h) and any(h == t or (subdomains and h.endswith("." + t)) for t in trusted if t)


def _names_our_verifier(key: str, value: str) -> bool:
    s = get_settings()
    if key == "received-spf":
        # RFC 7208 9.1 key-value list; from_resend writes receiver=inbound.resend.com.
        m = re.search(r"\breceiver=\s*\"?([^\s;\"]+)", value, re.IGNORECASE)
        host = m.group(1).rsplit("@", 1)[-1] if m else None
        return _host_is(host, s.INBOUND_EMAIL_SPF_VERIFIERS, subdomains=True)
    # RFC 8601 2.2: the authserv-id is the first token, compared exactly.
    first = value.split(";", 1)[0].split()
    return bool(first) and _host_is(first[0], s.INBOUND_EMAIL_AUTHSERV_IDS, subdomains=False)


def trusted_headers(header_list: list[tuple[str, str]]) -> dict[str, str]:
    """The header dict handlers see: first copy of each ordinary header, and
    a sender verdict (AUTH_VERDICT_HEADERS) only when exactly one copy was
    written by our own verifier. Forged or ambiguous verdicts are dropped."""
    out: dict[str, str] = {}
    seen: set[str] = set()
    verdicts: dict[str, list[tuple[str, str]]] = {}
    for name, value in header_list:
        key = name.strip().lower()
        if key in AUTH_VERDICT_HEADERS:
            if _names_our_verifier(key, value):
                verdicts.setdefault(key, []).append((name, value))
            continue
        if key and key not in seen:
            seen.add(key)
            out[name] = value
    for key, found in verdicts.items():
        if len(found) == 1:
            out[found[0][0]] = found[0][1]
        else:
            log.warning("inbound email has %d copies of %s naming our verifier: verdict ignored", len(found), key)
    return out


@dataclass
class InboundEmail:
    cur: Any
    inbound_id: int
    hotel_slug: str
    purpose: str  # inquiries | reviews | ingest | <raw token>
    purpose_token: str | None
    ref: str | None
    alias: str
    sender: str | None
    sender_name: str | None
    to: str
    subject: str | None
    text: str | None
    html: str | None
    stripped_reply: str | None
    message_id: str
    attachments: list[Any] = field(default_factory=list)  # files.FileRef
    skipped_attachments: list[dict] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    sender_hmac: bytes | None = None  # contact_hmac(hotel_slug, "email", sender); also on the inbound row
    # Every header in message order, duplicates kept (the payload's Headers
    # array). None: built from `headers` (a dict cannot hold duplicates).
    header_list: list[tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        # `headers` is what handlers read, so it only ever carries sender
        # verdicts our own verifier wrote (see trusted_headers). A forged
        # "Authentication-Results: x; dmarc=pass" never reaches a module.
        if self.header_list is None:
            self.header_list = [(str(k), str(v)) for k, v in (self.headers or {}).items()]
        self.headers = trusted_headers(self.header_list)

    # ─── Sender authentication (verdicts from trusted_headers only) ───
    # A handler that trusts the From address (reviews' forwarded-email
    # sender check, ingest's auto-commit) must tie a pass to the From domain:
    # dmarc=pass, or an SPF / DKIM pass whose domain aligns with From. None
    # means no trusted verdict, which is not a pass.

    def _header(self, name: str) -> str:
        want = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == want), "") or ""

    @property
    def spf(self) -> str | None:
        """First word of Received-SPF, lowercased: pass, fail, softfail, neutral, none, ..."""
        v = self._header("Received-SPF").strip()
        return v.split()[0].lower().rstrip(";") if v else None

    @property
    def dkim(self) -> str | None:
        """dkim=<result> from Authentication-Results (pass, fail, none, ...)."""
        m = re.search(r"\bdkim=([a-z]+)", self._header("Authentication-Results").lower())
        return m.group(1) if m else None


@dataclass(frozen=True)
class _EmailHandler:
    fn: Callable[[InboundEmail], Any]
    purpose: str
    priority: int
    order: int
    match: Callable[[InboundEmail], bool] | None
    name: str


_email_handlers: list[_EmailHandler] = []


def on_inbound_email(purpose: str = "*", *, priority: int = 50, match: Callable[[InboundEmail], bool] | None = None) -> Callable:
    def deco(fn: Callable[[InboundEmail], Any]) -> Callable[[InboundEmail], Any]:
        name = f"{fn.__module__}.{fn.__qualname__}"
        with _lock:
            _email_handlers[:] = [h for h in _email_handlers if h.name != name]
            _email_handlers.append(_EmailHandler(fn, purpose, priority, next(_order), match, name))
        return fn

    return deco


def unregister_handler(fn: Callable) -> None:
    """Tests only."""
    name = f"{fn.__module__}.{fn.__qualname__}"
    with _lock:
        _wa_handlers[:] = [h for h in _wa_handlers if h.name != name]
        _email_handlers[:] = [h for h in _email_handlers if h.name != name]
        _status_hooks[:] = [h for h in _status_hooks if h.name != name]


# ─── WhatsApp processing ───


def _e164(wa_id: str | None) -> str:
    digits = "".join(ch for ch in (wa_id or "") if ch.isdigit())
    return "+" + digits if digits else ""


def _sender_hmac(hotel_slug: str | None, kind: str, raw: str | None, cur: Any) -> bytes | None:
    """contact_hmac of an inbound sender, or None when there is no hotel (the
    HMAC is per hotel) or the address does not parse. Never raises: a sender
    we cannot hash is still a message we must record and dispatch."""
    if not hotel_slug or not raw:
        return None
    from clefs.core.privacy import contact_hmac

    try:
        return contact_hmac(hotel_slug, kind, raw, cur=cur)  # type: ignore[arg-type]
    except (ClefsError, ValueError):  # invalid address, or NotConfigured (a ClefsError) in production
        return None


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return clock.now()


def _resolve_sender(cur: Any, sender: str, phone_number_id: str | None) -> dict:
    """Audience, hotel and identity of an inbound WhatsApp sender."""
    out: dict[str, Any] = {"audience": "unknown", "hotel_slug": None, "user_id": None, "staff_member_id": None}
    hotel_slug = None
    if phone_number_id:
        cur.execute("SELECT slug FROM hotels WHERE whatsapp_phone_number_id = %s LIMIT 1", (phone_number_id,))
        row = cur.fetchone()
        hotel_slug = row["slug"] if row else None
    cur.execute("SELECT id FROM users WHERE phone_e164 = %s AND is_active", (sender,))
    user = cur.fetchone()
    if user is not None:
        out["user_id"] = user["id"]
        cur.execute(
            "SELECT hotel_slug FROM hotel_memberships WHERE user_id = %s AND is_active ORDER BY hotel_slug",
            (user["id"],),
        )
        slugs = [r["hotel_slug"] for r in cur.fetchall()]
        if hotel_slug is None or hotel_slug in slugs:
            out["audience"] = "staff"
            out["hotel_slug"] = hotel_slug or (slugs[0] if len(slugs) == 1 else None)
            return out
    cur.execute(
        """
        SELECT id, hotel_slug FROM staff_members
        WHERE (whatsapp_e164 = %s OR phone_number = %s) AND is_active AND (%s::TEXT IS NULL OR hotel_slug = %s)
        ORDER BY id LIMIT 1
        """,
        (sender, sender, hotel_slug, hotel_slug),
    )
    staff = cur.fetchone()
    if staff is not None:
        out.update(audience="staff", hotel_slug=staff["hotel_slug"], staff_member_id=staff["id"])
        return out
    if hotel_slug is not None:
        out.update(audience="guest", hotel_slug=hotel_slug)
    return out


def _message_fields(m: dict) -> dict:
    kind = m.get("type") or "unknown"
    out: dict[str, Any] = {"kind": kind, "text": None, "button_id": None, "button_title": None}
    if kind == "text":
        out["text"] = (m.get("text") or {}).get("body")
    elif kind == "button":
        b = m.get("button") or {}
        out.update(button_id=b.get("payload"), button_title=b.get("text"), text=b.get("text"))
    elif kind == "interactive":
        it = m.get("interactive") or {}
        sub = it.get("type")
        reply = it.get(sub) or {}
        out.update(kind=sub or "interactive", button_id=reply.get("id"), button_title=reply.get("title"), text=reply.get("title"))
    elif kind in ("image", "document", "audio", "video", "sticker"):
        media = m.get(kind) or {}
        out["text"] = media.get("caption")
    return out


def _dispatch_message(cur: Any, value: dict, m: dict, contacts: dict[str, str]) -> str:
    provider_id = m.get("id")
    if not provider_id:
        return "ignored"
    sender = _e164(m.get("from"))
    phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
    fields = _message_fields(m)
    who = _resolve_sender(cur, sender, phone_number_id)
    # Hashed before the insert so the row carries it: modules look senders up
    # later by (hotel_slug, sender_hmac) (outbox.wa_window_open_until).
    sender_hmac = _sender_hmac(who["hotel_slug"], "phone", sender, cur)
    cur.execute(
        """
        INSERT INTO inbound_messages (channel, provider_id, kind, hotel_slug, sender, recipient, audience, payload,
                                      received_at, sender_hmac)
        VALUES ('whatsapp', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (provider_id) DO NOTHING RETURNING id
        """,
        (provider_id, fields["kind"], who["hotel_slug"], sender, phone_number_id, who["audience"], db.Json(m), clock.now(),
         sender_hmac),
    )
    row = cur.fetchone()
    if row is None:
        return "duplicate"
    at = _ts(m.get("timestamp"))
    window = outbox.open_window(cur, phone_number_id or outbox_platform_id(), sender, at=at, hotel_slug=who["hotel_slug"])
    ctx = InboundCtx(
        cur=cur,
        inbound_id=row["id"],
        provider_id=provider_id,
        kind=fields["kind"],
        audience=who["audience"],
        sender=sender,
        recipient_id=phone_number_id,
        hotel_slug=who["hotel_slug"],
        sender_name=contacts.get(m.get("from") or ""),
        text=fields["text"],
        button_id=fields["button_id"],
        button_ref=tokens.verify_ref(fields["button_id"]) if fields["button_id"] else None,
        button_title=fields["button_title"],
        context_id=(m.get("context") or {}).get("id"),
        referral=m.get("referral"),
        user_id=who["user_id"],
        staff_member_id=who["staff_member_id"],
        sender_hmac=sender_hmac,
        window_open_until=window,
        timestamp=at,
        message=m,
    )
    return _run_handlers(cur, row["id"], [h for h in whatsapp_handlers() if _wa_matches(h, ctx)], ctx)


def _run_handlers(cur: Any, inbound_id: int, handlers: list[Any], ctx: Any) -> str:
    for h in handlers:
        cur.execute("SAVEPOINT clefs_inbound_handler")
        try:
            result = h.fn(ctx)
            cur.execute("RELEASE SAVEPOINT clefs_inbound_handler")
        except Exception as exc:
            cur.execute("ROLLBACK TO SAVEPOINT clefs_inbound_handler")
            cur.execute("RELEASE SAVEPOINT clefs_inbound_handler")
            log.exception("inbound handler %s failed", h.name)
            cur.execute(
                "UPDATE inbound_messages SET status = 'error', handled_by = %s, error = %s, handled_at = %s WHERE id = %s",
                (h.name, f"{type(exc).__name__}: {exc}"[:1000], clock.now(), inbound_id),
            )
            return "error"
        if _handled(result):
            cur.execute(
                "UPDATE inbound_messages SET status = 'handled', handled_by = %s, handled_at = %s WHERE id = %s",
                (h.name, clock.now(), inbound_id),
            )
            return "handled"
    cur.execute("UPDATE inbound_messages SET status = 'unhandled', handled_at = %s WHERE id = %s", (clock.now(), inbound_id))
    return "unhandled"


def outbox_platform_id() -> str:
    from clefs.notify.send import platform_sender_id

    return platform_sender_id() or "platform"


def _apply_status(cur: Any, value: dict, st: dict) -> str:
    wamid = st.get("id")
    status = st.get("status")
    if not wamid or status not in ("sent", "delivered", "read", "failed"):
        return "ignored"
    cur.execute(
        """
        INSERT INTO inbound_messages (channel, provider_id, kind, sender, recipient, payload, status, received_at)
        VALUES ('whatsapp', %s, 'status', %s, %s, %s, 'handled', %s)
        ON CONFLICT (provider_id) DO NOTHING RETURNING id
        """,
        (f"status:{wamid}:{status}", _e164(st.get("recipient_id")), (value.get("metadata") or {}).get("phone_number_id"),
         db.Json(st), clock.now()),
    )
    if cur.fetchone() is None:
        return "duplicate"
    cur.execute("SELECT * FROM outbound_messages WHERE provider_id = %s FOR UPDATE", (wamid,))
    row = cur.fetchone()
    if row is None:
        return "unknown_message"
    row = dict(row)
    at = _ts(st.get("timestamp"))
    errors = st.get("errors") or []
    code = str(errors[0].get("code")) if errors else None
    pricing = st.get("pricing") or {}
    changes: dict[str, Any] = {}
    if pricing:
        changes["billable"] = pricing.get("billable")
        changes["pricing_category"] = pricing.get("category")
    if status == "failed":
        changes.update(status="failed", failed_at=at, error_code=code, error=(errors[0].get("title") if errors else None))
    elif outbox.STATUS_RANK.get(status, 0) > outbox.STATUS_RANK.get(row["status"], -1):
        changes["status"] = status
        if status == "delivered":
            changes["delivered_at"] = at
        if status == "read":
            changes["read_at"] = at
            changes.setdefault("delivered_at", row.get("delivered_at") or at)
    if changes:
        row = outbox._update(cur, row["id"], changes)
    if status == "failed":
        now = clock.now()
        if code == "131047":
            outbox._fallback_template(cur, row, "131047")
        elif code == "131049":
            outbox.retry_copy(cur, row, not_before=now + timedelta(hours=24), reason="131049")
        elif code == "131056":
            outbox.retry_copy(cur, row, not_before=now + outbox._backoff(int((row.get("meta") or {}).get("retry", 0)) + 1), reason="131056")
        # Reload: the follow-up stamped retried_by / fallback_id on this row,
        # which is how a hook tells "being re-sent" from "lost".
        cur.execute("SELECT * FROM outbound_messages WHERE id = %s", (row["id"],))
        row = dict(cur.fetchone())
    fire_status_hooks(cur, row, status, code)
    return status


def process_whatsapp(payload: dict) -> dict:
    """Process one verified webhook body. Each message/status runs in its own
    transaction so one bad entry cannot drop the rest (Meta retries the whole
    body on a non-200, and dedupe makes that safe)."""
    counts: dict[str, int] = {}
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []}
            for m in value.get("messages") or []:
                outcome = _safe(lambda cur, m=m: _dispatch_message(cur, value, m, contacts))
                counts[outcome] = counts.get(outcome, 0) + 1
            for st in value.get("statuses") or []:
                outcome = _safe(lambda cur, st=st: _apply_status(cur, value, st))
                counts[f"status_{outcome}"] = counts.get(f"status_{outcome}", 0) + 1
    return counts


def _safe(fn: Callable[[Any], str]) -> str:
    try:
        with db.db() as cur:
            return fn(cur)
    except Exception:
        log.exception("webhook entry failed")
        return "error"


# ─── Email addresses ───
# One grammar for every inbound address (04 spec F4):
#     <hotel_alias>+<purpose>[-<ref>]@in.clefs.world
# The purpose is the token before the first "-"; everything after it is the
# ref (it may itself contain "-"). Addresses are compared lowercased, since
# mail systems do not preserve case reliably, so a ref must not rely on case.

_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_TOKEN_RE = re.compile(r"^[a-z0-9_.]+$")  # no "-": it ends the purpose
_REF_RE = re.compile(r"^[a-z0-9_.-]+$")


def inbound_domain() -> str:
    """INBOUND_EMAIL_DOMAIN, else in.clefs.world. Never "any domain": a Cc to
    reservations@someone-else.com must not route to the hotel whose alias
    happens to be "reservations"."""
    return (get_settings().INBOUND_EMAIL_DOMAIN or EMAIL_DOMAIN_DEFAULT).strip().lower()


def purpose_for_token(token: str | None) -> str:
    """Address token -> the purpose handlers register for ("inq" -> "inquiries",
    none -> "ingest"); an unknown token is its own purpose."""
    return EMAIL_PURPOSES.get(token, token or "ingest")


def inbound_address(alias: str, purpose: str | None = None, ref: str | int | None = None, *, domain: str | None = None) -> str:
    """Build an inbound address: inbound_address("demo", "inquiries", 42) ->
    "demo+inq-42@in.clefs.world"; ("demo", "reviews") -> "demo+reviews@...";
    ("demo") -> "demo@..." (ingest). Raises ValueError for anything the parser
    would read back differently."""
    a = (alias or "").strip().lower()
    if not _ALIAS_RE.match(a):
        raise ValueError(f"not an inbound email alias: {alias!r}")
    p = (purpose or "").strip().lower() or None
    token = None if p in (None, "ingest") else EMAIL_TOKENS.get(p, p)
    ref_s = None if ref is None or str(ref).strip() == "" else str(ref).strip().lower()
    if token is None:
        if ref_s is not None:
            raise ValueError("a ref needs a purpose (ingest addresses carry none)")
        local = a
    else:
        if not _TOKEN_RE.match(token):
            raise ValueError(f"purpose token must be [a-z0-9_.] (no '-'): {token!r}")
        if ref_s is not None and not _REF_RE.match(ref_s):
            raise ValueError(f"ref must be [a-z0-9_.-]: {ref!r}")
        local = f"{a}+{token}" + (f"-{ref_s}" if ref_s is not None else "")
    if len(local) > 64:
        raise ValueError("the address local part is longer than 64 characters")
    return f"{local}@{(domain or inbound_domain()).strip().lower()}"


def hotel_inbound_address(cur: Any, hotel_slug: str, purpose: str | None = None, ref: str | int | None = None) -> str | None:
    """The hotel's inbound address for a purpose, or None while the hotel has
    no hotels.inbound_email_alias (email intake not set up: callers degrade)."""
    cur.execute("SELECT inbound_email_alias FROM hotels WHERE slug = %s", (hotel_slug,))
    row = cur.fetchone()
    alias = (row or {}).get("inbound_email_alias")
    return inbound_address(alias, purpose, ref) if alias else None


# ─── Email processing ───


def parse_address(address: str, domain: str | None) -> tuple[str, str | None, str | None] | None:
    """"demo+inq-42@in.clefs.world" -> ("demo", "inq", "42"); None if another domain."""
    addr = (address or "").strip().strip("<>").lower()
    if "@" not in addr:
        return None
    local, _, dom = addr.rpartition("@")
    if domain and dom != domain.lower():
        return None
    alias, plus, rest = local.partition("+")
    if not alias:
        return None
    if not plus or not rest:
        return alias, None, None
    token, dash, ref = rest.partition("-")
    return alias, (token or None), (ref or None) if dash else None


def _recipients(p: dict) -> list[str]:
    out: list[str] = []
    for key in ("ToFull", "CcFull", "BccFull"):
        for r in p.get(key) or []:
            if r.get("Email"):
                out.append(r["Email"])
    # The envelope recipients (one address, or Resend's received_for list):
    # forwarded mail names our address only here, its To being the hotel's
    # own mailbox.
    orig = p.get("OriginalRecipient")
    for r in ([orig] if isinstance(orig, str) else orig or []):
        if isinstance(r, str) and r:
            out.append(r)
    for key in ("To", "Cc"):
        for part in (p.get(key) or "").split(","):
            if "@" in part:
                out.append(part.split("<")[-1].strip().rstrip(">"))
    return list(dict.fromkeys(out))


def _file_purpose(purpose: str) -> str:
    """The purpose token comes from a stranger's address; files accept only
    [a-z0-9_.:-], so anything else is folded to '_'."""
    return ("email." + re.sub(r"[^a-z0-9_.:-]", "_", purpose.lower()))[:64]


def process_email(p: dict) -> dict:
    """Route one inbound message, given as our email payload (Postmark-style
    field names; from_resend builds it): hotel by alias, dedupe on MessageID,
    attachments to files, then @on_inbound_email handlers."""
    from clefs import files

    domain = inbound_domain()
    message_id = str(p.get("MessageID") or "")
    if not message_id:
        return {"ok": True, "status": "ignored"}
    with db.db() as cur:
        target = None
        for addr in _recipients(p):
            parsed = parse_address(addr, domain)
            if parsed is None:
                continue
            cur.execute("SELECT slug FROM hotels WHERE lower(inbound_email_alias) = %s", (parsed[0],))
            row = cur.fetchone()
            if row is not None:
                target = (row["slug"], addr, *parsed)
                break
        sender = ((p.get("FromFull") or {}).get("Email") or p.get("From") or "").strip().lower() or None
        if sender and "<" in sender:  # "Name <addr>" when FromFull is missing
            sender = sender.rsplit("<", 1)[-1].rstrip(">").strip() or None
        sender_hmac = _sender_hmac(target[0] if target else None, "email", sender, cur)
        slim = {k: v for k, v in p.items() if k != "Attachments"}
        slim["Attachments"] = [
            {"Name": a.get("Name"), "ContentType": a.get("ContentType"), "ContentLength": a.get("ContentLength")}
            for a in p.get("Attachments") or []
        ]
        cur.execute(
            """
            INSERT INTO inbound_messages (channel, provider_id, kind, hotel_slug, sender, recipient, payload, received_at,
                                          status, sender_hmac)
            VALUES ('email', %s, 'email', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider_id) DO NOTHING RETURNING id
            """,
            (
                f"email:{message_id}",
                target[0] if target else None,
                sender,
                target[1] if target else None,
                db.Json(slim),
                clock.now(),
                "received" if target else "ignored",
                sender_hmac,
            ),
        )
        row = cur.fetchone()
        if row is None:
            return {"ok": True, "status": "duplicate"}
        if target is None:
            return {"ok": True, "status": "unrouted"}
        slug, to_addr, alias, token, ref = target
        purpose = purpose_for_token(token)
        stored, skipped = [], []
        max_bytes = get_settings().FILE_MAX_BYTES
        for a in p.get("Attachments") or []:
            declared = a.get("ContentLength")
            if isinstance(declared, int) and declared > max_bytes:
                # Never downloaded: fetch_resend_email lists it with its size only.
                skipped.append({"name": a.get("Name"), "reason": "too_large"})
                continue
            try:
                data = base64.b64decode(a.get("Content") or "", validate=False)
            except (binascii.Error, ValueError):
                skipped.append({"name": a.get("Name"), "reason": "bad_encoding"})
                continue
            if len(data) > max_bytes:
                skipped.append({"name": a.get("Name"), "reason": "too_large"})
                continue
            stored.append(
                files.put_file(
                    cur, slug, data, filename=a.get("Name") or "attachment", content_type=a.get("ContentType"),
                    purpose=_file_purpose(purpose), source="email",
                )
            )
        mail = InboundEmail(
            cur=cur,
            inbound_id=row["id"],
            hotel_slug=slug,
            purpose=purpose,
            purpose_token=token,
            ref=ref,
            alias=alias,
            sender=sender,
            sender_name=(p.get("FromFull") or {}).get("Name") or p.get("FromName"),
            to=to_addr,
            subject=p.get("Subject"),
            text=p.get("TextBody"),
            html=p.get("HtmlBody"),
            stripped_reply=p.get("StrippedTextReply"),
            message_id=message_id,
            attachments=stored,
            skipped_attachments=skipped,
            header_list=[(str(h.get("Name") or ""), str(h.get("Value") or "")) for h in p.get("Headers") or []],
            raw=slim,
            sender_hmac=sender_hmac,
        )
        with _lock:
            handlers = sorted(_email_handlers, key=lambda h: (h.priority, h.order))
        matching = []
        for h in handlers:
            if h.purpose not in ("*", purpose):
                continue
            if h.match is not None and not h.match(mail):
                continue
            matching.append(h)
        status = _run_handlers(cur, row["id"], matching, mail)
    return {"ok": True, "status": status, "purpose": purpose, "hotel_slug": slug, "attachments": len(stored)}


# ─── Resend inbound ───
# The email.received webhook carries metadata only (Resend keeps bodies and
# attachments out of it, so no request size limit applies). The message and
# its attachments are read back with RESEND_API_KEY, converted to our email
# payload and routed by process_email. A Resend error or timeout answers 5xx,
# so Resend delivers the webhook again later; MessageID dedupe makes the
# repeat harmless.

RESEND_RECEIVING_URL = "https://api.resend.com/emails/receiving"
ATTACHMENT_PAGES_MAX = 10  # of 100 attachments each
_RESEND_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# The sender chooses the Message-ID. One that could not be a UNIQUE index key
# (inbound_messages.provider_id) or a References token gives way to Resend's id.
_MESSAGE_ID_RE = re.compile(r"[^\s<>]{1,250}")
_VERDICT_RE = re.compile(r"[a-z_]{1,40}")  # pass | fail | gray | processing_failed | unknown


def from_resend(msg: dict, attachments: list[dict] | None = None) -> dict:
    """A received email from Resend's API (GET /emails/receiving/{id}) as our
    email payload, ready for process_email. `attachments` are already in
    payload form (fetch_resend_email).

    Sender verdicts come only from the API's `authentication` object: every
    Authentication-Results and Received-SPF line in the message is dropped
    (the sender wrote those) and one of each is written from the API, naming
    RESEND_INBOUND_VERIFIER (see AUTH_VERDICT_HEADERS). Those results are
    about the From header, so when it names anything but exactly the API's
    sender, none are written: nobody could say whose domain they proved."""
    email_id = str(msg.get("id") or "")
    message_headers = msg.get("headers") if isinstance(msg.get("headers"), dict) else {}
    headers: list[dict] = []
    from_values: list[str] = []
    for name, value in message_headers.items():
        key = str(name).strip().lower()
        for item in (value if isinstance(value, list) else [value]):  # a repeated header may come as a list
            v = "" if item is None else str(item)
            if key == "from":
                from_values.append(v)
            if key not in AUTH_VERDICT_HEADERS:
                headers.append({"Name": str(name), "Value": v})
    api_from = str(msg.get("from") or "").strip()
    sender = parseaddr(api_from)[1]
    named = getaddresses(from_values)
    one_sender = len(named) == 1 and bool(sender) and named[0][1].lower() == sender.lower()
    if one_sender:
        shown, display_name = from_values[0], named[0][0]  # the header carries the display name
    else:
        shown, display_name = api_from, parseaddr(api_from)[0]
    if from_values and not one_sender:
        log.warning("Resend email %s: its From header does not name exactly the sender; no sender verdict kept",
                    email_id)
    elif sender:
        auth = msg.get("authentication") if isinstance(msg.get("authentication"), dict) else {}
        verdicts = {k: str(auth.get(k) or "").strip().lower() for k in ("spf", "dkim", "dmarc")}
        verdicts = {k: v for k, v in verdicts.items() if _VERDICT_RE.fullmatch(v)}
        if "spf" in verdicts:
            headers.append({"Name": "Received-SPF", "Value": f"{verdicts['spf']} receiver={RESEND_INBOUND_VERIFIER}"})
        if verdicts:
            results = "; ".join(f"{k}={v}" for k, v in verdicts.items())
            headers.append({"Name": "Authentication-Results", "Value": f"{RESEND_INBOUND_VERIFIER}; {results}"})

    def addrs(key: str) -> list[str]:
        found = msg.get(key)
        return [str(a).strip() for a in ([found] if isinstance(found, str) else found or []) if a and str(a).strip()]

    def full(key: str) -> list[dict]:
        return [{"Email": a, "Name": n} for n, a in getaddresses(addrs(key)) if a]

    message_id = str(msg.get("message_id") or "").strip()
    if message_id.startswith("<") and message_id.endswith(">"):
        # Bare, the way In-Reply-To / References tokens are read (inquiries threads on them).
        message_id = message_id[1:-1].strip()
    return {
        "MessageID": message_id if _MESSAGE_ID_RE.fullmatch(message_id) else email_id,
        "ResendEmailID": email_id,
        "From": shown,
        "FromFull": {"Email": sender, "Name": display_name},
        "To": ", ".join(addrs("to")),
        "ToFull": full("to"),
        "Cc": ", ".join(addrs("cc")),
        "CcFull": full("cc"),
        "BccFull": full("bcc"),
        "OriginalRecipient": addrs("received_for"),
        "ReplyTo": ", ".join(addrs("reply_to")),
        "Subject": msg.get("subject"),
        "TextBody": msg.get("text"),
        "HtmlBody": msg.get("html"),
        "Headers": headers,
        "Attachments": attachments or [],
    }


def _resend_json(r: Any, what: str) -> dict:
    """A 2xx JSON object from Resend, else 502 (Resend then retries the webhook)."""
    if r.status_code >= 400:
        log.warning("Resend answered %s for %s: %s", r.status_code, what, r.text[:300])
        raise ClefsError(502, f"Resend answered {r.status_code} for {what}", "resend_error")
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ClefsError(502, f"Resend sent no JSON object for {what}", "resend_error")
    return body


def _resend_attachment(client: Any, a: dict, max_bytes: int) -> dict:
    """One listed attachment in payload form. Over FILE_MAX_BYTES it is not
    downloaded at all (process_email records it as too_large from its
    ContentLength); the download is capped as well, in case the listed size
    is missing or wrong."""
    out: dict[str, Any] = {"Name": a.get("filename") or "attachment", "ContentType": a.get("content_type"),
                           "ContentLength": a.get("size")}
    if isinstance(a.get("size"), int) and a["size"] > max_bytes:
        return out
    url = str(a.get("download_url") or "")
    if not url.startswith("https://"):
        raise ClefsError(502, "Resend listed an attachment without a download link", "resend_error")
    chunks, size = [], 0
    # A signed CDN link: no Authorization header (the API key never leaves api.resend.com).
    with client.stream("GET", url, follow_redirects=True) as r:
        if r.status_code >= 400:
            raise ClefsError(502, f"Resend answered {r.status_code} for an attachment", "resend_error")
        for chunk in r.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                out["ContentLength"] = size
                return out
            chunks.append(chunk)
    data = b"".join(chunks)
    out.update(Content=base64.b64encode(data).decode("ascii"), ContentLength=len(data))
    return out


def _resend_attachments(client: Any, url: str, auth: dict, max_bytes: int) -> list[dict]:
    out: list[dict] = []
    params: dict[str, Any] = {"limit": 100}
    for _ in range(ATTACHMENT_PAGES_MAX):
        page = _resend_json(client.get(f"{url}/attachments", params=params, headers=auth), "the attachments")
        listed = [a for a in page.get("data") or [] if isinstance(a, dict)]
        out += [_resend_attachment(client, a, max_bytes) for a in listed]
        if not page.get("has_more") or not listed or not listed[-1].get("id"):
            break
        params["after"] = listed[-1]["id"]
    return out


def fetch_resend_email(email_id: str) -> dict | None:
    """Read one received email back from Resend and convert it (from_resend):
    the message, with inline images left as cid: references to attachments
    (the default would inline them in the HTML as base64), then its
    attachments from their signed links. None when Resend has no such email."""
    import httpx

    s = get_settings()
    if not s.RESEND_API_KEY:
        raise ClefsError(503, "Inbound email is not configured", "not_configured")
    auth = {"Authorization": f"Bearer {s.RESEND_API_KEY}"}
    url = f"{RESEND_RECEIVING_URL}/{email_id}"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
            r = client.get(url, params={"html_format": "cid"}, headers=auth)
            if r.status_code == 404:
                return None
            msg = _resend_json(r, "the email")
            attachments = _resend_attachments(client, url, auth, s.FILE_MAX_BYTES) if msg.get("attachments") else []
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        log.warning("Resend unreachable reading email %s: %s", email_id, exc)
        raise ClefsError(502, "Resend is not reachable right now", "resend_unreachable") from exc
    return from_resend(msg, attachments)


def process_resend(event: Any) -> dict:
    """One verified Resend webhook body. email.received is read back from
    Resend and routed (process_email); any other event type is ignored, and
    so is an email Resend does not have (404, logged)."""
    if not isinstance(event, dict) or event.get("type") != "email.received":
        return {"ok": True, "status": "ignored"}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    email_id = str(data.get("email_id") or "")
    if not _RESEND_ID_RE.fullmatch(email_id):
        log.warning("Resend email.received without a usable email_id: ignored")
        return {"ok": True, "status": "ignored"}
    p = fetch_resend_email(email_id)
    if p is None:
        log.warning("Resend has no received email %s (404): acknowledged, nothing routed", email_id)
        return {"ok": True, "status": "ignored"}
    return process_email(p)
