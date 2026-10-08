"""
Platform default consent wording (03 spec 5.3), seeded into the foundation
consent_texts table by migration 0801 (platform rows, hotel_slug NULL). A hotel
adds its own wording as a new version through POST .../trust/consent-texts.

WHY here and not in a migration body: the same wording is needed by the seed
migration, the console (a hotel may add its own version, never edit one) and
tests. core.consent.record_consent_text is idempotent per (purpose, version,
language) and refuses different wording under an existing version, so a new
wording always ships as a new VERSION, never an edit.

The wording is plain and purpose-by-purpose because the PDPL has no
legitimate-interest basis: each purpose is asked for separately (Canon PDPL
note). It must be reviewed by counsel before a hotel goes live; the version
string is what a consent row points at, so review means a new version.
"""

from __future__ import annotations

from typing import Any

from clefs.core import consent

# Prefixed so it can never collide with a version a hotel or a test records.
VERSION = "clefs-2026-10"

# ─── Wording ───

TEXTS: dict[str, dict[str, str]] = {
    "recording": {
        "en": "I agree that this call or chat may be recorded and transcribed so the hotel can handle my request "
        "and check the quality of its service.",
        "ar": "أوافق على تسجيل هذه المكالمة أو المحادثة وتحويلها إلى نص حتى يتمكن الفندق من معالجة طلبي "
        "ومراجعة جودة خدمته.",
    },
    "ai_processing": {
        "en": "I agree that an AI assistant may read and process what I write or say, to answer me and pass my "
        "requests to the hotel team.",
        "ar": "أوافق على أن يقرأ مساعد ذكاء اصطناعي ما أكتبه أو أقوله ويعالجه، للرد علي وإحالة طلباتي إلى فريق الفندق.",
    },
    "transfer_abroad": {
        "en": "I agree that my data may be processed outside the UAE (for example by AI and cloud providers in the "
        "United States and Singapore) to provide this service.",
        "ar": "أوافق على معالجة بياناتي خارج دولة الإمارات (مثلا لدى مزودي الذكاء الاصطناعي والخدمات السحابية في "
        "الولايات المتحدة وسنغافورة) لتقديم هذه الخدمة.",
    },
    "marketing": {
        "en": "I agree to receive offers and news from the hotel. I can stop them at any time by replying STOP.",
        "ar": "أوافق على تلقي العروض والأخبار من الفندق، ويمكنني إيقافها في أي وقت بالرد بكلمة STOP.",
    },
    "ai_analytics": {
        "en": "I agree that my conversations may be included, without my name or contact details, in analysis "
        "that helps the hotel improve its service.",
        "ar": "أوافق على إدراج محادثاتي، دون اسمي أو بيانات الاتصال الخاصة بي، في تحليلات تساعد الفندق على "
        "تحسين خدمته.",
    },
    "profile_memory": {
        "en": "I agree that the hotel may remember my preferences between stays. I can ask for them to be "
        "deleted at any time.",
        "ar": "أوافق على أن يحتفظ الفندق بتفضيلاتي بين الإقامات، ويمكنني طلب حذفها في أي وقت.",
    },
    "auto_reply": {
        "en": "I agree that an AI assistant may reply to me on the hotel's behalf without a staff member "
        "checking each reply first.",
        "ar": "أوافق على أن يرد علي مساعد ذكاء اصطناعي نيابة عن الفندق دون أن يراجع أحد الموظفين كل رد مسبقا.",
    },
    "whatsapp_updates": {
        "en": "I agree to receive updates about my stay and my requests on WhatsApp.",
        "ar": "أوافق على تلقي تحديثات عن إقامتي وطلباتي عبر واتساب.",
    },
}

LANGUAGES = ("en", "ar")


# ─── Seeding ───


def seed_consent_texts(cur: Any, *, hotel_slug: str | None = None) -> list[dict]:
    """Record every purpose's wording at VERSION in both languages (platform
    default when hotel_slug is None). Idempotent: an identical row is
    returned as is. Only purposes core.consent knows are seeded, so a
    purpose added here before its registration cannot fail the migration."""
    known = set(consent.purposes())
    out = []
    for purpose, by_lang in TEXTS.items():
        if purpose not in known:
            continue
        for language in LANGUAGES:
            body = by_lang.get(language)
            if body:
                out.append(
                    consent.record_consent_text(cur, purpose, VERSION, body, language=language, hotel_slug=hotel_slug)
                )
    return out
