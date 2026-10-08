"""
Journey replies (04 spec 3.8): the WhatsApp handler at guest slot
`guests.journey_replies` (priority 10; STOP at 5 runs first).

A message is a journey reply when it answers a journey message: by
context.id (the wamid it quotes), a signed J5 button, or else the latest
journey message sent to that number within 24 h. Anything else passes on.

- YES after J3 -> the human task guests.offer_accept for the front desk.
- YES after the J5 consent ask -> marketing_whatsapp consent only (evidence:
  the inbound message), then a /g link (24 h) showing only the transfer
  tick; the guest_direct point is created when the guest ticks it (3.4).
- Classification: a deterministic EN/AR lexicon first; the LLM only when the
  sender gave transfer_abroad consent or the hotel allows AI on unconsented
  inbound (default off, 7.7). The LLM may upgrade, never downgrade.
- Negative or a request: in house -> service.api.create_request; after
  check-out -> service.api.create_followup(guest_issue). One fixed
  acknowledgement, then a person takes over (AI stays silent).
- A complaint about the messages themselves: opt-out plus
  report_incident(guest_complaint) on the step's action type.
- Wrong number: suppression scope all, the point suppressed.
Neutral replies are recorded (the reservation timeline shows them) and
passed on, so the concierge can still answer a question.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from clefs import ai, notify
from clefs.core import clock
from clefs.modules.guests import contacts, identity
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import steps

log = logging.getLogger("clefs.guests.journeys.replies")

YES_WORDS = frozenset({"yes", "y", "نعم", "ايوه", "أيوه", "اي", "yes please"})
NEGATIVE = ("not working", "doesn't work", "does not work", "broken", "dirty", "noisy", "noise", "cold", "hot water",
            "smell", "leak", "problem", "issue", "fix", "complain", "bad", "terrible",
            "لا يعمل", "وسخ", "مشكلة", "معطل", "ضجيج", "رائحة", "بارد")
WRONG_NUMBER = ("wrong number", "not me", "who is this", "رقم خطأ", "رقم غلط")
MESSAGING_COMPLAINT = ("spam", "stop messaging", "stop sending", "too many messages", "why are you messaging",
                       "رسائل مزعجة", "توقفوا عن الرسائل")
POSITIVE = ("all good", "good", "great", "perfect", "fine", "thank", "كل شيء جيد", "ممتاز", "شكرا")
DEPARTMENT_HINTS = ("housekeeping", "engineering", "ird", "front_office", "concierge", "security", "general")
LEXICON_DEPARTMENTS = {
    "engineering": ("not working", "broken", "ac", "air con", "cold", "hot water", "leak", "لا يعمل", "معطل", "بارد"),
    "housekeeping": ("dirty", "towel", "clean", "smell", "وسخ", "رائحة", "منشفة"),
    "security": ("noisy", "noise", "ضجيج"),
}
ACK = {
    "en": "Thank you, the team has been told and will contact you shortly.",
    "ar": "شكراً لك، تم إبلاغ الفريق وسيتواصلون معك قريباً.",
}
CONSENT_ASK = {
    "en": "Would you like direct-booking offers from {hotel} on WhatsApp? Reply YES. Reply STOP anytime.",
    "ar": "هل ترغب في تلقي عروض الحجز المباشر من {hotel} عبر واتساب؟ أرسل نعم. أرسل إيقاف في أي وقت.",
}
CONSENT_LINK = {
    "en": "Thank you. One last step to confirm: {url}",
    "ar": "شكراً لك. خطوة أخيرة للتأكيد: {url}",
}
OFFER_ACK = {
    "en": "Thank you, the front desk will add it to your booking.",
    "ar": "شكراً لك، سيضيفه مكتب الاستقبال إلى حجزك.",
}


class ReplyClass(BaseModel):
    sentiment: Literal["positive", "neutral", "negative"]
    is_request: bool
    summary_en: str
    department_hint: Literal["housekeeping", "engineering", "ird", "front_office", "concierge", "security", "general"]
    severity: Literal["L1", "L2", "L3"]
    is_opt_out: bool
    is_complaint_about_messaging: bool
    is_wrong_number: bool
    language: Literal["en", "ar", "other"]


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").lower())).strip()


def is_arabic(text: str | None) -> bool:
    return bool(re.search(r"[؀-ۿ]", text or ""))


def _hit(t: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if (re.search(rf"(^|\s){re.escape(w)}($|\s)", t) if w.isascii() else w in t):
            return w
    return None


def lexicon(text: str | None, button: str | None = None) -> dict:
    """The deterministic pass: {negative, positive, request, wrong_number,
    messaging_complaint, yes, department_hint, hit}."""
    t = norm(text)
    out = {"negative": False, "positive": False, "request": False, "wrong_number": False,
           "messaging_complaint": False, "yes": t in YES_WORDS, "department_hint": "general", "hit": None}
    if button == "gst_j5_fix":
        out.update(negative=True, request=True, hit="button:fix")
    elif button == "gst_j5_ok":
        out.update(positive=True, hit="button:ok")
    for key, words in (("wrong_number", WRONG_NUMBER), ("messaging_complaint", MESSAGING_COMPLAINT)):
        h = _hit(t, words)
        if h:
            out[key], out["hit"] = True, out["hit"] or h
    h = _hit(t, NEGATIVE)
    if h and not out["positive"]:
        out.update(negative=True, request=True, hit=out["hit"] or h)
    if not out["negative"] and _hit(t, POSITIVE):
        out["positive"] = True
    for dept, words in LEXICON_DEPARTMENTS.items():
        if _hit(t, words):
            out["department_hint"] = dept
            break
    return out


# ─── Which journey message is this answering? ───


def sender_hmac(ctx: notify.InboundCtx) -> bytes | None:
    if ctx.sender_hmac:
        return bytes(ctx.sender_hmac)
    try:
        return identity.hmac_for(ctx.cur, ctx.hotel_slug, "phone", ctx.sender)
    except Exception:
        return None


def _plan(cur: Any, hotel_slug: str, where: str, params: tuple) -> dict | None:
    from clefs.modules.guests.journeys import planner

    cur.execute(f"SELECT {', '.join('p.' + c.strip() for c in planner.PLAN_COLS.split(','))} FROM gst_journey_plans p "
                f"WHERE p.hotel_slug = %s AND {where} ORDER BY p.last_attempt_at DESC NULLS LAST, p.id DESC LIMIT 1",
                (hotel_slug, *params))
    r = cur.fetchone()
    return dict(r) if r else None


def find_context(cur: Any, ctx: notify.InboundCtx, h: bytes, now: datetime) -> dict | None:
    """{kind: plan|consent_ask, plan?, message?, button?} or None."""
    slug = ctx.hotel_slug
    if ctx.button_ref and ctx.button_ref.startswith("gstj:"):
        _, pid, payload = (ctx.button_ref.split(":", 2) + ["", ""])[:3]
        p = _plan(cur, slug, "p.id = %s", (int(pid),)) if pid.isdigit() else None
        if p:
            return {"kind": "plan", "plan": p, "button": payload}
    if ctx.context_id:
        p = _plan(cur, slug, "p.wamid = %s", (ctx.context_id,))
        if p:
            return {"kind": "plan", "plan": p}
        cur.execute(
            "SELECT m.* FROM gst_journey_messages m JOIN outbound_messages o ON o.id = m.outbound_message_id "
            "WHERE m.hotel_slug = %s AND o.provider_id = %s LIMIT 1",
            (slug, ctx.context_id),
        )
        m = cur.fetchone()
        if m:
            return {"kind": m["kind"], "message": dict(m)}
    since = now - steps.REPLY_MATCH_WINDOW
    p = _plan(cur, slug, "p.last_attempt_at >= %s AND p.status = ANY(%s) AND p.contact_point_id IN "
              "(SELECT id FROM gst_contact_points WHERE hotel_slug = p.hotel_slug AND value_hmac = %s)",
              (since, list(steps.LIVE_SENT), h))
    cur.execute(
        "SELECT * FROM gst_journey_messages WHERE hotel_slug = %s AND value_hmac = %s AND kind = 'consent_ask' "
        "AND sent_at >= %s ORDER BY sent_at DESC LIMIT 1",
        (slug, h, since),
    )
    m = cur.fetchone()
    if m and (p is None or m["sent_at"] >= (p["last_attempt_at"] or m["sent_at"])):
        return {"kind": "consent_ask", "message": dict(m)}
    return {"kind": "plan", "plan": p} if p else None


def _match(ctx: notify.InboundCtx) -> bool:
    if not ctx.hotel_slug or ctx.audience != "guest":
        return False
    h = sender_hmac(ctx)
    return h is not None and find_context(ctx.cur, ctx, h, clock.now()) is not None


# ─── Recording ───


def record_reply(cur: Any, hotel_slug: str, *, plan: dict | None, reservation_id: int | None, ctx: notify.InboundCtx,
                 classification: dict, summary_en: str | None, hit: str | None, llm_used: bool, outcome: str,
                 service_ref: str | None = None, action_id: int | None = None) -> int:
    from clefs.db import Json

    cur.execute(
        """
        INSERT INTO gst_journey_replies (hotel_slug, plan_id, reservation_id, inbound_message_id, wamid, classification,
                                         summary_en, deterministic_hit, llm_used, outcome, service_ref, action_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (hotel_slug, (plan or {}).get("id"), reservation_id, ctx.inbound_id, ctx.provider_id, Json(classification),
         summary_en, hit, llm_used, outcome, service_ref, action_id, clock.now()),
    )
    reply_id = int(cur.fetchone()["id"])
    if plan and plan["status"] in ("sent", "delivered", "read"):
        cur.execute("UPDATE gst_journey_plans SET status = 'replied', updated_at = %s WHERE id = %s", (clock.now(), plan["id"]))
    return reply_id


# ─── Free-form replies (inside the window the guest opened) ───


def send_free_form(cur: Any, ctx: notify.InboundCtx, hotel: Any, *, kind: str, text: str, plan: dict | None,
                   reservation_id: int | None, h: bytes, email_ok: bool = True) -> bool:
    """WhatsApp text while the WABA is billing-ready; otherwise email to the
    stay's email point (3.5), or nothing. Logged in gst_journey_messages."""
    from clefs.modules.guests.journeys import channels, executor

    cfg = gsettings.load(cur, hotel.slug)
    now = clock.now()
    key = f"gst-msg:{kind}:{ctx.inbound_id}"
    if channels.whatsapp_usable(cur, hotel, cfg):
        res = ctx.reply_text(text, purpose=f"guests.journey_msg.{kind}", idempotency_key=key)
        ch, point_id = "whatsapp", None
    else:
        if not email_ok or reservation_id is None:
            return False
        stay = executor.load_stay(cur, hotel.slug, reservation_id)
        pts = [p for p in channels.stay_points(cur, hotel.slug, stay or {"reservation_id": reservation_id})
               if p["kind"] == "email" and (p["usable_until"] is None or p["usable_until"] > now)
               and not contacts.is_suppressed(cur, hotel.slug, [bytes(p["value_hmac"])], marketing=False)]
        to = executor.recipient(cur, pts[0]) if pts else None
        if not to:
            return False
        res = notify.send_email(to, hotel.name, f"<p>{text}</p>", text=text, hotel_slug=hotel.slug,
                                purpose=f"guests.journey_msg.{kind}", idempotency_key=key, cur=cur)
        ch, point_id = "email", pts[0]["id"]
    if not getattr(res, "ok", False):
        return False
    cur.execute(
        "INSERT INTO gst_journey_messages (hotel_slug, reservation_id, plan_id, kind, channel, contact_point_id, value_hmac, "
        "outbound_message_id, sent_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (hotel.slug, reservation_id, (plan or {}).get("id"), kind if kind in ("consent_ask", "ack", "consent_link", "offer_ack")
         else "ack", ch, point_id, h, res.outbox_id, now),
    )
    return True


# ─── Classification ───


def llm_allowed(cur: Any, hotel_slug: str, h: bytes) -> bool:
    """3.8 / 7.7: only with the sender's transfer_abroad consent, or the
    hotel's explicit setting (default off until counsel signs off)."""
    if contacts.identity_granted(cur, hotel_slug, h, "transfer_abroad"):
        return True
    return gsettings.load(cur, hotel_slug).ai_on_unconsented_inbound


SYSTEM = (
    "You classify a hotel guest's WhatsApp reply to a hotel's stay message. Return the schema only. "
    "is_request: the guest asks for something or reports a problem. severity L1 = safety or the room is unusable, "
    "L2 = a fault to fix today, L3 = minor. summary_en: one neutral English sentence, no names or numbers from the guest."
)


def classify(cur: Any, hotel_slug: str, h: bytes, text: str | None, button: str | None) -> tuple[dict, bool, str | None]:
    """(classification, llm_used, summary_en). The lexicon result stands;
    the LLM may only add negative / request / opt-out flags to it."""
    lex = lexicon(text, button)
    cls = {"negative": lex["negative"], "positive": lex["positive"], "request": lex["request"],
           "wrong_number": lex["wrong_number"], "messaging_complaint": lex["messaging_complaint"],
           "opt_out": False, "department_hint": lex["department_hint"], "severity": "L2" if lex["negative"] else None,
           "language": "ar" if is_arabic(text) else "en"}
    summary = None
    llm_used = False
    if text and not button and llm_allowed(cur, hotel_slug, h):
        try:
            rc = ai.structured("guests.journey_reply", system=SYSTEM, input=text[:2000], schema=ReplyClass,
                               hotel_slug=hotel_slug, store=False)
            llm_used = True
            cls["negative"] = cls["negative"] or rc.sentiment == "negative"
            cls["request"] = cls["request"] or rc.is_request
            cls["wrong_number"] = cls["wrong_number"] or rc.is_wrong_number
            cls["messaging_complaint"] = cls["messaging_complaint"] or rc.is_complaint_about_messaging
            cls["opt_out"] = rc.is_opt_out
            if cls["department_hint"] == "general":
                cls["department_hint"] = rc.department_hint
            if cls["negative"] or cls["request"]:
                sev = [s for s in ("L1", "L2", "L3") if s in (rc.severity, cls["severity"] or "L3")]
                cls["severity"] = sev[0]
            cls["positive"] = cls["positive"] and not cls["negative"]
            summary = rc.summary_en[:500]
        except Exception:  # AIDisabled, AIError: the lexicon result stands
            log.info("journey reply classification fell back to the lexicon at %s", hotel_slug)
    if summary is None and (cls["negative"] or cls["request"]):
        summary = f"Guest reported: {lex['hit'] or 'an issue'} (from a journey reply)"
    return cls, llm_used, summary


# ─── Outcomes ───


def _offer_accept(cur: Any, hotel: Any, plan: dict, stay: dict | None) -> int | None:
    from clefs.core import actions

    cur.execute("SELECT * FROM gst_journey_offers WHERE id = %s", (plan.get("offer_id"),))
    offer = dict(cur.fetchone() or {})
    ref = (stay or {}).get("booking_ref") or plan["reservation_id"]
    a = actions.create_action(
        cur, hotel.slug, type=steps.OFFER_ACCEPT, title=f"Add {offer.get('title_en') or 'the offer'} to booking {ref}",
        params={"plan_id": plan["id"], "reservation_id": plan["reservation_id"], "offer_id": offer.get("id"),
                "offer_price_minor": offer.get("price_minor"), "currency": offer.get("currency")},
        owner_role="front_desk", dedupe_key=f"gst:offer_accept:{plan['id']}", actor=actions.SYSTEM,
    )
    return a.get("id")


def _consent_yes(cur: Any, ctx: notify.InboundCtx, hotel: Any, msg: dict, h: bytes, lang: str) -> None:
    """YES records marketing_whatsapp only; the transfer tick comes through
    the /g link (a single YES cannot stand for two consents)."""
    from clefs.core import consent
    from clefs.modules.guests.journeys import executor
    from clefs.modules.guests.journeys import tokens as jtokens

    consent.record_consent(
        cur, hotel.slug, h.hex(), "marketing_whatsapp", True, source="j5_reply", subject_type="contact",
        evidence={"inbound_message_id": ctx.inbound_id}, language=lang,
        text_version=identity.text_version(cur, hotel.slug, "marketing_whatsapp", lang),
    )
    token = jtokens.mint(cur, hotel.slug, int(msg["reservation_id"]), "consent",
                         expires_at=clock.now() + steps.CONSENT_TOKEN_TTL,
                         ref={"inbound_message_id": ctx.inbound_id, "plan_id": msg.get("plan_id")})
    url = executor.page_url(f"/g/{token}")
    send_free_form(cur, ctx, hotel, kind="consent_link", text=CONSENT_LINK[lang].format(url=url), plan=None,
                   reservation_id=msg["reservation_id"], h=h, email_ok=False)


def _maybe_consent_ask(cur: Any, ctx: notify.InboundCtx, hotel: Any, plan: dict, h: bytes, lang: str) -> bool:
    """After a non-negative J5 reply: one free-form ask per stay, only to a
    guest who has not already given marketing_whatsapp consent."""
    if contacts.identity_granted(cur, hotel.slug, h, "marketing_whatsapp"):
        return False
    if contacts.is_suppressed(cur, hotel.slug, [h], marketing=True):
        return False
    cur.execute("SELECT 1 FROM gst_journey_messages WHERE hotel_slug = %s AND reservation_id = %s AND kind = 'consent_ask'",
                (hotel.slug, plan["reservation_id"]))
    if cur.fetchone():
        return False
    return send_free_form(cur, ctx, hotel, kind="consent_ask", text=CONSENT_ASK[lang].format(hotel=hotel.name),
                          plan=plan, reservation_id=plan["reservation_id"], h=h, email_ok=False)


def _file_issue(cur: Any, hotel: Any, stay: dict | None, plan: dict, cls: dict, text: str | None,
                summary: str | None) -> tuple[str, str | None]:
    """In house: a Service request for the room. Otherwise a follow-up."""
    from clefs.modules.service import api as service_api

    rid = plan["reservation_id"]
    in_house = (stay or {}).get("status") == "IN_HOUSE" and (stay or {}).get("room")
    if in_house:
        req = service_api.create_request(
            cur, hotel.slug, room=stay["room"],
            type=service_api.REQUEST_TYPE_FOR_DEPARTMENT.get(cls["department_hint"], "other"),
            description=(text or summary or "Guest reported an issue")[:1000], source="whatsapp_journey",
            language=cls["language"] if cls["language"] in ("en", "ar") else None, guest_ref=str(rid),
            priority_hint=cls.get("severity"),
        )
        return "service_request", str(req.get("id") or req.get("tracking_token") or "") or None
    fu = service_api.create_followup(
        cur, hotel.slug, kind="guest_issue", summary=(summary or text or "Guest reported an issue")[:300], guest_ref=rid,
        owner_role="gm", due_at=None, source="whatsapp_journey", language=cls["language"] if cls["language"] in ("en", "ar") else None,
        room=(stay or {}).get("room"),
    )
    return "followup", str(fu.get("id") or "") or None


# ─── The handler ───


@notify.on_whatsapp("guest", priority=notify.GUEST_PRIORITY["guests.journey_replies"], match=_match)
def journey_reply(ctx: notify.InboundCtx) -> dict:
    from clefs.core import actions
    from clefs.core.hotels import get_hotel
    from clefs.modules.guests.journeys import executor

    cur, now = ctx.cur, clock.now()
    h = sender_hmac(ctx)
    found = find_context(cur, ctx, h, now) if h else None
    if found is None:
        return {"handled": False}
    hotel = get_hotel(cur, ctx.hotel_slug)
    text = ctx.text or ctx.button_title
    lang = "ar" if is_arabic(text) else "en"
    cur.execute("UPDATE gst_contact_points SET last_used_at = %s WHERE hotel_slug = %s AND value_hmac = %s",
                (now, hotel.slug, h))
    lex = lexicon(text, found.get("button"))
    if found["kind"] == "consent_ask":
        msg = found["message"]
        if lex["yes"]:
            _consent_yes(cur, ctx, hotel, msg, h, lang)
            record_reply(cur, hotel.slug, plan=None, reservation_id=msg["reservation_id"], ctx=ctx,
                         classification={"yes": True}, summary_en=None, hit="yes", llm_used=False, outcome="consent_yes")
            return {"handled": True, "outcome": "consent_yes"}
        found = {"kind": "plan", "plan": _plan(cur, hotel.slug, "p.id = %s", (msg.get("plan_id") or 0,))}
        if found["plan"] is None:
            return {"handled": False}
    plan = found["plan"]
    stay = executor.load_stay(cur, hotel.slug, plan["reservation_id"])
    if lex["yes"] and plan["step"] == "j3":
        aid = _offer_accept(cur, hotel, plan, stay)
        send_free_form(cur, ctx, hotel, kind="offer_ack", text=OFFER_ACK[lang], plan=plan,
                       reservation_id=plan["reservation_id"], h=h)
        record_reply(cur, hotel.slug, plan=plan, reservation_id=plan["reservation_id"], ctx=ctx,
                     classification={"yes": True}, summary_en=None, hit="yes", llm_used=False, outcome="offer_accept",
                     action_id=aid)
        return {"handled": True, "outcome": "offer_accept"}
    if lex["yes"]:
        return {"handled": False}  # a YES with nothing to agree to: the next handler decides
    cls, llm_used, summary = classify(cur, hotel.slug, h, text, found.get("button"))
    base = dict(plan=plan, reservation_id=plan["reservation_id"], ctx=ctx, classification=cls, summary_en=summary,
                hit=lex["hit"], llm_used=llm_used)
    if cls["wrong_number"]:
        contacts.suppress(cur, hotel.slug, h, scope="all", reason="wrong_number", source="journey_reply")
        record_reply(cur, hotel.slug, outcome="wrong_number", **base)
        return {"handled": True, "outcome": "wrong_number"}
    if cls["messaging_complaint"] or cls["opt_out"]:
        contacts.suppress(cur, hotel.slug, h, scope="all",
                          reason="complaint_about_messaging" if cls["messaging_complaint"] else "stop", source="journey_reply")
        if cls["messaging_complaint"] and plan["step"] in steps.ACTION_TYPES:
            actions.report_incident(cur, hotel.slug, kind="guest_complaint", action_types=[steps.ACTION_TYPES[plan["step"]]],
                                    evidence=[{"kind": "row", "label": "complaint about messaging", "ref": {"plan_id": plan["id"]}}])
        record_reply(cur, hotel.slug, outcome="opt_out", **base)
        return {"handled": True, "outcome": "opt_out"}
    if cls["negative"] or cls["request"]:
        outcome, ref = _file_issue(cur, hotel, stay, plan, cls, text, summary)
        send_free_form(cur, ctx, hotel, kind="ack", text=ACK[lang], plan=plan, reservation_id=plan["reservation_id"], h=h)
        record_reply(cur, hotel.slug, outcome=outcome, service_ref=ref, **base)
        return {"handled": True, "outcome": outcome}
    record_reply(cur, hotel.slug, outcome="none", **base)
    if plan["step"] == "j5" and _maybe_consent_ask(cur, ctx, hotel, plan, h, lang):
        return {"handled": True, "outcome": "consent_ask"}
    return {"handled": bool(found.get("button")), "outcome": "none"}
