"""
Concierge on WhatsApp: GUEST_PRIORITY slot 30 ("concierge.whatsapp_chat",
04 spec F4). Runs after the opt-out handler (5), journey replies (10) and
Inquiry Guard (20), before Service's fallback (90), which files anything no
one else took as a request for staff.

Who it answers: a text from a known guest (guests.api.is_known_guest_sender:
in house, or arriving within a day) to the hotel's own number, at a hotel
with the concierge switched on. Everyone else passes through untouched.

Consent first (PDPL): AI processing and transfer abroad. A guest without
both gets one question with two buttons (the current concierge consent
wording, constants.CONSENT_TEXT_VERSION, in the language their message is
written in); their first message waits in the session and is answered once
they say yes. "No" hands them to a person, and later messages go to
Service's fallback.

The reply is not written in the webhook: the handler binds the message to a
WhatsApp session (cc_sessions, surface whatsapp, one per sender for 2 hours)
and enqueues concierge.whatsapp_reply in the webhook's transaction. The task
runs the text model with the same prompt and tools as the web, then sends
the answer inside the 24 h service window, so Meta gets its 200 at once.
"""

from __future__ import annotations

import logging
from typing import Any

from clefs import jobs
from clefs.auth import tokens
from clefs.core import clock
from clefs.core import consent as consent_mod
from clefs.core.hotels import get_hotel, module_enabled
from clefs.db import Json
from clefs.modules.concierge import constants as C
from clefs.notify import on_whatsapp

log = logging.getLogger("clefs.concierge.whatsapp")

PRIORITY = 30          # clefs.notify GUEST_PRIORITY["concierge.whatsapp_chat"]
PURPOSE = "concierge.whatsapp_chat"
REPLY_TASK = "concierge.whatsapp_reply"
BUTTON_PREFIX = "ccwa:"
REQUIRED = C.REQUIRED_CONSENTS    # ai_processing, transfer_abroad
TEXT_MAX = 2000


# ─── Sender identity and consent ───


def sender_ref(ctx: Any) -> str | None:
    return bytes(ctx.sender_hmac).hex() if ctx.sender_hmac else None


def subject_ref(ref: str) -> str:
    """consents.subject_ref of a WhatsApp guest: the hex contact hmac (the
    table's convention), so a consent given here is the same person's
    consent everywhere, and the number itself is never stored."""
    return ref


def consent_flags(cur: Any, hotel_slug: str, subject: str) -> dict[str, bool]:
    return {p: consent_mod.has_consent(cur, hotel_slug, subject, p) for p in C.CONSENT_PURPOSES}


def refused(cur: Any, hotel_slug: str, subject: str) -> bool:
    """True when the guest answered No: the latest ai_processing decision is
    a refusal (no row at all means we have not asked yet)."""
    row = consent_mod.latest(cur, hotel_slug, subject, "ai_processing")
    return row is not None and not row["granted"]


# ─── The sender's session ───


def guest_room(cur: Any, hotel_slug: str, ref: str) -> str | None:
    """The in-house room of this sender, when the Guests module can tell.
    guests.api has no such lookup yet (cross-lane request): until it does
    the session has no room, and requests carry the room the guest types,
    marked unverified for a door check."""
    from clefs.modules.concierge.prompt import _savepoint
    from clefs.modules.guests import api as guests_api

    fn = getattr(guests_api, "room_for_sender", None)
    if fn is None:
        return None
    room = _savepoint(cur, "cc_wa_room", lambda: fn(cur, hotel_slug, phone_hmac=bytes.fromhex(ref)), None)
    return str(room) if room else None


def open_session(cur: Any, hotel_slug: str, ref: str, *, language: str | None = None) -> dict:
    """The sender's live WhatsApp session, or a new one (2 hours, like the
    web). A room known from the reservation's own contact is trusted as
    verified; a typed one never is."""
    from clefs.modules.concierge import api, sessions

    cur.execute(
        f"SELECT {sessions.SESSION_COLUMNS} FROM cc_sessions WHERE hotel_slug = %s AND sender_ref = %s "
        "AND ended_at IS NULL AND expires_at > %s ORDER BY created_at DESC LIMIT 1",
        (hotel_slug, ref, clock.now()),
    )
    row = cur.fetchone()
    if row is not None:
        return dict(row)
    room = guest_room(cur, hotel_slug, ref)
    subject = subject_ref(ref)
    spec = api.build_surface_session(hotel_slug, surface="whatsapp", room=room, language=language, verified=bool(room), cur=cur)
    row, _token = sessions.create_session(
        cur, spec, key_status="whatsapp", mode="text", transport="none", consent_ref=subject,
        consents=consent_flags(cur, hotel_slug, subject), with_token=False,
    )
    cur.execute("UPDATE cc_sessions SET sender_ref = %s WHERE id = %s", (ref, row["id"]))
    return row


def _pending(cur: Any, session: dict) -> list[str]:
    cur.execute("SELECT chat_history FROM cc_sessions WHERE id = %s", (session["id"],))
    hist = (cur.fetchone() or {}).get("chat_history") or []
    return [m.get("content") for m in hist if isinstance(m, dict) and m.get("role") == "pending" and m.get("content")]


def _set_pending(cur: Any, session: dict, texts: list[str]) -> None:
    """Messages that arrived before the consent answer. Held in the
    session's working memory (never logged) and cleared once answered."""
    cur.execute("UPDATE cc_sessions SET chat_history = %s WHERE id = %s",
                (Json([{"role": "pending", "content": t[:TEXT_MAX]} for t in texts[-3:]]), session["id"]))


# ─── Consent question ───
# One entry per guest language (constants.GUEST_LANGUAGES; a test checks).
# Button titles stay within WhatsApp's 20 characters.

BUTTONS = {
    "en": ("Yes, that's fine", "No, a person"),
    "ar": ("نعم، موافق", "لا، أريد موظفا"),
    "ru": ("Да, хорошо", "Нет, нужен человек"),
    "hi": ("हाँ, ठीक है", "नहीं, स्टाफ़ से बात"),
    "ur": ("جی ہاں، ٹھیک ہے", "نہیں، عملے سے بات"),
    "zh": ("好的，同意", "不，转人工"),
    "de": ("Ja, einverstanden", "Nein, Mitarbeiter"),
    "fr": ("Oui, d'accord", "Non, une personne"),
    "ja": ("はい、同意します", "いいえ、スタッフと話す"),
    "ko": ("네, 동의합니다", "아니요, 직원 연결"),
    "th": ("ยอมรับ", "ไม่ ขอคุยกับพนักงาน"),
}
INTRO = {
    "en": "Hello, I am {name}, the AI assistant of {hotel}. Before I answer:",
    "ar": "مرحبا، أنا {name}، المساعد الذكي في {hotel}. قبل أن أجيب:",
    "ru": "Здравствуйте! Я {name}, ИИ-ассистент отеля {hotel}. Прежде чем я отвечу:",
    "hi": "नमस्ते, मैं {name} हूँ, {hotel} का AI सहायक। जवाब देने से पहले:",
    "ur": "السلام علیکم، میں {name} ہوں، {hotel} کا AI اسسٹنٹ۔ جواب دینے سے پہلے:",
    "zh": "您好，我是{name}，{hotel}的AI助手。在回答之前，请您确认：",
    "de": "Hallo, ich bin {name}, der KI-Assistent von {hotel}. Bevor ich antworte:",
    "fr": "Bonjour, je suis {name}, l'assistant IA de {hotel}. Avant de vous répondre :",
    "ja": "こんにちは。{hotel}のAIアシスタント、{name}です。お答えする前に、以下をご確認ください。",
    "ko": "안녕하세요, {hotel}의 AI 어시스턴트 {name}입니다. 답변드리기 전에 확인해 주세요.",
    "th": "สวัสดีค่ะ ดิฉัน {name} ผู้ช่วย AI ของ {hotel} ก่อนตอบคำถาม โปรดอ่านข้อความต่อไปนี้ค่ะ",
}
DECLINED = {
    "en": "No problem. A member of our team will reply to you here shortly.",
    "ar": "لا مشكلة. سيرد عليك أحد أعضاء فريقنا هنا قريبا.",
    "ru": "Хорошо. Наш сотрудник скоро ответит вам здесь.",
    "hi": "कोई बात नहीं। हमारी टीम का कोई सदस्य जल्द ही आपको यहीं जवाब देगा।",
    "ur": "کوئی بات نہیں۔ ہماری ٹیم کا کوئی رکن جلد ہی آپ کو یہیں جواب دے گا۔",
    "zh": "没问题。我们的工作人员很快会在这里回复您。",
    "de": "Kein Problem. Ein Mitglied unseres Teams antwortet Ihnen hier in Kürze.",
    "fr": "Pas de problème. Un membre de notre équipe vous répondra ici très bientôt.",
    "ja": "承知しました。まもなくスタッフがこちらでご返信いたします。",
    "ko": "알겠습니다. 곧 저희 직원이 여기에서 답변드리겠습니다.",
    "th": "ได้เลยค่ะ เจ้าหน้าที่ของเราจะตอบกลับคุณที่นี่ในไม่ช้าค่ะ",
}
THANKS = {
    "en": "Thank you. How can I help?",
    "ar": "شكرا لك. كيف يمكنني مساعدتك؟",
    "ru": "Спасибо. Чем могу помочь?",
    "hi": "धन्यवाद। मैं आपकी कैसे मदद करूँ?",
    "ur": "شکریہ۔ بتائیے، میں آپ کی کیا مدد کروں؟",
    "zh": "谢谢。请问有什么可以帮您？",
    "de": "Vielen Dank. Wie kann ich Ihnen helfen?",
    "fr": "Merci. Comment puis-je vous aider ?",
    "ja": "ありがとうございます。どのようなご用件でしょうか？",
    "ko": "감사합니다. 무엇을 도와드릴까요?",
    "th": "ขอบคุณค่ะ มีอะไรให้ช่วยไหมคะ",
}

# Letters only Urdu uses among the Arabic-script guest languages.
_URDU_LETTERS = frozenset("ٹڈڑںھہۂۃےۓ")
# Scripts that name one guest language, checked in this order: kana before
# the Han characters, which Japanese writes too.
_SCRIPT_LANGS: tuple[tuple[tuple[tuple[str, str], ...], str], ...] = (
    ((("\u3040", "\u30ff"), ("\u31f0", "\u31ff"), ("\uff66", "\uff9f")), "ja"),
    ((("\uac00", "\ud7af"), ("\u1100", "\u11ff"), ("\u3130", "\u318f")), "ko"),
    ((("\u0e00", "\u0e7f"),), "th"),
    ((("\u0900", "\u097f"),), "hi"),
    ((("\u0400", "\u04ff"),), "ru"),
    ((("\u3400", "\u4dbf"), ("\u4e00", "\u9fff")), "zh"),
)


def guess_language(text: str | None) -> str:
    """The guest's language from the script of the message: Arabic or Urdu,
    Japanese, Korean, Thai, Hindi, Russian or Chinese. Latin script reads
    as English: a short German or French message cannot be told apart
    reliably, and the model answers in the guest's language anyway."""
    s = text or ""
    if any("\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f" for ch in s):
        return "ur" if any(ch in _URDU_LETTERS for ch in s) else "ar"
    for ranges, lang in _SCRIPT_LANGS:
        if any(lo <= ch <= hi for ch in s for lo, hi in ranges):
            return lang
    return "en"


def consent_body(cur: Any, hotel_slug: str, lang: str) -> str:
    from clefs.modules.concierge import sessions

    hotel = get_hotel(cur, hotel_slug)
    name = (hotel.assistant_name or "").strip() or "Clefs"
    texts = sessions.consent_texts(cur, hotel_slug, lang)
    parts = [INTRO.get(lang, INTRO["en"]).format(name=name, hotel=hotel.name)]
    parts += [texts[p]["text"] for p in REQUIRED if p in texts]
    return "\n\n".join(parts)[:1000]


def ask_consent(ctx: Any, session: dict, lang: str) -> None:
    from clefs.notify import send_whatsapp_interactive

    yes, no = BUTTONS.get(lang, BUTTONS["en"])
    sid = str(session["id"])
    send_whatsapp_interactive(
        ctx.sender, consent_body(ctx.cur, ctx.hotel_slug, lang),
        buttons=[(tokens.sign_ref(f"{BUTTON_PREFIX}yes:{sid}:{lang}"), yes), (tokens.sign_ref(f"{BUTTON_PREFIX}no:{sid}:{lang}"), no)],
        hotel_slug=ctx.hotel_slug, purpose=PURPOSE, sender="hotel", idempotency_key=f"ccwa:consent:{ctx.provider_id}",
        cur=ctx.cur,
    )


def enqueue_reply(ctx: Any, session: dict, *, use_pending: bool = False) -> None:
    """The answer is written by the worker (see the module note); queued in
    the webhook's transaction, so it exists only if the webhook commits.
    The task reads the text from inbound_messages (or, right after the
    consent answer, the messages held while waiting for it)."""
    jobs.enqueue(REPLY_TASK, {"session_id": str(session["id"]), "inbound_id": ctx.inbound_id, "use_pending": use_pending},
                 hotel_slug=ctx.hotel_slug, dedupe_key=f"ccwa:{ctx.inbound_id}", cur=ctx.cur)


# ─── Handlers ───


def _eligible(ctx: Any) -> str | None:
    """The sender ref when the concierge should take this message."""
    from clefs.modules.guests import api as guests_api

    ref = sender_ref(ctx)
    if not (ctx.hotel_slug and ref and module_enabled(ctx.cur, ctx.hotel_slug, "concierge")):
        return None
    from clefs.modules.concierge import live

    if not live.is_live(ctx.cur, ctx.hotel_slug):
        return None  # not switched live yet: Service's staff fallback takes the message
    from clefs.modules.concierge.prompt import _savepoint

    # In a savepoint: a failure in the Guests lane's lookup must leave the
    # message to the next handler, not abort the webhook's transaction.
    known = _savepoint(ctx.cur, "cc_wa_known", lambda: guests_api.is_known_guest_sender(
        ctx.cur, ctx.hotel_slug, phone_hmac=bytes(ctx.sender_hmac)), False)
    return ref if known else None


@on_whatsapp("guest", priority=PRIORITY, kind="text")
def whatsapp_chat(ctx: Any) -> dict:
    """A known guest's text. Returns {"handled": False} to pass it on."""
    text = (ctx.text or "").strip()[:TEXT_MAX]
    ref = _eligible(ctx) if text else None
    if ref is None:
        return {"handled": False}
    subject = subject_ref(ref)
    if refused(ctx.cur, ctx.hotel_slug, subject):
        return {"handled": False}  # they asked for a person: Service's fallback files it for staff
    lang = guess_language(text)
    session = open_session(ctx.cur, ctx.hotel_slug, ref, language=lang)
    flags = consent_flags(ctx.cur, ctx.hotel_slug, subject)
    if not all(flags.get(p) for p in REQUIRED):
        pending = _pending(ctx.cur, session)
        _set_pending(ctx.cur, session, pending + [text])
        if not pending:
            ask_consent(ctx, session, lang)
        return {"handled": True, "session_id": str(session["id"]), "awaiting_consent": True}
    enqueue_reply(ctx, session)
    return {"handled": True, "session_id": str(session["id"])}


@on_whatsapp("guest", priority=PRIORITY, button_prefix=BUTTON_PREFIX)
def whatsapp_consent_button(ctx: Any) -> dict:
    """The answer to the consent question (a verified sign_ref id)."""
    from clefs.modules.concierge import sessions

    try:
        _prefix, answer, sid, lang = (ctx.button_ref or "").split(":", 3)
    except ValueError:
        return {"handled": False}
    session = sessions.get_session(ctx.cur, sid)
    ref = sender_ref(ctx)
    if session is None or session["hotel_slug"] != ctx.hotel_slug or session.get("consent_ref") != (ref and subject_ref(ref)):
        return {"handled": False}
    granted = answer == "yes"
    sessions.record_grants(ctx.cur, ctx.hotel_slug, session["consent_ref"],
                           {p: granted for p in REQUIRED}, language=lang, evidence={"channel": "whatsapp", "session_id": sid})
    ctx.cur.execute("UPDATE cc_sessions SET consents = %s WHERE id = %s",
                    (Json(consent_flags(ctx.cur, ctx.hotel_slug, session["consent_ref"])), sid))
    pending = _pending(ctx.cur, session)
    if not granted:
        from clefs.modules.service import api as service_api

        _set_pending(ctx.cur, session, [])
        service_api.escalate_to_human(ctx.cur, ctx.hotel_slug, room=session.get("room") or "",
                                      reason="WhatsApp guest asked for a person", channel="whatsapp", sensitive=False,
                                      excerpt=" / ".join(pending)[:300] or None)
        ctx.reply_text(DECLINED.get(lang, DECLINED["en"]), purpose=PURPOSE,
                       idempotency_key=f"ccwa:declined:{ctx.provider_id}")
        return {"handled": True, "consent": False}
    if pending:
        enqueue_reply(ctx, session, use_pending=True)
    else:
        ctx.reply_text(THANKS.get(lang, THANKS["en"]), purpose=PURPOSE, idempotency_key=f"ccwa:thanks:{ctx.provider_id}")
    return {"handled": True, "consent": True}


# ─── The reply (worker task) ───


@jobs.task(REPLY_TASK, max_attempts=2, backoff_s=10, timeout_s=90)
def whatsapp_reply(ctx: Any, payload: dict) -> dict:
    """Answer one guest message with the text model and send it back inside
    the 24 h window. A model failure answers with the hand-off sentence
    (text_chat never raises for that), so a retry only covers a crash."""
    from clefs.modules.concierge import sessions, text_chat
    from clefs.notify import send_whatsapp_text

    cur = ctx.cur
    session = sessions.get_session(cur, payload.get("session_id") or "")
    cur.execute("SELECT sender, payload FROM inbound_messages WHERE id = %s", (payload.get("inbound_id"),))
    inbound = cur.fetchone()
    if session is None or inbound is None or session.get("ended_at") is not None:
        return {"skipped": "gone"}
    session.pop("token_hash", None)
    if payload.get("use_pending"):
        text = "\n".join(_pending(cur, session))
        _set_pending(cur, session, [])
    else:
        text = (((inbound["payload"] or {}).get("text") or {}).get("body") or "").strip()
    if not text:
        return {"skipped": "empty"}
    reply = text_chat.chat_turn(session, text[:TEXT_MAX], cur=cur)
    sent = send_whatsapp_text(inbound["sender"], reply["text"][:4000], hotel_slug=session["hotel_slug"], purpose=PURPOSE,
                              sender="hotel", idempotency_key=f"ccwa:reply:{payload.get('inbound_id')}", cur=cur)
    return {"session_id": str(session["id"]), "sent": bool(sent.ok), "status": sent.status, "handoff": reply["handoff"]}
