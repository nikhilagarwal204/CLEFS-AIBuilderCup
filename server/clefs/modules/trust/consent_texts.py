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
# clefs-2026-10b: the transfer wording names this deployment (Google Cloud,
# India region) instead of the UAE and Singapore, and Japanese is added;
# migration 0823 records it and retires clefs-2026-10.
VERSION = "clefs-2026-10b"
PREVIOUS_VERSIONS = ("clefs-2026-10",)

# ─── Wording ───

TEXTS: dict[str, dict[str, str]] = {
    "recording": {
        "en": "I agree that this call or chat may be recorded and transcribed so the hotel can handle my request "
        "and check the quality of its service.",
        "ar": "أوافق على تسجيل هذه المكالمة أو المحادثة وتحويلها إلى نص حتى يتمكن الفندق من معالجة طلبي "
        "ومراجعة جودة خدمته.",
        "ja": "ホテルが私の依頼に対応し、サービスの品質を確認するために、この通話またはチャットが録音され、"
        "文字に起こされることに同意します。",
    },
    "ai_processing": {
        "en": "I agree that an AI assistant may read and process what I write or say, to answer me and pass my "
        "requests to the hotel team.",
        "ar": "أوافق على أن يقرأ مساعد ذكاء اصطناعي ما أكتبه أو أقوله ويعالجه، للرد علي وإحالة طلباتي إلى فريق الفندق.",
        "ja": "私が書いたり話したりした内容をAIアシスタントが読み取って処理し、私への回答やホテルスタッフへの"
        "依頼の伝達に使うことに同意します。",
    },
    "transfer_abroad": {
        "en": "I agree that my data may be processed outside my country to provide this service: by Google "
        "Cloud's Gemini AI models, which may process it in other countries including the United States, and "
        "stored in Google Cloud's India region.",
        "ar": "أوافق على معالجة بياناتي خارج بلدي لتقديم هذه الخدمة: من خلال نماذج Gemini للذكاء الاصطناعي من "
        "Google Cloud، التي قد تعالجها في دول أخرى منها الولايات المتحدة، مع تخزينها في منطقة الهند لدى Google Cloud.",
        "ja": "このサービスの提供のため、私のデータが国外で処理されることに同意します。Google CloudのGemini AI"
        "モデルが米国を含む他の国で処理する場合があり、データはGoogle Cloudのインド地域に保存されます。",
    },
    "marketing": {
        "en": "I agree to receive offers and news from the hotel. I can stop them at any time by replying STOP.",
        "ar": "أوافق على تلقي العروض والأخبار من الفندق، ويمكنني إيقافها في أي وقت بالرد بكلمة STOP.",
        "ja": "ホテルからのお得な情報やお知らせを受け取ることに同意します。STOPと返信すれば、いつでも停止できます。",
    },
    "ai_analytics": {
        "en": "I agree that my conversations may be included, without my name or contact details, in analysis "
        "that helps the hotel improve its service.",
        "ar": "أوافق على إدراج محادثاتي، دون اسمي أو بيانات الاتصال الخاصة بي، في تحليلات تساعد الفندق على "
        "تحسين خدمته.",
        "ja": "ホテルのサービス改善に役立つ分析に、私の会話が氏名や連絡先を除いた形で含まれることに同意します。",
    },
    "profile_memory": {
        "en": "I agree that the hotel may remember my preferences between stays. I can ask for them to be "
        "deleted at any time.",
        "ar": "أوافق على أن يحتفظ الفندق بتفضيلاتي بين الإقامات، ويمكنني طلب حذفها في أي وقت.",
        "ja": "ホテルが次回以降の滞在でも私の好みを記憶することに同意します。いつでも削除を依頼できます。",
    },
    "auto_reply": {
        "en": "I agree that an AI assistant may reply to me on the hotel's behalf without a staff member "
        "checking each reply first.",
        "ar": "أوافق على أن يرد علي مساعد ذكاء اصطناعي نيابة عن الفندق دون أن يراجع أحد الموظفين كل رد مسبقا.",
        "ja": "スタッフが事前に一つひとつ確認しなくても、AIアシスタントがホテルに代わって私に返信することに同意します。",
    },
    "whatsapp_updates": {
        "en": "I agree to receive updates about my stay and my requests on WhatsApp.",
        "ar": "أوافق على تلقي تحديثات عن إقامتي وطلباتي عبر واتساب.",
        "ja": "滞在や依頼に関する最新情報をWhatsAppで受け取ることに同意します。",
    },
}

LANGUAGES = ("en", "ar", "ja")


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
