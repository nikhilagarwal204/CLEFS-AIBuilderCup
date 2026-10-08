"""
0202_concierge_consent_texts: the platform wording (hotel_slug NULL) of the
concierge consent sheet, EN and AR (03 spec 2.3, Canon PDPL).

Why a migration: consent evidence must never change after the fact, so each
(purpose, version, language) is written once and core.consent refuses other
wording for an existing version. The stay context serves these texts, so the
guest page never hardcodes what a guest agreed to.

Version "concierge-1" (constants.CONSENT_TEXT_VERSION), not "1": the guests
lane seeds "gst.v1" for some of the same purposes, and a shared version would
make whichever migration ran second fail the immutability check. "{hotel}" is
replaced with the hotel's name when shown; the stored body is the template.
Rows are inserted directly with record_consent_text's sha256, so the
migration does not depend on module import order.
"""

import hashlib

VERSION = "concierge-1"

TEXTS: dict[str, dict[str, str]] = {
    # Required before the AI runs at all.
    "ai_processing": {
        "en": (
            "I agree that {hotel} may use an AI assistant, provided by Google (Gemini API), to understand and answer "
            "my questions and requests, and to pass them to hotel staff."
        ),
        "ar": "أوافق على أن يستخدم {hotel} مساعدا بالذكاء الاصطناعي تقدمه Google (Gemini API) لفهم أسئلتي وطلباتي والرد عليها وإحالتها إلى موظفي الفندق.",
    },
    # PDPL: express consent to processing outside the UAE (Google's Gemini API in the US,
    # the database in Singapore). Required: there is no UAE region today.
    "transfer_abroad": {
        "en": (
            "I agree that {hotel} and its service providers may process my messages and voice outside the UAE, "
            "including in Singapore and the United States (Google, for the Gemini API)."
        ),
        "ar": "أوافق على أن يقوم {hotel} ومزودو خدماته بمعالجة رسائلي وصوتي خارج دولة الإمارات، بما في ذلك سنغافورة والولايات المتحدة (Google لواجهة Gemini API).",
    },
    # Required for voice. A text-only guest may decline: no transcript is kept.
    "recording": {
        "en": "Keep a transcript of my conversation with the assistant for up to 12 months so the hotel can check its service.",
        "ar": "الاحتفاظ بنص محادثتي مع المساعد لمدة تصل إلى 12 شهرا حتى يتمكن الفندق من مراجعة خدمته.",
    },
    # Optional.
    "ai_analytics": {
        "en": "Use my conversation, without my name, to improve the assistant's answers.",
        "ar": "استخدام محادثتي، دون اسمي، لتحسين إجابات المساعد.",
    },
}


def up(cur):
    for purpose, by_lang in TEXTS.items():
        for language, body in by_lang.items():
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            cur.execute(
                """
                SELECT sha256 FROM consent_texts
                WHERE hotel_slug IS NULL AND purpose = %s AND version = %s AND language = %s
                """,
                (purpose, VERSION, language),
            )
            if cur.fetchone() is not None:
                continue  # immutable: an existing version is never rewritten
            cur.execute(
                """
                INSERT INTO consent_texts (hotel_slug, purpose, version, language, body, sha256)
                VALUES (NULL, %s, %s, %s, %s, %s)
                """,
                (purpose, VERSION, language, body, digest),
            )
