"""
Tokyo demo part "core": the property and its people.

Hotel Aozora Shinjuku (aozora = blue sky): a fictional mid-range hotel in
Nishi-Shinjuku, about 8 minutes on foot from Shinjuku Station's West Exit
and 3 from Nishi-shinjuku Station (Marunouchi Line). 80 rooms on 8 floors
of 10 (40 Moderate Doubles, 30 Standard Twins, 10 Family Rooms for four),
JPY, Asia/Tokyo, the Japanese and international booking channels, six
Service departments, 24 staff with PINs, six console users and one paired
front-desk tablet.

The deck cast is the UAE seed's, with Japanese leaders: Room 204 on floor
2, Maria Santos (Housekeeping attendant, floors 1-4, reads Tagalog, PIN
2046), Priya Sharma (Housekeeping attendant, floors 5-8), Hana Suzuki
(Housekeeping supervisor, 3 minutes), Yuki Sato (duty manager, 5 minutes,
and the console login the landing page signs in as) and Kenji Tanaka (GM,
10 minutes). The team mixes Japanese and foreign staff (Vietnamese,
Nepali, Indonesian, Filipino, Chinese), each with the language they read
their jobs in first.

The steps are demo_uae.core's (they read the names below through
ctx.data("core")). Tax: Japan's 10% consumption tax is inside every price
(prices are shown tax-inclusive) and Tokyo's accommodation tax is a fixed
amount per person per night; core.tax has no rule for either (its rules
are the UAE fee stack, for UAE hotels only), so nothing is split out of a
night and the hotel's tax_profile records the Japanese setup as data. The
guest-facing side is the menu's included 10% (core_catalog) and the
knowledge base.

Modules outside the prototype (OFF_MODULES) are switched off for the hotel,
as the AI Builder Cup deployment switches them off for the process. The
roster's labour guardrails are Japan's (LABOUR_RULES), not Service's UAE
defaults.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from clefs.core.hotels import set_module_settings
from clefs.db import Json
from clefs.seed.demo_uae import core as uae

PART_ORDER = 10
ACTOR = uae.ACTOR

# ─── The hotel row ───

NAME = "Hotel Aozora Shinjuku"
# A plausible Nishi-Shinjuku address, fictional, like the hotel.
ADDRESS = "7-18-4 Nishi-Shinjuku, Shinjuku-ku, Tokyo 160-0023"
FRONT_DESK_PHONE = "+81 3 6205 2040"
LAT, LNG = "35.6954", "139.6951"
# Every guest UI language the hotel offers: English and Japanese first,
# then the JAPAC markets (Korean, Chinese, Thai, Hindi) and the long-haul
# ones. ja, ko and th are concierge.constants.GUEST_LANGUAGES entries.
GUEST_LANGUAGES = ("en", "ja", "ko", "zh", "th", "hi", "fr", "de", "ru", "ar", "ur")
# Japan: 10% consumption tax included in every price; Tokyo's accommodation
# tax per person per night, by the room rate per person per night (JPY).
TAX_PROFILE = {
    "country": "JP",
    "prices_include_tax": True,
    "consumption_tax_pct": "10",
    "accommodation_tax": {"authority": "Tokyo Metropolitan Government", "basis": "per_person_per_night",
                          "bands": [{"from": 0, "amount": 0}, {"from": 10000, "amount": 100},
                                    {"from": 15000, "amount": 200}]},
}


def hotel_fields(slug: str) -> dict[str, Any]:
    from clefs.seed.demo_jp import DEMO_SLUG

    return {
        "name": NAME if slug == DEMO_SLUG else f"{NAME} ({slug})",
        "city": "Tokyo",
        "country": "JP",
        "emirate": None,
        "timezone": "Asia/Tokyo",
        "currency": "JPY",
        "locale": "en-JP",
        "guest_languages": list(GUEST_LANGUAGES),
        "weekend_days": [6, 7],
        "star_rating": 3,
        "rooms_count": 80,
        "category": "hotel",
        "status": "demo",
        "lat": Decimal(LAT),
        "lng": Decimal(LNG),
        "address": ADDRESS,
        "front_desk_phone": FRONT_DESK_PHONE,
        "assistant_name": "Clefs",
        "website": "https://example.com/hotel-aozora-shinjuku-demo",
        "tax_profile": TAX_PROFILE,
        "legacy_open_access": False,
    }


# ─── The deck cast ───
# demo_access, the deck part and docs/DEMO.md name these people.
DECK_ROOM = uae.DECK_ROOM
DECK_ATTENDANT = uae.DECK_ATTENDANT
DECK_ATTENDANT_PIN = uae.DECK_ATTENDANT_PIN
DECK_SECOND_ATTENDANT = uae.DECK_SECOND_ATTENDANT
DECK_SUPERVISOR = "Hana Suzuki"
DECK_DUTY_MANAGER = "Yuki Sato"
DECK_GM = "Kenji Tanaka"
DUTY_MANAGER_LOCAL = uae.DUTY_MANAGER_LOCAL
DUTY_MANAGER_ROLE = uae.DUTY_MANAGER_ROLE
demo_email = uae.demo_email

# ─── Rooms ───

ROOM_TYPES = (
    {"code": "MOD_DBL", "name": "Moderate Double", "bedrooms": 1, "max_occupancy": 2, "room_count": 40},
    {"code": "STD_TWIN", "name": "Standard Twin", "bedrooms": 1, "max_occupancy": 2, "room_count": 30},
    {"code": "FAM_QUAD", "name": "Family Room", "bedrooms": 1, "max_occupancy": 4, "room_count": 10},
)
FAMILY_TYPE = "FAM_QUAD"


def room_type_for(floor: int, pos: int) -> str:
    """x01-x05 doubles on every floor (40); the family rooms are x10 on
    floors 1-6 plus x09 and x10 on 7-8 (10); every other room is a twin."""
    if pos <= 5:
        return "MOD_DBL"
    if pos == 10 or (floor >= 7 and pos == 9):
        return FAMILY_TYPE
    return "STD_TWIN"


def room_plan() -> list[dict]:
    """Odd rooms face Shinjuku Central Park, even ones the station side;
    room phones dial 6 + room number."""
    return uae.room_plan(type_for=room_type_for, wings=("Park", "Station"), extension_prefix="6",
                         two_bedroom=frozenset())


# ─── Channels ───

# code, display name, kind, default collect model. Rakuten Travel and Jalan
# are Japan's own OTAs (guests mostly pay at the hotel); Trip.com sells to
# the Chinese-speaking markets and collects, like Agoda.
CHANNELS = (
    ("BOOKING", "Booking.com", "ota", "HOTEL_COLLECT"),
    ("AGODA", "Agoda", "ota", "OTA_COLLECT"),
    ("EXPEDIA", "Expedia", "ota", "OTA_COLLECT"),
    ("RAKUTEN", "Rakuten Travel", "ota", "HOTEL_COLLECT"),
    ("JALAN", "Jalan", "ota", "HOTEL_COLLECT"),
    ("TRIPCOM", "Trip.com", "ota", "OTA_COLLECT"),
    ("DIRECT_WEB", "Direct (website)", "direct", "HOTEL_COLLECT"),
    ("DIRECT_PHONE", "Direct (phone and email)", "direct", "HOTEL_COLLECT"),
    ("CORPORATE", "Corporate accounts", "corporate", "HOTEL_COLLECT"),
    ("WALKIN", "Walk-in", "walk_in", "HOTEL_COLLECT"),
)

# ─── Departments ───

# key, name, Arabic name (Service's own, for Arabic readers), legacy
# category, colour, sort. The six desks a Shinjuku hotel of this size runs.
DEPARTMENTS = (
    ("housekeeping", "Housekeeping", "التدبير المنزلي", "Housekeeping", "#3b82f6", 10),
    ("front_office", "Front Desk", "مكتب الاستقبال", "Front Desk", "#8b5cf6", 20),
    ("engineering", "Maintenance", "الصيانة", "Maintenance", "#ef4444", 30),
    ("ird", "In-Room Dining", "خدمة الطعام في الغرف", "Food & Beverage", "#f59e0b", 40),
    ("concierge", "Concierge", "الكونسيرج", "Concierge", "#10b981", 50),
    ("security", "Security", "الأمن", "Security", "#111827", 60),
)

# ─── Staff ───

# name, legacy role, legacy department, department_key, role_key, floors,
# languages (the first is the one a person reads their jobs in).
STAFF = (
    (DECK_ATTENDANT, "Room Attendant", "Housekeeping", "housekeeping", "staff", ["1", "2", "3", "4"], ["fil", "en"]),
    ("Lan Nguyen", "Room Attendant", "Housekeeping", "housekeeping", "staff", ["1", "2"], ["vi", "ja"]),
    ("Sabina Thapa", "Room Attendant", "Housekeeping", "housekeeping", "staff", ["3", "4"], ["ne", "en"]),
    (DECK_SUPERVISOR, "Housekeeping Supervisor", "Housekeeping", "housekeeping", "supervisor", [], ["ja", "en"]),
    ("Dewi Lestari", "Room Attendant", "Housekeeping", "housekeeping", "staff", ["7", "8"], ["id", "ja"]),
    ("Takeshi Kobayashi", "Night Housekeeping Supervisor", "Housekeeping", "housekeeping", "supervisor", [],
     ["ja"]),
    ("Hiroshi Yamada", "In-Room Dining Supervisor", "Food & Beverage", "ird", "supervisor", [], ["ja", "en"]),
    ("Wei Chen", "Order Taker", "Food & Beverage", "ird", "staff", [], ["zh", "ja", "en"]),
    ("Bikash Rai", "Room Service Waiter", "Food & Beverage", "ird", "staff", [], ["ne", "ja"]),
    ("Ryo Nakamura", "Commis Chef", "Food & Beverage", "ird", "staff", [], ["ja"]),
    ("Masato Kato", "Chief Engineer", "Maintenance", "engineering", "supervisor", [], ["ja"]),
    ("Agus Pratama", "Maintenance Technician", "Maintenance", "engineering", "staff", [], ["id", "ja"]),
    ("Daiki Ishikawa", "Maintenance Technician", "Maintenance", "engineering", "staff", [], ["ja", "en"]),
    (DECK_DUTY_MANAGER, "Duty Manager", "Front Desk", "front_office", "duty_manager", [], ["ja", "en"]),
    (DECK_SECOND_ATTENDANT, "Room Attendant", "Housekeeping", "housekeeping", "staff", ["5", "6", "7", "8"],
     ["hi", "en"]),
    ("Kenta Mori", "Night Auditor", "Front Desk", "front_office", "staff", [], ["ja", "en"]),
    ("Rina Matsumoto", "Assistant Front Office Manager", "Front Desk", "front_office", "duty_manager", [],
     ["ja", "en", "ko"]),
    ("Mei Lin", "Front Desk Agent", "Front Desk", "front_office", "staff", [], ["zh", "ja", "en"]),
    ("Akira Hayashi", "Concierge", "Concierge", "concierge", "staff", [], ["ja", "en"]),
    ("Joshua Reyes", "Bell Attendant", "Concierge", "concierge", "staff", [], ["fil", "en"]),
    ("Tetsuya Ogawa", "Security Officer", "Security", "security", "staff", [], ["ja"]),
    ("Ram Tamang", "Security Officer", "Security", "security", "staff", [], ["ne", "ja"]),
    ("Kazuki Fujita", "Night Security Officer", "Security", "security", "staff", [], ["ja", "en"]),
    # The GM is on the escalation ladder (10 minutes) but takes no jobs.
    (DECK_GM, "General Manager", "Management", None, "gm", [], ["ja", "en"]),
)

# ─── Console users ───

# role, email local part, display name, departments (empty = all).
USERS = (
    ("owner", "owner", "Michiko Takahashi", []),
    ("gm", "gm", DECK_GM, []),
    ("finance", "finance", "Naoko Ito", []),
    ("revenue", "revenue", "Daisuke Yamamoto", []),
    ("front_desk", "frontdesk", "Aiko Watanabe", ["front_office"]),
    (DUTY_MANAGER_ROLE, DUTY_MANAGER_LOCAL, DECK_DUTY_MANAGER, []),
)
STAFF_USER_LINKS = ((DECK_DUTY_MANAGER, DUTY_MANAGER_ROLE), (DECK_GM, "gm"))

# ─── Modules ───

# The AI Builder Cup deployment runs without these (CLEFS_DISABLED_MODULES);
# the hotel switches them off too, so a full build shows the same console.
OFF_MODULES = ("profit_guard", "demand", "reviews", "brief", "ask", "voice", "visibility", "inquiries", "finance")


def seed_modules(ctx: Any) -> dict:
    """hotel_module_settings rows with enabled = false, through the audited
    core writer (an empty settings object: nothing to configure)."""
    for module in OFF_MODULES:
        set_module_settings(ctx.cur, ctx.slug, module, {}, actor=ACTOR, enabled=False)
    return {"modules_off": len(OFF_MODULES)}


# ─── Labour guardrails (Service's svc_labour_rules) ───

# Japan's Labour Standards Act: 40 hours a week and 8 a day before overtime,
# one day off a week. Service's defaults are UAE rules (48 hours, the Ramadan
# reduction and the summer midday outdoor work ban); Japan has neither, so
# no Ramadan dates and an empty ban window.
LABOUR_RULES = {"weekly_max_h": 40, "daily_max_h": 8, "max_consecutive_days": 6, "ramadan_reduction_h": 0,
                "midday_ban": {"from": "06-15", "to": "09-15", "start": "12:30", "end": "12:30"}}


def seed_labour_rules(ctx: Any) -> dict:
    """The row the roster's labour settings page writes (no api door)."""
    if not ctx.table_exists("svc_labour_rules"):
        return {}
    r = LABOUR_RULES
    ctx.cur.execute(
        """
        INSERT INTO svc_labour_rules (hotel_slug, weekly_max_h, daily_max_h, max_consecutive_days, ramadan_start,
            ramadan_end, ramadan_reduction_h, midday_ban, updated_at)
        VALUES (%s, %s, %s, %s, NULL, NULL, %s, %s, NOW())
        ON CONFLICT (hotel_slug) DO UPDATE SET weekly_max_h = EXCLUDED.weekly_max_h,
            daily_max_h = EXCLUDED.daily_max_h, max_consecutive_days = EXCLUDED.max_consecutive_days,
            ramadan_start = NULL, ramadan_end = NULL, ramadan_reduction_h = EXCLUDED.ramadan_reduction_h,
            midday_ban = EXCLUDED.midday_ban, updated_at = NOW()
        """,
        (ctx.slug, r["weekly_max_h"], r["daily_max_h"], r["max_consecutive_days"], r["ramadan_reduction_h"],
         Json(r["midday_ban"])),
    )
    return {"labour_weekly_max_h": r["weekly_max_h"]}


# ─── Part entry ───


def seed_part(ctx: Any) -> dict:
    out: dict[str, Any] = {}
    for step in (uae.seed_rooms, uae.seed_channels, uae.seed_departments, seed_labour_rules, uae.seed_staff,
                 uae.seed_users, uae.seed_device, seed_modules):
        out.update(step(ctx))
    return out
