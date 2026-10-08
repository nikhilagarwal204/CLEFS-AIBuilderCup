"""
Demo part "core_stays": what the PMS says happened (02 spec 9, "Windows").

- History: every stay night from anchor-119 to the anchor, ~2,600
  reservations and ~6,200 room nights at full scale, occupancy 55-66% and
  net ADR AED 240-330 in summer, with Demo Summer Expo pickup 18-20 Aug.
- Forward: stays to anchor+125 that were booked before the anchor, with
  pace building into CABSAT, GITEX (ADR about AED 650) and NYE.
- Channel mix Booking 45 / Agoda 15 / Expedia 12 / Direct 20 /
  Corporate + Walk-in 8, plus cancellations on top.
- Reservation nights, fees and stay events through core.stays
  upsert_reservation (the single write path); folio charges through
  upsert_folio_charges; occupancy daily and by floor through
  derive_occupancy; 2024-2025 baselines (and Jan-May 2026) through
  set_occupancy(source="DEMO"); the Mar-Jul 2026 distortion covered by
  baseline_exclusions; OTB snapshots for pace.

How the stays are drawn: each room walks the calendar. On a free night a
stay starts with probability q = o / (L (1 - o) + o) (o: that night's
target occupancy, L: mean length of stay). The idle run before a stay then
averages (1 - q) / q nights, so the long-run occupied share is
L / (L + (1 - q) / q) = o. A forward stay is kept only if its sampled booking time
falls before the anchor, which is exactly what "on the books" means, so
pace falls out of the lead-time distribution instead of being painted on.

Clean by design: no early departures, and no-shows only on direct and
corporate bookings. OTA anomalies are planted by the statements part with
manifest entries; a natural Booking no-show here would be an unplanned L14
finding and cost the detectors their precision target.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from clefs.core import stays
from clefs.seed.demo_uae import _calendar as cal
from clefs.seed.demo_uae.core import ACTOR

PART_ORDER = 30

# ─── Mix and pricing ───

CHANNEL_MIX = (
    ("BOOKING", 0.45), ("AGODA", 0.15), ("EXPEDIA", 0.12), ("DIRECT_WEB", 0.12), ("DIRECT_PHONE", 0.08),
    ("CORPORATE", 0.05), ("WALKIN", 0.03),
)
OTAS = ("BOOKING", "AGODA", "EXPEDIA")
# Mean lead time (days) of the short-lead component, per channel.
LEAD_MEAN = {"BOOKING": 18, "AGODA": 14, "EXPEDIA": 22, "DIRECT_WEB": 12, "DIRECT_PHONE": 8, "CORPORATE": 6,
             "WALKIN": 0}
CHANNEL_RATE = {"BOOKING": 1.0, "AGODA": 0.97, "EXPEDIA": 1.0, "DIRECT_WEB": 0.95, "DIRECT_PHONE": 0.96,
                "CORPORATE": 0.86, "WALKIN": 1.08}
TYPE_RATE = {"STD_KING": 1.0, "STD_TWIN": 0.97, "FAM_2BR": 1.65}
CANCEL_P = {"BOOKING": 0.16, "AGODA": 0.12, "EXPEDIA": 0.10, "DIRECT_WEB": 0.07, "DIRECT_PHONE": 0.05,
            "CORPORATE": 0.06, "WALKIN": 0.0}
NO_SHOW_P = {"DIRECT_PHONE": 0.04, "CORPORATE": 0.03}
# Length of stay: (nights, weight). Mean ~2.4 nights (6,200 / 2,600).
LOS = ((1, 30), (2, 29), (3, 18), (4, 10), (5, 6), (6, 3), (7, 2), (9, 1), (12, 1))
MEAN_LOS = sum(n * w for n, w in LOS) / sum(w for _n, w in LOS)

# ─── Guests ───

# country, language, weight, first names, family names (synthetic combinations)
GUESTS = (
    ("IN", "hi", 22, ("Rahul", "Priya", "Amit", "Sneha", "Vikram", "Anjali", "Arjun", "Pooja", "Sanjay", "Kavya"),
     ("Sharma", "Patel", "Reddy", "Nair", "Gupta", "Iyer", "Singh", "Menon", "Joshi", "Kapoor")),
    ("SA", "ar", 8, ("Abdullah", "Faisal", "Noura", "Saud", "Reem", "Turki", "Lama", "Majed"),
     ("Al Qahtani", "Al Harbi", "Al Otaibi", "Al Ghamdi", "Al Shehri", "Al Dossary")),
    ("EG", "ar", 6, ("Mohamed", "Mahmoud", "Yasmin", "Omar", "Heba", "Karim", "Nour", "Tarek"),
     ("Hassan", "Ibrahim", "Mostafa", "Abdel Rahman", "El Sayed", "Fawzy")),
    ("RU", "ru", 10, ("Alexei", "Olga", "Dmitry", "Natalia", "Sergei", "Irina", "Pavel", "Ekaterina"),
     ("Ivanov", "Smirnova", "Kuznetsov", "Popova", "Volkov", "Sokolova", "Morozov", "Lebedeva")),
    ("CN", "zh", 8, ("Wei", "Fang", "Jun", "Li Na", "Hao", "Xiu Ying", "Lei", "Mei"),
     ("Wang", "Li", "Zhang", "Liu", "Chen", "Yang", "Zhao", "Huang")),
    ("PK", "en", 8, ("Usman", "Ayesha", "Hamza", "Sana", "Bilal", "Zainab", "Imran", "Fatima"),
     ("Khan", "Malik", "Qureshi", "Butt", "Chaudhry", "Siddiqui")),
    ("GB", "en", 6, ("James", "Emma", "Oliver", "Sophie", "Harry", "Charlotte", "George", "Amelia"),
     ("Smith", "Jones", "Taylor", "Brown", "Wilson", "Evans", "Walker", "Wright")),
    ("PH", "en", 5, ("Jose", "Maria", "Mark", "Angelica", "John Paul", "Kristine"),
     ("Reyes", "Garcia", "Bautista", "Mendoza", "Villanueva", "Castillo")),
    ("KZ", "ru", 4, ("Nursultan", "Aigerim", "Yerlan", "Dana", "Timur", "Saule"),
     ("Nurlanov", "Akhmetova", "Seitkali", "Bekova", "Zhumabayev")),
    ("AE", "ar", 3, ("Ahmed", "Mariam", "Rashid", "Hessa", "Saeed", "Shamma"),
     ("Al Mazrouei", "Al Ketbi", "Al Shamsi", "Al Nuaimi", "Al Falasi")),
    ("NG", "en", 4, ("Chinedu", "Ngozi", "Emeka", "Funmilayo", "Tunde", "Adaeze"),
     ("Okafor", "Adeyemi", "Nwosu", "Balogun", "Eze")),
    ("DE", "en", 3, ("Lukas", "Anna", "Felix", "Lena", "Jonas", "Laura"),
     ("Muller", "Schmidt", "Schneider", "Fischer", "Weber")),
    ("BD", "en", 4, ("Rafiq", "Nasrin", "Tanvir", "Sharmin", "Arif", "Farzana"),
     ("Rahman", "Hossain", "Islam", "Ahmed", "Chowdhury")),
    ("OM", "ar", 3, ("Salim", "Aisha", "Khalfan", "Maryam", "Hilal"),
     ("Al Balushi", "Al Hinai", "Al Rawahi", "Al Busaidi")),
    ("US", "en", 3, ("Michael", "Jessica", "David", "Ashley", "Daniel", "Sarah"),
     ("Johnson", "Williams", "Miller", "Davis", "Martinez")),
)


# ─── Plan (pure: rng + anchor in, stays out) ───


@dataclass
class Stay:
    pms_res_id: str
    channel: str
    room_type: str
    check_in: date
    check_out: date
    booked_at: datetime
    rates: list[int]                      # net room rate per night, minor units
    guest_name: str
    country: str
    language: str
    room_number: str | None = None
    status: str = "CONFIRMED"
    cancelled_at: datetime | None = None
    checked_in_at: datetime | None = None
    checked_out_at: datetime | None = None
    ota_res_id: str | None = None
    collect_model: str = "HOTEL_COLLECT"
    payment_method: str = "CARD"
    rate_plan: str = "BAR"
    segment: str = "LEISURE"
    adults: int = 2
    children: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


def _weighted(rng: Any, pairs: tuple) -> Any:
    total = sum(w for _v, w in pairs)
    x = rng.random() * total
    for v, w in pairs:
        x -= w
        if x < 0:
            return v
    return pairs[-1][0]


def _lead_days(rng: Any, channel: str, check_in: date) -> float:
    """Mixture: a short exponential lead per channel, or (with the night's
    early share) a long uniform 30-180 days lead. Walk-ins arrive the day
    they book."""
    if channel == "WALKIN":
        return 0.0
    if rng.random() < cal.early_share(check_in):
        return rng.uniform(30, 180)
    return rng.expovariate(1 / LEAD_MEAN[channel])


def _guest(rng: Any) -> tuple[str, str, str]:
    country, lang, _w, firsts, lasts = _weighted(rng, tuple((g, g[2]) for g in GUESTS))
    return f"{rng.choice(firsts)} {rng.choice(lasts)}", country, lang


def _rates(rng: Any, check_in: date, nights: int, channel: str, room_type: str) -> list[int]:
    base = CHANNEL_RATE[channel] * TYPE_RATE[room_type] * math.exp(rng.gauss(0, 0.05))
    out = []
    for i in range(nights):
        aed = cal.target_adr(check_in + timedelta(days=i)) * base
        out.append(int(round(aed)) * 100)  # whole dirhams, as a PMS rate table would hold
    return out


def _ota_id(rng: Any, channel: str) -> str | None:
    if channel == "BOOKING":
        return str(rng.randint(4_000_000_000, 4_999_999_999))
    if channel == "AGODA":
        return str(rng.randint(800_000_000, 999_999_999))
    if channel == "EXPEDIA":
        return str(rng.randint(7_100_000_000, 7_399_999_999))
    return None


def _terms(rng: Any, s: Stay) -> None:
    """Channel-shaped details: collect model, payment, rate plan, segment
    and party size."""
    ch = s.channel
    if ch == "BOOKING":
        s.collect_model = "OTA_COLLECT" if rng.random() < 0.15 else "HOTEL_COLLECT"
        s.rate_plan = _weighted(rng, (("BAR", 60), ("BAR_NR", 25), ("BB", 15)))
    elif ch == "AGODA":
        s.collect_model, s.rate_plan = "OTA_COLLECT", _weighted(rng, (("BAR", 70), ("BAR_NR", 30)))
    elif ch == "EXPEDIA":
        s.collect_model = "OTA_COLLECT" if rng.random() < 0.6 else "HOTEL_COLLECT"
        s.rate_plan = _weighted(rng, (("BAR", 65), ("BB", 35)))
    elif ch == "CORPORATE":
        s.rate_plan, s.segment = "CORP", "CORPORATE"
    elif ch == "WALKIN":
        s.rate_plan = "RACK"
    else:
        s.rate_plan = _weighted(rng, (("BAR_DIRECT", 75), ("BB_DIRECT", 25)))
    # Agoda's UPC and Expedia's EVC are virtual cards (VCC); corporate
    # accounts settle by bank transfer against the city ledger.
    if s.collect_model == "OTA_COLLECT":
        s.payment_method = "VCC"
    elif ch == "CORPORATE":
        s.payment_method = "BANK"
    elif ch == "WALKIN" and rng.random() < 0.5:
        s.payment_method = "CASH"
    if ch != "CORPORATE" and cal.event_on(s.check_in) is not None and cal.event_on(s.check_in).key.startswith(
            ("gitex", "cabsat", "demo-summer")) and rng.random() < 0.35:
        s.segment = "MICE"
    if s.room_type == "FAM_2BR":
        s.adults, s.children = 2, rng.randint(1, 3)
    elif s.room_type == "STD_TWIN":
        s.adults = 2
    else:
        s.adults = 1 if (s.segment in ("CORPORATE", "MICE") or rng.random() < 0.35) else 2


def _status(rng: Any, ctx: Any, s: Stay) -> None:
    """Where the stay stands at the anchor evening, with PMS timestamps."""
    anchor = ctx.anchor
    if s.check_out <= anchor:
        if rng.random() < NO_SHOW_P.get(s.channel, 0.0):
            s.status = "NO_SHOW"
            return
        s.status = "CHECKED_OUT"
        s.checked_in_at = ctx.at(s.check_in, rng.randint(14, 22), rng.randint(0, 59))
        s.checked_out_at = ctx.at(s.check_out, rng.randint(7, 11), rng.randint(0, 59))
    elif s.check_in <= anchor:
        s.status = "IN_HOUSE"
        s.checked_in_at = ctx.at(s.check_in, rng.randint(14, 20), rng.randint(0, 59))
    else:
        s.status = "CONFIRMED"


def _stay(ctx: Any, rng: Any, room: dict, ci: date, co: date, channel: str, booked: datetime) -> Stay:
    name, country, lang = _guest(rng)
    s = Stay(pms_res_id="", channel=channel, room_type=room["room_type_code"], check_in=ci, check_out=co,
             booked_at=booked, rates=_rates(rng, ci, (co - ci).days, channel, room["room_type_code"]),
             guest_name=name, country=country, language=lang, ota_res_id=_ota_id(rng, channel))
    # The PMS assigns rooms at arrival: forward stays beyond tomorrow have a
    # room type only (occupancy by floor is a history figure).
    if ci <= ctx.anchor + timedelta(days=1):
        s.room_number = room["room_number"]
    _terms(rng, s)
    _status(rng, ctx, s)
    return s


def _booked_at(ctx: Any, rng: Any, channel: str, ci: date) -> datetime:
    if channel == "WALKIN":
        return ctx.at(ci, rng.randint(12, 19), rng.randint(0, 59))
    lead = _lead_days(rng, channel, ci)
    return ctx.at(ci, 15) - timedelta(days=lead)


def _cancelled_twin(ctx: Any, rng: Any, room: dict, s: Stay) -> Stay | None:
    """A cancelled booking near a kept one (it never held a room). Kept only
    when both the booking and the cancellation happened before the anchor
    evening; a cancellation still in the future would be live inventory."""
    ci = s.check_in + timedelta(days=rng.randint(-10, 10))
    co = ci + timedelta(days=_weighted(rng, LOS))
    if co <= ctx.history_start:
        return None
    booked = _booked_at(ctx, rng, s.channel, ci) - timedelta(days=rng.uniform(1, 20))
    window = (ctx.at(ci, 14) - booked).total_seconds()
    cancelled = booked + timedelta(seconds=window * rng.uniform(0.1, 0.9))
    if booked >= ctx.now or cancelled >= ctx.now:
        return None
    twin = _stay(ctx, rng, room, ci, co, s.channel, booked)
    twin.room_number, twin.status, twin.cancelled_at = None, "CANCELLED", cancelled
    twin.checked_in_at = twin.checked_out_at = None
    return twin


def start_probability(occ: float) -> float:
    return min(0.97, occ / (MEAN_LOS * (1 - occ) + occ))


def rooms_used(ctx: Any) -> list[dict]:
    """The rooms that take stays. At scale < 1 (tests) a prefix of rooms
    ordered by (position, floor), so every floor and type still appears."""
    rooms = sorted(ctx.ids["rooms"].values(), key=lambda r: (r["room_number"][-2:], r["floor"]))
    n = max(4, min(len(rooms), round(len(rooms) * ctx.scale)))
    return rooms[:n]


def plan_stays(ctx: Any) -> list[Stay]:
    """Every stay and cancellation, deterministic for (seed, anchor, scale).
    Ids are given in booking order (a PMS numbers reservations as they are
    made)."""
    rng = ctx.rng("core:stays")
    start, end = ctx.history_start - timedelta(days=7), ctx.forward_end
    out: list[Stay] = []
    for room in rooms_used(ctx):
        d = start
        while d <= end:
            o = cal.target_occ(d)
            if rng.random() >= start_probability(o):
                d += timedelta(days=1)
                continue
            ci, co = d, d + timedelta(days=_weighted(rng, LOS))
            d = co
            channel = _weighted(rng, CHANNEL_MIX)
            booked = _booked_at(ctx, rng, channel, ci)
            if co <= ctx.history_start or booked >= ctx.now:
                continue  # before the window, or not booked yet (future pickup)
            s = _stay(ctx, rng, room, ci, co, channel, booked)
            out.append(s)
            if rng.random() < CANCEL_P[channel]:
                twin = _cancelled_twin(ctx, rng, room, s)
                if twin is not None:
                    out.append(twin)
    out.sort(key=lambda s: (s.booked_at, s.check_in, s.room_type, s.guest_name))
    for i, s in enumerate(out, start=1):
        s.pms_res_id = f"RC{100000 + i}"
    return out


# ─── Write through core.stays ───

# Stays this close to the anchor carry a guest email (Journeys and Guests
# work on recent and upcoming stays); example.com never delivers.
CONTACT_WINDOW = (45, 30)  # days before / after the anchor


def _data(s: Stay) -> dict:
    data: dict[str, Any] = {
        "pms_res_id": s.pms_res_id, "channel_code": s.channel, "ota_res_id": s.ota_res_id,
        "guest_name": s.guest_name, "guest_country": s.country, "guest_language": s.language,
        "booked_at": s.booked_at, "check_in": s.check_in, "check_out": s.check_out, "rooms_count": 1,
        "room_type_code": s.room_type, "room_number": s.room_number, "rate_plan_code": s.rate_plan,
        "status": s.status, "collect_model": s.collect_model, "payment_method": s.payment_method,
        "currency": "AED", "segment": s.segment, "adults": s.adults, "children": s.children,
    }
    for k in ("cancelled_at", "checked_in_at", "checked_out_at"):
        if getattr(s, k) is not None:
            data[k] = getattr(s, k)
    return data


def _contacts(ctx: Any, s: Stay) -> list[dict] | None:
    lo, hi = ctx.anchor - timedelta(days=CONTACT_WINDOW[0]), ctx.anchor + timedelta(days=CONTACT_WINDOW[1])
    if s.status == "CANCELLED" or s.check_out < lo or s.check_in > hi:
        return None
    local = "".join(ch for ch in s.guest_name.lower().replace(" ", ".") if ch.isalnum() or ch == ".")
    return [{"kind": "email", "value": f"{local}.{s.pms_res_id.lower()}@example.com"}]


# The whole seed is one transaction, so autovacuum never analyzes what it
# writes: the planner keeps believing reservations is empty and picks nested
# loops that make each chain lookup scan everything written so far (minutes
# at full scale). ANALYZE samples the transaction's own uncommitted rows.
ANALYZE_EVERY = 250
_ANALYZE_TABLES = ("reservations", "reservation_nights", "reservation_contacts", "stay_events")


def _analyze(ctx: Any) -> None:
    ctx.cur.execute("ANALYZE " + ", ".join(_ANALYZE_TABLES))


def write_stays(ctx: Any, plan: list[Stay]) -> list[dict]:
    """One upsert_reservation per stay: nights, fees (core.tax per night),
    stay chains and stay events all come from the single write path."""
    out: list[dict] = []
    # Written in arrival order (ids stay in booking order). upsert_reservation
    # re-links the back-to-back chain forward from each new stay, walking every
    # stay that arrives the day it departs; in arrival order that set is still
    # empty, so each write stays O(1) instead of walking up to 200 stays.
    for i, s in enumerate(sorted(plan, key=lambda x: (x.check_in, x.pms_res_id))):
        if i % ANALYZE_EVERY == 0:
            _analyze(ctx)
        nights = [{"stay_date": s.check_in + timedelta(days=i), "rate_minor": r, "room_number": s.room_number,
                   "room_type_code": s.room_type} for i, r in enumerate(s.rates)]
        res = stays.upsert_reservation(ctx.cur, ctx.slug, _data(s), source_system="PMS", nights=nights,
                                       contacts=_contacts(ctx, s))
        out.append({
            "id": res["id"], "pms_res_id": s.pms_res_id, "channel": s.channel, "ota_res_id": s.ota_res_id,
            "status": s.status, "check_in": s.check_in, "check_out": s.check_out, "room_number": s.room_number,
            "room_type": s.room_type, "guest_name": s.guest_name, "country": s.country, "language": s.language,
            "collect_model": s.collect_model, "rate_plan": s.rate_plan, "segment": s.segment,
            "booked_at": s.booked_at, "cancelled_at": s.cancelled_at, "rates": list(s.rates),
        })
    _analyze(ctx)
    out.sort(key=lambda r: r["pms_res_id"])
    return out


# ─── Folio ───

FEE_CODES = (("rate_minor", "ROOM"), ("sc_minor", "SC"), ("mf_minor", "MF"), ("tf_minor", "TF"),
             ("vat_minor", "VAT"), ("td_minor", "TD"))


def seed_folio(ctx: Any) -> dict:
    """The night audit's postings for every stayed night (room, service
    charge, municipality fee, VAT, Tourism Dirham, straight from the fees
    core.stays computed per night), plus some in-room dining and minibar.
    Clean on purpose: the T1-T7 folio anomalies are planted by the
    statements part with manifest entries."""
    rng = ctx.rng("core:folio")
    ctx.cur.execute(
        """
        SELECT r.pms_res_id, n.stay_date, n.room_number, n.rooms, n.rate_minor, n.sc_minor, n.mf_minor,
               n.tf_minor, n.vat_minor, n.td_minor
        FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
        WHERE n.hotel_slug = %s AND n.stayed AND r.source_system = 'PMS'
        ORDER BY r.pms_res_id, n.stay_date, n.room_seq
        """,
        (ctx.slug,),
    )
    rows: list[dict] = []
    for n in ctx.cur.fetchall():
        tag = f"{n['pms_res_id']}-{n['stay_date']:%Y%m%d}"
        for col, code in FEE_CODES:
            amount = int(n[col] or 0) * (int(n["rooms"] or 1) if col == "rate_minor" else 1)
            if amount:
                rows.append({"pms_txn_id": f"{tag}-{code}", "posted_date": n["stay_date"], "stay_date": n["stay_date"],
                             "code": code, "amount_minor": amount, "pms_res_id": n["pms_res_id"],
                             "room_number": n["room_number"], "description": code.title()})
        if rng.random() < 0.18:
            rows.append({"pms_txn_id": f"{tag}-FNB", "posted_date": n["stay_date"], "stay_date": n["stay_date"],
                         "code": "FNB", "amount_minor": rng.choice((45, 58, 79, 97, 120, 152)) * 100,
                         "pms_res_id": n["pms_res_id"], "room_number": n["room_number"],
                         "description": "In-Room Dining"})
        if rng.random() < 0.07:
            rows.append({"pms_txn_id": f"{tag}-MB", "posted_date": n["stay_date"], "stay_date": n["stay_date"],
                         "code": "MINIBAR", "amount_minor": rng.choice((12, 20, 24, 36)) * 100,
                         "pms_res_id": n["pms_res_id"], "room_number": n["room_number"], "description": "Minibar"})
    written = 0
    for i in range(0, len(rows), 2000):
        written += stays.upsert_folio_charges(ctx.cur, ctx.slug, rows[i:i + 2000], currency="AED")["written"]
    return {"folio_charges": written}


# ─── Occupancy, baselines, exclusions ───


def seed_occupancy(ctx: Any) -> dict:
    """History days from the stays themselves (DERIVED rows and by floor),
    so the occupancy screens and the reservations always agree."""
    out = stays.derive_occupancy(ctx.cur, ctx.slug, ctx.history_start, ctx.anchor, today=ctx.anchor)
    return {"occupancy_days": out.get("days"), "occupancy_by_floor_rows": out.get("by_floor_rows")}


def seed_baselines(ctx: Any) -> dict:
    """2024-01-01 to the day before the history window from the baseline
    formula (source DEMO, which ranks with PMS above DERIVED): 2024-2025 for
    seasonality, then Jan-May 2026 with the Mar-Jul distortion."""
    rng = ctx.rng("core:baseline")
    rooms = len(ctx.ids["rooms"])
    d, n = cal.BASELINE_START, 0
    while d < ctx.history_start:
        occupied = min(rooms, max(0, round(rooms * cal.baseline_occ(d) * (1 + rng.gauss(0, 0.02)))))
        adr = int(round(cal.baseline_adr(d) * math.exp(rng.gauss(0, 0.03))))
        arrivals = round(occupied / MEAN_LOS)
        stays.set_occupancy(ctx.cur, ctx.slug, d, {
            "rooms_available": rooms, "rooms_ooo": 0, "rooms_occupied": occupied, "arrivals": arrivals,
            "departures": arrivals, "room_revenue_minor": occupied * adr * 100,
        }, source="DEMO")
        d += timedelta(days=1)
        n += 1
    return {"baseline_days": n}


def seed_exclusions(ctx: Any) -> dict:
    """The 0021 migration seeds a global row for the Mar-Jul 2026 distortion;
    the hotel gets its own only if that row is missing (a hand-edited
    database), so calibration always skips the distorted months."""
    lo, hi = cal.DISTORTION
    rows = stays.baseline_exclusions(ctx.cur, ctx.slug)
    if any(r["from_date"] <= lo and r["to_date"] >= hi for r in rows):
        return {"baseline_exclusions": len(rows)}
    stays.add_exclusion(ctx.cur, ctx.slug, lo, hi, "Regional conflict distortion 2026", actor=ACTOR)
    return {"baseline_exclusions": len(rows) + 1}


# ─── On-the-books snapshots (pace) ───

PACE_WEEKS = 16
# What was on the books at the end of snapshot day S: booked by then and
# not cancelled by then, whatever its status is now (a stay now CHECKED_OUT
# was CONFIRMED on S). The same columns as core.stays.snapshot_otb writes.
_AS_OF = """
WITH days AS (SELECT d::date AS stay_date FROM generate_series(%(snap)s::date, %(end)s::date, '1 day') d),
booked AS (
    SELECT n.stay_date, r.channel_code AS channel, SUM(n.rooms) AS rooms, SUM(n.rate_minor * n.rooms) AS revenue
    FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
    WHERE n.hotel_slug = %(hotel)s AND r.booked_at < %(cutoff)s
      AND (r.cancelled_at IS NULL OR r.cancelled_at >= %(cutoff)s)
      AND n.stay_date BETWEEN %(snap)s AND %(end)s AND n.stay_date < r.check_out
    GROUP BY 1, 2
),
per_day AS (
    SELECT stay_date, SUM(rooms) AS rooms, SUM(revenue) AS revenue,
           jsonb_object_agg(channel, rooms) AS by_channel, jsonb_object_agg(channel, revenue) AS by_revenue
    FROM booked GROUP BY stay_date
)
INSERT INTO otb_snapshots (hotel_slug, snapshot_date, stay_date, rooms_otb, revenue_otb_minor, currency,
                           by_channel, by_channel_revenue)
SELECT %(hotel)s, %(snap)s, days.stay_date, COALESCE(p.rooms, 0), COALESCE(p.revenue, 0), 'AED',
       COALESCE(p.by_channel, '{}'), COALESCE(p.by_revenue, '{}')
FROM days LEFT JOIN per_day p USING (stay_date)
ON CONFLICT (hotel_slug, snapshot_date, stay_date) DO UPDATE SET
    rooms_otb = EXCLUDED.rooms_otb, revenue_otb_minor = EXCLUDED.revenue_otb_minor,
    by_channel = EXCLUDED.by_channel, by_channel_revenue = EXCLUDED.by_channel_revenue
"""


def seed_otb(ctx: Any) -> dict:
    """Weekly photographs for the last PACE_WEEKS weeks (reconstructed from
    booked_at and cancelled_at: core.stays only photographs the present, and
    a demo needs the pace curve a nightly job would have built), then the
    anchor's own through stays.snapshot_otb.
    FOUNDATION-REQUEST: snapshot_otb(as_of=) would let this call core."""
    written = 0
    end = ctx.forward_end + timedelta(days=30)
    for k in range(PACE_WEEKS, 0, -1):
        snap = ctx.anchor - timedelta(days=7 * k)
        cutoff = ctx.at(snap + timedelta(days=1), 0)
        ctx.cur.execute(_AS_OF, {"hotel": ctx.slug, "snap": snap, "end": end, "cutoff": cutoff})
        written += ctx.cur.rowcount or 0
    written += stays.snapshot_otb(ctx.cur, ctx.slug, snapshot_date=ctx.anchor,
                                  horizon_days=(end - ctx.anchor).days)
    return {"otb_snapshot_rows": written}


# ─── Part entry ───


def seed_part(ctx: Any) -> dict:
    plan = plan_stays(ctx)
    ctx.ids["reservations"] = write_stays(ctx, plan)
    out: dict[str, Any] = {"reservations": len(plan),
                           "cancelled": sum(1 for s in plan if s.status == "CANCELLED"),
                           "room_nights": sum(s.nights for s in plan if s.status not in ("CANCELLED", "NO_SHOW"))}
    for step in (seed_folio, seed_occupancy, seed_baselines, seed_exclusions, seed_otb):
        out.update(step(ctx))
    return out
