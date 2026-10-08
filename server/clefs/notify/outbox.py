"""
The outbox: every message is a row first, then a delivery.

    row, deduped = create(cur, channel="whatsapp", kind="template", ...)   # joins the caller's tx
    deliver(cur, row_id)                                                     # the core.notify.deliver task

Why an outbox: a send must not happen for work that rolled back, and a send
that failed must be retried without the caller waiting. create() writes the
row and enqueues core.notify.deliver in the caller's transaction, so nothing
goes out unless that commits. In capture/log modes (no network, no side
effects) the row is also delivered inline so tests and local runs see the
result at once.

Delivery rules (01 spec E6, 02 spec 6.2, 04 spec F4, Canon WhatsApp):
  - idempotency_key is UNIQUE: a second create with the same key returns the
    first row (deduped=True). A deliberate retry needs a new key, by the
    convention attempt_key(base, n) -> "<base>:a<n>" (journeys:
    "gst:{plan_id}:a2"). The outbox's own follow-ups use "<key>:retry:<n>"
    and "<key>:fallback".
  - At least 6 s between WhatsApp messages to the same recipient, whichever
    of our numbers sends (hotel or platform) and whichever module queued it:
    this task is the only place that paces, so Brief, Journeys, Inquiries and
    Service share one clock per guest. A per-recipient advisory lock keeps two
    workers from both reading "nothing sent yet" and sending together.
  - Marketing sends wait for 09:00-18:00 Asia/Dubai.
  - Session messages (text, interactive, document) need the 24 h customer
    service window; outside it the row is `outside_window` and, when the
    caller gave fallback_template, a template goes out instead.
  - Meta 131056 (pair rate limit): retry after 6 s x 2^n.
    Meta 130429 (throughput): hold the whole sender 30 s x 2^n (CONTRACTS
    Part 2; 04 spec F4 says 6 s, pending amendment).
    Meta 131047 (window closed): fail, then the template fallback.
    Meta 131049 (marketing frequency cap): try again after 24 h.
  - Anything else backs off and fails after MAX_ATTEMPTS.
  - on_outbound_status hooks hear about every terminal outcome: Meta's status
    webhook reports WhatsApp sent / delivered / read / failed; this module
    reports what no webhook will, namely a failure at send time (any channel),
    a queued session message whose window closed before it went out
    ("failed", error_code "outside_window"), and "sent" for email and SMS,
    which have no status webhook here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from clefs import db, jobs
from clefs.core import clock
from clefs.notify.transport import LogTransport, ProviderError, b64, get_transport

log = logging.getLogger("clefs.notify")

MAX_ATTEMPTS = 7
PAIR_SPACING = timedelta(seconds=6)
WINDOW = timedelta(hours=24)
MARKETING_TZ = ZoneInfo("Asia/Dubai")
MARKETING_START = time(9, 0)
MARKETING_END = time(18, 0)
SESSION_KINDS = frozenset({"text", "interactive", "document"})
DELIVERED_STATES = ("sent", "delivered", "read")
STATUS_RANK = {"queued": 0, "sending": 1, "sent": 2, "delivered": 3, "read": 4}
DELIVER_TASK = "core.notify.deliver"
# Namespace of the two-int advisory lock that serialises deliveries to one
# recipient (pg_try_advisory_xact_lock(PACE_LOCK_CLASS, hashtext(recipient))).
# The two-int form never collides with the single-bigint locks elsewhere
# (clefs_scheduler, clefs_migrate).
PACE_LOCK_CLASS = 7261


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


def attempt_key(base: str, attempt: int) -> str:
    """The idempotency key of a deliberate re-send (04 spec F4): "<base>:a<n>".
    outbound_messages.idempotency_key is UNIQUE, so attempt 2 of a journey step
    is gst:{plan_id}:a2, never gst:{plan_id} again (that would dedupe to the
    first, failed row and send nothing)."""
    if not base or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt_key needs a base key and an attempt >= 1")
    return f"{base}:a{attempt}"


def next_marketing_slot(now: datetime) -> datetime | None:
    """None when `now` is inside [09:00, 18:00) Dubai, else the next 09:00."""
    local = now.astimezone(MARKETING_TZ)
    if MARKETING_START <= local.time() < MARKETING_END:
        return None
    day = local.date() if local.time() < MARKETING_START else local.date() + timedelta(days=1)
    return datetime.combine(day, MARKETING_START, tzinfo=MARKETING_TZ)


# ─── Windows and holds ───


def open_window(cur: Any, sender_id: str, recipient: str, *, at: datetime, hotel_slug: str | None = None) -> datetime:
    """An inbound message opens (or extends) the 24 h window to reply freely."""
    expires = at + WINDOW
    cur.execute(
        """
        INSERT INTO whatsapp_windows (sender_id, recipient, hotel_slug, last_inbound_at, csw_expires_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (sender_id, recipient) DO UPDATE SET
            last_inbound_at = GREATEST(whatsapp_windows.last_inbound_at, EXCLUDED.last_inbound_at),
            csw_expires_at = GREATEST(whatsapp_windows.csw_expires_at, EXCLUDED.csw_expires_at),
            hotel_slug = COALESCE(EXCLUDED.hotel_slug, whatsapp_windows.hotel_slug)
        RETURNING csw_expires_at
        """,
        (sender_id, recipient, hotel_slug, at, expires),
    )
    return cur.fetchone()["csw_expires_at"]


def window_expires_at(cur: Any, sender_id: str | None, recipient: str) -> datetime | None:
    if not sender_id:
        return None
    cur.execute(
        "SELECT csw_expires_at FROM whatsapp_windows WHERE sender_id = %s AND recipient = %s", (sender_id, recipient)
    )
    row = cur.fetchone()
    return row["csw_expires_at"] if row else None


def window_open(cur: Any, sender_id: str | None, recipient: str, *, at: datetime | None = None) -> bool:
    exp = window_expires_at(cur, sender_id, recipient)
    return exp is not None and exp > (at or clock.now())


def wa_window_open_until(
    cur: Any, hotel_slug: str, *, sender_hmac: bytes | memoryview, at: datetime | None = None
) -> datetime | None:
    """When the 24 h window that this sender last opened with us closes, or
    None when it is closed (04 spec F4).

    For code that holds a contact hmac but no raw number and no InboundCtx:
    journeys ("the guest wrote first today", 131047 handling), inquiries
    (wa_window_expires_at). The sender's latest inbound WhatsApp message at
    this hotel names the pair (our number, their number); the window itself
    comes from whatsapp_windows, so it counts from Meta's message timestamp
    exactly as send-time checks do."""
    from clefs.notify.send import platform_sender_id

    if not sender_hmac:
        return None
    cur.execute(
        """
        SELECT w.csw_expires_at
        FROM inbound_messages i
        JOIN whatsapp_windows w ON w.sender_id = COALESCE(i.recipient, %s) AND w.recipient = i.sender
        WHERE i.hotel_slug = %s AND i.sender_hmac = %s AND i.channel = 'whatsapp' AND i.sender_hmac IS NOT NULL
        ORDER BY i.received_at DESC, i.id DESC
        LIMIT 1
        """,
        (platform_sender_id() or "platform", hotel_slug, bytes(sender_hmac)),
    )
    row = cur.fetchone()
    if row is None:
        return None
    exp = row["csw_expires_at"]
    return exp if exp > (at or clock.now()) else None


def _hold_until(cur: Any, key: str) -> datetime | None:
    cur.execute("SELECT until FROM notify_holds WHERE key = %s", (key,))
    row = cur.fetchone()
    return row["until"] if row else None


def _set_hold(cur: Any, key: str, until: datetime, reason: str) -> None:
    cur.execute(
        """
        INSERT INTO notify_holds (key, until, reason, updated_at) VALUES (%s, %s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET until = GREATEST(notify_holds.until, EXCLUDED.until),
                                        reason = EXCLUDED.reason, updated_at = EXCLUDED.updated_at
        """,
        (key, until, reason, clock.now()),
    )


# ─── Create ───


def create(
    cur: Any,
    *,
    channel: str,
    kind: str,
    recipient: str,
    payload: dict,
    purpose: str,
    hotel_slug: str | None,
    category: str | None = None,
    sender: str = "platform",
    sender_id: str | None = None,
    idempotency_key: str | None = None,
    template_name: str | None = None,
    template_lang: str | None = None,
    meta: dict | None = None,
    not_before: datetime | None = None,
    action_id: int | None = None,
    fallback_of: int | None = None,
    configured: bool = True,
) -> tuple[dict, bool]:
    """Insert the outbox row and enqueue its delivery. Returns (row, deduped)."""
    now = clock.now()
    if category == "marketing":
        slot = next_marketing_slot(max(now, not_before or now))
        if slot is not None:
            not_before = slot
    status = "queued" if configured else "skipped_unconfigured"
    cur.execute(
        """
        INSERT INTO outbound_messages (hotel_slug, channel, kind, purpose, category, sender, sender_id, recipient, payload,
                                       status, idempotency_key, template_name, template_lang, meta, not_before, action_id,
                                       fallback_of, error, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING *
        """,
        (
            hotel_slug,
            channel,
            kind,
            purpose,
            category,
            sender,
            sender_id,
            recipient,
            db.Json(_jsonable(payload)),
            status,
            idempotency_key,
            template_name,
            template_lang,
            db.Json(_jsonable(meta or {})),
            not_before,
            action_id,
            fallback_of,
            None if configured else f"{channel} is not configured",
            now,
            now,
        ),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute("SELECT * FROM outbound_messages WHERE idempotency_key = %s", (idempotency_key,))
        return dict(cur.fetchone()), True
    row = dict(row)
    if status == "queued":
        jobs.enqueue(
            DELIVER_TASK,
            {"outbox_id": row["id"]},
            hotel_slug=hotel_slug,
            run_at=not_before or now,
            dedupe_key=f"{DELIVER_TASK}:{row['id']}",
            cur=cur,
        )
    return row, False


# ─── Cancel ───

# How far cancel_queued follows fallback_of from the row it was given: the
# outbox's own retry copies ("<key>:retry:<n>") and template fallbacks
# ("<key>:fallback") hang off the original, and a copy can have its own.
FOLLOWUP_DEPTH = 5


def cancel_queued(
    cur: Any,
    *,
    idempotency_key: str | None = None,
    outbox_id: int | None = None,
    hotel_slug: str | None = None,
    reason: str = "cancelled",
    followups: bool = True,
) -> dict:
    """Retract a send that has not gone out yet (a journey step whose stay
    was cancelled, a message a newer one replaces).

    The row is found by `idempotency_key` or `outbox_id` (both given: both
    must match), limited to `hotel_slug` when given. Only `queued` rows
    change: they become `cancelled` with meta.cancel_reason and their
    pending core.notify.deliver task is cancelled too. With `followups`,
    queued retry copies and template fallbacks the outbox made from the row
    (fallback_of chain) are cancelled as well, so a retract after a failed
    first attempt still stops the re-send.

    Race-safe against delivery: the UPDATE takes the row lock, so a worker
    mid-send finishes first and the row is then no longer queued (status
    "sent", cancelled False). No on_outbound_status hook fires: the caller
    decided this outcome and already knows it.

    Returns {outbox_id, status, cancelled, cancelled_ids}: `status` is the
    row's status after the call (None and outbox_id None when no row
    matched), `cancelled` whether anything was cancelled, `cancelled_ids`
    every row this call cancelled (the row first, then follow-ups)."""
    if idempotency_key is None and outbox_id is None:
        raise ValueError("pass idempotency_key or outbox_id")
    conds, params = [], []
    if idempotency_key is not None:
        conds.append("idempotency_key = %s")
        params.append(idempotency_key)
    if outbox_id is not None:
        conds.append("id = %s")
        params.append(int(outbox_id))
    if hotel_slug is not None:
        conds.append("hotel_slug = %s")
        params.append(hotel_slug)
    cur.execute(f"SELECT id, status FROM outbound_messages WHERE {' AND '.join(conds)} FOR UPDATE", params)
    target = cur.fetchone()
    if target is None:
        return {"outbox_id": None, "status": None, "cancelled": False, "cancelled_ids": []}
    ids = [target["id"]]
    if followups:
        cur.execute(
            """
            WITH RECURSIVE chain (id, depth) AS (
                SELECT id, 0 FROM outbound_messages WHERE id = %s
                UNION ALL
                SELECT o.id, c.depth + 1 FROM outbound_messages o JOIN chain c ON o.fallback_of = c.id
                WHERE c.depth < %s
            )
            SELECT id FROM chain WHERE id <> %s ORDER BY depth, id
            """,
            (target["id"], FOLLOWUP_DEPTH, target["id"]),
        )
        ids += [r["id"] for r in cur.fetchall()]
    now = clock.now()
    cur.execute(
        """
        UPDATE outbound_messages
           SET status = 'cancelled', updated_at = %s,
               meta = meta || jsonb_build_object('cancel_reason', %s::TEXT, 'cancelled_at', %s::TEXT)
         WHERE id = ANY(%s) AND status = 'queued'
        RETURNING id
        """,
        (now, (reason or "cancelled")[:200], now.isoformat(), ids),
    )
    done = {r["id"] for r in cur.fetchall()}
    for oid in done:
        jobs.cancel(cur, dedupe_key=f"{DELIVER_TASK}:{oid}")
    cur.execute("SELECT status FROM outbound_messages WHERE id = %s", (target["id"],))
    status = cur.fetchone()["status"]
    if done:
        log.info("outbox: cancelled %s (%s)", sorted(done), reason)
    return {"outbox_id": target["id"], "status": status, "cancelled": bool(done),
            "cancelled_ids": [i for i in ids if i in done]}


# ─── Deliver ───


class NotDue(Exception):
    def __init__(self, when: datetime):
        super().__init__(f"not due until {when.isoformat()}")
        self.when = when


def _load(cur: Any, outbox_id: int) -> dict:
    cur.execute("SELECT * FROM outbound_messages WHERE id = %s FOR UPDATE", (outbox_id,))
    row = cur.fetchone()
    if row is None:
        raise LookupError(f"outbox row {outbox_id} not found")
    return dict(row)


def _update(cur: Any, outbox_id: int, changes: dict) -> dict:
    changes = {**changes, "updated_at": clock.now()}
    sets = ", ".join(f"{k} = %({k})s" for k in changes)
    params = {k: (db.Json(_jsonable(v)) if isinstance(v, dict) else v) for k, v in changes.items()}
    params["_id"] = outbox_id
    cur.execute(f"UPDATE outbound_messages SET {sets} WHERE id = %(_id)s RETURNING *", params)
    return dict(cur.fetchone())


def _link(cur: Any, outbox_id: int, key: str, other_id: int) -> dict:
    """Stamp meta[key] = other_id on a row (retried_by / fallback_id), so an
    on_outbound_status hook that sees "failed" can tell a message the outbox
    is already re-sending from one that is really lost."""
    cur.execute(
        "UPDATE outbound_messages SET meta = meta || jsonb_build_object(%s::TEXT, %s::BIGINT) WHERE id = %s RETURNING *",
        (key, other_id, outbox_id),
    )
    return dict(cur.fetchone())


def _fire_status(cur: Any, outbox_id: int, status: str, error_code: str | None) -> None:
    """Run on_outbound_status hooks for an outcome decided here, not by a
    webhook. Reloads the row so hooks see the follow-up links."""
    from clefs.notify.inbound import fire_status_hooks

    cur.execute("SELECT * FROM outbound_messages WHERE id = %s", (outbox_id,))
    row = cur.fetchone()
    if row is not None:
        fire_status_hooks(cur, dict(row), status, error_code)


def _backoff(attempts: int, base: timedelta = PAIR_SPACING) -> timedelta:
    return min(base * (2 ** max(0, attempts)), timedelta(hours=1))


def _defer(cur: Any, row: dict, until: datetime, *, count_attempt: bool, error: str | None = None, code: str | None = None) -> dict:
    changes: dict[str, Any] = {"status": "queued", "not_before": until}
    if count_attempt:
        changes["attempts"] = row["attempts"] + 1
    if error is not None:
        changes["error"] = error
    if code is not None:
        changes["error_code"] = code
    return _update(cur, row["id"], changes)


def _fallback_template(cur: Any, row: dict, reason: str) -> dict | None:
    """Send the caller-provided template instead of a session message the
    window no longer allows (131047 / outside_window)."""
    fb = (row.get("meta") or {}).get("fallback_template")
    if not fb:
        return None
    from clefs.notify import validate

    payload = validate.template_payload(
        row["recipient"], fb["name"], fb.get("lang", "en"), fb.get("params") or [],
        header=fb.get("header"), buttons=fb.get("buttons"),
    )
    key = f"{row['idempotency_key'] or 'outbox:' + str(row['id'])}:fallback"
    new, _ = create(
        cur,
        channel="whatsapp",
        kind="template",
        recipient=row["recipient"],
        payload=payload,
        purpose=row["purpose"],
        hotel_slug=row["hotel_slug"],
        category=fb.get("category", "utility"),
        sender=row["sender"],
        sender_id=row["sender_id"],
        idempotency_key=key,
        template_name=fb["name"],
        template_lang=fb.get("lang", "en"),
        meta={"fallback_reason": reason},
        action_id=row.get("action_id"),
        fallback_of=row["id"],
    )
    _link(cur, row["id"], "fallback_id", new["id"])
    if inline_delivery():
        new = deliver(cur, new["id"], raise_not_due=False)
    return new


def retry_copy(cur: Any, row: dict, *, not_before: datetime, reason: str) -> dict:
    """A new outbox row for a message Meta failed after accepting it (status
    webhook): the original stays failed, the copy carries the next attempt."""
    n = int((row.get("meta") or {}).get("retry", 0)) + 1
    base_key = row["idempotency_key"] or f"outbox:{row['id']}"
    new, _ = create(
        cur,
        channel=row["channel"],
        kind=row["kind"],
        recipient=row["recipient"],
        payload=row["payload"],
        purpose=row["purpose"],
        hotel_slug=row["hotel_slug"],
        category=row["category"],
        sender=row["sender"],
        sender_id=row["sender_id"],
        idempotency_key=f"{base_key.split(':retry:')[0]}:retry:{n}",
        template_name=row["template_name"],
        template_lang=row["template_lang"],
        meta={**{k: v for k, v in (row.get("meta") or {}).items() if k not in ("retried_by", "fallback_id")},
              "retry": n, "retry_reason": reason},
        not_before=not_before,
        action_id=row.get("action_id"),
        fallback_of=row["id"],
    )
    _link(cur, row["id"], "retried_by", new["id"])
    return new


def inline_delivery() -> bool:
    from clefs.settings import get_settings

    return get_settings().NOTIFY_MODE in ("capture", "log")


def _resolve_document(cur: Any, row: dict) -> dict:
    """Swap our file id for a signed link Meta can fetch (valid 1 hour)."""
    from clefs import files
    from clefs.settings import get_settings

    payload = json.loads(json.dumps(row["payload"]))
    doc = payload.get("document") or {}
    file_id = doc.pop("file_id", None)
    if file_id and row.get("hotel_slug"):
        doc["link"] = get_settings().PUBLIC_API_ORIGIN.rstrip("/") + files.signed_path(row["hotel_slug"], file_id, ttl_s=3600)
    payload["document"] = doc
    return payload


def _email_payload(cur: Any, row: dict) -> dict:
    """Load attachments (file ids) into our email payload at send time (the
    transport converts it for Resend)."""
    from clefs import files

    payload = json.loads(json.dumps(row["payload"]))
    ids = payload.pop("AttachmentFileIds", []) or []
    atts = []
    for fid in ids:
        ref, data = files.get_file(cur, row["hotel_slug"], fid)
        atts.append({"Name": ref.filename, "Content": b64(data), "ContentType": ref.content_type})
    if atts:
        payload["Attachments"] = atts
    return payload


def deliver(cur: Any, outbox_id: int, *, raise_not_due: bool = True) -> dict:
    """Attempt one delivery. Returns the row. With raise_not_due, a row that
    must wait raises NotDue(when) (the task turns it into jobs.Retry)."""
    row = _load(cur, outbox_id)
    if row["status"] != "queued":
        return row
    now = clock.now()
    if row["not_before"] is not None and row["not_before"] > now:
        if raise_not_due:
            raise NotDue(row["not_before"])
        return row
    transport = get_transport()
    if transport.name == "live" and _demo_hotel(cur, row.get("hotel_slug")):
        # A demo hotel's guests, staff and inquirers are synthetic, but a
        # made-up phone number can be a real person's: its messages are
        # logged and marked sent, never delivered.
        transport = LogTransport()
    channel = row["channel"]

    if channel == "whatsapp":
        hold = _hold_until(cur, f"wa:{row['sender_id']}")
        if hold is not None and hold > now:
            row = _defer(cur, row, hold, count_attempt=False)
            return _maybe_raise(row, raise_not_due)
        # One delivery per recipient at a time, across every worker and cron
        # tick. Without it two workers each read "nothing sent in 6 s" and
        # both send (Meta 131056). A try-lock, never a wait: a busy recipient
        # just comes back one spacing later, so no delivery blocks on another
        # and no lock order can deadlock. Held to the end of this transaction,
        # which is when the other sender's sent_at becomes visible.
        cur.execute("SELECT pg_try_advisory_xact_lock(%s, hashtext(%s)) AS ok", (PACE_LOCK_CLASS, row["recipient"]))
        if not cur.fetchone()["ok"]:
            row = _defer(cur, row, now + PAIR_SPACING, count_attempt=False)
            return _maybe_raise(row, raise_not_due)
        cur.execute(
            """
            SELECT MAX(sent_at) AS last FROM outbound_messages
            WHERE channel = 'whatsapp' AND recipient = %s AND sent_at IS NOT NULL AND id <> %s
            """,
            (row["recipient"], row["id"]),
        )
        last = cur.fetchone()["last"]
        if last is not None and now < last + PAIR_SPACING:
            row = _defer(cur, row, last + PAIR_SPACING, count_attempt=False)
            return _maybe_raise(row, raise_not_due)
        if row["kind"] in SESSION_KINDS and not window_open(cur, row["sender_id"], row["recipient"], at=now):
            # The window closed while the row waited (pacing, a 130429 hold,
            # a queue delay). Meta never saw this message, so no webhook will
            # ever report it: the hooks hear it from here, as "failed" with
            # code outside_window (a local 131047). After the fallback, so
            # meta.fallback_id tells a hook the template is already going.
            row = _update(cur, row["id"], {"status": "outside_window", "error": "the 24 h customer service window is closed"})
            _fallback_template(cur, row, "outside_window")
            _fire_status(cur, row["id"], "failed", "outside_window")
            return _load(cur, row["id"])

    row = _update(cur, row["id"], {"status": "sending"})
    try:
        if channel == "whatsapp":
            payload = _resolve_document(cur, row) if row["kind"] == "document" else row["payload"]
            provider_id = transport.whatsapp(row["sender_id"], payload)
            provider = "meta" if transport.name == "live" else transport.name
        elif channel == "email":
            provider_id = transport.email(_email_payload(cur, row))
            provider = "resend" if transport.name == "live" else transport.name
        else:
            sms_provider = (row.get("meta") or {}).get("sms_provider", "twilio")
            provider_id = transport.sms(sms_provider, row["payload"])
            provider = sms_provider if transport.name == "live" else transport.name
    except ProviderError as exc:
        return _handle_error(cur, row, exc, raise_not_due=raise_not_due)
    except Exception as exc:  # a bug or an unexpected library error: back off like a provider error
        log.exception("delivering outbox %s failed", row["id"])
        return _handle_error(cur, row, ProviderError("exception", f"{type(exc).__name__}: {exc}"), raise_not_due=raise_not_due)
    row = _update(
        cur,
        row["id"],
        {
            "status": "sent",
            "provider": provider,
            "provider_id": provider_id,
            "sent_at": now,
            "attempts": row["attempts"] + 1,
            "error": None,
            "error_code": None,
        },
    )
    if channel != "whatsapp":
        # WhatsApp's "sent" arrives by webhook (and firing here too would
        # report it twice); email and SMS have no status webhook, so the
        # provider's acceptance is the last word hooks will get.
        _fire_status(cur, row["id"], "sent", None)
        row = _load(cur, row["id"])
    return row


def _demo_hotel(cur: Any, hotel_slug: str | None) -> bool:
    if not hotel_slug:
        return False
    cur.execute("SELECT status FROM hotels WHERE slug = %s", (hotel_slug,))
    r = cur.fetchone()
    return bool(r) and r["status"] == "demo"


def _maybe_raise(row: dict, raise_not_due: bool) -> dict:
    if raise_not_due and row["not_before"] is not None:
        raise NotDue(row["not_before"])
    return row


def _handle_error(cur: Any, row: dict, exc: ProviderError, *, raise_not_due: bool) -> dict:
    now = clock.now()
    code = exc.code or "error"
    attempts = row["attempts"] + 1
    msg = (exc.message or str(exc))[:1000]
    if code == "131056":
        row = _defer(cur, row, now + _backoff(attempts), count_attempt=True, error=msg, code=code)
        return _maybe_raise(row, raise_not_due)
    if code == "130429":
        until = now + _backoff(attempts, timedelta(seconds=30))
        _set_hold(cur, f"wa:{row['sender_id']}", until, "130429 throughput")
        row = _defer(cur, row, until, count_attempt=True, error=msg, code=code)
        return _maybe_raise(row, raise_not_due)
    if code == "131049":
        row = _defer(cur, row, now + timedelta(hours=24), count_attempt=True, error=msg, code=code)
        return _maybe_raise(row, raise_not_due)
    if code == "131047":
        row = _update(cur, row["id"], {"status": "failed", "failed_at": now, "attempts": attempts, "error": msg, "error_code": code})
        _fallback_template(cur, row, "131047")
        return _failed_at_send(cur, row["id"], code)
    if exc.permanent or attempts >= MAX_ATTEMPTS:
        row = _update(cur, row["id"], {"status": "failed", "failed_at": now, "attempts": attempts, "error": msg, "error_code": code})
        return _failed_at_send(cur, row["id"], code)
    row = _defer(cur, row, now + _backoff(attempts, timedelta(seconds=30)), count_attempt=True, error=msg, code=code)
    return _maybe_raise(row, raise_not_due)


def _failed_at_send(cur: Any, outbox_id: int, code: str) -> dict:
    """A terminal failure the provider returned synchronously: no status
    webhook will ever mention this message, so the hooks hear it from here."""
    _fire_status(cur, outbox_id, "failed", code)
    return _load(cur, outbox_id)


@jobs.task(DELIVER_TASK, max_attempts=20, backoff_s=30)
def _deliver_task(ctx: jobs.JobCtx, payload: dict) -> dict:
    try:
        row = deliver(ctx.cur, int(payload["outbox_id"]))
    except NotDue as nd:
        raise jobs.Retry(nd.when, "outbox pacing") from nd
    except LookupError:
        return {"missing": True}
    return {"status": row["status"]}


@jobs.scheduled("core.notify.sweep", jobs.every(minutes=1), per_hotel=False)
def _sweep(ctx: jobs.JobCtx) -> dict:
    """Safety net: deliver queued rows that are due but whose task is gone
    (it went dead, or the row was created before a deploy)."""
    ctx.cur.execute(
        """
        SELECT o.id FROM outbound_messages o
        WHERE o.status = 'queued' AND (o.not_before IS NULL OR o.not_before <= %s)
          AND o.created_at < %s
          AND NOT EXISTS (SELECT 1 FROM job_queue q WHERE q.dedupe_key = %s || o.id AND q.status IN ('queued', 'running'))
        ORDER BY o.id LIMIT 200
        """,
        (ctx.now, ctx.now - timedelta(minutes=2), f"{DELIVER_TASK}:"),
    )
    ids = [r["id"] for r in ctx.cur.fetchall()]
    sent = 0
    for oid in ids:
        ctx.cur.execute("SAVEPOINT clefs_sweep_one")
        try:
            row = deliver(ctx.cur, oid, raise_not_due=False)
            ctx.cur.execute("RELEASE SAVEPOINT clefs_sweep_one")
            sent += row["status"] in DELIVERED_STATES
        except Exception:
            ctx.cur.execute("ROLLBACK TO SAVEPOINT clefs_sweep_one")
            ctx.cur.execute("RELEASE SAVEPOINT clefs_sweep_one")
            log.exception("sweep could not deliver outbox %s", oid)
    return {"due": len(ids), "sent": sent}
