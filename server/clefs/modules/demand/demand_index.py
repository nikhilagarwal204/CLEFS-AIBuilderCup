"""
Demand index per stay night (02 spec 4.3):

    DI = 100 x logistic(0.35 season_z + 0.30 event_z + 0.20 pace_z + 0.15 comp_z)

- season_z: the night's same-DOW, same-month baseline occupancy (2024-2025,
  core baseline_exclusions left out) against the hotel's whole baseline.
- event_z: min(EVENT_Z_CAP, EVENT_Z_SCALE x event_score), where event_score
  sums relevance x night weight over the night's events plus the
  source-market holiday contribution. The scale puts a strong nearby show
  (score about 1) at about +3 sigma, the same footing as the z inputs; the
  spec leaves the unit of event_score open, so this is the calibration
  knob (documented in README).
- pace_z = (OTB_d(L) - OTB_base(L)) / sigma at the same lead time L, where
  OTB_base is comparable past nights (same weekday, trailing 12 weeks, not
  excluded) photographed at lead L +/- 2 days by core otb_snapshots.
- comp_z = (median comp price_d / same-DOW median over d +/- 14 nights
  without event nights - 1) / sigma, plus 0.1 per 10% of comps sold out
  (a sold-out comp is a strong signal, never a zero price).

Coverage (0-1) says how many inputs had data; demand.api.demand_index is
None when nothing but an empty calendar stands behind a number.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.hotels import find_hotel
from clefs.core.time import local_date
from clefs.db import Json
from clefs.modules.demand import comp_rates, relevance as rel

WEIGHTS = {"season": 0.35, "event": 0.30, "pace": 0.20, "comp": 0.15}
EVENT_Z_SCALE = 3.0
EVENT_Z_CAP = 4.0
Z_CAP = 4.0
COMP_SIGMA_DEFAULT = 0.10
COMP_REF_DAYS = 14
PACE_LOOKBACK_WEEKS = 12
PACE_LEAD_TOLERANCE = 2
EVENT_NIGHT_MIN = 0.05  # event_score above this marks a night as an event night (comp reference)
MAX_DAYS = 400


def logistic(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def combine(season_z: float, event_z: float, pace_z: float, comp_z: float) -> int:
    x = (WEIGHTS["season"] * season_z + WEIGHTS["event"] * event_z + WEIGHTS["pace"] * pace_z
         + WEIGHTS["comp"] * comp_z)
    return int(round(100 * logistic(x)))


def _zcap(v: float) -> float:
    return max(-Z_CAP, min(Z_CAP, v))


# ─── Inputs for a window (loaded once, then per night) ───


class Inputs:
    def __init__(self, cur: Any, hotel: Any, start: date, end: date, *, today: date):
        slug = hotel.slug
        self.hotel, self.today = hotel, today
        self.capacity = rel.capacity(cur, hotel)
        self.ranges = rel.excluded_ranges(cur, slug)
        self.base_hist = rel.baseline_history(cur, slug)
        vals = list(self.base_hist.values())
        self.base_mean = sum(vals) / len(vals) if vals else None
        self.base_std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        # Events and holidays over the comp reference fortnight too, so a
        # reference night that is itself an event night is recognised.
        ref_lo, ref_hi = start - timedelta(days=COMP_REF_DAYS), end + timedelta(days=COMP_REF_DAYS)
        self.rel_rows = rel.relevance_rows(cur, slug, ref_lo, ref_hi)
        self.sm = rel.market_holiday_scores(cur, slug, ref_lo, ref_hi)
        # Latest OTB photograph, and past nights' photographs for the pace base.
        snap = clefs_db.fetch_one("SELECT max(snapshot_date) AS d FROM otb_snapshots WHERE hotel_slug = %s",
                                  (slug,), cur=cur)
        self.snapshot_date = snap["d"] if snap else None
        self.otb: dict[date, int] = {}
        if self.snapshot_date:
            for r in clefs_db.fetch_all(
                    "SELECT stay_date, rooms_otb FROM otb_snapshots WHERE hotel_slug = %s AND snapshot_date = %s "
                    "AND stay_date BETWEEN %s AND %s", (slug, self.snapshot_date, start, end), cur=cur):
                self.otb[r["stay_date"]] = r["rooms_otb"]
        lb = today - timedelta(weeks=PACE_LOOKBACK_WEEKS)
        self.past_snaps: dict[date, dict[int, int]] = defaultdict(dict)
        for r in clefs_db.fetch_all(
                "SELECT stay_date, snapshot_date, rooms_otb FROM otb_snapshots WHERE hotel_slug = %s "
                "AND stay_date BETWEEN %s AND %s", (slug, lb, today - timedelta(days=1)), cur=cur):
            if not rel.is_excluded(r["stay_date"], self.ranges):
                self.past_snaps[r["stay_date"]][(r["stay_date"] - r["snapshot_date"]).days] = r["rooms_otb"]
        # Comp prices for the window plus the reference fortnight either side.
        self.comp: dict[date, list[dict]] = defaultdict(list)
        for r in comp_rates.latest_rows(cur, slug, start - timedelta(days=COMP_REF_DAYS),
                                        end + timedelta(days=COMP_REF_DAYS)):
            self.comp[r["stay_date"]].append(r)
        self.event_nights = {d for d in _days(start - timedelta(days=COMP_REF_DAYS), end + timedelta(days=COMP_REF_DAYS))
                             if self.event_score(d)[0] >= EVENT_NIGHT_MIN}

    # ─── Components ───

    def event_score(self, night: date) -> tuple[float, list[tuple[dict, float]], dict | None]:
        scored = rel.night_scores(self.rel_rows, night)
        sm = self.sm.get(night)
        return sum(s for _, s in scored) + (sm["score"] if sm else 0.0), scored, sm

    def season(self, night: date) -> tuple[float, dict | None]:
        b = rel.baseline_for(self.base_hist, night)
        if b is None or self.base_mean is None:
            return 0.0, None
        sd = self.base_std or 0.05
        return _zcap((b["mean"] - self.base_mean) / sd), {"baseline_occ": round(b["mean"], 4), "n": b["n"]}

    def pace(self, night: date) -> tuple[float, dict | None]:
        if self.snapshot_date is None or night not in self.otb or not self.capacity:
            return 0.0, None
        lead = (night - self.snapshot_date).days
        base = []
        for d, by_lead in self.past_snaps.items():
            if d.isoweekday() != night.isoweekday():
                continue
            near = [by_lead[k] for k in range(lead - PACE_LEAD_TOLERANCE, lead + PACE_LEAD_TOLERANCE + 1) if k in by_lead]
            if near:
                base.append(by_lead[lead] if lead in by_lead else statistics.mean(near))
        if len(base) < 2:
            return 0.0, {"otb": self.otb[night], "lead": lead, "base": None}
        mean = statistics.mean(base)
        sd = max(statistics.pstdev(base), 0.05 * self.capacity, 1.0)
        gap = (self.otb[night] - mean) / self.capacity
        return _zcap((self.otb[night] - mean) / sd), {"otb": self.otb[night], "lead": lead, "base": round(mean, 2),
                                                     "n": len(base), "pace_gap": round(gap, 4)}

    def comp_z(self, night: date) -> tuple[float, dict | None]:
        rows = self.comp.get(night) or []
        summ = comp_rates.night_summary(rows) if rows else None
        if not summ or (summ["median_minor"] is None and not summ["sold_out"]):
            return 0.0, None
        ref = []
        for k in range(-COMP_REF_DAYS, COMP_REF_DAYS + 1):
            d = night + timedelta(days=k)
            if k == 0 or d.isoweekday() != night.isoweekday() or d in self.event_nights:
                continue
            s = comp_rates.night_summary(self.comp[d]) if self.comp.get(d) else None
            if s and s["median_minor"]:
                ref.append(s["median_minor"])
        sold_share = summ["sold_out"] / summ["comps"] if summ["comps"] else 0.0
        sold_bonus = 0.1 * (sold_share * 100 // 10)
        info = {"median_minor": summ["median_minor"], "sold_out": summ["sold_out"], "comps": summ["comps"],
                "ref_median_minor": None, "comp_move": None}
        if not ref or summ["median_minor"] is None:
            return _zcap(sold_bonus), info
        ref_med = statistics.median(ref)
        move = summ["median_minor"] / ref_med - 1
        sigma = max(statistics.pstdev([r / ref_med - 1 for r in ref]) if len(ref) > 1 else 0.0, COMP_SIGMA_DEFAULT)
        info.update(ref_median_minor=int(round(ref_med)), comp_move=round(move, 4))
        return _zcap(move / sigma + sold_bonus), info


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ─── One night, a window, and the stored calendar ───


def night(inp: Inputs, d: date) -> dict:
    """The index and its components for one night (not persisted)."""
    ev_score, scored, sm = inp.event_score(d)
    season_z, season = inp.season(d)
    pace_z, pace = inp.pace(d)
    comp_z, comp = inp.comp_z(d)
    event_z = min(EVENT_Z_CAP, EVENT_Z_SCALE * ev_score)
    has = {"season": season is not None, "pace": bool(pace and pace.get("base") is not None),
           "comp": comp is not None, "events": True}
    coverage = round(sum(has.values()) / len(has), 3)
    top = [r for r, _ in scored[:5]]
    return {
        "stay_date": d.isoformat(), "demand_index": combine(season_z, event_z, pace_z, comp_z),
        "coverage": coverage,
        "components": {
            "season_z": round(season_z, 3), "event_score": round(ev_score, 4), "event_z": round(event_z, 3),
            "pace_z": round(pace_z, 3), "comp_z": round(comp_z, 3), "season": season, "pace": pace, "comp": comp,
            "market_holidays": (sm or {}).get("holidays", []), "has": has,
            "events": [{"event_id": str(r["event_id"]), "title": r["title"], "category": r["category"],
                        "night_score": round(s, 4), "score": float(r["score"]),
                        "distance_km": None if r["distance_km"] is None else float(r["distance_km"]),
                        "confidence": float(r["confidence"]), "verification": r["verification"],
                        "status": r["status"], "k": (r["features"] or {}).get("k"),
                        "k_source": (r["features"] or {}).get("k_source"),
                        "calibration_fit": (r["features"] or {}).get("calibration_fit"),
                        "event_nights": sum(1 for w in (r["nights"] or {}).values() if w >= 0.6)}
                       for r, s in scored[:5]],
        },
        "top_event_ids": [str(r["event_id"]) for r in top],
        "signal_free": not (has["season"] or has["pace"] or has["comp"]) and ev_score == 0,
    }


def compute(cur: Any, hotel: Any, *, start: date, days: int, today: date | None = None) -> list[dict]:
    """Score [start, start + days) and upsert dr_demand_days."""
    today = today or local_date(hotel)
    days = max(1, min(int(days), MAX_DAYS))
    end = start + timedelta(days=days - 1)
    inp = Inputs(cur, hotel, start, end, today=today)
    now = clock.now()
    out = []
    for d in _days(start, end):
        n = night(inp, d)
        clefs_db.execute(
            """INSERT INTO dr_demand_days (hotel_slug, stay_date, demand_index, components, coverage, top_event_ids,
                   computed_at) VALUES (%s, %s, %s, %s, %s, %s::uuid[], %s)
               ON CONFLICT (hotel_slug, stay_date) DO UPDATE SET demand_index = EXCLUDED.demand_index,
                   components = EXCLUDED.components, coverage = EXCLUDED.coverage,
                   top_event_ids = EXCLUDED.top_event_ids, computed_at = EXCLUDED.computed_at""",
            (hotel.slug, d, n["demand_index"], Json({**n["components"], "signal_free": n["signal_free"]}),
             n["coverage"], n["top_event_ids"], now), cur=cur)
        out.append(n)
    return out


def stored(cur: Any, hotel_slug: str, start: date, end: date) -> dict[date, dict]:
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_demand_days WHERE hotel_slug = %s AND stay_date BETWEEN %s AND %s ORDER BY stay_date",
        (hotel_slug, start, end), cur=cur)
    return {r["stay_date"]: r for r in rows}


def day_out(r: dict) -> dict:
    comp = r["components"] or {}
    return {"stay_date": r["stay_date"].isoformat(), "demand_index": r["demand_index"],
            "coverage": float(r["coverage"]), "components": comp,
            "top_event_ids": rel.uuid_list(r["top_event_ids"]), "computed_at": r["computed_at"].isoformat()}


def value_for(cur: Any, hotel_slug: str, stay_date: date) -> int | None:
    """demand.api.demand_index: the stored index, else a live one-night
    computation (not stored); None when no input has data."""
    r = clefs_db.fetch_one("SELECT demand_index, components FROM dr_demand_days WHERE hotel_slug = %s "
                           "AND stay_date = %s", (hotel_slug, stay_date), cur=cur)
    if r is not None:
        return None if (r["components"] or {}).get("signal_free") else int(r["demand_index"])
    hotel = find_hotel(cur, hotel_slug)
    if hotel is None:
        return None
    n = night(Inputs(cur, hotel, stay_date, stay_date, today=local_date(hotel)), stay_date)
    return None if n["signal_free"] else n["demand_index"]
