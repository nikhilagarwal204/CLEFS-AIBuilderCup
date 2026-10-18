"""
Demo part "core_catalog": what the hotel sells and who it competes with.

- Concierge knowledge base in English and Arabic, approved (the structured
  facts are what Visibility diffs AI answers against, so its planted "wrong
  check-in time" and "no parking" errors need these exact values).
- Outlets and an AED menu with pairings (Upsell).
- The amenity catalog (Amenities' UAE defaults).
- Demand: corridor tags and a comp set of five demo hotels.
- Profit Guard: OTA contracts, programs and channel configuration, with the
  dates the planted cases hinge on (Preferred ended 31 Jul, MF/TD configured
  as charges on Booking from 1 Aug; 02 spec 9).

Module doors: concierge.propose_kb_entry (api.py) writes each entry. No
module api exposes approval, menus, comp sets or contracts, so those rows
are written directly into the owning module's tables, each guarded by the
table existing (a module switched off in this process is skipped).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clefs.db import Json
from clefs.seed.demo_uae.core import ACTOR, demo_name

PART_ORDER = 20

# ─── Concierge knowledge base ───

# fact_key, answer_en (the value the normaliser reads), question_en, question_ar, answer_ar
KB_FACTS = (
    ("checkin_time", "14:00", "What time is check-in?", "متى يبدأ تسجيل الوصول؟",
     "يبدأ تسجيل الوصول الساعة ٢:٠٠ ظهرًا."),
    ("checkout_time", "12:00", "What time is check-out?", "متى موعد تسجيل المغادرة؟",
     "تسجيل المغادرة حتى الساعة ١٢:٠٠ ظهرًا."),
    ("parking", "free", "Is there parking?", "هل يوجد موقف سيارات؟",
     "نعم، يوجد موقف سيارات مجاني في الطابق السفلي للنزلاء."),
    ("pool", "yes", "Do you have a pool?", "هل يوجد مسبح؟", "نعم، مسبح على السطح مفتوح من ٧ صباحًا حتى ١٠ مساءً."),
    ("breakfast", "yes", "Is breakfast available?", "هل يتوفر الإفطار؟",
     "نعم، بوفيه الإفطار من ٦:٣٠ حتى ١٠:٣٠ صباحًا في مطعم الريقة."),
    ("wifi", "free", "Is there Wi-Fi?", "هل يتوفر واي فاي؟", "نعم، الواي فاي مجاني في جميع أنحاء الفندق."),
    ("pets", "no", "Are pets allowed?", "هل يسمح بالحيوانات الأليفة؟", "عذرًا، لا يسمح بالحيوانات الأليفة."),
    ("star_rating", "3", "How many stars is the hotel?", "كم عدد نجوم الفندق؟", "فندق ثلاث نجوم."),
)

FACT_SENTENCES = {
    "checkin_time": "Check-in is from 14:00 (2 pm). Early check-in depends on availability.",
    "checkout_time": "Check-out is by 12:00 noon.",
    "parking": "Yes, free basement parking for hotel guests.",
    "pool": "Yes, a rooftop pool, open 07:00 to 22:00.",
    "breakfast": "Yes, a breakfast buffet from 06:30 to 10:30 in Rigga Kitchen (AED 45, or included in B&B rates).",
    "wifi": "Yes, free Wi-Fi everywhere in the hotel.",
    "pets": "Sorry, pets are not allowed.",
    "star_rating": "We are a 3-star hotel.",
}

# question_en, answer_en, question_ar, answer_ar
KB_FAQS = (
    ("How do I get to the Gold Souk?",
     "The Gold Souk is about 1.5 km away: 5 minutes by taxi, or two stops on the Green Line from Al Rigga metro.",
     "كيف أصل إلى سوق الذهب؟",
     "سوق الذهب على بعد ١.٥ كم تقريبًا: ٥ دقائق بالتاكسي أو محطتان على الخط الأخضر من محطة الرقة."),
    ("Which metro station is closest?",
     "Al Rigga metro station (Red Line) is a 4-minute walk from the lobby.",
     "ما هي أقرب محطة مترو؟", "محطة مترو الرقة (الخط الأحمر) على بعد ٤ دقائق سيرًا من الردهة."),
    ("How far is the airport?",
     "Dubai International (DXB) Terminals 1 and 3 are about 6 km away, 15 minutes by taxi outside rush hour.",
     "كم تبعد المطار؟", "مطار دبي الدولي على بعد ٦ كم تقريبًا، ١٥ دقيقة بالتاكسي خارج أوقات الذروة."),
    ("Is late check-out possible?",
     "Late check-out until 16:00 is AED 100, subject to availability; ask the front desk on the morning you leave.",
     "هل يمكن تأخير المغادرة؟", "يمكن تأخير المغادرة حتى الساعة ٤ مساءً مقابل ١٠٠ درهم حسب التوفر."),
    ("Do you have a family room?",
     "Our Family Two-Bedroom units sleep up to five, with two bedrooms and a sofa bed.",
     "هل لديكم غرف عائلية؟", "لدينا وحدات عائلية بغرفتي نوم تتسع حتى خمسة أشخاص."),
    ("What is the Tourism Dirham fee?",
     "Dubai's Tourism Dirham is AED 10 per room per night for our category (per bedroom in family units), "
     "for the first 30 nights, payable at the hotel.",
     "ما هي رسوم درهم السياحة؟", "درهم السياحة ١٠ دراهم لكل غرفة في الليلة لأول ٣٠ ليلة، ويدفع في الفندق."),
)


def seed_kb(ctx: Any) -> dict:
    """Proposed through concierge.api (the entry, its section and revision),
    then approved by the demo owner. Approval has no api door, so it sets
    the same columns kb.approve_entry does."""
    api = ctx.module_api("concierge")
    if api is None:
        return {"kb_entries": 0}
    owner = ctx.ids.get("users", {}).get("owner")
    ids: list[int] = []
    for key, value, q_en, q_ar, a_ar in KB_FACTS:
        eid = api.propose_kb_entry(ctx.cur, ctx.slug, question_en=q_en, answer_en=value, question_ar=q_ar,
                                   answer_ar=a_ar, fact_key=key, source="demo", actor=ACTOR)
        # The value went in answer_en for the normaliser (fact_value keeps
        # it); guests hear the sentence.
        ctx.cur.execute("UPDATE cc_kb_entries SET answer_en = %s WHERE id = %s", (FACT_SENTENCES[key], eid))
        ids.append(eid)
    for q_en, a_en, q_ar, a_ar in KB_FAQS:
        ids.append(api.propose_kb_entry(ctx.cur, ctx.slug, question_en=q_en, answer_en=a_en, question_ar=q_ar,
                                        answer_ar=a_ar, source="demo", actor=ACTOR))
    ctx.cur.execute(
        """
        UPDATE cc_kb_entries SET status = 'approved', approved_by = %s, approved_at = %s, updated_at = NOW()
        WHERE hotel_slug = %s AND id = ANY(%s)
        """,
        (owner, ctx.now, ctx.slug, ids),
    )
    ctx.ids["kb_entries"] = ids
    return {"kb_entries": len(ids)}


# ─── Outlets and menu (Upsell) ───

ALL_DAYS = 127
# key, name_en, name_ar, kind, hours, delivers_to_room
OUTLETS = (
    ("ird", "In-Room Dining", "خدمة الطعام في الغرف (تجريبي)", "in_room_dining",
     [{"from": "00:00", "to": "23:59", "days_mask": ALL_DAYS}], True),
    ("rigga-kitchen", "Rigga Kitchen", "مطبخ الرقة (تجريبي)", "restaurant",
     [{"from": "06:30", "to": "23:00", "days_mask": ALL_DAYS}], True),
    ("lobby-cafe", "Creek Lobby Cafe", "مقهى الخور (تجريبي)", "cafe",
     [{"from": "07:00", "to": "22:00", "days_mask": ALL_DAYS}], False),
)

# outlet, sku, category, category_ar, name_en, name_ar, desc_en, price AED, dietary, prep_min, from, to
MENU = (
    ("ird", "IRD-BRK-01", "Breakfast", "الإفطار", "Arabic breakfast tray", "صينية فطور عربي",
     "Foul, hummus, labneh, zaatar manakish, olives, tea", 55, ["vegetarian", "halal"], 20, "05:00", "11:30"),
    ("ird", "IRD-BRK-02", "Breakfast", "الإفطار", "Continental breakfast", "فطور كونتيننتال",
     "Croissant, toast, butter, jam, fruit, juice, coffee", 49, ["vegetarian"], 15, "05:00", "11:30"),
    ("ird", "IRD-BRK-03", "Breakfast", "الإفطار", "Masala omelette", "أومليت ماسالا",
     "Three eggs, onion, chilli, coriander, paratha", 35, ["vegetarian", "halal", "spicy"], 15, "05:00", "11:30"),
    ("ird", "IRD-MN-01", "Mains", "الأطباق الرئيسية", "Chicken biryani", "برياني دجاج",
     "Basmati, spiced chicken, raita, salad", 58, ["halal", "spicy"], 30, None, None),
    ("ird", "IRD-MN-02", "Mains", "الأطباق الرئيسية", "Mixed grill", "مشاوي مشكلة",
     "Shish tawook, kofta, lamb tikka, garlic sauce, bread", 79, ["halal"], 30, None, None),
    ("ird", "IRD-MN-03", "Mains", "الأطباق الرئيسية", "Beef burger", "برغر لحم",
     "Beef patty, cheddar, pickles, brioche bun", 52, ["halal"], 25, None, None),
    ("ird", "IRD-MN-04", "Mains", "الأطباق الرئيسية", "Club sandwich", "كلوب ساندويتش",
     "Chicken, turkey ham, egg, cheese, lettuce, tomato", 45, ["halal"], 20, None, None),
    ("ird", "IRD-MN-05", "Mains", "الأطباق الرئيسية", "Dal makhani with rice", "دال مخني مع أرز",
     "Black lentils, butter, cream, jeera rice", 42, ["vegetarian", "gluten_free"], 20, None, None),
    ("ird", "IRD-MN-06", "Mains", "الأطباق الرئيسية", "Vegetable noodles", "نودلز بالخضار",
     "Wok-fried noodles, seasonal vegetables, soy", 38, ["vegan"], 20, None, None),
    ("ird", "IRD-SD-01", "Sides", "أطباق جانبية", "French fries", "بطاطس مقلية",
     "Skin-on fries, sea salt", 18, ["vegan", "gluten_free"], 10, None, None),
    ("ird", "IRD-SD-02", "Sides", "أطباق جانبية", "Hummus and bread", "حمص مع خبز",
     "Chickpea hummus, olive oil, Arabic bread", 22, ["vegan"], 5, None, None),
    ("ird", "IRD-DS-01", "Desserts", "الحلويات", "Umm Ali", "أم علي",
     "Warm bread pudding, nuts, cream", 28, ["vegetarian", "contains_nuts"], 15, None, None),
    ("ird", "IRD-DS-02", "Desserts", "الحلويات", "Fresh fruit platter", "طبق فواكه طازجة",
     "Seasonal cut fruit", 30, ["vegan", "gluten_free"], 10, None, None),
    ("ird", "IRD-BV-01", "Drinks", "المشروبات", "Fresh orange juice", "عصير برتقال طازج",
     "Freshly squeezed", 20, ["vegan", "gluten_free"], 5, None, None),
    ("ird", "IRD-BV-02", "Drinks", "المشروبات", "Mango lassi", "لاسي المانجو",
     "Yoghurt, mango, cardamom", 18, ["vegetarian", "gluten_free"], 5, None, None),
    ("ird", "IRD-BV-03", "Drinks", "المشروبات", "Karak chai", "شاي كرك",
     "Spiced milk tea", 10, ["vegetarian"], 5, None, None),
    ("ird", "IRD-BV-04", "Drinks", "المشروبات", "Soft drink", "مشروب غازي",
     "Cola, lemon-lime or diet, 330 ml (price includes UAE excise)", 12, ["vegan", "gluten_free"], 2, None, None),
    ("rigga-kitchen", "RK-BUF-01", "Buffet", "البوفيه", "Breakfast buffet", "بوفيه الإفطار",
     "Hot and cold international buffet", 45, ["halal"], 0, "06:30", "10:30"),
    ("rigga-kitchen", "RK-BUF-02", "Buffet", "البوفيه", "Dinner buffet", "بوفيه العشاء",
     "Arabic and Indian dinner buffet", 69, ["halal"], 0, "19:00", "23:00"),
    ("lobby-cafe", "LC-01", "Coffee", "القهوة", "Cappuccino", "كابتشينو",
     "Double shot, steamed milk", 16, ["vegetarian"], 5, None, None),
    ("lobby-cafe", "LC-02", "Coffee", "القهوة", "Arabic coffee and dates", "قهوة عربية وتمر",
     "Cardamom gahwa, Khalas dates", 18, ["vegan", "gluten_free"], 5, None, None),
)

# item sku, suggested sku, kind, reason_en, reason_ar
PAIRINGS = (
    ("IRD-MN-03", "IRD-SD-01", "pairing", "Fries with your burger?", "بطاطس مع البرغر؟"),
    ("IRD-MN-04", "IRD-SD-01", "pairing", "Add fries to the club?", "إضافة بطاطس؟"),
    ("IRD-MN-01", "IRD-BV-02", "pairing", "A mango lassi goes well with biryani", "لاسي المانجو يناسب البرياني"),
    ("IRD-MN-02", "IRD-SD-02", "pairing", "Hummus with the grill?", "حمص مع المشاوي؟"),
    ("IRD-BRK-02", "IRD-BV-01", "pairing", "Fresh orange juice?", "عصير برتقال طازج؟"),
    ("IRD-BRK-02", "IRD-BRK-01", "upgrade", "Try the Arabic breakfast tray", "جرب صينية الفطور العربي"),
    ("IRD-MN-05", "IRD-BV-03", "pairing", "Karak chai to finish?", "شاي كرك؟"),
    ("IRD-DS-01", "IRD-BV-03", "pairing", "Karak with your Umm Ali?", "شاي كرك مع أم علي؟"),
)


def seed_menu(ctx: Any) -> dict:
    """Upsell's tables have no api writer (its console and MENU import are
    HTTP-only), so the rows go in directly with the columns the importer
    writes. Prices are AED in fils (Canon 2)."""
    if not ctx.table_exists("ups_outlets"):
        return {"outlets": 0, "menu_items": 0}
    cur, outlet_ids, item_ids = ctx.cur, {}, {}
    for sort, (key, name, name_ar, kind, hours, delivers) in enumerate(OUTLETS):
        cur.execute(
            """
            INSERT INTO ups_outlets (hotel_slug, key, name_en, name_ar, kind, hours, delivers_to_room, active, sort)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s) RETURNING id
            """,
            (ctx.slug, key, name, name_ar, kind, Json(hours), delivers, sort * 10),
        )
        outlet_ids[key] = cur.fetchone()["id"]
    for sort, (outlet, sku, cat, cat_ar, name, name_ar, desc, aed, diet, prep, frm, to) in enumerate(MENU):
        cur.execute(
            """
            INSERT INTO ups_menu_items (hotel_slug, outlet_id, sku, category, category_ar, name_en, name_ar, desc_en,
                price_minor, currency, available_from, available_to, dietary, prep_min, active, sort)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'AED', %s, %s, %s, %s, TRUE, %s) RETURNING id
            """,
            (ctx.slug, outlet_ids[outlet], sku, cat, cat_ar, name, name_ar, desc, aed * 100, frm, to, diet, prep,
             sort * 10),
        )
        item_ids[sku] = cur.fetchone()["id"]
    for sort, (item, suggest, kind, reason, reason_ar) in enumerate(PAIRINGS):
        cur.execute(
            """
            INSERT INTO ups_upsell_pairs (hotel_slug, item_id, suggest_item_id, kind, reason_en, reason_ar, active,
                sort)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
            """,
            (ctx.slug, item_ids[item], item_ids[suggest], kind, reason, reason_ar, sort),
        )
    ctx.ids["outlets"], ctx.ids["menu_items"] = outlet_ids, item_ids
    return {"outlets": len(outlet_ids), "menu_items": len(item_ids), "pairings": len(PAIRINGS)}


# ─── Amenity catalog ───


def seed_amenities(ctx: Any) -> dict:
    """Amenities seeds its UAE default catalog itself (catalog.seed_defaults,
    run on a hotel's first catalog visit and recorded in amn_seed_runs so a
    deleted item never returns). Its api.py has no door to that function:
    FOUNDATION-REQUEST: expose amenities.api.seed_defaults(cur, hotel_slug,
    currency). Until then the seed calls it when present and otherwise
    leaves the catalog to the first-visit seeding (the wipe cleared
    amn_seed_runs, so the first visit re-seeds)."""
    api = ctx.module_api("amenities")
    if api is None:
        return {"amenity_items": 0}
    fn = getattr(api, "seed_defaults", None)
    if callable(fn):
        fn(ctx.cur, ctx.slug, "AED")
    items = api.list_items(ctx.cur, ctx.slug, active_only=False)
    ctx.ids["amenity_items"] = {str(i.get("key") or i.get("id")): i.get("id") for i in items}
    return {"amenity_items": len(items)}


# ─── Demand: corridor tags and comp set ───

# Deira sits on the Deira-Bur Dubai corridor and the Metro Red Line: events
# at venues tagged the same way score as reachable (02 spec 9).
CORRIDOR_TAGS = ["DEIRA_BURDUBAI", "RED_LINE"]

# comp_id, name, lat, lon, stars, rooms, aliases. Synthetic positions at the
# spec's distances: Creekview ~2 km, Gold Souk ~1.5 km, Port Saeed ~3 km,
# Trade Axis near DWTC, Barsha Metro ~18 km.
COMP_SET = (
    ("demo-creekview-inn", "Creekview Inn", "25.2555", "55.3065", "3", 96, ["creekview"]),
    ("demo-gold-souk-boutique", "Gold Souk Boutique", "25.2735", "55.3130", "3", 54, ["gold souk boutique"]),
    ("demo-port-saeed-suites", "Port Saeed Suites", "25.2480", "55.3440", "3", 120, ["port saeed suites"]),
    ("demo-trade-axis", "Trade Axis Hotel", "25.2250", "55.2850", "4", 210, ["trade axis"]),
    ("demo-barsha-metro", "Barsha Metro Hotel", "25.1300", "55.2200", "3", 140, ["barsha metro"]),
)


def seed_demand(ctx: Any) -> dict:
    out = {"comp_set": 0}
    if ctx.table_exists("dr_engine_settings"):
        ctx.cur.execute(
            """
            INSERT INTO dr_engine_settings (hotel_slug, corridor_tags) VALUES (%s, %s)
            ON CONFLICT (hotel_slug) DO UPDATE SET corridor_tags = EXCLUDED.corridor_tags, updated_at = now()
            """,
            (ctx.slug, CORRIDOR_TAGS),
        )
        out["corridor_tags"] = len(CORRIDOR_TAGS)
    if ctx.table_exists("dr_comp_set"):
        for comp_id, name, lat, lon, stars, n_rooms, aliases in COMP_SET:
            ctx.cur.execute(
                """
                INSERT INTO dr_comp_set (hotel_slug, comp_id, name, lat, lon, stars, rooms, aliases, is_demo, active,
                    origin)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, TRUE, 'demo')
                """,
                (ctx.slug, comp_id, demo_name(name), lat, lon, stars, n_rooms, aliases),
            )
        ctx.ids["comp_set"] = [c[0] for c in COMP_SET]
        out["comp_set"] = len(COMP_SET)
    return out


# ─── Profit Guard: contracts, programs, channel configuration ───

_HOURS48 = [{"kind": "HOURS_AFTER_CHECKOUT", "hours": 48, "applies_to": "no_show_or_change"}]
# ota, entity, country, pct, base rule, excluded charge types, collect models, dispute rules, vcc rules.
# Entities outside the UAE are what L15 (VAT reverse charge) keys on.
CONTRACTS = (
    ("BOOKING", "Booking.com B.V. (DEMO contract)", "NL", "15", "ROOM_INCL_VAT", ["MUNICIPALITYFEE", "TOURISMFEE"],
     ["HOTEL_COLLECT", "OTA_COLLECT"], _HOURS48, {"charge_within_days_after_checkout": 365}),
    ("AGODA", "Agoda Company Pte. Ltd. (DEMO contract)", "SG", "18", "RSR_TAX_INCL", ["TOURISMFEE"],
     ["OTA_COLLECT", "HOTEL_COLLECT"], [{"kind": "BUSINESS_DAYS_AFTER_INVOICE", "days": 14}],
     {"upc_charge_within_h": 24}),
    ("EXPEDIA", "Expedia Lodging Partner Services Sarl (DEMO contract)", "CH", "18", "ROOM_EXCL_TAX", [],
     ["HOTEL_COLLECT", "OTA_COLLECT"], [{"kind": "DAY_OF_NEXT_MONTH", "day": 4, "applies_to": "hotel_collect"}],
     {"expires_days_after_checkout": 180}),
)
CONTRACT_FROM = date(2025, 1, 1)

# ota, program, kind, pct, stay_from, stay_to, label. Booking Preferred
# (+3%) ran 1 Jun to 31 Jul 2026: the L2 plant is August lines still at 18%,
# its decoy July lines at 18% while Preferred was on (02 spec 9).
PROGRAMS = (
    ("BOOKING", "PREFERRED", "COMMISSION_UPLIFT", "3", date(2026, 6, 1), date(2026, 7, 31), "Preferred Partner"),
    ("BOOKING", "GENIUS", "DISCOUNT", "10", None, None, "Genius level 1"),
    ("BOOKING", "GENIUS", "DISCOUNT", "15", None, None, "Genius levels 2-3"),
    ("BOOKING", "MOBILE", "DISCOUNT", "10", None, None, "Mobile rate"),
    ("BOOKING", "EARLY_BOOKER", "DISCOUNT", "15", None, None, "Early Booker deal"),
    ("AGODA", "AGODA_VIP", "DISCOUNT", "10", None, None, "Agoda VIP"),
)

# ota, configured_from, MF as charge, TD as charge, card fee %, payments fee %, booking engine fee %, note.
# Booking had MF/TD folded into the rate until the 1 Aug extranet fix, so June
# and July are unconfigured (the L1b estimate) and August is L1 (02 spec 9).
CHANNEL_CONFIG = (
    ("BOOKING", date(2025, 1, 1), False, False, None, None, None, "MF and TD inside the rate"),
    ("BOOKING", date(2026, 8, 1), True, True, None, None, None, "MF and TD set up as charges on 1 Aug"),
    ("AGODA", date(2025, 1, 1), True, True, None, None, None, None),
    ("EXPEDIA", date(2025, 1, 1), True, True, None, None, None, None),
    ("DIRECT_WEB", date(2025, 1, 1), None, None, "2.2", None, "3", "Card fee and booking engine fee"),
)


def seed_contracts(ctx: Any) -> dict:
    if not ctx.table_exists("pfg_ota_contracts"):
        return {"contracts": 0}
    cur, owner = ctx.cur, ctx.ids.get("users", {}).get("owner")
    for ota, entity, country, pct, rule, excluded, collect, disputes, vcc in CONTRACTS:
        cur.execute(
            """
            INSERT INTO pfg_ota_contracts (hotel_slug, ota_code, entity_name, entity_country, effective_from,
                base_commission_pct, commission_base_rule, excluded_charge_types, collect_models, dispute_rules,
                vcc_rules, clauses, extracted_by, status, note, created_by, confirmed_by, confirmed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '[]', 'MANUAL', 'CONFIRMED', %s, %s, %s, %s)
            """,
            (ctx.slug, ota, entity, country, CONTRACT_FROM, pct, rule, excluded, collect, Json(disputes), Json(vcc),
             "Synthetic demo contract", owner, owner, ctx.now),
        )
    for ota, program, kind, pct, stay_from, stay_to, label in PROGRAMS:
        cur.execute(
            """
            INSERT INTO pfg_ota_programs (hotel_slug, ota_code, program, kind, pct, stay_from, stay_to, stackable,
                source, label, is_active, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, 'MANUAL', %s, TRUE, %s)
            """,
            (ctx.slug, ota, program, kind, pct, stay_from, stay_to, label, owner),
        )
    for ota, since, mf, td, card, pay, engine, note in CHANNEL_CONFIG:
        cur.execute(
            """
            INSERT INTO pfg_channel_config (hotel_slug, ota_code, configured_from, mf_configured_as_charge,
                td_configured_as_charge, card_fee_pct, payments_fee_pct, booking_engine_fee_pct, note, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (ctx.slug, ota, since, mf, td, card, pay, engine, note, owner),
        )
    return {"contracts": len(CONTRACTS), "programs": len(PROGRAMS), "channel_config": len(CHANNEL_CONFIG)}


# ─── Part entry ───


def seed_part(ctx: Any) -> dict:
    out: dict[str, Any] = {}
    for step in (seed_kb, seed_menu, seed_amenities, seed_demand, seed_contracts):
        out.update(step(ctx))
    return out
