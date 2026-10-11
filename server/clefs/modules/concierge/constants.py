"""
Concierge constants: surfaces, fact vocabulary, required KB sections,
emergency numbers and consent purposes. One place, so the prompt, the tools,
the stay context and the console never disagree.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── Surfaces (03 spec 2.1) ───
# web_room: the QR page of one room. web_location: a venue QR (spa, lobby).
# phone: the in-room phone via SIP (voice module). text_fallback: the HTTP
# chat when the Live connection fails. eval: Trust's runner and "Test this answer".
# whatsapp: a known in-house guest on WhatsApp (GUEST_PRIORITY slot 30).
Surface = Literal["web_room", "web_location", "phone", "text_fallback", "eval", "whatsapp"]
SURFACES: tuple[str, ...] = ("web_room", "web_location", "phone", "text_fallback", "eval", "whatsapp")

# The service.api SOURCES value each surface files requests under.
SURFACE_SOURCE: dict[str, str] = {
    "web_room": "web",
    "web_location": "web",
    "phone": "sip",
    "text_fallback": "text",
    "eval": "text",
    "whatsapp": "whatsapp",
}

# Surfaces whose room is fixed by the session (QR room key or PBX
# extension). A `room` argument from the model is ignored there (03 spec 0.3).
ROOM_BOUND_SURFACES = frozenset({"web_room", "phone"})

# ─── Knowledge base ───

# 04 spec 5.5: the closed fact vocabulary. price_band is Visibility's own
# (trailing ADR); star_rating is allowed but its truth is hotels.star_rating.
FACT_KEYS: tuple[str, ...] = ("checkin_time", "checkout_time", "parking", "pool", "breakfast", "star_rating", "pets", "wifi")
FACT_LABELS: dict[str, str] = {
    "checkin_time": "Check-in time",
    "checkout_time": "Check-out time",
    "parking": "Parking",
    "pool": "Pool",
    "breakfast": "Breakfast",
    "star_rating": "Star rating",
    "pets": "Pets allowed",
    "wifi": "Wi-Fi",
}
PARKING_VALUES = ("none", "free", "paid")
BOOLEAN_FACTS = frozenset({"pool", "breakfast", "pets", "wifi"})
TIME_FACTS = frozenset({"checkin_time", "checkout_time"})

KB_KINDS: tuple[str, ...] = ("fact", "faq", "policy", "local_place", "venue_note")
KB_STATUSES: tuple[str, ...] = ("pending", "approved", "rejected", "archived")
KB_MODES: tuple[str, ...] = ("legacy_text", "structured")

# The readiness gate's required sections (03 spec 5.4), plus the sections the
# importer files into. (key, English title, Arabic title, required)
STANDARD_SECTIONS: tuple[tuple[str, str, str, bool], ...] = (
    ("wifi", "Wi-Fi", "الواي فاي", True),
    ("checkout", "Check-in and check-out", "تسجيل الوصول والمغادرة", True),
    ("breakfast", "Breakfast", "الإفطار", True),
    ("pool", "Pool and gym", "المسبح والنادي الرياضي", True),
    ("parking", "Parking", "مواقف السيارات", True),
    ("emergency", "Safety and emergencies", "السلامة والطوارئ", True),
    ("pets", "Pets", "الحيوانات الأليفة", True),
    ("smoking", "Smoking", "التدخين", True),
    ("dining", "Restaurants and in-room dining", "المطاعم وخدمة الغرف", False),
    ("spa", "Spa and wellness", "السبا والعافية", False),
    ("transport", "Transport and airport", "المواصلات والمطار", False),
    ("local", "Around the hotel", "حول الفندق", False),
    ("policies", "Hotel policies", "سياسات الفندق", False),
    ("general", "General", "عام", False),
)
REQUIRED_SECTIONS: tuple[str, ...] = tuple(k for k, _, _, req in STANDARD_SECTIONS if req)
# Where a keyed fact lands when nothing else says (Visibility proposals).
FACT_SECTION: dict[str, str] = {
    "checkin_time": "checkout",
    "checkout_time": "checkout",
    "parking": "parking",
    "pool": "pool",
    "breakfast": "breakfast",
    "star_rating": "general",
    "pets": "pets",
    "wifi": "wifi",
}

# Above about 6k tokens of approved KB the prompt inlines section titles and
# top questions only, and exposes search_hotel_info (03 spec 2.1 block 4).
# Four characters per token is the usual rough estimate.
KB_INLINE_CHAR_BUDGET = 24_000

# ─── Consent (03 spec 2.3, Canon PDPL) ───
# Required before the AI runs at all: ai_processing and transfer_abroad
# (Google Cloud's Gemini models may process a message outside the guest's
# country, and the data is stored in Google Cloud's India region). recording
# is required for voice (a text-only guest may decline, and then no
# transcript is stored). ai_analytics is optional. Marketing is asked later
# and separately (guests module).
CONSENT_PURPOSES: tuple[str, ...] = ("ai_processing", "transfer_abroad", "recording", "ai_analytics")
REQUIRED_CONSENTS: tuple[str, ...] = ("ai_processing", "transfer_abroad")
VOICE_REQUIRED_CONSENTS: tuple[str, ...] = ("ai_processing", "transfer_abroad", "recording")
# The platform wording the sheet shows and grants point at. A distinct
# version string, so the Guests lane's own "1" texts never collide with it.
# concierge-2 (0208_concierge_consent_texts_v2.py, every guest language)
# replaced concierge-1 (0202, EN/AR, written for a UAE deployment); the
# concierge-1 rows stay as the evidence earlier grants point at.
CONSENT_TEXT_VERSION = "concierge-2"

# ─── Emergency numbers ───
# Canon "Emergency numbers" (UAE), Japan (the Tokyo demo hotel: 119 is fire
# and ambulance in one, 110 police, 118 the coast guard) and the legacy
# Indian hotels; an unknown country gets none (the prompt then says "local
# emergency services"). kind -> the number said first.
EMERGENCY_NUMBERS: dict[str, tuple[tuple[str, str], ...]] = {
    "JP": (
        ("119", "fire_ambulance"),
        ("110", "police"),
        ("118", "coast_guard"),
    ),
    "AE": (
        ("999", "police"),
        ("998", "ambulance"),
        ("997", "civil_defence"),
        ("996", "coast_guard"),
        ("995", "search_rescue"),
        ("991", "electricity_water"),
    ),
    "IN": (
        ("112", "emergency"),
        ("100", "police"),
        ("108", "ambulance"),
        ("101", "fire"),
    ),
}
# The number to say first for an emergency kind (Service's lexicon kinds).
PRIMARY_NUMBER_SERVICE: dict[str, str] = {
    "medical": "ambulance",
    "fire": "civil_defence",
    "gas": "civil_defence",
    "smoke": "civil_defence",
    "flood": "civil_defence",
    "violence": "police",
    "weapon": "police",
    "threat": "police",
    "missing_child": "police",
    "lift_entrapment": "civil_defence",
    "death": "police",
    "drugs": "police",
}

# ─── Sessions ───
SESSION_TTL_S = 2 * 3600          # bearer session token (03 spec 2.2)
CLIENT_SECRET_TTL_S = 1800        # Gemini Live ephemeral token (expire_time 30 min)
NEW_SESSION_WINDOW_S = 60        # the token must open its Live session within 1 min
STAY_TOKEN_TTL_S = 12 * 3600      # signed "this device scanned a valid QR" ticket
QUOTE_TTL_S = 600                 # place_order confirm window
EMERGENCY_DEDUPE_S = 30           # server double check: skip if escalated this recently
REQUEST_MERGE_S = 120             # the same request twice in 2 minutes is one request
MAX_REQUESTS_PER_SESSION = 15

# ─── Mint rate limits (03 spec 2.2) ───
# Per source address (hashed, never stored raw) and per room, over sliding
# windows in clefs.ratelimit; the hotel cap is per hotel-local day so one
# leaked QR photo cannot burn the hotel's whole Gemini API budget.
MINT_PER_IP_LIMIT = 30
MINT_PER_IP_WINDOW_S = 3600
MINT_PER_ROOM_LIMIT = 10
MINT_PER_ROOM_WINDOW_S = 600
MINT_PER_HOTEL_DAILY_DEFAULT = 500
CONTEXT_READS_PER_IP_TARGET = 60
CONTEXT_READS_PER_IP = 300
CONTEXT_WINDOW_S = 600
# Lookups of a room or venue that does not exist (404), per hashed address,
# across the stay-context and mint routes. Recorded in their own committed
# transaction: the 404 rolls the request's transaction back, so a miss
# counted inside it would be free and room numbers could be walked forever.
TARGET_MISSES_PER_IP = 20
TARGET_MISS_WINDOW_S = 600

# The frozen api.build_session takes a channel; each maps to one surface.
CHANNEL_SURFACE: dict[str, str] = {"web": "web_room", "sip": "phone", "text": "text_fallback", "whatsapp": "whatsapp"}
SURFACE_CHANNEL: dict[str, str] = {
    "web_room": "web",
    "web_location": "web",
    "phone": "sip",
    "text_fallback": "text",
    "eval": "text",
    "whatsapp": "whatsapp",
}
# Surfaces that may order from the menu (prompt block 5). A venue QR orders
# in the venue; the phone orders by room; evals exercise the same path.
ORDER_SURFACES = frozenset({"web_room", "web_location", "phone", "text_fallback", "eval"})
LIVE_SURFACES = frozenset({"web_room", "web_location", "phone"})
SESSION_MODES: tuple[str, ...] = ("voice", "text", "chip", "prewarm")

# Guest UI languages (03 spec 2.6); anything else falls back to English.
# ja, ko and th joined for JAPAC guests (the Tokyo demo hotel). Every list
# keyed by these must cover all of them: the client dictionaries
# (client/app/stay/i18n, checked by scripts/check-i18n.mjs), the greetings
# (prompt._PARTS, _INTRO), the consent texts (latest concierge migration),
# the WhatsApp consent question and the safety lexicons. Only ar and ur are
# right-to-left.
GUEST_LANGUAGES: tuple[str, ...] = ("en", "ar", "ru", "hi", "ur", "zh", "de", "fr", "ja", "ko", "th")
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ar": "Arabic",
    "ru": "Russian",
    "hi": "Hindi",
    "ur": "Urdu",
    "zh": "Chinese",
    "de": "German",
    "fr": "French",
    "ja": "Japanese",
    "ko": "Korean",
    "th": "Thai",
}
DEFAULT_VOICE = "Kore"  # a Gemini prebuilt voice

# ─── Module settings (hotel_module_settings['concierge']) ───
# Read with core.hotels.get_module_settings(cur, slug, "concierge", ConciergeSettings).
# extra="allow" keeps keys other code stores here (the legacy stay route's
# legacy_qr_personalization) when the console saves kb_mode.


class ConciergeSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    # None: legacy_text when hotels.knowledge_base has text, else structured
    # (kb.kb_mode). Approving a legacy import sets "structured" explicitly.
    kb_mode: Literal["legacy_text", "structured"] | None = None
    # Legacy QR codes (no ?k=) personalise only when this is true (read by
    # the legacy /public/stay route and by the stay context).
    legacy_qr_personalization: bool = False
    daily_session_cap: int = Field(default=MINT_PER_HOTEL_DAILY_DEFAULT, ge=0, le=100_000)
    voice: str | None = None
    # Quick-reply chips shown on the guest page, per language: [{key, label}].
    chips: dict[str, list[dict]] = {}
    # The default chips are the six the guest page is designed around (05
    # spec: towels, water, cleaning, Wi-Fi, check-out, front desk). "Order
    # food" joins them only when a hotel switches this on and has a menu.
    menu_chip: bool = False
