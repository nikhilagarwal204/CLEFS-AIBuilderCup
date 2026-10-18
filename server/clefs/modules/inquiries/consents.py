"""
Web form consents (04 spec 4.3).

Five separate lines, each recorded with core.consent.record_consent against
the exact wording shown (a hotel-specific consent_texts row):

- inquiry_contact (required): "{hotel} may contact me about this request."
- transfer_abroad (required, separate): processing outside the UAE by the
  hotel's providers (Clefs and its sub-processors), with the list link.
- inquiry_whatsapp (optional): the hotel may reply on WhatsApp.
- marketing by WhatsApp / by email (optional, separate, unticked).

The version is "inq1-<sha8 of the wording>": consent texts are immutable per
version, and the wording embeds the hotel's name, so a renamed hotel gets a
new version rather than a 409.
"""

from __future__ import annotations

import hashlib
from typing import Any

from clefs.core import consent, privacy

SUBPROCESSORS_URL = "https://www.clefs.world/subprocessors"

consent.register_consent_purpose("inquiry_contact")
consent.register_consent_purpose("inquiry_whatsapp")

# key -> (purpose, {lang: wording})
LINES: dict[str, tuple[str, dict[str, str]]] = {
    "consent_contact": ("inquiry_contact", {
        "en": "{hotel} may contact me about this request.",
        "ar": "يمكن لـ {hotel} التواصل معي بخصوص هذا الطلب.",
    }),
    "consent_transfer_abroad": ("transfer_abroad", {
        "en": "I agree my request may be processed outside the UAE by {hotel}'s service providers "
              "(Clefs and its sub-processors: {subprocessors}).",
        "ar": "أوافق على معالجة طلبي خارج دولة الإمارات العربية المتحدة من قبل مزودي الخدمة لدى {hotel} "
              "(Clefs ومعالجوها الفرعيون: {subprocessors}).",
    }),
    "whatsapp_optin": ("inquiry_whatsapp", {
        "en": "{hotel} may reply to me on WhatsApp.",
        "ar": "يمكن لـ {hotel} الرد علي عبر واتساب.",
    }),
    "consent_marketing_whatsapp": ("marketing", {
        "en": "Send me offers from {hotel} by WhatsApp",
        "ar": "أرسلوا لي عروض {hotel} عبر واتساب",
    }),
    "consent_marketing_email": ("marketing", {
        "en": "Send me offers from {hotel} by email",
        "ar": "أرسلوا لي عروض {hotel} عبر البريد الإلكتروني",
    }),
}
REQUIRED = ("consent_contact", "consent_transfer_abroad")


def lang_of(language: str | None) -> str:
    return "ar" if (language or "").lower().startswith("ar") else "en"


def wording(key: str, hotel_name: str, language: str | None) -> str:
    return LINES[key][1][lang_of(language)].format(hotel=hotel_name, subprocessors=SUBPROCESSORS_URL)


def version_of(body: str) -> str:
    return "inq1-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]


def texts(hotel: Any, language: str | None) -> list[dict]:
    """What the widget shows: [{key, purpose, required, text, version}]."""
    out = []
    for key, (purpose, _) in LINES.items():
        body = wording(key, hotel.name, language)
        out.append({"key": key, "purpose": purpose, "required": key in REQUIRED, "text": body,
                    "version": version_of(body), "language": lang_of(language)})
    return out


def record(cur: Any, hotel: Any, *, granted: dict[str, bool], phone: str | None, email: str | None,
           language: str | None, evidence: dict) -> tuple[list[int], dict[str, str]]:
    """Record every granted line against each identity it concerns.
    Returns (consent ids, {line key: text version}) for the inquiry row and
    for guests.api.record_own_contact (two lines share `marketing`, so the
    versions are keyed by line, not purpose)."""
    lang = lang_of(language)
    ids: list[int] = []
    versions: dict[str, str] = {}
    refs = {
        "phone": privacy.contact_ref(hotel.slug, "phone", phone, cur=cur) if phone else None,
        "email": privacy.contact_ref(hotel.slug, "email", email, cur=cur) if email else None,
    }
    for key, (purpose, _) in LINES.items():
        if not granted.get(key):
            continue
        body = wording(key, hotel.name, lang)
        version = version_of(body)
        consent.record_consent_text(cur, purpose, version, body, language=lang, hotel_slug=hotel.slug)
        versions[key] = version
        if key == "consent_marketing_whatsapp" or key == "whatsapp_optin":
            subjects = [refs["phone"]]
        elif key == "consent_marketing_email":
            subjects = [refs["email"]]
        else:
            subjects = [refs["phone"], refs["email"]]
        for ref in [s for s in subjects if s]:
            row = consent.record_consent(
                cur, hotel.slug, ref, purpose, True, source="inquiry_web_form", subject_type="lead",
                text_version=version, language=lang, evidence={**evidence, "line": key},
            )
            ids.append(int(row["id"]))
    return ids, versions
