"""
The UAE default amenity catalog (02 spec 5; Canon: UAE defaults carry a
source_url). Every seeded row has needs_review = true: the costs are Clefs
estimates in AED fils and the standards come from vendor guidance, so the
owner confirms both before the numbers drive money alerts. Seeding runs once
per hotel (amn_seed_runs), so an item the owner deleted never comes back.

Why these items: the housekeeping consumables a Dubai hotel issues daily
(bath set, water, tea and coffee), the on-request kits whose request rate the
hotel learns from its own logs (research: no published rate exists), and the
minibar lines whose unit cost includes UAE excise (50% sweetened drinks, 100%
energy drinks). Plastic kits are flagged is_single_use_plastic for the Dubai
ban phases; none of the banned 2025/2026 items (stirrers, straws, cotton buds
with plastic stems, plastic cups, cutlery) is seeded.
"""

from __future__ import annotations

from typing import Any

SEED_VERSION = 1

_OPS = "https://pinkantstore.com/blogs/news/hotel-shampoo-a-buying-guide-for-bathroom-amenities"
_PAR = "https://www.rongroupglobal.com/opening-a-restaurant/blog-hotel-housekeeping-supplies-procurement-guide.html"
_SUP = "https://www.khaleejtimes.com/uae/dubai-single-use-plastic-ban-final-phase-january-2026-awareness-guide"
_EXCISE = "https://tax.gov.ae/en/taxes/excise.tax.aspx"
_REQ = "https://hospitality.institute/bha205/handling-special-guest-requests-at-housekeeping-control-desk/"

# key, name_en, name_ar, category, basis, std_qty, unit_cost_fils, excise_category, excise_fils,
# single-use plastic, request_type_key, chargeable price fils, aliases, source_url
_ROWS: tuple[tuple, ...] = (
    ("SHAMPOO", "Shampoo 30 ml", "شامبو ٣٠ مل", "BATH", "PER_OCCUPIED_ROOM_NIGHT", "1", 120, "NONE", 0, False,
     "amenities", None, ("shampoo", "شامبو"), _OPS),
    ("CONDITIONER", "Conditioner 30 ml", "بلسم ٣٠ مل", "BATH", "PER_OCCUPIED_ROOM_NIGHT", "0.5", 120, "NONE", 0,
     False, "amenities", None, ("conditioner", "بلسم"), _OPS),
    ("SHOWER_GEL", "Shower gel 30 ml", "جل الاستحمام ٣٠ مل", "BATH", "PER_OCCUPIED_ROOM_NIGHT", "1", 120, "NONE", 0,
     False, "amenities", None, ("shower gel", "body wash", "جل الاستحمام"), _OPS),
    ("BODY_LOTION", "Body lotion 30 ml", "لوشن الجسم ٣٠ مل", "BATH", "PER_OCCUPIED_ROOM_NIGHT", "0.5", 130, "NONE",
     0, False, "amenities", None, ("lotion", "body lotion", "moisturiser", "moisturizer", "لوشن"), _OPS),
    ("SOAP_BAR", "Soap bar 30 g", "صابونة ٣٠ غ", "BATH", "PER_OCCUPIED_ROOM_NIGHT", "1", 80, "NONE", 0, False,
     "amenities", None, ("soap", "صابون", "صابونة"), _OPS),
    ("DENTAL_KIT", "Dental kit", "طقم أسنان", "DENTAL", "ON_REQUEST", "0", 150, "NONE", 0, True, "amenities", None,
     ("toothbrush", "tooth brush", "toothpaste", "dental kit", "dental", "فرشاة أسنان", "معجون أسنان"), _REQ),
    ("SHAVING_KIT", "Shaving kit", "طقم حلاقة", "SHAVING", "ON_REQUEST", "0", 250, "NONE", 0, True, "amenities",
     None, ("razor", "shaving kit", "shaving", "shaver", "ماكينة حلاقة", "حلاقة"), _REQ),
    ("VANITY_KIT", "Vanity kit (paper buds, pads)", "طقم تجميل", "BATH", "ON_REQUEST", "0", 90, "NONE", 0, False,
     "amenities", None, ("vanity kit", "cotton pads", "cotton buds", "q-tips", "قطن"), _REQ),
    ("SHOWER_CAP", "Shower cap", "غطاء الاستحمام", "BATH", "ON_REQUEST", "0", 40, "NONE", 0, True, "amenities",
     None, ("shower cap", "غطاء شعر"), _SUP),
    ("COMB", "Comb", "مشط", "OTHER", "ON_REQUEST", "0", 50, "NONE", 0, True, "amenities", None,
     ("comb", "hair comb", "مشط"), _SUP),
    ("SEWING_KIT", "Sewing kit", "طقم خياطة", "OTHER", "ON_REQUEST", "0", 70, "NONE", 0, False, "amenities", None,
     ("sewing kit", "needle", "thread", "طقم خياطة"), _REQ),
    ("SLIPPERS", "Slippers (pair)", "خف (زوج)", "SLIPPERS", "PER_ARRIVAL", "2", 600, "NONE", 0, False, "amenities",
     None, ("slippers", "slipper", "شبشب", "خف"), _REQ),
    ("WATER_500", "Water 500 ml", "مياه ٥٠٠ مل", "WATER", "PER_OCCUPIED_ROOM_NIGHT", "2", 60, "NONE", 0, False,
     "water", None, ("water", "bottled water", "water bottle", "mineral water", "مياه", "ماء"), _PAR),
    ("TEA_COFFEE", "Tea and coffee set", "طقم شاي وقهوة", "TEA_COFFEE", "PER_OCCUPIED_ROOM_NIGHT", "1", 200, "NONE",
     0, False, "amenities", None, ("tea", "coffee", "sugar", "creamer", "شاي", "قهوة"), _PAR),
    ("MB_SOFT_DRINK", "Minibar soft drink 330 ml", "مشروب غازي ٣٣٠ مل", "MINIBAR", "ON_REQUEST", "0", 270,
     "SWEETENED", 90, False, "drinks", 1500, ("soft drink", "cola", "soda", "مشروب غازي"), _EXCISE),
    ("MB_ENERGY", "Minibar energy drink 250 ml", "مشروب طاقة ٢٥٠ مل", "MINIBAR", "ON_REQUEST", "0", 700, "ENERGY",
     350, False, "drinks", 2500, ("energy drink", "مشروب طاقة"), _EXCISE),
    ("MB_JUICE", "Minibar juice 250 ml", "عصير ٢٥٠ مل", "MINIBAR", "ON_REQUEST", "0", 250, "NONE", 0, False,
     "drinks", 1500, ("juice", "عصير"), _PAR),
    ("MB_SNACK", "Minibar snack", "وجبة خفيفة", "MINIBAR", "ON_REQUEST", "0", 400, "NONE", 0, False, "drinks", 2000,
     ("snack", "chips", "nuts", "chocolate", "وجبة خفيفة"), _PAR),
)


def default_rows(currency: str) -> list[dict[str, Any]]:
    """The seed as item dicts (keys as amn_items columns plus excise_minor)."""
    out = []
    for i, r in enumerate(_ROWS):
        (key, name_en, name_ar, category, basis, std, cost, excise_cat, excise, sup, rtype, price, aliases, url) = r
        out.append({
            "key": key, "name_en": name_en, "name_ar": name_ar, "category": category, "basis": basis,
            "std_qty": std, "unit_cost_minor": cost, "excise_category": excise_cat, "excise_minor": excise,
            "is_single_use_plastic": sup, "request_type_key": rtype, "chargeable": price is not None,
            "price_minor": price, "aliases": list(aliases), "source_url": url, "currency": currency,
            "sort": (i + 1) * 10,
        })
    return out
