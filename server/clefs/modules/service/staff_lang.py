"""
Staff in their own language, and what they say in the guest's (05 spec,
decision 10).

- A worker reads jobs in staff_members.languages[0] (English when empty).
  The job's title (item and quantity) and its English text are translated
  by the fast model once per (request, language) and cached in
  svc_translations; a changed text (a merge appends words) has a new hash.
  A new holder's translation is queued at assignment (prewarm, the
  service.translate_job task); a list that still finds one missing
  translates it on read within a short budget and shows English for the
  rest, never an error.
- A staff reply, typed or a voice note Gemini transcribes, is rewritten for
  the guest in the request's language (staff_requests.language) and stored
  as a guest message: the tracker shows it, the request's history keeps a
  "guest_message" event.
- Status lines and approved recovery gestures reach the guest the same way,
  cached per language.

Every model call degrades: English (or the worker's own words, flagged
translated=false) when the model is off, slow or wrong. Guest messages never
carry a surname.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import time as _time
from typing import Any

from pydantic import BaseModel

from clefs import ai, db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.modules.service import constants as C
from clefs.modules.service import state
from clefs.modules.service.routing import first_name
from clefs.settings import get_settings

log = logging.getLogger("clefs.service.staff_lang")

JOB_TIMEOUT_S = 4.0
GUEST_TIMEOUT_S = 4.0
TRANSCRIBE_TIMEOUT_S = 20.0
# A job list translates what is missing for at most this long, then shows
# English for the rest (the queued task fills them in).
LIST_BUDGET_S = 6.0
MAX_REPLY_CHARS = 1000
MAX_AUDIO_BYTES = 2 * 1024 * 1024
AUDIO_TYPES = frozenset({"audio/webm", "audio/ogg", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/mpeg", "audio/mp3",
                         "audio/wav", "audio/x-wav", "audio/wave", "audio/aac", "audio/flac", "audio/aiff"})

# Names the prompts use. Staff languages are wider than the guest UI's 8.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "ar": "Arabic", "ru": "Russian", "hi": "Hindi", "ur": "Urdu", "zh": "Chinese (Simplified)",
    "de": "German", "fr": "French", "fil": "Filipino (Tagalog)", "tl": "Filipino (Tagalog)", "ne": "Nepali",
    "ml": "Malayalam", "si": "Sinhala", "sw": "Swahili", "bn": "Bengali", "ta": "Tamil", "te": "Telugu",
    "kn": "Kannada", "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "fa": "Persian", "tr": "Turkish", "id": "Indonesian", "ms": "Malay", "th": "Thai",
    "vi": "Vietnamese", "ja": "Japanese", "ko": "Korean", "am": "Amharic", "so": "Somali", "uk": "Ukrainian",
}


def norm_lang(code: str | None) -> str:
    """'ar-AE' -> 'ar', 'FIL' -> 'fil'; English when empty."""
    c = (code or "").strip().lower().replace("_", "-").split("-")[0]
    return c[:8] if c.isalpha() else "en"


def language_name(code: str | None) -> str:
    c = norm_lang(code)
    return LANGUAGE_NAMES.get(c) or f"the language with code '{c}'"


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# ─── The job as the worker reads it ───


def item_label(row: dict) -> str:
    """What was asked for, in English: the model's own label (subtype), else
    the request type's label, else the start of the description."""
    if (row.get("subtype") or "").strip():
        return row["subtype"].strip()[:100]
    key = row.get("request_type_key") or ""
    label = C.REQUEST_TYPES.get(key, {}).get("label_en")
    if label and key != "other":
        return label
    text = " ".join((row.get("description") or "").split())
    return (text[:60] + ("..." if len(text) > 60 else "")) or label or row.get("type") or "Request"


def job_title_en(row: dict) -> str:
    qty = row.get("quantity")
    label = item_label(row)
    return f"{qty} x {label}" if isinstance(qty, int) and qty > 1 else label


def job_text_en(row: dict) -> str:
    return (row.get("description") or "").strip() or job_title_en(row)


def reading_language(cur: Any, hotel_slug: str, staff_id: int | None) -> str:
    """The worker's reading language: staff_members.languages[0] (05 decision 10)."""
    if not staff_id:
        return "en"
    cur.execute("SELECT languages FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, hotel_slug))
    r = cur.fetchone()
    langs = [x for x in ((r or {}).get("languages") or []) if x]
    return norm_lang(langs[0]) if langs else "en"


class JobText(BaseModel):
    title: str
    text: str


def _job_system(lang: str) -> str:
    return (
        f"You translate a hotel job for the hotel worker who will do it. Translate the title and the text into "
        f"{language_name(lang)}, in plain words a busy worker reads at a glance. Keep every number (room numbers, "
        "quantities, times) exactly as written. Do not add anything. Reply as JSON with title and text."
    )


def _cache_get(cur: Any, hotel_slug: str, purpose: str, lang: str, hashes: list[str]) -> dict[str, dict]:
    if not hashes:
        return {}
    cur.execute(
        """SELECT source_hash, title, text FROM svc_translations
           WHERE hotel_slug = %s AND purpose = %s AND lang = %s AND source_hash = ANY(%s)""",
        (hotel_slug, purpose, lang, hashes),
    )
    return {r["source_hash"]: dict(r) for r in cur.fetchall()}


def _cache_put(cur: Any, hotel_slug: str, purpose: str, lang: str, source_hash: str, *, text: str,
               title: str | None = None, request_id: int | None = None, model: str | None = None) -> None:
    cur.execute(
        """INSERT INTO svc_translations (hotel_slug, purpose, source_hash, lang, request_id, title, text, model, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (hotel_slug, purpose, source_hash, lang, request_id, title, text, model, clock.now()),
    )


def _with_cur(cur: Any, fn: Any, *args: Any, **kw: Any) -> Any:
    """Run fn(cur, ...) on the caller's cursor, else in a short transaction of
    its own (so a model call never holds a request's transaction open)."""
    if cur is not None:
        return fn(cur, *args, **kw)
    with db.db() as c:
        return fn(c, *args, **kw)


def _english(row: dict) -> dict:
    return {"display_title": job_title_en(row), "display_text": job_text_en(row), "display_language": "en",
            "display_translated": None}


def translate_job(hotel_slug: str, row: dict, lang: str, *, cur: Any = None, timeout_s: float = JOB_TIMEOUT_S) -> dict:
    """{display_title, display_text, display_language, display_translated}
    for one job in `lang`: from the cache, else one fast-model call (cached
    on success). English with display_translated=False when it fails."""
    lang = norm_lang(lang)
    if lang == "en":
        return _english(row)
    title_en, text_en = job_title_en(row), job_text_en(row)
    h = _hash(title_en, text_en)
    hit = _with_cur(cur, _cache_get, hotel_slug, "job", lang, [h]).get(h)
    if hit:
        return {"display_title": hit["title"] or title_en, "display_text": hit["text"], "display_language": lang,
                "display_translated": True}
    model = get_settings().AI_MODEL_FAST
    try:
        out = ai.structured(
            "service.translate_job", system=_job_system(lang), input=json.dumps({"title": title_en, "text": text_en}),
            schema=JobText, hotel_slug=hotel_slug, model=model, max_output_tokens=600, timeout_s=timeout_s,
            temperature=0,
        )
    except Exception as exc:  # AIDisabled, AIError, a timeout: English it is
        log.info("job translation skipped for %s: %s", hotel_slug, type(exc).__name__)
        return {**_english(row), "display_translated": False}
    title, text = (out.title or "").strip()[:300], (out.text or "").strip()[:4000]
    if not text:
        return {**_english(row), "display_translated": False}
    _with_cur(cur, _cache_put, hotel_slug, "job", lang, h, text=text, title=title or title_en, request_id=row.get("id"),
              model=model)
    return {"display_title": title or title_en, "display_text": text, "display_language": lang, "display_translated": True}


def display_fields(hotel_slug: str, rows: list[dict], lang: str, *, budget_s: float = LIST_BUDGET_S,
                   cur: Any = None) -> dict[int, dict]:
    """Display fields for a job list: one cache read for all of them, then
    model calls for the misses while the budget lasts (English after)."""
    lang = norm_lang(lang)
    if lang == "en" or not rows:
        return {r["id"]: _english(r) for r in rows}
    hashes = {r["id"]: _hash(job_title_en(r), job_text_en(r)) for r in rows}
    cached = _with_cur(cur, _cache_get, hotel_slug, "job", lang, sorted(set(hashes.values())))
    out: dict[int, dict] = {}
    deadline = _time.monotonic() + budget_s
    for r in rows:
        hit = cached.get(hashes[r["id"]])
        if hit:
            out[r["id"]] = {"display_title": hit["title"] or job_title_en(r), "display_text": hit["text"],
                            "display_language": lang, "display_translated": True}
            continue
        left = deadline - _time.monotonic()
        if left < 0.5:
            out[r["id"]] = {**_english(r), "display_translated": False}
            continue
        out[r["id"]] = translate_job(hotel_slug, r, lang, cur=cur, timeout_s=min(JOB_TIMEOUT_S, left))
    return out


def prewarm(cur: Any, row: dict) -> int | None:
    """Queue the job's translation for its new holder when they read
    another language and the cache has none (state.transition calls this
    when the holder changes). -> the queued task id, or None."""
    from clefs import jobs

    lang = reading_language(cur, row["hotel_slug"], row.get("assigned_staff_id"))
    if lang == "en":
        return None
    h = _hash(job_title_en(row), job_text_en(row))
    if _cache_get(cur, row["hotel_slug"], "job", lang, [h]):
        return None
    return jobs.enqueue("service.translate_job", {"request_id": row["id"], "lang": lang}, hotel_slug=row["hotel_slug"],
                        dedupe_key=f"svc.tr:{row['id']}:{lang}:{h[:16]}", cur=cur)


# ─── Lines for the guest ───


def english_to(hotel_slug: str, text_en: str, lang: str | None, *, cur: Any = None,
               timeout_s: float = GUEST_TIMEOUT_S, cache: bool = True) -> tuple[str, bool | None]:
    """An English line from the hotel (a status line, an approved gesture,
    the photo check's reason for the worker) in the reader's language,
    cached per language unless cache=False (a one-off line). -> (text,
    translated: None when English was asked for, False when the model
    failed and the English stands)."""
    lang = norm_lang(lang)
    text_en = (text_en or "").strip()
    if lang == "en" or not text_en:
        return text_en, None
    h = _hash(text_en)
    if cache:
        hit = _with_cur(cur, _cache_get, hotel_slug, "guest", lang, [h]).get(h)
        if hit:
            return hit["text"], True
    model = get_settings().AI_MODEL_FAST
    try:
        out = ai.text(
            "service.translate_line",
            system=(f"Translate this message from a hotel into {language_name(lang)}. Keep it warm, polite and short; "
                    "keep numbers, times and names exactly. Output only the translation."),
            input=text_en, hotel_slug=hotel_slug, model=model, max_output_tokens=400, timeout_s=timeout_s,
        ).strip()
    except Exception as exc:
        log.info("line translation skipped for %s: %s", hotel_slug, type(exc).__name__)
        return text_en, False
    if not out:
        return text_en, False
    if cache:
        _with_cur(cur, _cache_put, hotel_slug, "guest", lang, h, text=out[:2000], model=model)
    return out[:2000], True


def reply_for_guest(hotel_slug: str, text: str, lang: str | None, *, timeout_s: float = GUEST_TIMEOUT_S) -> tuple[str, bool]:
    """A staff member's words (any language) rewritten for the guest in
    their language. -> (text, translated); the worker's own words with
    translated=False when the model is off or fails."""
    lang = norm_lang(lang)
    try:
        out = ai.text(
            "service.translate_reply",
            system=(f"A hotel staff member wrote or said this message for a guest about the guest's request. Write it "
                    f"for the guest in {language_name(lang)}: the same meaning, polite and short (one or two "
                    "sentences). Keep times, numbers and room numbers exactly. Do not add promises, refunds, discounts "
                    "or compensation the staff member did not make, and never add a surname. Output only the message."),
            input=text, hotel_slug=hotel_slug, model=get_settings().AI_MODEL_FAST, max_output_tokens=400,
            timeout_s=timeout_s,
        ).strip()
    except Exception as exc:
        log.info("reply translation skipped for %s: %s", hotel_slug, type(exc).__name__)
        return text, False
    return (out[:2000], True) if out else (text, False)


def transcribe(hotel_slug: str, audio: bytes, mime: str, *, context: str = "") -> str:
    """A worker's voice note as text, by Gemini (ai.text with the audio as
    media). Raises ClefsError 503 when it cannot be heard or AI is off."""
    try:
        out = ai.text(
            "service.staff_voice",
            system=("Transcribe this short voice message from a hotel worker, word for word, in the language spoken. "
                    "Output only the words said; output nothing if nobody speaks."),
            input=f"A hotel worker's reply to a guest about this job: {context}"[:500],
            hotel_slug=hotel_slug, media=[(audio, mime)], max_output_tokens=500, timeout_s=TRANSCRIBE_TIMEOUT_S,
        )
    except Exception as exc:
        log.info("voice reply not transcribed for %s: %s", hotel_slug, type(exc).__name__)
        raise ClefsError(503, "The voice message could not be heard. Please type your reply.", "transcription_failed") from exc
    return " ".join((out or "").split())[:MAX_REPLY_CHARS]


def audio_from(b64: str, mime: str | None) -> tuple[bytes, str]:
    """Decode the staff app's base64 audio; 400 / 413 / 415 on bad input."""
    kind = (mime or "").split(";")[0].strip().lower() or "audio/webm"
    if kind not in AUDIO_TYPES:
        raise ClefsError(415, "Send the voice message as audio (webm, ogg, mp4, mpeg or wav)", "unsupported_audio")
    if len(b64) > MAX_AUDIO_BYTES * 4 // 3 + 16:
        raise ClefsError(413, "Voice messages can be at most about one minute", "audio_too_large")
    try:
        data = base64.b64decode(b64.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ClefsError(400, "audio_b64 is not valid base64", "bad_request") from exc
    if not data:
        raise ClefsError(400, "The voice message is empty", "bad_request")
    if len(data) > MAX_AUDIO_BYTES:
        raise ClefsError(413, "Voice messages can be at most about one minute", "audio_too_large")
    return data, kind


# ─── Guest messages ───


def add_message(cur: Any, hotel_slug: str, *, kind: str, text: str, language: str, request_id: int | None = None,
                session_id: str | None = None, case_id: int | None = None, text_original: str | None = None,
                language_original: str | None = None, translated: bool | None = None,
                staff_member_id: int | None = None, staff_first_name: str | None = None, via: str | None = None) -> dict:
    cur.execute(
        """INSERT INTO svc_guest_messages (hotel_slug, request_id, session_id, case_id, kind, text, language,
               text_original, language_original, translated, staff_member_id, staff_first_name, via, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (hotel_slug, request_id, session_id, case_id, kind, text[:2000], norm_lang(language), (text_original or None)
         and text_original[:2000], language_original, translated, staff_member_id, staff_first_name, via, clock.now()),
    )
    return dict(cur.fetchone())


def message_out(m: dict) -> dict:
    """The tracker's shape (05 spec 3: {at, kind, text, staff_first_name});
    `at` is epoch ms like every tracker instant."""
    return {"id": m["id"], "at": int(m["created_at"].timestamp() * 1000), "kind": m["kind"], "text": m["text"],
            "staff_first_name": m.get("staff_first_name"), "language": m.get("language")}


def messages_for(cur: Any, hotel_slug: str, row: dict, *, limit: int = 50) -> list[dict]:
    """The request's guest messages, plus the conversation's own (a gesture
    for a frustrated guest whose chat filed no job), oldest first."""
    cur.execute(
        """SELECT * FROM svc_guest_messages
           WHERE hotel_slug = %s AND (request_id = %s
                 OR (request_id IS NULL AND session_id IS NOT NULL AND session_id = %s))
           ORDER BY id DESC LIMIT %s""",
        (hotel_slug, row["id"], row.get("session_id") or "", limit),
    )
    return [message_out(dict(m)) for m in reversed(cur.fetchall())]


# English sources of the status lines Service writes to the guest.
STATUS_LINES = {
    "not_fixed": "We are sorry it is not fixed yet. We have sent it back to the team and told the duty manager.",
}


def status_line(cur: Any, row: dict, key: str) -> dict | None:
    """Write one status line to the guest, in their language."""
    text_en = STATUS_LINES.get(key)
    if not text_en:
        return None
    lang = norm_lang(row.get("language"))
    text, translated = english_to(row["hotel_slug"], text_en, lang, cur=cur)
    return add_message(cur, row["hotel_slug"], kind="status", text=text, language=lang, request_id=row["id"],
                       session_id=row.get("session_id"), text_original=text_en, language_original="en",
                       translated=translated, via="system")


# ─── Staff replies (POST /device/service/requests/{id}/reply) ───


def reply(ctx: Any, request_id: int, *, staff_id: int, text: str | None = None, audio_b64: str | None = None,
          mime: str | None = None) -> dict:
    """Store a staff reply for the guest. Typed text is used as written; a
    voice note is transcribed first. The model calls run between two short
    transactions, never inside one. -> {text_original, text_for_guest,
    language, translated, via, message}."""
    slug = ctx.hotel.slug
    with db.db(readonly=True) as cur:
        row = state.load(cur, slug, request_id)
        if row is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        cur.execute("SELECT name FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, slug))
        who = cur.fetchone()
    lang = norm_lang(row.get("language"))
    if text and text.strip():
        original, via = " ".join(text.split())[:MAX_REPLY_CHARS], "typed"
    elif audio_b64:
        audio, kind = audio_from(audio_b64, mime)
        original, via = transcribe(slug, audio, kind, context=f"{job_title_en(row)}, room {row.get('room') or '-'}"), "voice"
        if not original:
            raise ClefsError(422, "Nothing was heard in the voice message. Please try again.", "nothing_heard")
    else:
        raise ClefsError(400, "Send text or audio_b64", "bad_request")
    for_guest, translated = reply_for_guest(slug, original, lang)
    name = first_name((who or {}).get("name"))
    with db.db() as cur:
        row = state.load(cur, slug, request_id) or row
        msg = add_message(cur, slug, kind="staff_reply", text=for_guest, language=lang, request_id=row["id"],
                          session_id=row.get("session_id"), text_original=original, translated=translated,
                          staff_member_id=staff_id, staff_first_name=name, via=via)
        state.record_event(cur, row, "guest_message", state.Actor.from_ctx(ctx, staff_id=staff_id),
                           payload={"message_id": msg["id"], "kind": "staff_reply", "via": via,
                                    "text_original": original, "text_for_guest": for_guest, "language": lang,
                                    "translated": translated})
        state.publish_changed(cur, row, "guest_message")
    return {"text_original": original, "text_for_guest": for_guest, "language": lang, "translated": translated,
            "via": via, "message": message_out(msg)}
