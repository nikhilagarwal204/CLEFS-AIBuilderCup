"""
Demand Radar relevance model (02 spec 4.3): which stay nights an event
touches, how much it matters to this hotel, and how the hotel's own pickup
calibrates the expected room-night uplift.

    score = w_class x decay(d) x corridor x scale x dow_fit x segment_fit

- decay = exp(-d / lambda): lambda 8 km (< 10k attendance), 15 km (10k-100k),
  25 km (> 100k or CITYWIDE). A measured travel time for the venue (engine
  settings `travel_minutes`) overrides the km decay with a band factor.
- corridor = 1.5 when the hotel's corridor tags meet the venue's (a
  dual-venue show takes the best venue), else 1.0.
- scale = min(1, log10(attendance) / 5), else phq_rank / 100, else a
  per-category default (a holiday has no crowd figure).
- dow_fit is per night (MICE weekdays, leisure Fri/Sat, UAE holidays Thu/Fri)
  and the event takes the weighted mean over its nights.
- segment_fit compares the hotel's own mix (midweek vs Fri/Sat nights, guest
  countries) with the market default, clipped to [0.5, 1.5]; 1.0 without
  enough history.
- w_class = (useful + 2) / (useful + dismissed + 3), learned per category and
  per category:venue from dismissals and approved or realised lift; a muted
  class scores 0.

Calibration: lift = occupancy on event nights / same-DOW same-season
baseline - 1, baseline from 2024-2025 minus core `baseline_exclusions`
(the 2026 conflict months). k = sum(lift) / sum(score) per category, and
expected_rn_uplift = k x score x capacity. Fewer than 5 own events of a
class fall back to global priors with confidence capped at 0.6.

Scores are relative weights for ranking and the demand index, not a
forecast: nothing here claims an event will move the hotel's numbers.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.demand import dedup, events, markets

log = logging.getLogger("clefs.demand.relevance")

# ─── Constants ───

MICE = frozenset({"CONFERENCE", "EXPO"})
LEISURE = frozenset({"SPORTS", "CONCERT", "FESTIVAL", "COMMUNITY", "CITYWIDE"})
UAE_HOLIDAYS = frozenset({"PUBLIC_HOLIDAY", "RELIGIOUS", "SCHOOL_HOLIDAY"})
HOLIDAYS = UAE_HOLIDAYS | {"SOURCE_MARKET_HOLIDAY"}

CORRIDOR_BOOST = 1.5
SEGMENT_CLIP = (0.5, 1.5)
MIN_OWN_EVENTS = 5          # below this a class uses the global prior
PRIOR_CONFIDENCE_CAP = 0.6
BASELINE_YEARS = (2024, 2025)  # Canon: seasonality uses 2024-25
MIN_SEGMENT_NIGHTS = 200    # own room nights before segment_fit leaves 1.0
# Market default segment shares (Dubai, UNVERIFIED placeholders: DET does
# not publish a segment split); segment_fit divides the hotel's own by these.
MARKET_SEGMENT_SHARE = {"MICE": 0.30, "LEISURE": 0.60}

# Global priors for k (room-night lift per unit of score): conservative
# placeholders until a hotel has 5 own events of the class. MICE moves
# Dubai hotels most (research 3: Deira 60% -> 80% on show pickup).
K_PRIORS = {
    "CONFERENCE": 0.20, "EXPO": 0.20, "CITYWIDE": 0.15, "SPORTS": 0.10, "CONCERT": 0.06, "FESTIVAL": 0.08,
    "COMMUNITY": 0.03, "PUBLIC_HOLIDAY": 0.08, "RELIGIOUS": 0.05, "SCHOOL_HOLIDAY": 0.05,
    "SOURCE_MARKET_HOLIDAY": 0.06,
}
# A holiday or an event without a crowd figure still needs a scale.
SCALE_DEFAULTS = {"PUBLIC_HOLIDAY": 0.6, "RELIGIOUS": 0.5, "SCHOOL_HOLIDAY": 0.5, "SOURCE_MARKET_HOLIDAY": 0.5,
                  "CITYWIDE": 0.8}
SCALE_UNKNOWN = 0.4
# Source-market holiday weight by kind (02 spec 4.3).
KIND_WEIGHTS = {"SCHOOL": 1.0, "RELIGIOUS": 0.7, "PUBLIC": 0.5}
# Travel-time bands (minutes -> factor) used instead of the km decay.
TRAVEL_BANDS = ((15, "0-15 min", 1.0), (30, "15-30 min", 0.7), (45, "30-45 min", 0.45), (60, "45-60 min", 0.25))
TRAVEL_FAR = ("60+ min", 0.1)


# ─── Stay nights affected (02 spec 4.3) ───
# A "night" is the date a guest sleeps over (check-in date semantics).

LEISURE_NIGHTS = (5, 6)  # ISO Fri, Sat: the UAE leisure nights (Canon)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)] if end >= start else []


def _base_nights(category: str, start: date, end: date, weekend: tuple[int, ...]) -> dict[date, float]:
    out: dict[date, float] = {}
    if category in MICE:
        # Delegates arrive the evening before and leave on the last day:
        # nights s-1 .. e-2 in full, the last night (e-1) at 0.6.
        for d in _days(start - timedelta(days=1), end - timedelta(days=2)):
            out[d] = 1.0
        out.setdefault(end - timedelta(days=1), 0.6)
    elif category in ("SPORTS", "CONCERT"):
        for d in _days(start, end):
            out[d] = 1.0
    elif category in ("FESTIVAL", "COMMUNITY", "CITYWIDE"):
        for d in _days(start, end):
            out[d] = 1.0 if d.isoweekday() in LEISURE_NIGHTS else 0.5
    elif category in UAE_HOLIDAYS:
        # The block of days off: the holiday, a bridge day when it falls on
        # a Tue (Mon) or Thu (Fri), and the weekend it touches. People stay
        # every night of the block except the last; the eve counts half.
        block = set(_days(start, end))
        for d in list(block):
            if d.isoweekday() == 2:
                block.add(d - timedelta(days=1))
            elif d.isoweekday() == 4:
                block.add(d + timedelta(days=1))
        grew = True
        while grew:
            grew = False
            lo, hi = min(block), max(block)
            for d in (lo - timedelta(days=1), hi + timedelta(days=1)):
                if d.isoweekday() in weekend and d not in block:
                    block.add(d)
                    grew = True
        lo, hi = min(block), max(block)
        for d in _days(lo, hi - timedelta(days=1)):
            out[d] = 1.0
        out.setdefault(lo - timedelta(days=1), 0.5)
    else:  # SOURCE_MARKET_HOLIDAY: visitors on their own calendar, every night
        for d in _days(start, end):
            out[d] = 1.0
    return out


def nights_affected(category: str, start: date, end: date, *, uncertainty_days: int = 0,
                    moon_dependent: bool = False, weekend: tuple[int, ...] | list[int] = (6, 7)) -> dict[date, float]:
    """{night: weight} for an event on [start, end]. A moon-dependent date
    (Hijri holidays) widens the window by uncertainty_days on both sides;
    the shifted nights count half, since the sighting decides which apply."""
    wk = tuple(weekend or ())
    out = _base_nights(category, start, end, wk)
    widen = int(uncertainty_days or 0) if (moon_dependent or uncertainty_days) else 0
    for k in range(-widen, widen + 1):
        if k == 0:
            continue
        shift = timedelta(days=k)
        for d, w in _base_nights(category, start + shift, end + shift, wk).items():
            out[d] = max(out.get(d, 0.0), round(w * 0.5, 3))
    return dict(sorted(out.items()))


# ─── Score components ───


def decay_lambda_km(category: str, attendance: int | None) -> float:
    if category == "CITYWIDE" or (attendance or 0) > 100_000:
        return 25.0
    return 15.0 if (attendance or 0) >= 10_000 else 8.0


def decay(distance_km: float | None, category: str, attendance: int | None) -> float:
    """exp(-d/lambda). No location (a holiday, a nationwide date) is 1.0."""
    if distance_km is None:
        return 1.0
    return math.exp(-max(0.0, distance_km) / decay_lambda_km(category, attendance))


def travel_band(minutes: float) -> tuple[str, float]:
    for limit, label, factor in TRAVEL_BANDS:
        if minutes <= limit:
            return label, factor
    return TRAVEL_FAR


def scale(category: str, attendance: int | None, phq_rank: int | None) -> float:
    if attendance and attendance > 1:
        return min(1.0, math.log10(attendance) / 5)
    if phq_rank:
        return min(1.0, max(0.0, phq_rank / 100))
    return SCALE_DEFAULTS.get(category, SCALE_UNKNOWN)


def dow_fit(category: str, night: date) -> float:
    wd = night.isoweekday()
    if category in MICE:
        return 0.6 if wd in LEISURE_NIGHTS else 1.0
    if category in LEISURE:
        return 1.0 if wd in LEISURE_NIGHTS else 0.5
    if category in ("PUBLIC_HOLIDAY", "RELIGIOUS"):
        return 1.0 if wd in (4, 5) else 0.8  # GCC holiday travel peaks Thu/Fri nights
    return 1.0


def w_class(useful: int, dismissed: int, muted: bool = False) -> float:
    if muted:
        return 0.0
    return (useful + 2) / (useful + dismissed + 3)


def clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ─── Engine settings (per hotel) ───

SETTINGS_DEFAULTS = {"corridor_tags": [], "travel_minutes": {}, "stayover_ratio": 0.8, "credits_per_attendant": 14.0,
                     "commission_share": None}


def get_settings(cur: Any, hotel_slug: str) -> dict:
    row = clefs_db.fetch_one("SELECT * FROM dr_engine_settings WHERE hotel_slug = %s", (hotel_slug,), cur=cur)
    out = dict(SETTINGS_DEFAULTS)
    if row:
        out["corridor_tags"] = list(row["corridor_tags"] or [])
        out["travel_minutes"] = dict(row["travel_minutes"] or {})
        for k in ("stayover_ratio", "credits_per_attendant", "commission_share"):
            if row[k] is not None:
                out[k] = float(row[k])
    return out


def put_settings(cur: Any, hotel_slug: str, changes: dict, *, user_id: int | None) -> dict:
    """Partial update. Corridor tags are upper-cased; travel minutes are
    {venue_code: minutes > 0}; ratios are checked against sane bounds."""
    cur_s = get_settings(cur, hotel_slug)
    if "corridor_tags" in changes:
        tags = changes["corridor_tags"] or []
        if not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip() for t in tags):
            raise ClefsError(400, "corridor_tags is a list of tags", "invalid_settings")
        cur_s["corridor_tags"] = sorted({t.strip().upper()[:40] for t in tags})[:20]
    if "travel_minutes" in changes:
        tm = changes["travel_minutes"] or {}
        try:
            clean = {str(k).strip().upper(): float(v) for k, v in dict(tm).items()}
        except (TypeError, ValueError) as exc:
            raise ClefsError(400, "travel_minutes is {venue_code: minutes}", "invalid_settings") from exc
        if any(v <= 0 or v > 600 for v in clean.values()):
            raise ClefsError(400, "Travel minutes are between 1 and 600", "invalid_settings")
        cur_s["travel_minutes"] = clean
    bounds = {"stayover_ratio": (0.1, 1.0), "credits_per_attendant": (4.0, 30.0), "commission_share": (0.0, 1.0)}
    for k, (lo, hi) in bounds.items():
        if k in changes:
            v = changes[k]
            if v is not None and not (lo <= float(v) <= hi):
                raise ClefsError(400, f"{k} is between {lo} and {hi}", "invalid_settings")
            cur_s[k] = None if v is None else float(v)
    clefs_db.execute(
        """INSERT INTO dr_engine_settings (hotel_slug, corridor_tags, travel_minutes, stayover_ratio,
                                           credits_per_attendant, commission_share, updated_by, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (hotel_slug) DO UPDATE SET corridor_tags = EXCLUDED.corridor_tags,
               travel_minutes = EXCLUDED.travel_minutes, stayover_ratio = EXCLUDED.stayover_ratio,
               credits_per_attendant = EXCLUDED.credits_per_attendant, commission_share = EXCLUDED.commission_share,
               updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at""",
        (hotel_slug, cur_s["corridor_tags"], Json(cur_s["travel_minutes"]), cur_s["stayover_ratio"],
         cur_s["credits_per_attendant"], cur_s["commission_share"], user_id, clock.now()), cur=cur)
    return get_settings(cur, hotel_slug)


# ─── Class preferences (w_class) ───


def class_keys(ev: dict) -> list[str]:
    keys = [ev["category"]]
    keys += [f"{ev['category']}:{v}" for v in (ev.get("venue_codes") or [])]
    return keys


def class_prefs(cur: Any, hotel_slug: str) -> dict[str, dict]:
    rows = clefs_db.fetch_all("SELECT * FROM dr_event_class_prefs WHERE hotel_slug = %s", (hotel_slug,), cur=cur)
    return {r["class_key"]: r for r in rows}


def class_weight(prefs: dict[str, dict], ev: dict) -> float:
    """The most cautious of the category's and the category:venue's weight:
    muting either silences the event; no feedback yet gives the prior 2/3."""
    ws = []
    for key in class_keys(ev):
        p = prefs.get(key)
        ws.append(w_class(p["useful"], p["dismissed"], p["muted"]) if p else w_class(0, 0))
    return min(ws) if ws else w_class(0, 0)


def bump_class(cur: Any, hotel_slug: str, class_key: str, *, useful: int = 0, dismissed: int = 0,
               muted: bool | None = None) -> None:
    clefs_db.execute(
        """INSERT INTO dr_event_class_prefs (hotel_slug, class_key, useful, dismissed, muted, updated_at)
           VALUES (%s, %s, %s, %s, COALESCE(%s, FALSE), %s)
           ON CONFLICT (hotel_slug, class_key) DO UPDATE SET useful = dr_event_class_prefs.useful + EXCLUDED.useful,
               dismissed = dr_event_class_prefs.dismissed + EXCLUDED.dismissed,
               muted = COALESCE(%s, dr_event_class_prefs.muted), updated_at = EXCLUDED.updated_at""",
        (hotel_slug, class_key[:120], max(0, useful), max(0, dismissed), muted, clock.now(), muted), cur=cur)


# ─── Baseline occupancy (2024-2025, minus core baseline_exclusions) ───


def excluded_ranges(cur: Any, hotel_slug: str) -> list[tuple[date, date]]:
    rows = clefs_db.fetch_all(
        "SELECT from_date, to_date FROM baseline_exclusions WHERE hotel_slug IS NULL OR hotel_slug = %s",
        (hotel_slug,), cur=cur)
    return [(r["from_date"], r["to_date"]) for r in rows]


def is_excluded(d: date, ranges: list[tuple[date, date]]) -> bool:
    return any(a <= d <= b for a, b in ranges)


def occupancy_history(cur: Any, hotel_slug: str, start: date, end: date, *,
                      ranges: list[tuple[date, date]] | None = None) -> dict[date, float]:
    """{date: occupancy ratio} from core occupancy_daily, skipping excluded
    days (the 2026 conflict months are seeded as a global exclusion)."""
    ranges = excluded_ranges(cur, hotel_slug) if ranges is None else ranges
    rows = clefs_db.fetch_all(
        "SELECT date, rooms_occupied, rooms_available FROM occupancy_daily "
        "WHERE hotel_slug = %s AND date BETWEEN %s AND %s AND rooms_available > 0",
        (hotel_slug, start, end), cur=cur)
    return {r["date"]: r["rooms_occupied"] / r["rooms_available"] for r in rows
            if not is_excluded(r["date"], ranges)}


def baseline_history(cur: Any, hotel_slug: str) -> dict[date, float]:
    y0, y1 = BASELINE_YEARS
    return occupancy_history(cur, hotel_slug, date(y0, 1, 1), date(y1, 12, 31))


def baseline_for(hist: dict[date, float], night: date, *, exclude: set[date] | None = None) -> dict | None:
    """Same ISO weekday, same month, over the baseline years: {mean, std, n};
    None when the hotel has no such night on record."""
    vals = [v for d, v in hist.items()
            if d.month == night.month and d.isoweekday() == night.isoweekday() and not (exclude and d in exclude)]
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0.0
    return {"mean": mean, "std": std, "n": len(vals)}


# ─── Segment fit (own mix vs market default) ───


def segment_fits(cur: Any, hotel_slug: str, today: date) -> dict[str, float]:
    """{MICE, LEISURE}: the hotel's own segment mix over the trailing year
    against the market default. Reservations tagged with a segment are used
    when at least MIN_SEGMENT_NIGHTS room nights carry one; otherwise the
    midweek vs Fri/Sat split of occupied nights stands in (against a flat
    5/7 vs 2/7). 1.0 without enough history."""
    lo, hi = SEGMENT_CLIP
    seg = clefs_db.fetch_one(
        "SELECT COALESCE(SUM(nights * rooms_count), 0) AS total, "
        "COALESCE(SUM(nights * rooms_count) FILTER (WHERE segment IN ('MICE', 'CORPORATE', 'GROUP')), 0) AS mice, "
        "COALESCE(SUM(nights * rooms_count) FILTER (WHERE segment = 'LEISURE'), 0) AS leisure "
        "FROM reservations WHERE hotel_slug = %s AND segment IS NOT NULL AND status NOT IN ('CANCELLED', 'NO_SHOW') "
        "AND check_in BETWEEN %s AND %s", (hotel_slug, today - timedelta(days=365), today - timedelta(days=1)), cur=cur)
    if seg and int(seg["total"]) >= MIN_SEGMENT_NIGHTS:
        t = int(seg["total"])
        return {"MICE": round(clip((int(seg["mice"]) / t) / MARKET_SEGMENT_SHARE["MICE"], lo, hi), 3),
                "LEISURE": round(clip((int(seg["leisure"]) / t) / MARKET_SEGMENT_SHARE["LEISURE"], lo, hi), 3)}
    rows = clefs_db.fetch_all(
        "SELECT date, rooms_occupied FROM occupancy_daily WHERE hotel_slug = %s AND date BETWEEN %s AND %s",
        (hotel_slug, today - timedelta(days=365), today - timedelta(days=1)), cur=cur)
    ranges = excluded_ranges(cur, hotel_slug)
    total = leisure = 0
    for r in rows:
        if is_excluded(r["date"], ranges):
            continue
        total += r["rooms_occupied"]
        if r["date"].isoweekday() in LEISURE_NIGHTS:
            leisure += r["rooms_occupied"]
    if total < MIN_SEGMENT_NIGHTS:
        return {"MICE": 1.0, "LEISURE": 1.0}
    return {"MICE": round(clip(((total - leisure) / total) / (5 / 7), lo, hi), 3),
            "LEISURE": round(clip((leisure / total) / (2 / 7), lo, hi), 3)}


def segment_fit_for(category: str, fits: dict[str, float]) -> float:
    if category in MICE:
        return fits.get("MICE", 1.0)
    if category in LEISURE:
        return fits.get("LEISURE", 1.0)
    return 1.0


# ─── Capacity ───


def capacity(cur: Any, hotel: Any) -> int:
    row = clefs_db.fetch_one("SELECT count(*) AS n FROM hotel_rooms WHERE hotel_slug = %s AND is_active",
                             (hotel.slug,), cur=cur)
    n = int(row["n"]) if row else 0
    return n or int(getattr(hotel, "rooms_count", 0) or 0)


# ─── Scoring one event for one hotel ───


class HotelModel:
    """Everything the score needs about the hotel, loaded once per run."""

    def __init__(self, cur: Any, hotel: Any, *, today: date):
        self.hotel = hotel
        self.today = today
        self.settings = get_settings(cur, hotel.slug)
        self.venues = {v["code"]: v for v in events.venues(cur)}
        self.prefs = class_prefs(cur, hotel.slug)
        self.fits = segment_fits(cur, hotel.slug, today)
        self.calib = {r["category"]: r for r in clefs_db.fetch_all(
            "SELECT * FROM dr_calibration WHERE hotel_slug = %s", (hotel.slug,), cur=cur)}
        self.capacity = capacity(cur, hotel)
        self.weekend = tuple(getattr(hotel, "weekend_days", None) or (6, 7))
        self.corridors = set(self.settings["corridor_tags"])

    def k_for(self, category: str) -> tuple[float, float, str]:
        """(k, fit, source): own calibration with enough events, else prior."""
        c = self.calib.get(category)
        if c and c["source"] == "own" and c["n_events"] >= MIN_OWN_EVENTS:
            return float(c["k"]), float(c["fit"]), "own"
        return K_PRIORS.get(category, 0.05), 0.3, "prior"


def _place(model: HotelModel, ev: dict) -> tuple[float | None, str | None, float | None, bool]:
    """(distance_km, travel_band, band_factor, corridor_match) over the
    event's venues (dual-venue: the closest distance, the best corridor)."""
    h = model.hotel
    has_hotel = getattr(h, "lat", None) is not None and getattr(h, "lng", None) is not None
    dists, bands, corridor = [], [], False
    for code in ev.get("venue_codes") or []:
        v = model.venues.get(code)
        if not v:
            continue
        if model.corridors & set(v.get("corridor_tags") or []):
            corridor = True
        if has_hotel and v.get("lat") is not None:
            dists.append(dedup.haversine_km(float(h.lat), float(h.lng), float(v["lat"]), float(v["lon"])))
        mins = model.settings["travel_minutes"].get(code)
        if mins:
            bands.append(travel_band(mins))
    if not dists and has_hotel and ev.get("lat") is not None and ev.get("lon") is not None:
        dists.append(dedup.haversine_km(float(h.lat), float(h.lng), float(ev["lat"]), float(ev["lon"])))
    best_band = max(bands, key=lambda b: b[1]) if bands else None
    return (min(dists) if dists else None, best_band[0] if best_band else None,
            best_band[1] if best_band else None, corridor)


def score_event(model: HotelModel, ev: dict) -> dict:
    """The relevance row for one event (not persisted)."""
    cat = ev["category"]
    nights = nights_affected(cat, ev["start_date"], ev["end_date"], uncertainty_days=ev.get("date_uncertainty_days") or 0,
                             moon_dependent=bool(ev.get("moon_dependent")), weekend=model.weekend)
    dist, band, band_factor, corridor_match = _place(model, ev)
    att = ev.get("attendance_est")
    dec = band_factor if band_factor is not None else decay(dist, cat, att)
    if cat == "CITYWIDE" and (ev.get("emirate") or "").lower() == (getattr(model.hotel, "emirate", "") or "").lower():
        dec = max(dec, 0.6)  # a city-wide event reaches every hotel in its emirate
    cor = CORRIDOR_BOOST if corridor_match else 1.0
    sc = scale(cat, att, ev.get("phq_rank"))
    wsum = sum(nights.values()) or 1.0
    dfit = sum(w * dow_fit(cat, d) for d, w in nights.items()) / wsum if nights else 1.0
    sfit = segment_fit_for(cat, model.fits)
    wc = class_weight(model.prefs, ev)
    status_factor = 0.0 if ev.get("status") in ("CANCELLED", "POSTPONED") else 1.0
    score = round(wc * dec * cor * sc * dfit * sfit * status_factor, 4)
    k, fit, ksrc = model.k_for(cat)
    uplift = round(k * score * sum(nights.values()) * model.capacity, 2)
    return {
        "event_id": str(ev["id"]), "class_key": cat, "distance_km": None if dist is None else round(dist, 2),
        "travel_band": band, "corridor_match": corridor_match, "score": score, "expected_rn_uplift": uplift,
        "nights": {d.isoformat(): round(w, 3) for d, w in nights.items()},
        "features": {"w_class": round(wc, 3), "decay": round(dec, 4), "corridor": cor, "scale": round(sc, 3),
                     "dow_fit": round(dfit, 3), "segment_fit": sfit, "k": k, "k_source": ksrc, "calibration_fit": fit,
                     "lambda_km": decay_lambda_km(cat, att), "status": ev.get("status")},
    }


# ─── Persisted relevance (the dr.relevance job and the calendar read it) ───

HORIZON_DAYS = 180


def visible_events(cur: Any, hotel_slug: str, start: date, end: date, *, include_cancelled: bool = True) -> list[dict]:
    """Live (unmerged) events the hotel sees: global rows plus its own.
    AI discoveries count only when corroborated or owner-confirmed (they
    are UNVERIFIED hotel-scoped rows until then)."""
    rows = clefs_db.fetch_all(
        "SELECT e.*, (SELECT count(DISTINCT s.source) FROM dr_event_sources s WHERE s.event_id = e.id) AS n_sources, "
        "EXISTS (SELECT 1 FROM dr_event_sources s WHERE s.event_id = e.id AND s.source <> 'AI_WEB') AS non_ai "
        "FROM dr_events e WHERE e.merged_into IS NULL AND (e.hotel_slug IS NULL OR e.hotel_slug = %s) "
        "AND e.end_date >= %s AND e.start_date <= %s AND (%s OR e.status <> 'CANCELLED') ORDER BY e.start_date",
        (hotel_slug, start - timedelta(days=2), end + timedelta(days=2), include_cancelled), cur=cur)
    return [r for r in rows if actionable(r)]


def actionable(ev: dict) -> bool:
    """AI-only rows drive nothing until another source or the owner vouches
    for them (02 spec 4.2)."""
    if ev.get("owner_confirmed_at"):
        return True
    if "non_ai" in ev:
        return bool(ev["non_ai"])
    return (ev.get("date_source") or "") != "AI_WEB"


def compute(cur: Any, hotel: Any, *, today: date, horizon_days: int = HORIZON_DAYS,
            model: HotelModel | None = None) -> dict:
    """Score every visible event touching [today, today + horizon] and
    upsert dr_event_relevance (dismissals survive a recompute)."""
    model = model or HotelModel(cur, hotel, today=today)
    evs = visible_events(cur, hotel.slug, today, today + timedelta(days=horizon_days))
    now = clock.now()
    for ev in evs:
        r = score_event(model, ev)
        clefs_db.execute(
            """INSERT INTO dr_event_relevance (hotel_slug, event_id, class_key, distance_km, travel_band, corridor_match,
                   score, expected_rn_uplift, nights, features, computed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (hotel_slug, event_id) DO UPDATE SET class_key = EXCLUDED.class_key,
                   distance_km = EXCLUDED.distance_km, travel_band = EXCLUDED.travel_band,
                   corridor_match = EXCLUDED.corridor_match, score = EXCLUDED.score,
                   expected_rn_uplift = EXCLUDED.expected_rn_uplift, nights = EXCLUDED.nights,
                   features = EXCLUDED.features, computed_at = EXCLUDED.computed_at""",
            (hotel.slug, r["event_id"], r["class_key"], r["distance_km"], r["travel_band"], r["corridor_match"],
             r["score"], r["expected_rn_uplift"], Json(r["nights"]), Json(r["features"]), now), cur=cur)
    return {"events": len(evs)}


def relevance_rows(cur: Any, hotel_slug: str, start: date, end: date, *, include_dismissed: bool = False) -> list[dict]:
    """Stored relevance rows whose nights touch [start, end], joined to the
    live event (a folded or cancelled event scores 0 on its next compute)."""
    rows = clefs_db.fetch_all(
        "SELECT r.*, e.title, e.category, e.start_date, e.end_date, e.status, e.verification, e.confidence, "
        "e.venue_codes, e.date_source, e.last_checked_at FROM dr_event_relevance r JOIN dr_events e ON e.id = r.event_id "
        "WHERE r.hotel_slug = %s AND e.merged_into IS NULL AND e.end_date >= %s AND e.start_date <= %s "
        "AND (%s OR NOT r.dismissed) ORDER BY r.score DESC",
        (hotel_slug, start - timedelta(days=HOLIDAY_REACH_DAYS), end + timedelta(days=HOLIDAY_REACH_DAYS),
         include_dismissed), cur=cur)
    return rows


HOLIDAY_REACH_DAYS = 6  # bridges, weekends and the Hijri widening reach past the event's own dates


def night_scores(rows: list[dict], night: date) -> list[tuple[dict, float]]:
    """[(relevance row, score x night weight)] for one night, strongest first."""
    key = night.isoformat()
    out = [(r, float(r["score"]) * float((r["nights"] or {}).get(key, 0))) for r in rows]
    return sorted([x for x in out if x[1] > 0], key=lambda x: -x[1])


def market_holiday_scores(cur: Any, hotel_slug: str, start: date, end: date) -> dict[date, dict]:
    """Source-market holiday contribution per night: sum over markets of
    share x kind weight (school 1.0, religious 0.7, public 0.5), one kind
    per market per night so a region's many countries count once."""
    per: dict[date, dict[str, tuple[float, str]]] = defaultdict(dict)
    for h in markets.market_holidays(cur, hotel_slug, start=start, end=end):
        if h["country_code"] == "AE":
            continue  # UAE holidays are calendar events already
        share = float(Decimal(h["market_share_pct"])) / 100
        w = KIND_WEIGHTS.get(h["kind"], 0.5)
        s, e = date.fromisoformat(h["start_date"]), date.fromisoformat(h["end_date"])
        u = int(h.get("uncertainty_days") or 0) if h.get("moon_dependent") else 0
        for d in _days(max(start, s - timedelta(days=u)), min(end, e + timedelta(days=u))):
            prev = per[d].get(h["market_code"])
            if prev is None or share * w > prev[0]:
                per[d][h["market_code"]] = (share * w, f"{h['name']} ({h['country_code']})")
    return {d: {"score": round(sum(v[0] for v in m.values()), 4), "holidays": sorted({v[1] for v in m.values()})}
            for d, m in per.items()}


# ─── Owner feedback: dismiss, mute, mark useful ───

DISMISS_SCOPES = ("event", "class", "venue")


def dismiss(cur: Any, hotel_slug: str, event_id: str, *, reason: str | None, scope: str = "event") -> dict:
    """Dismiss one event, or teach the model about its class: `class` counts
    a dismissal against the category (and mutes it when the reason is
    "mute"), `venue` against category:venue. Every scope hides the event."""
    if scope not in DISMISS_SCOPES:
        raise ClefsError(400, f"scope is one of {', '.join(DISMISS_SCOPES)}", "invalid_scope")
    ev = clefs_db.fetch_one(
        "SELECT * FROM dr_events WHERE id = %s AND (hotel_slug IS NULL OR hotel_slug = %s)",
        (event_id, hotel_slug), cur=cur) if events._is_uuid(event_id) else None
    if ev is None:
        raise ClefsError(404, "No such event", "event_not_found")
    reason = (reason or "").strip()[:300] or None
    mute = (reason or "").lower() == "mute"
    if scope == "class":
        bump_class(cur, hotel_slug, ev["category"], dismissed=1, muted=True if mute else None)
    elif scope == "venue":
        for v in ev["venue_codes"] or [None]:
            bump_class(cur, hotel_slug, f"{ev['category']}:{v}" if v else ev["category"], dismissed=1,
                       muted=True if mute else None)
    else:
        bump_class(cur, hotel_slug, ev["category"], dismissed=1)
    clefs_db.execute(
        """INSERT INTO dr_event_relevance (hotel_slug, event_id, class_key, dismissed, dismiss_reason, computed_at)
           VALUES (%s, %s, %s, TRUE, %s, %s)
           ON CONFLICT (hotel_slug, event_id) DO UPDATE SET dismissed = TRUE, dismiss_reason = EXCLUDED.dismiss_reason""",
        (hotel_slug, str(ev["id"]), ev["category"], reason, clock.now()), cur=cur)
    return {"event_id": str(ev["id"]), "dismissed": True, "scope": scope, "reason": reason,
            "class_key": ev["category"] if scope != "venue" else [f"{ev['category']}:{v}" for v in ev["venue_codes"] or []]}


def undismiss(cur: Any, hotel_slug: str, event_id: str) -> bool:
    return clefs_db.execute(
        "UPDATE dr_event_relevance SET dismissed = FALSE, dismiss_reason = NULL WHERE hotel_slug = %s AND event_id = %s",
        (hotel_slug, event_id), cur=cur) > 0


def mark_useful(cur: Any, hotel_slug: str, event_ids: list[str]) -> None:
    """An approved recommendation or a realised lift: the event's class
    earns a point (w_class rises toward 1)."""
    if not event_ids:
        return
    for ev in clefs_db.fetch_all("SELECT category FROM dr_events WHERE id = ANY(%s::uuid[])",
                                 ([str(e) for e in event_ids],), cur=cur):
        bump_class(cur, hotel_slug, ev["category"], useful=1)


def list_class_prefs(cur: Any, hotel_slug: str) -> list[dict]:
    return [{"class_key": r["class_key"], "useful": r["useful"], "dismissed": r["dismissed"], "muted": r["muted"],
             "weight": round(w_class(r["useful"], r["dismissed"], r["muted"]), 3)}
            for r in sorted(class_prefs(cur, hotel_slug).values(), key=lambda r: r["class_key"])]


def set_muted(cur: Any, hotel_slug: str, class_key: str, muted: bool) -> dict:
    bump_class(cur, hotel_slug, class_key, muted=muted)
    return next(p for p in list_class_prefs(cur, hotel_slug) if p["class_key"] == class_key[:120])


# ─── events.upcoming scorer (demand.api.upcoming_events uses the model) ───


def _scorer(cur: Any, hotel: Any, rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {}
    ids = [str(r["id"]) for r in rows]
    stored = clefs_db.fetch_all(
        "SELECT event_id, score, dismissed FROM dr_event_relevance WHERE hotel_slug = %s AND event_id = ANY(%s::uuid[])",
        (hotel.slug, ids), cur=cur)
    return {str(r["event_id"]): (0.0 if r["dismissed"] else float(r["score"])) for r in stored}


events.register_scorer(_scorer)


# ─── Calibration from the hotel's own pickup ───

CALIBRATION_FROM = date(2024, 1, 1)


def event_lift(hist: dict[date, float], base_hist: dict[date, float], nights: dict[date, float]) -> float | None:
    """Observed occupancy on the event's nights over the same-DOW,
    same-season baseline (its own nights left out), minus one."""
    occ, base = [], []
    own = set(nights)
    for d, w in nights.items():
        if w < 0.6 or d not in hist:
            continue  # a half-weight edge night says little about the event
        b = baseline_for(base_hist, d, exclude=own)
        if b is None or b["mean"] <= 0:
            continue
        occ.append(hist[d])
        base.append(b["mean"])
    if not occ:
        return None
    return sum(occ) / len(occ) / (sum(base) / len(base)) - 1


def calibrate(cur: Any, hotel: Any, *, today: date) -> dict:
    """k per category from past events (2024 onwards, excluded days
    skipped). Classes with fewer than MIN_OWN_EVENTS keep the prior."""
    model = HotelModel(cur, hotel, today=today)
    ranges = excluded_ranges(cur, hotel.slug)
    hist = occupancy_history(cur, hotel.slug, CALIBRATION_FROM, today - timedelta(days=1), ranges=ranges)
    base_hist = {d: v for d, v in hist.items() if d.year in BASELINE_YEARS}
    per: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for ev in visible_events(cur, hotel.slug, CALIBRATION_FROM, today - timedelta(days=1), include_cancelled=False):
        if ev["end_date"] >= today:
            continue
        r = score_event(model, ev)
        if r["score"] <= 0:
            continue
        nights = {date.fromisoformat(k): v for k, v in r["nights"].items()}
        lift = event_lift(hist, base_hist, nights)
        if lift is not None:
            per[ev["category"]].append((lift, r["score"], str(ev["id"])))
    out = {}
    now = clock.now()
    for cat in sorted(set(per) | set(K_PRIORS)):
        pts = per.get(cat, [])
        ssum = sum(p[1] for p in pts)
        if len(pts) >= MIN_OWN_EVENTS and ssum > 0:
            k = sum(p[0] for p in pts) / ssum
            resid = math.sqrt(sum((p[0] - k * p[1]) ** 2 for p in pts) / len(pts))
            spread = math.sqrt(sum(p[0] ** 2 for p in pts) / len(pts)) + 0.05
            fit, source = round(clip(1 - resid / spread, 0.0, 1.0), 3), "own"
        else:
            k, fit, source = K_PRIORS.get(cat, 0.05), 0.3, "prior"
        detail = [{"event_id": p[2], "lift": round(p[0], 4), "score": p[1]} for p in pts[:50]]
        clefs_db.execute(
            """INSERT INTO dr_calibration (hotel_slug, category, k, n_events, fit, source, detail, computed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (hotel_slug, category) DO UPDATE SET k = EXCLUDED.k, n_events = EXCLUDED.n_events,
                   fit = EXCLUDED.fit, source = EXCLUDED.source, detail = EXCLUDED.detail,
                   computed_at = EXCLUDED.computed_at""",
            (hotel.slug, cat, round(k, 4), len(pts), fit, source, Json(detail), now), cur=cur)
        out[cat] = {"k": round(k, 4), "n_events": len(pts), "fit": fit, "source": source}
    return out


def calibration(cur: Any, hotel_slug: str) -> list[dict]:
    rows = clefs_db.fetch_all("SELECT * FROM dr_calibration WHERE hotel_slug = %s ORDER BY category",
                              (hotel_slug,), cur=cur)
    return [{"category": r["category"], "k": float(r["k"]), "n_events": r["n_events"], "fit": float(r["fit"]),
             "source": r["source"], "confidence_cap": None if r["source"] == "own" else PRIOR_CONFIDENCE_CAP,
             "computed_at": r["computed_at"].isoformat()} for r in rows]


def uuid_list(v: Any) -> list[str]:
    """A uuid[] column as psycopg2 returns it without the UUID array
    adapter ("{a,b}" text) or as a list: always a list of strings."""
    if v is None:
        return []
    if isinstance(v, str):
        inner = v.strip("{}")
        return [x.strip('"') for x in inner.split(",") if x.strip('"')] if inner else []
    return [str(x) for x in v]
