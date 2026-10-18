"""
0675_guests_consent_texts: the platform wording (hotel_slug NULL) of every
consent the guests module captures, EN and AR (04 spec 3.4).

Why a migration and not a startup hook: consent evidence must never change
after the fact, so each (purpose, version, language) is written once and
core.consent refuses a different body for an existing version. Public pages
fetch these through GET /public/journeys/{token} instead of hardcoding them.

Version "gst.v1", not "1": the concierge lane seeds its own sheet (for the
same purposes) and a shared "1" would make whichever migration runs second
fail its immutability check. "{hotel}" is replaced with the hotel's name when
the text is shown; the stored body (and its sha256) is the template itself,
which is what the guest was shown modulo the name.

Rows are inserted directly (not through core.consent.record_consent_text)
because marketing_whatsapp and marketing_email are registered by the module
at import (consent.register_consent_purpose), and a migration must not
depend on module import order. The sha256 matches record_consent_text's.
"""

import hashlib

VERSION = "gst.v1"

TEXTS: dict[str, dict[str, str]] = {
    # Meta's opt-in for utility messages: names the hotel, says WhatsApp.
    "whatsapp_updates": {
        "en": "Send me messages about my stay at {hotel} on WhatsApp.",
        "ar": "أرسلوا لي رسائل حول إقامتي في {hotel} عبر واتساب.",
    },
    # Marketing is separate per channel (these retire 03's single `marketing`).
    "marketing_whatsapp": {
        "en": "Send me offers from {hotel} on WhatsApp. I can reply STOP at any time.",
        "ar": "أرسلوا لي عروض {hotel} عبر واتساب. يمكنني إرسال إيقاف في أي وقت.",
    },
    "marketing_email": {
        "en": "Send me offers from {hotel} by email. I can unsubscribe at any time.",
        "ar": "أرسلوا لي عروض {hotel} عبر البريد الإلكتروني. يمكنني إلغاء الاشتراك في أي وقت.",
    },
    # PDPL Arts. 22-23: express consent to processing outside the UAE (Neon
    # Singapore, Google Gemini and Meta in the US). Required for every capture.
    "transfer_abroad": {
        "en": (
            "I agree that {hotel} and its service providers may store and process my contact details "
            "and messages outside the UAE, including in Singapore and the United States."
        ),
        "ar": (
            "أوافق على أن يقوم {hotel} ومزودو خدماته بتخزين ومعالجة بيانات الاتصال الخاصة بي "
            "ورسائلي خارج دولة الإمارات، بما في ذلك سنغافورة والولايات المتحدة."
        ),
    },
    # 03 spec 2.7: a guest profile exists only with this consent.
    "profile_memory": {
        "en": "Remember my preferences at {hotel} for my next stay.",
        "ar": "تذكروا تفضيلاتي في {hotel} لإقامتي القادمة.",
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
            row = cur.fetchone()
            if row is not None:
                continue  # immutable: an existing version is never rewritten
            cur.execute(
                """
                INSERT INTO consent_texts (hotel_slug, purpose, version, language, body, sha256)
                VALUES (NULL, %s, %s, %s, %s, %s)
                """,
                (purpose, VERSION, language, body, digest),
            )
