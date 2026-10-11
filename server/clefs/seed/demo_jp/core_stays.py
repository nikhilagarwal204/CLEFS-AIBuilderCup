"""
Tokyo demo part "core_stays": the stays behind the guests and the
occupancy the prototype shows.

The planner is demo_uae.core_stays' (each room walks the calendar; a
forward stay is kept only if it was booked before the anchor), playing this
module's market through ctx.data("core_stays"):

- Guests from the hotel's JAPAC-heavy markets: Japan, Korea, mainland
  China, Taiwan, the US, Australia, Hong Kong, Thailand, Singapore, India
  and the UK, with a few French and German guests.
- Channels: Booking.com, Agoda, Expedia, Rakuten Travel, Jalan, Trip.com,
  direct, corporate and walk-in; the Japanese OTAs' guests mostly pay at the
  hotel, a share of them in cash.
- Rates in JPY in steps of 100 yen, tax included, from the Tokyo curves in
  _calendar (about 85% occupancy, ADR about 19,000 yen across the room mix).
- Check-in from 15:00, check-out by 11:00.

Written through core.stays like the UAE stays, then occupancy is derived
from them. The folio, the 2024-2025 baselines and the pace snapshots feed
the money and growth modules, which this hotel does not run, so they are
left out.
"""

from __future__ import annotations

from typing import Any

from clefs.seed.demo_jp import _calendar as cal
from clefs.seed.demo_uae import core_stays as uae
from clefs.seed.demo_uae.core_stays import Stay, _weighted

PART_ORDER = 30

# ─── Market (the names demo_uae.core_stays reads) ───

CHANNEL_MIX = (
    ("BOOKING", 0.22), ("AGODA", 0.14), ("EXPEDIA", 0.10), ("RAKUTEN", 0.16), ("JALAN", 0.10), ("TRIPCOM", 0.06),
    ("DIRECT_WEB", 0.12), ("DIRECT_PHONE", 0.04), ("CORPORATE", 0.04), ("WALKIN", 0.02),
)
# Mean lead time (days) of the short-lead component, per channel.
LEAD_MEAN = {"BOOKING": 25, "AGODA": 18, "EXPEDIA": 28, "RAKUTEN": 21, "JALAN": 18, "TRIPCOM": 20, "DIRECT_WEB": 16,
             "DIRECT_PHONE": 10, "CORPORATE": 7, "WALKIN": 0}
CHANNEL_RATE = {"BOOKING": 1.0, "AGODA": 0.97, "EXPEDIA": 1.0, "RAKUTEN": 0.99, "JALAN": 0.98, "TRIPCOM": 0.96,
                "DIRECT_WEB": 0.95, "DIRECT_PHONE": 0.96, "CORPORATE": 0.85, "WALKIN": 1.10}
# A twin costs more than a double in Tokyo (two guests); the family rooms sleep four.
TYPE_RATE = {"MOD_DBL": 1.0, "STD_TWIN": 1.15, "FAM_QUAD": 1.9}
CANCEL_P = {"BOOKING": 0.18, "AGODA": 0.12, "EXPEDIA": 0.10, "RAKUTEN": 0.12, "JALAN": 0.12, "TRIPCOM": 0.10,
            "DIRECT_WEB": 0.07, "DIRECT_PHONE": 0.05, "CORPORATE": 0.06, "WALKIN": 0.0}
NO_SHOW_P = {"DIRECT_PHONE": 0.04, "CORPORATE": 0.03}
# Length of stay: (nights, weight). Inbound guests stay longer than the
# domestic weekend trips: mean about 2.7 nights.
LOS = ((1, 26), (2, 27), (3, 19), (4, 12), (5, 7), (6, 4), (7, 3), (9, 1), (12, 1))
MEAN_LOS = sum(n * w for n, w in LOS) / sum(w for _n, w in LOS)
CCY = "JPY"
PMS_PREFIX = "AZ"
CHECKIN_HOURS, IN_HOUSE_CHECKIN_HOURS, CHECKOUT_HOURS = (15, 22), (15, 20), (7, 10)

# country, language, weight, first names, family names (synthetic combinations)
GUESTS = (
    ("JP", "ja", 24, ("Haruto", "Yui", "Sota", "Aoi", "Ren", "Hina", "Takumi", "Sakura", "Kaito", "Mio"),
     ("Inoue", "Kimura", "Shimizu", "Yamaguchi", "Matsuda", "Okada", "Hasegawa", "Fujii", "Ishii", "Morita")),
    ("KR", "ko", 14, ("Min-jun", "Seo-yeon", "Ji-ho", "Ha-eun", "Do-yoon", "Ji-woo", "Seung-hyun", "Yu-na"),
     ("Kim", "Lee", "Park", "Choi", "Jung", "Kang", "Cho", "Yoon")),
    ("CN", "zh", 11, ("Wei", "Fang", "Jun", "Li Na", "Hao", "Xiu Ying", "Lei", "Mei"),
     ("Wang", "Li", "Zhang", "Liu", "Yang", "Zhao", "Huang", "Zhou")),
    ("TW", "zh", 9, ("Yu-ting", "Chia-hao", "Hsin-yi", "Chih-wei", "Pei-shan", "Wen-chieh"),
     ("Lin", "Huang", "Chang", "Wu", "Tsai", "Hsu")),
    ("US", "en", 9, ("Michael", "Jessica", "David", "Ashley", "Daniel", "Sarah", "Ryan", "Megan"),
     ("Johnson", "Williams", "Miller", "Davis", "Garcia", "Martinez")),
    ("AU", "en", 8, ("Emily", "Jack", "Olivia", "Liam", "Chloe", "Noah", "Isla", "Lachlan"),
     ("Smith", "Kelly", "Ryan", "Campbell", "Thompson", "Wilson", "Murphy", "Clarke")),
    ("HK", "zh", 6, ("Kelvin", "Winnie", "Jason", "Karen", "Alan", "Cindy"),
     ("Chan", "Wong", "Leung", "Cheung", "Lau", "Ho")),
    ("TH", "th", 5, ("Somchai", "Siriporn", "Nattapong", "Kanokwan", "Thanawat", "Pimchanok"),
     ("Saelim", "Wongsakul", "Chaiyaporn", "Srisuk", "Boonmee", "Rattanakorn")),
    ("SG", "en", 4, ("Wei Ming", "Hui Min", "Jun Jie", "Rachel", "Arjun", "Siti"),
     ("Tan", "Lim", "Ng", "Goh", "Teo", "Koh")),
    ("IN", "hi", 4, ("Rahul", "Ananya", "Amit", "Sneha", "Vikram", "Kavya"),
     ("Patel", "Reddy", "Nair", "Gupta", "Iyer", "Menon")),
    ("GB", "en", 3, ("James", "Emma", "Oliver", "Sophie", "Harry", "Charlotte"),
     ("Jones", "Taylor", "Brown", "Evans", "Walker", "Wright")),
    ("FR", "fr", 2, ("Camille", "Lucas", "Manon", "Hugo", "Chloe", "Louis"),
     ("Martin", "Bernard", "Dubois", "Moreau", "Laurent", "Lefevre")),
    ("DE", "de", 1, ("Lukas", "Anna", "Felix", "Lena", "Jonas", "Laura"),
     ("Muller", "Schmidt", "Schneider", "Fischer", "Weber", "Becker")),
)


def night_rate_minor(major: float) -> int:
    """A night as a Tokyo rate table holds it: steps of 100 yen (the yen has
    no minor unit, so minor units are yen)."""
    return int(round(major / 100)) * 100


def ota_id(rng: Any, channel: str) -> str | None:
    if channel == "BOOKING":
        return str(rng.randint(4_000_000_000, 4_999_999_999))
    if channel == "AGODA":
        return str(rng.randint(800_000_000, 999_999_999))
    if channel == "EXPEDIA":
        return str(rng.randint(7_100_000_000, 7_399_999_999))
    if channel == "RAKUTEN":
        return f"RY{rng.randint(10_000_000, 99_999_999)}"
    if channel == "JALAN":
        return str(rng.randint(10_000_000, 99_999_999))
    if channel == "TRIPCOM":
        return str(rng.randint(1_000_000_000_000, 9_999_999_999_999))
    return None


def channel_terms(rng: Any, s: Stay) -> None:
    """Channel-shaped details: collect model, payment, rate plan, segment
    and party size."""
    ch = s.channel
    if ch == "BOOKING":
        s.collect_model = "OTA_COLLECT" if rng.random() < 0.15 else "HOTEL_COLLECT"
        s.rate_plan = _weighted(rng, (("BAR", 60), ("BAR_NR", 25), ("BB", 15)))
    elif ch in ("AGODA", "TRIPCOM"):
        s.collect_model, s.rate_plan = "OTA_COLLECT", _weighted(rng, (("BAR", 70), ("BAR_NR", 20), ("BB", 10)))
    elif ch == "EXPEDIA":
        s.collect_model = "OTA_COLLECT" if rng.random() < 0.6 else "HOTEL_COLLECT"
        s.rate_plan = _weighted(rng, (("BAR", 65), ("BB", 35)))
    elif ch in ("RAKUTEN", "JALAN"):
        # Room-only and breakfast plans, paid at the hotel.
        s.collect_model, s.rate_plan = "HOTEL_COLLECT", _weighted(rng, (("ROOM_ONLY", 65), ("BB", 35)))
    elif ch == "CORPORATE":
        s.rate_plan, s.segment = "CORP", "CORPORATE"
    elif ch == "WALKIN":
        s.rate_plan = "RACK"
    else:
        s.rate_plan = _weighted(rng, (("BAR_DIRECT", 75), ("BB_DIRECT", 25)))
    if s.collect_model == "OTA_COLLECT":
        s.payment_method = "VCC"
    elif ch == "CORPORATE":
        s.payment_method = "BANK"
    elif ch == "WALKIN" and rng.random() < 0.5:
        s.payment_method = "CASH"
    elif ch in ("RAKUTEN", "JALAN") and rng.random() < 0.3:
        s.payment_method = "CASH"
    if s.room_type == "FAM_QUAD":
        s.adults, s.children = 2, rng.randint(1, 2)
    elif s.room_type == "STD_TWIN":
        s.adults = 2
    else:
        s.adults = 1 if (s.segment == "CORPORATE" or rng.random() < 0.4) else 2


# ─── Part entry ───


def seed_part(ctx: Any) -> dict:
    plan = uae.plan_stays(ctx)
    ctx.ids["reservations"] = uae.write_stays(ctx, plan)
    out: dict[str, Any] = {"reservations": len(plan),
                           "cancelled": sum(1 for s in plan if s.status == "CANCELLED"),
                           "room_nights": sum(s.nights for s in plan if s.status not in ("CANCELLED", "NO_SHOW"))}
    out.update(uae.seed_occupancy(ctx))
    return out
