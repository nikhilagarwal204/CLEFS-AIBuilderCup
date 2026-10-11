"""
Tokyo demo part "ops": the hotel's operations as the service desk shows
them, played by demo_uae.ops' sections with this module's data
(ctx.data("ops")).

  Service     the history (about 0.5 requests per occupied room-night, in
              the guests' own languages: Japanese, Korean, Chinese, Thai,
              Hindi), the roster and the deck fortnight, Friday evenings'
              SLA breach cluster, three emergencies (L1) through intake, a
              noise complaint with its recovery gesture in yen, room 504's
              air conditioner failing twice in a week, and the evening the
              console opens on.
  Amenities   the housekeeping issuance log (green tea, water, slippers and
              the on-request kits and loans) through core.ingest, with the
              planted spikes (toothbrush kits on floor 6's night shift,
              slippers on one cart) and the razor logging gap. Japan's
              plastic rules are not Dubai's, and Amenities' single-use
              plastic signal speaks of the UAE rules, so no item carries
              that flag here.
  Guest pages the front desk's room roster, so a verified room QR page
              greets its guest, and the stays snapshot the guest pages read.
  Upsell      in-room dining sessions through upsell.api over 30 days, with
              tonight's order on its way.

Reviews, journeys and inquiries are left out with their modules (off in the
prototype). The second report of the repeat AC fault comes from the guest
page instead of a journey reply.
"""

from __future__ import annotations

from typing import Any

from clefs.seed.demo_jp.core import (
    DECK_ATTENDANT, DECK_DUTY_MANAGER, DECK_ROOM, DECK_SECOND_ATTENDANT, DECK_SUPERVISOR,
)
from clefs.seed.demo_uae import ops as uae
from clefs.seed.demo_uae.ops import DEPT_OF, SLA  # noqa: F401  (shared: departments and SLA by request type)

PART_ORDER = 60

PMS_PREFIX, CCY = "AZ", "JPY"
DEFAULT_ROOM_TYPE = "MOD_DBL"
OPS_STAY_RATE = 16000  # a plant stay's night, JPY
# Room 204 is the reviewers' room: the anchor day's open requests, the open
# emergency and tonight's order go to other rooms.
KEEP_CLEAR = frozenset({DECK_ROOM})

# ─── Service ───

# type key, weight, ack min, resolve min (Service's SLA seeds, as in the
# UAE mix), English description.
REQUEST_MIX: tuple[tuple[str, int, int, int, str], ...] = (
    ("towels", 14, 3, 15, "Two extra bath towels please"),
    ("water", 10, 3, 10, "Can we get 2 more bottles of water"),
    ("amenities", 11, 3, 15, "Need a toothbrush kit and a razor"),
    ("make_up_room", 7, 5, 45, "Please clean the room now"),
    ("pillows", 5, 5, 20, "Could we have a lower pillow please"),
    ("linen", 3, 3, 15, "Please change the bed sheets"),
    ("extra_bed", 2, 5, 30, "We need an extra bed for our daughter"),
    ("ird", 8, 2, 30, "Tonkotsu ramen and an orange juice please"),
    ("breakfast", 3, 2, 25, "Japanese breakfast set in the room at 7:30 for two"),
    ("drinks", 3, 2, 15, "Two cold soft drinks please"),
    ("ac", 6, 5, 60, "The air conditioner is not cooling"),
    ("plumbing", 3, 5, 30, "The toilet is not flushing properly"),
    ("electrical", 2, 5, 45, "The bedside lamp does not work"),
    ("maintenance", 2, 5, 45, "The room safe will not open"),
    ("luggage", 5, 2, 10, "Please send our two suitcases to Narita Airport by takkyubin"),
    ("concierge", 4, 5, 45, "Can you book a taxi to Haneda Airport at 6 am"),
    ("wake_up", 3, 5, 45, "Wake-up call at 5:30 tomorrow please"),
    ("billing", 2, 5, 45, "Please email me the receipt"),
    ("checkout", 3, 5, 45, "Can we check out late, at 13:00?"),
    ("front_desk", 3, 5, 45, "Where is the nearest convenience store?"),
    ("complaint", 2, 5, 45, "The room next door is very noisy"),
    ("other", 1, 5, 45, "How do I switch the TV to English?"),
)
DESCRIPTIONS = {k: d for k, _w, _a, _r, d in REQUEST_MIX}

# What guests actually typed, by language (the console shows the original
# next to the English the replayed translation gives).
ORIGINALS: dict[str, dict[str, str]] = {
    "ja": {"towels": "バスタオルを2枚追加でお願いします", "water": "お水をあと2本いただけますか",
           "ac": "エアコンが効きません", "make_up_room": "今すぐ部屋の清掃をお願いします",
           "ird": "とんこつラーメンとオレンジジュースをお願いします"},
    "ko": {"towels": "수건 두 장 더 주세요", "water": "물 두 병 더 주실 수 있나요", "ac": "에어컨이 시원하지 않아요",
           "make_up_room": "지금 방 청소 부탁드려요"},
    "zh": {"towels": "请再给两条浴巾", "water": "可以再给两瓶水吗", "ac": "空调不制冷", "make_up_room": "请现在打扫房间"},
    "th": {"towels": "ขอผ้าเช็ดตัวเพิ่มอีกสองผืนค่ะ", "water": "ขอน้ำดื่มเพิ่มอีกสองขวดได้ไหมคะ",
           "ac": "แอร์ไม่เย็นเลยค่ะ", "make_up_room": "ช่วยทำความสะอาดห้องตอนนี้ได้ไหมคะ"},
    "hi": {"towels": "कृपया दो अतिरिक्त तौलिये दें", "water": "पानी की दो और बोतलें मिल सकती हैं",
           "ac": "एसी ठंडा नहीं कर रहा है"},
}

# source, weight. The room QR page leads in Tokyo, then calls to the desk;
# messaging is the foreign guests' habit.
SOURCES_MIX = (("web", 34), ("phone_desk", 20), ("voice", 14), ("whatsapp", 14), ("console", 8), ("device", 6),
               ("sip", 4))

# days before the anchor, hour, minute, source, description, close note (None: still open at the anchor)
L1_CASES = (
    (41, 14, 20, "voice", "My husband collapsed in the bathroom and is not responding, please send help",
     "Ambulance (119) called at 14:23; the guest was taken to hospital and the family informed."),
    (9, 23, 10, "phone_desk", "Smoke smell in the corridor on floor 4 near the linen room",
     "Maintenance found an overheated extractor fan motor and isolated it; the fire brigade was not needed."),
    (0, 19, 35, "web", "My child is locked in the bathroom and cannot open the door, please help fast", None),
)

# kind, JPY, description, the role that gave it (within its authority)
GESTURES = (("fnb_credit", 3000, "Dinner credit at Sora Dining", "supervisor"),
            ("amenity", 1500, "Fruit plate and a note from the duty manager", "staff"),
            ("late_checkout", 0, "Late check-out until 13:00", "supervisor"),
            ("fnb_credit", 4400, "Breakfast buffet for two on the house", "duty_manager"))

REPEAT_GUEST = ("Daniel Brooks", "AU", "en")
J5_SOURCE = "web"
J5_TEXT = "The air conditioner in room {room} is not cooling again, it is very hot"
REPEAT_FAULT_NOTE = "AC reported on A-4 and again from the guest page on the anchor."

# staff (plain name), shift, shift role, floors: the history roster (see
# demo_uae.ops.ROSTER). Housekeeping's evening shift is one attendant short
# on Fridays (Priya); the front desk has a duty manager round the clock,
# Concierge and Security cover their own shifts.
ROSTER = (
    ("Lan Nguyen", "morning", "attendant", ("1", "2")), ("Sabina Thapa", "morning", "attendant", ("3", "4")),
    ("Dewi Lestari", "morning", "attendant", ("5", "6", "7", "8")),
    (DECK_ATTENDANT, "evening", "attendant", ("1", "2", "3", "4")),
    (DECK_SECOND_ATTENDANT, "evening", "attendant", ("5", "6", "7", "8")),
    (DECK_SUPERVISOR, "evening", "supervisor", ()), ("Takeshi Kobayashi", "night", "supervisor", ()),
    ("Hiroshi Yamada", "morning", "supervisor", ()), ("Wei Chen", "evening", "supervisor", ()),
    ("Bikash Rai", "evening", "supervisor", ()), ("Ryo Nakamura", "morning", "supervisor", ()),
    ("Masato Kato", "morning", "supervisor", ()), ("Agus Pratama", "evening", "supervisor", ()),
    ("Daiki Ishikawa", "night", "supervisor", ()), (DECK_DUTY_MANAGER, "evening", "duty_manager", ()),
    ("Rina Matsumoto", "morning", "duty_manager", ()), ("Kenta Mori", "night", "duty_manager", ()),
    ("Mei Lin", "morning", "attendant", ()), ("Akira Hayashi", "morning", "attendant", ()),
    ("Joshua Reyes", "evening", "attendant", ()), ("Ram Tamang", "morning", "attendant", ()),
    ("Tetsuya Ogawa", "evening", "attendant", ()), ("Kazuki Fujita", "night", "attendant", ()),
)
FRIDAY_SHORT = DECK_SECOND_ATTENDANT
# The demo fortnight (demo_uae.ops.DEMO_DAYS): the deck cast round the clock,
# the other housekeepers on leave, the front office leads working as agents.
DECK_ON_CALL = {
    DECK_ATTENDANT: ("attendant", ("1", "2", "3", "4")),
    DECK_SECOND_ATTENDANT: ("attendant", ("5", "6", "7", "8")),
    DECK_SUPERVISOR: ("supervisor", ()),
    DECK_DUTY_MANAGER: ("duty_manager", ()),
}
DEMO_LEAVE = frozenset({"Lan Nguyen", "Sabina Thapa", "Dewi Lestari", "Takeshi Kobayashi"})
DEMO_AGENTS = frozenset({"Rina Matsumoto", "Kenta Mori"})

# ─── Amenities ───

# key, name EN, name AR, category, basis, std qty, unit cost (JPY), single-use
# plastic flag, aliases (English and Japanese). The room's consumables, the
# kits a guest asks for and what the front desk lends (unit cost 0).
AMN_ITEMS = (
    ("GREEN_TEA", "Green tea bags (2)", None, "TEA_COFFEE", "PER_OCCUPIED_ROOM_NIGHT", "1", 30, False,
     ["green tea", "tea", "ocha", "お茶", "緑茶"]),
    ("WATER_500", "Mineral water 500 ml", None, "WATER", "PER_OCCUPIED_ROOM_NIGHT", "2", 40, False,
     ["water", "bottled water", "mineral water", "水", "ミネラルウォーター"]),
    ("SLIPPERS", "Disposable slippers (pair)", None, "SLIPPERS", "PER_ARRIVAL", "2", 80, False,
     ["slippers", "slipper", "スリッパ"]),
    ("DENTAL_KIT", "Toothbrush kit", None, "DENTAL", "ON_REQUEST", "0", 35, False,
     ["toothbrush", "tooth brush", "toothpaste", "toothbrush kit", "dental kit", "歯ブラシ", "ハブラシ"]),
    ("SHAVING_KIT", "Razor kit", None, "SHAVING", "ON_REQUEST", "0", 60, False,
     ["razor", "shaver", "shaving kit", "shaving", "かみそり", "カミソリ"]),
    ("HAIR_BRUSH", "Hair brush", None, "OTHER", "ON_REQUEST", "0", 40, False,
     ["hair brush", "hairbrush", "comb", "ヘアブラシ", "くし"]),
    ("SHOWER_CAP", "Shower cap", None, "BATH", "ON_REQUEST", "0", 15, False, ["shower cap", "シャワーキャップ"]),
    ("BATH_TOWEL", "Bath towel (laundry)", None, "BATH", "ON_REQUEST", "0", 50, False,
     ["towel", "towels", "bath towel", "face towel", "タオル", "バスタオル"]),
    ("YUKATA", "Yukata robe (laundry)", None, "OTHER", "ON_REQUEST", "0", 120, False,
     ["yukata", "robe", "pajamas", "nightwear", "浴衣", "ゆかた", "パジャマ"]),
    ("HUMIDIFIER", "Humidifier (loan)", None, "OTHER", "ON_REQUEST", "0", 0, False, ["humidifier", "加湿器"]),
    ("PILLOW_LOW", "Low pillow (loan)", None, "OTHER", "ON_REQUEST", "0", 0, False,
     ["low pillow", "flat pillow", "低い枕"]),
    ("PILLOW_BUCKWHEAT", "Buckwheat pillow (loan)", None, "OTHER", "ON_REQUEST", "0", 0, False,
     ["buckwheat pillow", "sobagara", "firm pillow", "そば殻枕"]),
    ("PHONE_CHARGER", "Phone charger (loan)", None, "OTHER", "ON_REQUEST", "0", 0, False,
     ["phone charger", "charger", "usb-c cable", "lightning cable", "充電器"]),
    ("PLUG_ADAPTER", "Type A plug adapter (loan)", None, "OTHER", "ON_REQUEST", "0", 0, False,
     ["plug adapter", "adapter", "adaptor", "plug converter", "変換プラグ"]),
    ("UMBRELLA", "Umbrella (loan)", None, "OTHER", "ON_REQUEST", "0", 0, False, ["umbrella", "傘"]),
)
# What an "amenities" request asked for: description, [(item key, qty)].
AMENITY_ASKS = (
    ("Need a toothbrush kit please", [("DENTAL_KIT", 1)]),
    ("Two toothbrush kits for the room please", [("DENTAL_KIT", 2)]),
    ("Can I get a razor", [("SHAVING_KIT", 1)]),
    ("Need a toothbrush kit and a razor", [("DENTAL_KIT", 1), ("SHAVING_KIT", 1)]),
    ("Could we have a humidifier, the air is very dry", [("HUMIDIFIER", 1)]),
    ("Two more yukata please, a larger size", [("YUKATA", 2)]),
    ("Can I borrow a phone charger, USB-C", [("PHONE_CHARGER", 1)]),
    ("Do you have a plug adapter", [("PLUG_ADAPTER", 1)]),
)
ASK_ITEMS = {d: lines for d, lines in AMENITY_ASKS}
# The morning attendants who restock the floor closets, the night supervisor
# behind the toothbrush-kit plant and the attendant whose cart carries the
# slipper plant; green tea is restocked per occupied room-night.
FLOOR_ATTENDANT = {"1": "Lan Nguyen", "2": "Lan Nguyen", "3": "Sabina Thapa", "4": "Sabina Thapa",
                   "5": "Dewi Lestari", "6": "Dewi Lestari", "7": "Dewi Lestari", "8": "Dewi Lestari"}
NIGHT_HOUSEKEEPER, SLIPPER_CART_ATTENDANT = "Takeshi Kobayashi", "Sabina Thapa"
ROOM_NIGHT_RESTOCK = (("GREEN_TEA", 1),)
SUP_PLANT = False

# ─── Upsell ───

# main item, pairing target, meal (core_catalog's menu)
BASKETS = (("IRD-MN-01", "IRD-SD-02", "dinner"), ("IRD-MN-04", "IRD-BV-04", "lunch"),
           ("IRD-MN-03", "IRD-SD-03", "dinner"), ("IRD-MN-06", "IRD-SD-01", "dinner"),
           ("IRD-BRK-02", "IRD-BV-01", "breakfast"), ("IRD-MN-02", "IRD-DS-01", "dinner"),
           ("IRD-DS-01", "IRD-BV-02", "dinner"))


# ─── Part ───


def seed_guest_pages(ctx: Any) -> dict:
    """The front desk's room roster (what personalizes a verified room QR
    page) and the stays snapshot the hourly guests job keeps."""
    if ctx.module_api("guests") is None or not ctx.table_exists("guest_rooms") or not ctx.ids.get("reservations"):
        return {"roster_rooms": 0}
    out = {"roster_rooms": uae.guest_roster(ctx)}
    linked = uae.run_job(ctx, "guests.link_stays") or {}
    out["stays_linked"] = int(linked.get("pms_linked") or 0)
    return out


def seed_part(ctx: Any) -> dict:
    """Service first (its time-ordered episodes also play the in-room dining
    orders), then amenities, the guest pages and the upsell funnel, the
    evening's detectors and the outbox, as demo_uae.ops.seed_part runs them."""
    out: dict[str, Any] = {}
    with uae.replay_ai(uae.replay_for(ctx)):
        for fn in (uae.seed_service, uae.seed_amenities, seed_guest_pages, uae.seed_upsell):
            out.update(fn(ctx) or {})
        uae.final_detectors(ctx)
        out["outbox_settled"] = uae.settle_outbox(ctx)
    return out


def ops_checks(cur: Any, slug: str) -> list[tuple[str, bool, str]]:
    """demo_uae.ops.ops_checks against this hotel's manifest."""
    from clefs.seed.demo_jp import MANIFEST_PATH

    return uae.ops_checks(cur, slug, MANIFEST_PATH)


CHECKS: list = [ops_checks]
