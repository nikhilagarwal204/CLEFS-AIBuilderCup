"""
Stay context: GET /public/stay/{slug}/{target}/context (03 spec 2.3).

Replaces the guest page's roster download. It returns what one QR page needs
and nothing about any other room: the hotel card, this room (or venue), the
guest's first name only when the scan is verified (?k= matches the room's
current QR key, or a stay token minted from one), chips, features, the
emergency numbers and the consent sheet texts. A legacy QR without a key
gets limited mode: the same page, no name, verified=false.
"""

from __future__ import annotations

import re
from typing import Any

from clefs.core.hotels import Hotel, module_enabled
from clefs.core import rooms as rooms_mod
from clefs.modules.concierge import constants as C
from clefs.modules.concierge import live, sessions

_ROOM_PREFIX = re.compile(r"^room[-_ ]?", re.I)

DEFAULT_CHIPS: dict[str, list[tuple[str, str]]] = {
    "en": [
        ("towels", "Extra towels"),
        ("water", "Bottled water"),
        ("housekeeping", "Clean my room"),
        ("wifi", "Wi-Fi details"),
        ("checkout", "Check-out time"),
        ("room_service", "Order food"),
        ("front_desk", "Talk to the front desk"),
    ],
    "ar": [
        ("towels", "مناشف إضافية"),
        ("water", "مياه معبأة"),
        ("housekeeping", "تنظيف الغرفة"),
        ("wifi", "تفاصيل الواي فاي"),
        ("checkout", "موعد المغادرة"),
        ("room_service", "طلب طعام"),
        ("front_desk", "التحدث مع الاستقبال"),
    ],
}
LOCATION_CHIPS = ("wifi", "front_desk")


def split_target(cur: Any, hotel: Hotel, target: str, kind: str | None = None) -> tuple[str | None, str | None]:
    """(room, location) for a path segment. A registered or numeric room
    number is a room ("room502" and "502" alike); otherwise a venue slug,
    which is what the guest page's own rule does (non-numeric = venue)."""
    raw = (target or "").strip()
    if kind == "location":
        return None, raw
    stripped = _ROOM_PREFIX.sub("", raw) or raw
    if kind == "room" or stripped.isdigit():
        return stripped, None
    if rooms_mod.valid_room(stripped) and rooms_mod.get_room(cur, hotel.slug, stripped) is not None:
        return stripped, None
    return None, raw


def chips_for(cur: Any, hotel_slug: str, language: str, *, surface: str, menu: bool) -> list[dict]:
    cfg = sessions_settings(cur, hotel_slug)
    custom = cfg.chips.get(language) or cfg.chips.get("en")
    if custom:
        return [{"key": str(c.get("key") or ""), "label": str(c.get("label") or "")} for c in custom if isinstance(c, dict)][:8]
    base = DEFAULT_CHIPS.get(language) or DEFAULT_CHIPS["en"]
    out = []
    for key, label in base:
        if key == "room_service" and not menu:
            continue
        if surface == "web_location" and key not in LOCATION_CHIPS:
            continue
        out.append({"key": key, "label": label})
    return out


def sessions_settings(cur: Any, hotel_slug: str) -> C.ConciergeSettings:
    from clefs.modules.concierge import kb

    return kb.settings(cur, hotel_slug)


def emergency_numbers(country: str | None) -> list[dict]:
    return [{"number": num, "kind": kind} for num, kind in C.EMERGENCY_NUMBERS.get((country or "").upper(), ())]


def _menu_available(cur: Any, hotel_slug: str, language: str) -> bool:
    from clefs.modules.concierge.prompt import _savepoint
    from clefs.modules.upsell import api as upsell_api

    items = _savepoint(cur, "cc_ctx_menu", lambda: upsell_api.get_menu(cur, hotel_slug, outlet=None, language=language), [])
    return bool(items)


def resolve_language(requested: str | None, hotel: Hotel) -> str:
    """?lang when it is one of the guest UI languages, else the hotel's
    first guest language, else English (the client may still override from
    its own storage or the browser)."""
    for cand in (requested, *(hotel.guest_languages or [])):
        base = (cand or "").split("-")[0].lower()
        if base in C.GUEST_LANGUAGES:
            return base
    return "en"


def stay_context(
    cur: Any, hotel: Hotel, target: str, *, k: str | None = None, lang: str | None = None, stay_token: str | None = None, kind: str | None = None
) -> dict:
    from clefs.modules.concierge.prompt import greeting_text, guest_personal

    room, location = split_target(cur, hotel, target, kind)
    t = sessions.resolve_target(cur, hotel, room=room, location=location, k=k, stay_token=stay_token)
    language = resolve_language(lang, hotel)
    guest = None
    first = None
    if t.verified and t.room:
        gctx = sessions.guest_context(cur, hotel.slug, t.room)
        first, _ = guest_personal(gctx)
        # The PMS/profile language preselects the guest page's profile step
        # (verified rooms only, like the first name).
        glang = (gctx or {}).get("language") if isinstance(gctx, dict) else None
        glang = glang if isinstance(glang, str) and glang.strip() else None
        if first or glang:
            guest = {"first_name": first, **({"language": glang} if glang else {})}
    menu = t.surface in C.ORDER_SURFACES and _menu_available(cur, hotel.slug, language)
    name = (hotel.assistant_name or "").strip() or "Clefs"
    assistant_live = live.is_live(cur, hotel.slug)
    floor = None
    if t.room:
        registered = rooms_mod.get_room(cur, hotel.slug, t.room)
        floor = (registered or {}).get("floor") or rooms_mod.infer_floor(t.room)
    return {
        "hotel": {
            "slug": hotel.slug,
            "name": hotel.name,
            "assistant_name": name,
            "city": hotel.city,
            "tz": hotel.timezone,
            "currency": hotel.currency,
            "locale": hotel.locale,
            "languages": list(hotel.guest_languages or ["en"]),
            # the hotel's public WhatsApp Business number (tracker opt-in link)
            "whatsapp_number": hotel.whatsapp_display_number or None,
        },
        "surface": t.surface,
        "room": {"number": t.room, "floor": floor} if t.room else None,
        "location": {"slug": t.location["slug"], "name": t.location["name"], "type": t.location["type"]} if t.location else None,
        "verified": t.verified,
        "key_status": t.key_status,
        "guest": guest,
        "language": language,
        "greeting_text": greeting_text(hotel, name=name, language=language, first_name=first),
        "chips": chips_for(cur, hotel.slug, language, surface=t.surface, menu=menu),
        "features": {
            "menu": menu,
            "tracker": module_enabled(cur, hotel.slug, "service"),
            "whatsapp": bool(hotel.whatsapp_display_number),
            # Both follow concierge.live (QA GS-1): while it is off every
            # session mint answers 409 concierge_not_live, so the page must
            # say "call the front desk" up front instead of walking the
            # guest through the consent sheet first.
            "assistant": assistant_live,
            "voice": assistant_live,
        },
        "emergency_numbers": emergency_numbers(hotel.country),
        "consent_texts": sessions.consent_texts(cur, hotel.slug, language),
        "consent_version": C.CONSENT_TEXT_VERSION,
        "stay_session_key": sessions.mint_stay_token(hotel.slug, t.room, t.qr_version) if t.verified and t.room else None,
    }
