"""
Service Control on WhatsApp (03 spec 1.7, 04 spec F4).

- Tracker updates: a guest who opted in on the tracker page (public_api)
  gets the utility template request_status_v1 ({{1}} hotel, {{2}} request,
  {{3}} status) from the hotel's own number when the tracker state moves.
  `push_tracker_updates` (run every minute by the service jobs) sends one
  message per subscription per state,
  paced (never twice within PUSH_MIN_GAP), and only while the
  whatsapp_updates consent stands (a STOP handled by guests.optout_consent
  withdraws it, and nothing more goes out).
- The fallback handler (priority 90, the last guest slot in
  notify.GUEST_PRIORITY): a guest message no earlier handler took becomes a
  service request (source 'whatsapp', idempotent on the message id), so a
  message to the hotel's number is never dropped. The room comes from the
  sender's own recent tracker subscription (matched by the hashed phone),
  never from the message text; without one the request goes to Front
  Office with no room. Intake's safety classifier reads the text as usual.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from clefs import jobs, notify
from clefs.core import clock, consent, privacy
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.service import constants as C
from clefs.modules.service import intake, state

log = logging.getLogger("clefs.service.whatsapp")

PURPOSE = "service.tracker"
FALLBACK_PURPOSE = "service.whatsapp_fallback"
CONSENT_PURPOSE = "whatsapp_updates"
PUSH_MIN_GAP = timedelta(seconds=60)
ROOM_MEMORY = timedelta(days=7)  # how long a tracker subscription tells us the sender's room
STOP_RE = re.compile(r"^\s*(stop|unsubscribe|cancel updates|ايقاف|إيقاف|الغاء|إلغاء)\s*[.!]*\s*$", re.IGNORECASE)

STATE_LABELS = {
    "received": {"en": "Received", "ar": "تم الاستلام"},
    "assigned": {"en": "With our team", "ar": "لدى فريقنا"},
    "on_the_way": {"en": "On the way", "ar": "في الطريق"},
    "done": {"en": "Done", "ar": "تم"},
}

# ─── Tracker updates ───


def tracker_state(row: dict) -> str:
    return C.TRACKER_STATE.get(row.get("state") or "new", "received")


def _request_label(row: dict, lang: str) -> str:
    t = C.REQUEST_TYPES.get(row.get("request_type_key") or "", {})
    return (t.get("label_ar") if lang == "ar" else None) or t.get("label_en") or row.get("type") or "Request"


def send_status(cur: Any, hotel: Any, sub: dict, row: dict) -> Any:
    """One request_status_v1 message for the subscription's current state."""
    lang = "ar" if sub.get("language") == "ar" else "en"
    st = tracker_state(row)
    phone = privacy.unseal(bytes(sub["phone_enc"]))
    res = notify.send_whatsapp_template(
        phone, C.TRACKER_TEMPLATE, lang,
        [hotel.name[:60], _request_label(row, lang)[:60], STATE_LABELS[st][lang]],
        hotel_slug=hotel.slug, purpose=PURPOSE, sender="hotel", category="utility",
        idempotency_key=f"svc.track:{sub['id']}:{st}:{int(row.get('reopen_count') or 0)}", cur=cur,
    )
    cur.execute(
        "UPDATE svc_tracker_subscriptions SET last_state_sent = %s, last_sent_at = %s WHERE id = %s",
        (st, clock.now(), sub["id"]),
    )
    return res


def push_tracker_updates(cur: Any, hotel_slug: str) -> dict:
    """Send the state changes that subscribers have not seen yet."""
    hotel = get_hotel(cur, hotel_slug)
    cur.execute(
        """SELECT s.*, r.state, r.request_type_key, r.type, r.reopen_count, r.status
           FROM svc_tracker_subscriptions s JOIN staff_requests r ON r.id = s.request_id
           WHERE s.hotel_slug = %s AND s.opted_out_at IS NULL
             AND (s.last_sent_at IS NULL OR s.last_sent_at < %s)
             AND s.created_at > %s ORDER BY s.id LIMIT 200""",
        (hotel_slug, clock.now() - PUSH_MIN_GAP, clock.now() - timedelta(days=30)),
    )
    sent = skipped = 0
    for sub in [dict(r) for r in cur.fetchall()]:
        if tracker_state(sub) == sub.get("last_state_sent"):
            continue
        if not consent.has_consent(cur, hotel_slug, bytes(sub["phone_hmac"]).hex(), CONSENT_PURPOSE):
            cur.execute("UPDATE svc_tracker_subscriptions SET opted_out_at = %s WHERE id = %s", (clock.now(), sub["id"]))
            skipped += 1
            continue
        cur.execute("SAVEPOINT svc_tracker_push")
        try:
            send_status(cur, hotel, sub, sub)
            cur.execute("RELEASE SAVEPOINT svc_tracker_push")
            sent += 1
        except Exception as exc:  # noqa: BLE001 - one bad number never stalls the rest
            cur.execute("ROLLBACK TO SAVEPOINT svc_tracker_push")
            log.warning("tracker update %s failed for %s: %s", sub["id"], hotel_slug, exc)
            skipped += 1
    return {"sent": sent, "skipped": skipped}


def tracker_push_job(ctx: jobs.JobCtx) -> dict:
    """The body of the per-hotel, every-minute job service.tracker_push,
    registered in jobs.py (which owns, and pins by a test, the job set)."""
    return push_tracker_updates(ctx.cur, ctx.hotel_slug)


# ─── Fallback handler ───


def _room_for_sender(cur: Any, hotel_slug: str, sender_hmac: bytes | None) -> str | None:
    """The room of the sender's newest tracker subscription (they typed this
    number into that room's tracker), within ROOM_MEMORY."""
    if not sender_hmac:
        return None
    cur.execute(
        """SELECT r.room FROM svc_tracker_subscriptions s JOIN staff_requests r ON r.id = s.request_id
           WHERE s.hotel_slug = %s AND s.phone_hmac = %s AND s.created_at > %s
           ORDER BY s.created_at DESC LIMIT 1""",
        (hotel_slug, sender_hmac, clock.now() - ROOM_MEMORY),
    )
    r = cur.fetchone()
    return r["room"] if r else None


def _stop(cur: Any, ctx: Any) -> dict:
    """STOP reaching this slot (no earlier handler took it): end tracker
    updates for this sender and withdraw the consent they gave."""
    cur.execute(
        """UPDATE svc_tracker_subscriptions SET opted_out_at = %s
           WHERE hotel_slug = %s AND phone_hmac = %s AND opted_out_at IS NULL""",
        (clock.now(), ctx.hotel_slug, ctx.sender_hmac),
    )
    if ctx.sender_hmac:
        consent.record_consent(cur, ctx.hotel_slug, bytes(ctx.sender_hmac).hex(), CONSENT_PURPOSE, False,
                               source="whatsapp_stop", evidence={"inbound_id": ctx.inbound_id})
    return {"handled": True, "stopped": True}


@notify.on_whatsapp("guest", priority=90, kind="text")
def fallback(ctx: Any) -> dict:
    """A guest's WhatsApp message nobody else handled becomes a request."""
    text = (ctx.text or "").strip()
    if not ctx.hotel_slug or not text:
        return {"handled": False}
    cur = ctx.cur
    if STOP_RE.match(text):
        return _stop(cur, ctx)
    room = _room_for_sender(cur, ctx.hotel_slug, ctx.sender_hmac)
    try:
        out = _file(cur, ctx, text, room)
    except ClefsError as exc:
        if exc.code != "rate_limited":
            raise
        # the room's guest limit (5 per 10 minutes): the earlier messages are
        # already with the team; say so rather than drop this one silently
        _reply(ctx, "Thank you. The team already has your recent messages and will be with you shortly.")
        return {"handled": True, "rate_limited": True}
    if not out.replayed:
        where = f" for room {room}" if room else ""
        _reply(ctx, f"Thank you. We have passed your message to the team{where}.")
    return {"handled": True, "request_id": out.row["id"], "merged": out.merged}


def _reply(ctx: Any, body: str) -> None:
    try:
        ctx.reply_text(body, purpose=FALLBACK_PURPOSE)
    except Exception as exc:  # noqa: BLE001 - the request is filed; a failed courtesy reply is only logged
        log.info("fallback reply failed for %s: %s", ctx.hotel_slug, exc)


def _file(cur: Any, ctx: Any, text: str, room: str | None) -> Any:
    return intake.create_request(
        cur, ctx.hotel_slug,
        intake.IntakeIn(description=text[:intake.DESCRIPTION_MAX], source="whatsapp", room=room,
                        idempotency_key=f"wa:{ctx.provider_id}", channel_ref=ctx.provider_id,
                        flags={"whatsapp_fallback": True, "room_from": "tracker_subscription" if room else None}),
        actor=state.Actor("guest", label="whatsapp"),
    )
