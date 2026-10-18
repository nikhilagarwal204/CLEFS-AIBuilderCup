"""
Demo seed, growth part: Demand Radar, Visibility, Finance, then the Brief
(02 spec 9 "Demand Radar" row; 04 spec 7.4 Visibility and Finance; 00-PLAN
"Demo dataset").

What it seeds and why each path:

  Demand      the curated UAE calendar (dr.curated_sync), PredictHQ Events and
              Features payloads from fixtures/ through the adapters' own
              `results=` / `payload=` hooks (no vendor call; real public
              events only, since PredictHQ rows are global), the planted "Demo
              Summer Expo" at DWTC 18-20 Aug as the hotel's own event
              (core_stays already lifted its nights: the calibration pickup),
              two Lighthouse-style rate shops through the rate-shop import
              route (+60% on GITEX nights, +15% CABSAT, one comp sold out for
              GITEX; a sold-out cell cannot pass core ingest), a BAR calendar
              and min/max guardrails, then the registered engine jobs on the
              anchor (relevance, calibrate, demand index, recommend, graph
              actions, outcomes, weekly top 5, the GHC deadline).
  Visibility  Google Hotels details (with and without an official price, the
              parity plant 450 vs 412 on the Friday after the anchor, a 0.78%
              decoy), GBP Performance series (4 months of KPIs, keywords, the
              last 30 days of raw rows), and weekly AI radar batches whose
              answers mention the hotel, let competitors win, and plant 2 fact
              errors (check-in 12:00 vs KB 14:00; "no parking" vs KB free).
  Finance     finance.demo.seed_demo: 13 months of EN/AR P&L imported through
              core ingest, mapped and published (booked net in 2026-07, the
              labour anomaly in 2026-06), then its queued tasks.
  Brief       brief.generate for two weekly runs (with their verified-value
              reports) and the anchor's daily run, which ranks what every part
              raised into a top 5 in AED. Runs are stored, never delivered.

No vendor or model call is made: vendor calls are replayed from fixtures/
and clefs.ai is replaced for the part by a stand-in (demo_ai).

A section whose module is off (ctx.module_api returns None) or whose tables
are missing is skipped with a reason, so the part runs on any build. Plants
go to ctx.plants as {detector, key, klass, module, subjects, note}; no
database id is written, so the manifest bytes depend only on the seed and
the anchor.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from clefs.core import clock

# After money (50) and operations: the brief ranks everything they raised.
PART_ORDER = 70

FIXTURES = Path(__file__).with_name("fixtures")


# ─── Helpers ───


def fixture(name: str) -> Any:
    """A JSON fixture from fixtures/ (vendor-shaped payloads, never fetched)."""
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


@contextmanager
def at(when: datetime) -> Iterator[None]:
    """Run a block with the clock frozen at `when`, then restore the seed's
    anchor evening (the orchestrator froze it; jobs read clock.now())."""
    prev = clock.now()
    clock.freeze(when)
    try:
        yield
    finally:
        clock.freeze(prev)


def run_job(ctx: Any, name: str, *, when: datetime | None = None) -> Any:
    """A registered scheduled job's body in the seed's transaction, as the
    worker would run it at `when` (default: the anchor evening)."""
    from clefs import jobs

    j = next((j for j in jobs.registered_jobs() if j.name == name), None)
    if j is None:
        return {"skipped": f"job {name} is not registered"}
    with at(when or ctx.now):
        return j.fn(jobs.JobCtx(cur=ctx.cur, job=name, hotel_slug=ctx.slug if j.per_hotel else None,
                                scheduled_for=clock.now()))


def run_task(ctx: Any, name: str, payload: dict) -> Any:
    from clefs import jobs

    t = next((t for t in jobs.registered_tasks() if t.name == name), None)
    if t is None:
        return {"skipped": f"task {name} is not registered"}
    return t.fn(jobs.JobCtx(cur=ctx.cur, job=name, hotel_slug=ctx.slug, payload=payload), payload)


def drain(ctx: Any, names: tuple[str, ...], until: datetime, *, limit: int = 5000) -> int:
    """Run this hotel's queued tasks named `names` due by `until`, each at its
    run_at, oldest first (a task may queue the next one), as the worker
    would; each row is marked done so no worker replays it after the seed."""
    ran = 0
    while ran < limit:
        ctx.cur.execute(
            """SELECT id, name, payload, run_at FROM job_queue WHERE hotel_slug = %s AND status = 'queued'
               AND name = ANY(%s) AND run_at <= %s ORDER BY run_at, id LIMIT 1""",
            (ctx.slug, list(names), until))
        job = ctx.cur.fetchone()
        if job is None:
            break
        with at(job["run_at"]):
            result = run_task(ctx, job["name"], dict(job["payload"]))
            ctx.cur.execute("UPDATE job_queue SET status = 'done', attempts = attempts + 1, finished_at = %s, "
                            "result = %s WHERE id = %s", (clock.now(), json.dumps(result, default=str), job["id"]))
        ran += 1
    return ran


@contextmanager
def patched(target: Any, **attrs: Any) -> Iterator[None]:
    """Swap a module's vendor-facing functions for fixture replays while a
    block runs, then restore them. Only functions that make the vendor call
    are swapped; parsing, storage and signals run as shipped."""
    saved = {k: getattr(target, k) for k in attrs}
    for k, v in attrs.items():
        setattr(target, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(target, k, v)


# ─── AI: a deterministic stand-in ───
# The growth modules call clefs.ai for prose (why-this-matters), name and
# claim extraction. A seed must never reach a model: this backend answers the
# two extraction tasks from the text itself (so the fact checker runs as
# shipped) and fails every other task, which each module already handles by
# falling back to its template.

_CHECKIN = re.compile(r"check-in[^.]*?\b(\d{1,2}:\d{2})", re.I)
_NO_PARKING = re.compile(r"\b(no parking|does not offer parking)\b", re.I)
_FREE_PARKING = re.compile(r"\bfree (basement )?parking\b", re.I)


def _sentence(text: str, m: re.Match) -> str:
    start = text.rfind(".", 0, m.start()) + 1
    end = text.find(".", m.end())
    return text[start:end + 1 if end >= 0 else len(text)].strip()


def extract_facts(text: str) -> dict:
    """{facts: [{fact_key, value, quote}]} the way the extraction prompt asks
    (times HH:MM, parking yes/no/free/paid)."""
    out = []
    if m := _CHECKIN.search(text):
        out.append({"fact_key": "checkin_time", "value": m.group(1), "quote": _sentence(text, m)})
    if m := _NO_PARKING.search(text):
        out.append({"fact_key": "parking", "value": "no", "quote": _sentence(text, m)})
    elif m := _FREE_PARKING.search(text):
        out.append({"fact_key": "parking", "value": "free", "quote": _sentence(text, m)})
    return {"facts": out}


def demo_answer(req: Any) -> Any:
    from clefs import ai

    if req.task == "visibility.fact_extract":
        text = str(req.input or "").split("Text:\n", 1)[-1]
        return extract_facts(text)
    if req.task == "visibility.hotel_extract":
        return {"hotels": []}
    return ai.AIError(f"demo seed: no model call for {req.task}")


@contextmanager
def demo_ai() -> Iterator[None]:
    """Install the stand-in for the whole part; clefs.ai logs each call on its
    own connection, which fails its foreign key on a first seed (the hotel
    row is not committed yet), so its logger is quietened meanwhile."""
    from clefs import ai

    class DemoBackend(ai.FakeBackend):
        name = "demo_seed"

        def respond(self, req: Any) -> Any:
            self.push(demo_answer(req))
            return super().respond(req)

    prev = getattr(ai, "_backend_override", None)
    ai_log = logging.getLogger("clefs.ai")
    level = ai_log.level
    ai.set_backend(DemoBackend())
    ai_log.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        ai.set_backend(prev)
        ai_log.setLevel(level)


def plant(ctx: Any, *, detector: str, key: str, klass: str, module: str, subjects: dict | None = None,
          note: str = "", dedupe_key: str | None = None) -> None:
    """One manifest entry: detector is the signal kind (or the row kind for a
    non-signal plant), klass SIGNAL | ROW | DECOY; a signal plant names the
    dedupe_key its signal carries. No database id, so the bytes are stable."""
    p = {"detector": detector, "key": key, "klass": klass, "module": module, "subjects": subjects or {},
         "note": note}
    if dedupe_key:
        p["dedupe_key"] = dedupe_key
    ctx.plants.append(p)


# ─── Demand Radar: constants ───

# The planted calibration event: hotel-scoped (entered by the owner), because
# a synthetic event must never reach the global store other hotels read.
# core_stays already lifts occupancy on its nights (_calendar.EVENTS), which
# is the "visible pickup" dr.calibrate measures.
SUMMER_EXPO = {"title": "Demo Summer Expo", "category": "EXPO", "start": date(2026, 8, 18),
               "end": date(2026, 8, 20), "venue_codes": ["DWTC"], "attendance": 24000,
               "description": "Synthetic demo data: a trade expo at DWTC used to calibrate event pickup."}
CABSAT = (date(2026, 10, 5), date(2026, 10, 7))
GITEX = (date(2026, 12, 7), date(2026, 12, 11))
# Price level of each comp against our BAR (Trade Axis is the 4-star near
# DWTC, which sells out first for GITEX).
COMP_LEVEL = {"Creekview Inn": 0.94, "Gold Souk Boutique": 1.00, "Port Saeed Suites": 1.06,
              "Trade Axis Hotel": 1.28, "Barsha Metro Hotel": 0.90}
SOLD_OUT_COMP = "Trade Axis Hotel"
# (event, uplift at the earlier shop, uplift on the anchor): comps kept
# raising into the events, so the anchor's shop shows a comp move too.
UPLIFT = ((CABSAT, 0.08, 0.15), (GITEX, 0.40, 0.60))
BAR_PREMIUM = 1.10       # BAR sits above the net ADR the stays realise
ROOM_TYPE_BAR = {"STD_KING": 1.0, "STD_TWIN": 1.0, "FAM_2BR": 1.6}
GUARDRAILS = (
    {"room_type_code": "*", "floor": "220", "ceiling": "900", "max_step_pct": "15",
     "note": "Demo guardrail"},
    {"room_type_code": "*", "month": 12, "floor": "300", "ceiling": "1300", "max_step_pct": "20",
     "note": "December: GITEX and NYE"},
    {"room_type_code": "FAM_2BR", "floor": "380", "ceiling": "1600", "max_step_pct": "15",
     "note": "Family units"},
)
DEMAND_JOBS = ("dr.relevance", "dr.calibrate", "dr.demand_index", "dr.recommend", "dr.graph_actions",
               "dr.measure_outcomes", "dr.weekly_top5", "dr.ghc_deadline")


def _round5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def _in(d: date, span: tuple[date, date]) -> bool:
    return span[0] <= d <= span[1]


def base_adr(d: date) -> float:
    """The calendar's net ADR without any event multiplier: what the hotel
    and its comps charge on an ordinary night of that month and weekday."""
    from clefs.seed.demo_uae import _calendar

    return _calendar.MONTHS[d.month][1] * (1.04 if d.isoweekday() in (4, 5) else 1.0)


def bar_for(d: date) -> int:
    """The owner's BAR (AED) for the reference room type. GITEX and NYE are
    priced up already; CABSAT is still at an ordinary night's BAR (the gap
    dr.recommend should find against comps up 15%)."""
    from clefs.seed.demo_uae import _calendar

    adr = base_adr(d) if _in(d, CABSAT) else _calendar.target_adr(d)
    return _round5(adr * BAR_PREMIUM)


def _uplift(d: date, *, early: bool) -> float:
    for span, then, now in UPLIFT:
        if _in(d, span):
            return then if early else now
    return 0.0


def comp_grid_csv(ctx: Any, *, early: bool) -> str:
    """A Lighthouse-style wide rate-shop export (date, then one column per
    hotel) for the forward window. Deterministic noise per comp and night;
    the sold-out comp shows "Sold out" on every GITEX night."""
    rng = ctx.rng(f"growth:comp_grid:{int(early)}")
    names = list(COMP_LEVEL)
    lines = ["Rate shop export,Lighthouse,,,,", ",".join(["Date", "Day", *names])]
    d = ctx.anchor
    while d <= ctx.forward_end:
        base = base_adr(d) * BAR_PREMIUM
        cells = []
        for n in names:
            if n == SOLD_OUT_COMP and _in(d, GITEX) and not early:
                cells.append("Sold out")
                continue
            price = base * COMP_LEVEL[n] * (1 + _uplift(d, early=early)) * rng.uniform(0.97, 1.03)
            cells.append(f"{_round5(price)}")
        lines.append(",".join([d.isoformat(), d.strftime("%a"), *cells]))
        d += timedelta(days=1)
    return "\n".join(lines) + "\n"


# ─── Demand Radar: seeding ───
# Foundation code never imports a module's internals: Demand's api.py hands
# the seed its demo_seed_door(), the PredictHQ adapters (with their fixture
# inputs results= / payload=) and the functions the console routes call
# (manual events, the rate-shop import, BAR and guardrail PUTs). Everything
# derived (scores, calibration, the index, recommendations) comes from the
# registered jobs.


def demand_door(ctx: Any) -> Any:
    """demand.api.demo_seed_door(); seed_demand has already checked that
    the module is on for this hotel."""
    return ctx.module_api("demand").demo_seed_door()


def demand_events(ctx: Any, hotel: Any) -> dict:
    door = demand_door(ctx)
    out = {"curated": run_job(ctx, "dr.curated_sync", when=ctx.at(ctx.anchor, 4, 30))}
    with at(ctx.at(ctx.anchor, 5, 0)):
        out["phq_events"] = door.sync_phq_events(ctx.cur, hotel, results=fixture("phq_events.json")["results"])
    with at(ctx.at(ctx.anchor, 5, 30)):
        out["phq_features"] = door.sync_phq_features(ctx.cur, hotel, payload=fixture("phq_features.json"))
    # Entered in June, before the expo, as an owner would; the stays part
    # already filled its nights.
    with at(ctx.at(date(2026, 6, 10), 11)):
        ev = door.create_manual_event(ctx.cur, ctx.slug, dict(SUMMER_EXPO))
    ctx.ids["summer_expo_event_id"] = str(ev["id"])
    out["summer_expo"] = {"created": ev["created"], "matched": ev["matched"]}
    return out


def demand_rates(ctx: Any, hotel: Any) -> dict:
    """Two rate shops (a week ago and the anchor morning), then the BAR
    calendar and guardrails, each through the function its route calls."""
    door = demand_door(ctx)
    out: dict[str, Any] = {}
    for early, when in ((True, ctx.at(ctx.anchor - timedelta(days=7), 7)), (False, ctx.at(ctx.anchor, 7))):
        with at(when):
            res = door.import_rate_csv(ctx.cur, hotel, comp_grid_csv(ctx, early=early), source_hint="lighthouse")
        out["shop_early" if early else "shop_anchor"] = {k: res[k] for k in ("rates", "sold_out", "nights",
                                                                           "unmatched")}
    rows = []
    d = ctx.anchor
    while d <= ctx.forward_end:
        for code, mult in ROOM_TYPE_BAR.items():
            rows.append({"stay_date": d.isoformat(), "room_type_code": code, "bar": str(_round5(bar_for(d) * mult))})
        d += timedelta(days=1)
    owner = (ctx.ids.get("users") or {}).get("revenue")
    with at(ctx.at(ctx.anchor, 8)):
        out["bar"] = door.put_bar_rows(ctx.cur, hotel, rows, source="CSV", user_id=owner)
        out["guardrails"] = len(door.upsert_guardrails(ctx.cur, hotel, [dict(g) for g in GUARDRAILS],
                                                       user_id=owner))
    return out


def seed_demand(ctx: Any) -> dict:
    if ctx.module_api("demand") is None or not ctx.table_exists("dr_events"):
        return {"skipped": "demand is not enabled"}
    if ctx.anchor <= SUMMER_EXPO["end"]:
        return {"skipped": f"anchor on or before {SUMMER_EXPO['end'].isoformat()}"}
    from clefs import registry

    registry.load_module("demand")  # jobs, signal kinds, action types, the ICS/COMP_RATES handlers
    hotel = ctx.hotel
    out = {"events": demand_events(ctx, hotel), "rates": demand_rates(ctx, hotel)}
    # The engine's night: each job at its own slot on the anchor day (they
    # read local_date, so every one sees the anchor as today).
    slots = {"dr.relevance": (0, 5), "dr.calibrate": (0, 2), "dr.demand_index": (0, 15), "dr.recommend": (0, 30),
             "dr.graph_actions": (0, 45), "dr.measure_outcomes": (5, 0), "dr.weekly_top5": (6, 0),
             "dr.ghc_deadline": (7, 0)}
    jobs_out = {}
    for name in DEMAND_JOBS:
        h, m = slots[name]
        # The engine runs after the anchor morning's rate shop: the index and
        # recommendations must see it, so the slots are replayed at 09:xx.
        jobs_out[name] = run_job(ctx, name, when=ctx.at(ctx.anchor, 9 + h, m))
    out["jobs"] = {k: _brief_result(v) for k, v in jobs_out.items()}
    demand_plants(ctx)
    return out


def _brief_result(v: Any) -> Any:
    """A job's report trimmed for the CLI summary (lists become counts)."""
    if isinstance(v, dict):
        return {k: (len(x) if isinstance(x, (list, tuple)) else x) for k, x in v.items()
                if not isinstance(x, dict)}
    return v


def demand_plants(ctx: Any) -> None:
    plant(ctx, detector="dr.event", key="demo-summer-expo-2026", klass="ROW", module="demand",
          subjects={"title": SUMMER_EXPO["title"], "venue": "DWTC", "start": "2026-08-18", "end": "2026-08-20"},
          note="hotel-scoped calibration event with visible pickup")
    # The lift is only meaningful at full scale: a scaled-down test seed fills
    # a tenth of the rooms against full-size 2024-25 baselines.
    plant(ctx, detector="dr.calibration", key="EXPO", klass="ROW", module="demand",
          subjects={"category": "EXPO", "event": "demo-summer-expo-2026",
                    "positive_lift": ctx.scale >= 0.5},
          note="the expo is a calibration point; its nights show a lift over the baseline")
    plant(ctx, detector="dr.comp_sold_out", key="gitex-2026:demo-trade-axis", klass="ROW", module="demand",
          subjects={"comp": SOLD_OUT_COMP, "start": GITEX[0].isoformat(), "end": GITEX[1].isoformat()})
    plant(ctx, detector="dr.comp_uplift", key="gitex-2026", klass="ROW", module="demand",
          subjects={"start": GITEX[0].isoformat(), "end": GITEX[1].isoformat(), "uplift_pct": 60})
    plant(ctx, detector="dr.comp_uplift", key="cabsat-2026", klass="ROW", module="demand",
          subjects={"start": CABSAT[0].isoformat(), "end": CABSAT[1].isoformat(), "uplift_pct": 15})
    plant(ctx, detector="dr.recommendation", key="cabsat-2026", klass="ROW", module="demand",
          subjects={"start": CABSAT[0].isoformat(), "end": CABSAT[1].isoformat(), "direction": "up"},
          note="BAR left at an ordinary night while comps are up 15%")


# ─── Visibility: profile and Google Hotels ───
# Vendor calls are replayed from fixtures/ by swapping the one function per
# surface that makes the call (google_hotels.fetch_details / search_rank,
# gbp.ready / gbp.call, the AI radar's engine table). The registered jobs
# then run as shipped: parsing, parity analysis, exposure, Profit Guard rate
# evidence, signals, KPIs, cells and the fact checker.

VIS_PROFILE = {
    "website_url": "https://alriggacrest.example.com",
    "booking_engine_host": "book.alriggacrest.example.com",
    "brand_aliases": ["Al Rigga Crest", "Al Rigga Crest Hotel", "الرقة كرست"],
    "rank_keywords": ["hotels in deira", "hotel near al rigga metro", "family hotel deira"],
    "area": "Deira",
    "landmarks": ["Al Rigga Metro", "Deira City Centre"],
    "engines_enabled": ["gemini_web_search", "serpapi_ai_mode"],
    "serpapi_enabled": True,
    "gbp_location": "locations/demo-al-rigga-crest",
    "monthly_budget_minor": 150_000,
    "budget_currency": "AED",
}
PARITY_DATE = date(2026, 10, 2)      # the Friday after the anchor: 450 official vs Booking.com 412
DECOY_DATE = date(2026, 10, 26)      # 512 vs 508 = 0.78%, inside the 1% tolerance
NO_OFFICIAL_DATE = date(2026, 10, 27)


def vis_door(ctx: Any) -> Any:
    """visibility.api.demo_seed_door(); seed_visibility has already checked
    that the module is on for this hotel."""
    return ctx.module_api("visibility").demo_seed_door()


def gh_replay(ctx: Any) -> dict:
    """fetch_details / search_rank stand-ins over fixtures/google_hotels.json."""
    gh = vis_door(ctx).google_hotels
    fx = fixture("google_hotels.json")

    def fetch_details(hotel: Any, q: str, token: str, check_in: date, check_out: date, **_kw: Any) -> dict:
        return fx["details"].get(check_in.isoformat()) or {"property_token": token, "prices": []}

    def search_rank(hotel: Any, q: str, check_in: date, check_out: date, token: str | None) -> dict:
        organic, sponsored = gh.organic_properties(fx["area_page"])
        return {"organic_rank": gh.rank_in(organic, token), "organic_total": len(organic),
                "sponsored_count": sponsored, "pages_fetched": 1, "cache_key": None}

    return {"fetch_details": fetch_details, "search_rank": search_rank}


def vis_google(ctx: Any) -> dict:
    """Two mornings of visibility.google_hotels_daily: the day before the
    anchor (Friday pair 2 Oct, far pair 26 Oct: the decoy) and the anchor
    (2 Oct again, far pair 27 Oct: no official price)."""
    gh = vis_door(ctx).google_hotels
    out = {}
    with patched(gh, **gh_replay(ctx)):
        for d in (ctx.anchor - timedelta(days=1), ctx.anchor):
            out[d.isoformat()] = run_job(ctx, "visibility.google_hotels_daily", when=ctx.at(d, 5, 30))
    return out


# ─── Visibility: GBP Performance ───


def gbp_body(fx: dict, start: date, end: date) -> dict:
    """A fetchMultiDailyMetricsTimeSeries response for [start, end] from the
    fixture's levels (deterministic: no noise beyond weekday and month)."""
    series = []
    for metric, mean in fx["daily_means"].items():
        values = []
        d = start
        while d <= end:
            v = mean * fx["weekday_factor"][str(d.isoweekday())] * fx["month_factor"][str(d.month)] \
                * fx["year_growth"].get(str(d.year), 1.0)
            dv = {"date": {"year": d.year, "month": d.month, "day": d.day}}
            if round(v):
                dv["value"] = str(round(v))  # the API omits zero values
            values.append(dv)
            d += timedelta(days=1)
        series.append({"dailyMetric": metric, "timeSeries": {"datedValues": values}})
    return {"multiDailyMetricTimeSeries": [{"dailyMetricTimeSeries": series}]}


def gbp_replay(ctx: Any) -> dict:
    """gbp.connection_state / ready / call stand-ins: a connected location
    (no OAuth grant exists for a demo hotel), and the two Performance
    endpoints answered from fixtures/gbp_performance.json."""
    fx = fixture("gbp_performance.json")

    def connection_state(cur: Any, hotel_slug: str) -> dict:
        return {"status": "connected", "qpm": 0, "location": fx["location"], "message": None,
                "connection": {"status": "active", "connected_at": None}}

    def ready(cur: Any, hotel_slug: str) -> tuple[bool, dict]:
        return True, connection_state(cur, hotel_slug)

    def call(cur: Any, hotel_slug: str, method: str, url: str, **kw: Any) -> dict:
        params = dict(kw.get("params") or [])
        if url.endswith(":fetchMultiDailyMetricsTimeSeries"):
            s = date(params["dailyRange.startDate.year"], params["dailyRange.startDate.month"],
                     params["dailyRange.startDate.day"])
            e = date(params["dailyRange.endDate.year"], params["dailyRange.endDate.month"],
                     params["dailyRange.endDate.day"])
            return gbp_body(fx, s, e)
        if "/searchkeywords/" in url:
            return {"searchKeywordsCounts": fx["keywords"]}
        return {}

    return {"connection_state": connection_state, "ready": ready, "call": call}


def vis_gbp(ctx: Any) -> dict:
    """KPIs for May-Aug 2026 (the job on the 3rd of each following month),
    August keywords (the 5th of September), and raw daily rows for the last
    30 days only (three 10-day re-fetches), so gbp_raw_expire deletes
    nothing on day one."""
    gbp = vis_door(ctx).gbp
    out: dict[str, Any] = {"kpis": [], "daily": []}
    with patched(gbp, **gbp_replay(ctx)):
        for month in (6, 7, 8, 9):
            when = ctx.at(date(2026, month, 3), 6, 30)
            if when < ctx.now:
                out["kpis"].append(run_job(ctx, "visibility.gbp_kpis_monthly", when=when))
        if ctx.at(date(2026, 9, 5), 6) < ctx.now:
            out["keywords"] = run_job(ctx, "visibility.gbp_keywords_monthly", when=ctx.at(date(2026, 9, 5), 6))
        for back in (20, 10, 0):
            out["daily"].append(run_job(ctx, "visibility.gbp_daily",
                                        when=ctx.at(ctx.anchor - timedelta(days=back), 6)))
    return out


# ─── Visibility: AI radar ───
# Weekly batches run as shipped (prompts refresh, ai_radar_weekly plans the
# batch, ai_batch_step and ai_weekly_rollup drain from job_queue); only the
# engine calls are replayed. Answers rotate deterministically per prompt, so
# discovery prompts mention the hotel ~40% of the time (competitors win the
# rest) and fact prompts contradict the KB some weeks: check-in 12:00 (KB
# 14:00) and "no parking" (KB free parking).

AI_ENGINES = ("gemini_web_search", "serpapi_ai_mode")
AI_TASKS = ("visibility.ai_batch_step", "visibility.ai_weekly_rollup")
# Brand answers cycle good, good, wrong check-in, no parking; fact answers
# are wrong one run in four.
BRAND_CYCLE = (0, 0, 1, 2)


def gemini_body(ans: dict, model: str) -> dict:
    """A normalised Gemini body (ai.responses_raw with Google Search grounding;
    parsers/gemini.py)."""
    text, src = ans["text"], ans.get("sources") or []
    notes = [{"type": "url_citation", "url": u, "title": t, "start_index": 0, "end_index": len(text)}
             for u, t in src[:1]]
    return {"id": f"resp_demo_{ans['key']}", "model": model, "provider": "google", "output_text": text,
            "output": [
                {"type": "web_search_call", "status": "completed",
                 "action": {"type": "search", "query": ans.get("query") or ""}},
                {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": text, "annotations": notes}]}],
            "grounding": {"web_search_queries": [ans["query"]] if ans.get("query") else [],
                          "sources": [{"url": u, "title": t, "domain": u.split("/")[2] if "//" in u else u}
                                      for u, t in src]}}


def ai_mode_body(ans: dict) -> dict:
    """A SerpApi google_ai_mode body (parsers/ai_mode.py): one text block per
    line, each citing the first reference."""
    src = ans.get("sources") or []
    blocks = [{"type": "paragraph", "snippet": line, "reference_indexes": [0] if src else []}
              for line in ans["text"].splitlines() if line.strip()]
    return {"search_metadata": {"status": "Success"}, "text_blocks": blocks,
            "references": [{"index": i, "link": u, "title": t} for i, (u, t) in enumerate(src)]}


class AiReplay:
    """The engine table's stand-in. State (week, per-prompt counters) makes
    the rotation deterministic; calls run inline (CONCURRENCY 1), in id
    order, so the same seed always gives the same answers."""

    def __init__(self, fx: dict, kinds: dict[str, tuple[str, str | None]], ai_radar: Any) -> None:
        self.fx, self.kinds, self.ai_radar = fx, kinds, ai_radar
        self.week = 0
        self.model = fx["gemini_models"][0]
        self.counts: dict[tuple[str, str], int] = {}

    def answer(self, engine: str, prompt: dict) -> dict:
        import zlib

        text = prompt["text"]
        n = self.counts[(engine, text)] = self.counts.get((engine, text), -1) + 1
        intent, fact_key = self.kinds.get(text, ("generic", None))
        turn = n + self.week + (1 if engine == "serpapi_ai_mode" else 0)
        if intent == "fact_check":
            pool = self.fx["facts"].get(fact_key or "") or self.fx["facts"]["star_rating"]
            return pool[1] if len(pool) > 1 and turn % 4 == 1 else pool[0]
        if intent == "brand":
            return self.fx["brand_en"][BRAND_CYCLE[turn % len(BRAND_CYCLE)]]
        pool = self.fx["discovery_ar"] if prompt.get("language") == "ar" else self.fx["discovery_en"]
        return pool[(turn + zlib.crc32(text.encode("utf-8"))) % len(pool)]

    def engines(self) -> dict:
        ai_radar = self.ai_radar

        def gemini(prompt: dict, locale: dict, hotel_slug: str | None, purpose: str) -> tuple[dict, int | None]:
            return gemini_body(self.answer("gemini_web_search", prompt), self.model), 12_000

        def ai_mode(prompt: dict, locale: dict, hotel_slug: str | None, purpose: str) -> tuple[dict, int | None]:
            return ai_mode_body(self.answer("serpapi_ai_mode", prompt)), None

        calls = {"gemini_web_search": gemini, "serpapi_ai_mode": ai_mode}
        return {k: (ai_radar.Engine(e.key, e.label, e.provider, e.localised, calls[k]) if k in calls else e)
                for k, e in ai_radar.ENGINES.items()}


def vis_ai(ctx: Any) -> dict:
    door = vis_door(ctx)
    ai_radar, costs = door.ai_radar, door.costs
    fx = fixture("ai_answers.json")
    weeks = 8 if ctx.scale >= 0.5 else 3
    last_monday = ctx.anchor - timedelta(days=ctx.anchor.weekday() or 7)
    mondays = [last_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    change = min(int(fx.get("model_change_week") or 5) - 1, weeks - 1)
    out: dict[str, Any] = {"prompts": run_job(ctx, "visibility.ai_prompts_refresh",
                                              when=ctx.at(mondays[0] - timedelta(days=1), 20))}
    kinds = {p["text"]: (p["intent"], p.get("fact_key")) for p in door.active_prompts(ctx.cur, ctx.slug)}
    replay = AiReplay(fx, kinds, ai_radar)
    batches = []
    with patched(ai_radar, ENGINES=replay.engines(), CONCURRENCY=1, STEP_MAX_RUNS=48,
                 enabled_engines=lambda profile, snapshot=False: list(AI_ENGINES)), \
            patched(costs, serpapi_budget_ok=lambda cur, searches=1: True):
        for i, monday in enumerate(mondays):
            replay.week = i
            replay.model = fx["gemini_models"][1 if i >= change else 0]
            planned = run_job(ctx, "visibility.ai_radar_weekly", when=ctx.at(monday, 1))
            ran = drain(ctx, AI_TASKS, ctx.at(monday, 6))
            batches.append({"week": monday.isoformat(), "planned": planned.get("planned"),
                            "skipped": planned.get("skipped"), "tasks": ran})
    out["batches"] = batches
    ctx.cur.execute("SELECT fact_key, claimed_value, occurrences FROM vis_ai_facts WHERE hotel_slug = %s "
                    "ORDER BY fact_key", (ctx.slug,))
    out["facts"] = [dict(r) for r in ctx.cur.fetchall()]
    return out


# ─── Visibility: entry ───


def seed_visibility(ctx: Any) -> dict:
    if ctx.module_api("visibility") is None or not ctx.table_exists("vis_profile"):
        return {"skipped": "visibility is not enabled"}
    from clefs import registry

    registry.load_module("visibility")
    with at(ctx.at(ctx.anchor - timedelta(days=60), 10)):
        vis_door(ctx).update_profile(ctx.cur, ctx.slug, {**VIS_PROFILE, "gh_property_token":
                                                         fixture("google_hotels.json")["property_token"]},
                                     user_id=(ctx.ids.get("users") or {}).get("owner"))
    out = {"competitors": run_job(ctx, "visibility.competitors_sync", when=ctx.at(ctx.anchor, 3, 30)),
           "google_hotels": vis_google(ctx), "gbp": vis_gbp(ctx), "ai": vis_ai(ctx)}
    visibility_plants(ctx)
    return out


def visibility_plants(ctx: Any) -> None:
    plant(ctx, detector="vis.parity_gap_google", key=PARITY_DATE.isoformat(), klass="SIGNAL", module="visibility",
          subjects={"official": 450, "lowest": 412, "source": "Booking.com", "gap": 38, "currency": "AED"},
          note="official site price above Booking.com on Google Hotels",
          dedupe_key=f"vis.parity_gap_google:{PARITY_DATE.isoformat()}")
    plant(ctx, detector="vis.parity_gap_google", key=DECOY_DATE.isoformat(), klass="DECOY", module="visibility",
          subjects={"official": 512, "lowest": 508, "gap_pct": "0.78"}, note="inside the 1% tolerance",
          dedupe_key=f"vis.parity_gap_google:{DECOY_DATE.isoformat()}")
    plant(ctx, detector="vis.official_price_missing", key=NO_OFFICIAL_DATE.isoformat(), klass="SIGNAL",
          module="visibility", subjects={"stay_date": NO_OFFICIAL_DATE.isoformat()},
          dedupe_key="vis.official_price_missing")
    for fact_key, claimed, truth, err in (("checkin_time", "12:00", "14:00", "wrong_value"),
                                          ("parking", "no", "free", "false_absent")):
        plant(ctx, detector="vis.ai_fact_error", key=f"{fact_key}:{claimed}", klass="SIGNAL", module="visibility",
              subjects={"fact_key": fact_key, "claimed": claimed, "kb": truth, "error_type": err},
              note="an AI answer contradicts the concierge KB")
    plant(ctx, detector="vis.ai_mention", key="self", klass="ROW", module="visibility",
          subjects={"engines": list(AI_ENGINES), "intents": ["generic", "persona", "event"]},
          note="discovery answers name the hotel about 40% of the time")
    plant(ctx, detector="vis.ai_competitor_win", key="comp_set", klass="ROW", module="visibility",
          subjects={"comps": sorted(COMP_LEVEL)}, note="answers that name comps and not the hotel")
    plant(ctx, detector="vis.ai_model_change", key="gemini_web_search", klass="ROW", module="visibility",
          subjects={"from": "gemini-2.5-flash", "to": "gemini-2.5-flash-preview-09-2025"},
          note="a new baseline epoch")


# ─── Finance ───
# finance.demo.seed_demo is the module's own demo door (CONTRACTS: module
# demo seeds, owned here): 13 months of EN/AR P&L as one CSV through core
# ingest (kind PL_STATEMENT), mapped, checked and published in order, then a
# registered baseline. The finance user uploads it on 12 Sep, after August
# closed; the tasks publishing queues (mapping proposals, the anomaly scan)
# then drain as the worker would.

FIN_UPLOAD_DAY = date(2026, 9, 12)
FIN_TASKS = ("finance.map_propose", "finance.anomaly_scan", "finance.register_baseline")
LABOUR_MONTH, BOOKED_NET_MONTH = date(2026, 6, 1), date(2026, 7, 1)


@contextmanager
def memo_channel_economics(pg_api: Any | None) -> Iterator[None]:
    """Publishing 13 months asks Profit Guard for the same month's channel
    economics five times per month (stat inputs, checks, the headline), each
    a walk over every stay's taxes and fees: about 30 s of a full seed. The
    stays cannot change while finance seeds, so one answer per (hotel, start,
    end) is exact; callers get a copy, so none can alter another's rows.
    pg_api is profit_guard's api.py, loaded by name (the object finance
    itself calls); None when Profit Guard is off, and then nothing to memo."""
    import copy

    if pg_api is None:
        yield
        return
    real = pg_api.channel_economics
    seen: dict[tuple, Any] = {}

    def channel_economics(cur: Any, hotel_slug: str, *, start: date, end: date) -> list[dict]:
        key = (hotel_slug, start, end)
        if key not in seen:
            seen[key] = real(cur, hotel_slug, start=start, end=end)
        return copy.deepcopy(seen[key])

    with patched(pg_api, channel_economics=channel_economics):
        yield


def seed_finance(ctx: Any) -> dict:
    if ctx.module_api("finance") is None or not ctx.table_exists("fin_import_batches"):
        return {"skipped": "finance is not enabled"}
    if ctx.anchor <= FIN_UPLOAD_DAY:
        return {"skipped": f"anchor on or before {FIN_UPLOAD_DAY.isoformat()}"}
    from clefs import registry

    registry.load_module("finance")
    upload = ctx.at(FIN_UPLOAD_DAY, 10)
    seed_demo = ctx.module_api("finance").demo_seed_door().seed_demo
    with at(upload), memo_channel_economics(ctx.module_api("profit_guard")):
        res = seed_demo(ctx.cur, ctx.slug, ctx.rng("growth:finance"), ctx.anchor)
    out = {**res.get("finance", {}), "tasks": drain(ctx, FIN_TASKS, ctx.now)}
    # The Monday rollup (weekly NRevPAR from stays) on the anchor's week.
    monday = ctx.anchor - timedelta(days=ctx.anchor.weekday())
    out["weekly_rollup"] = _brief_result(run_job(ctx, "finance.weekly_rollup", when=ctx.at(monday, 5)))
    ctx.cur.execute("SELECT period_month, kind FROM fin_anomalies WHERE hotel_slug = %s ORDER BY 1, 2",
                    (ctx.slug,))
    out["anomalies"] = [f"{r['period_month']:%Y-%m}:{r['kind']}" for r in ctx.cur.fetchall()]
    finance_plants(ctx)
    return out


def finance_plants(ctx: Any) -> None:
    plant(ctx, detector="fin.anomaly", key=f"labour_ratio:{LABOUR_MONTH:%Y-%m}", klass="SIGNAL", module="finance",
          subjects={"month": LABOUR_MONTH.isoformat(), "tor": 450_000, "payroll": 142_000, "labour_pct": "31.56"},
          note="labour at least 4 pp above the trailing 6-month mean")
    plant(ctx, detector="fin.booked_net", key=f"{BOOKED_NET_MONTH:%Y-%m}", klass="SIGNAL", module="finance",
          subjects={"month": BOOKED_NET_MONTH.isoformat(), "rooms_revenue": 355_900, "commission_line": 0,
                    "pms_rooms_revenue": 400_000, "expected_commission": 44_100},
          dedupe_key=f"fin.booked_net:{BOOKED_NET_MONTH.isoformat()}", note="OTA revenue booked net")
    plant(ctx, detector="fin.anomaly", key="other-months", klass="DECOY", module="finance",
          subjects={"months": "2025-08..2026-08 except 2026-06 and 2026-07"},
          note="generated months stay inside the constraints, so nothing else fires")


# ─── Brief ───
# The runs are generated, not delivered: brief.daily / brief.weekly would
# also send (WhatsApp, email through the outbox), and no message may leave a
# seed. brief.generate stores exactly the run the job would, so the console,
# the magic-link page and Ask read it as if the 07:00 slot had fired. Weekly
# runs cover the two Mondays before the anchor (the second holds the week of
# the 12 Sep Booking credit note, whose recovered findings are the verified
# value), and the anchor's daily run ranks everything the parts raised.


def brief_top(ctx: Any, run_id: int) -> list[dict]:
    ctx.cur.execute(
        """SELECT item_no, kind, section, detector, title, impact_minor, currency FROM brf_items
           WHERE run_id = %s ORDER BY item_no""", (run_id,))
    return [dict(r) for r in ctx.cur.fetchall()]


def seed_brief(ctx: Any) -> dict:
    if ctx.module_api("brief") is None or not ctx.table_exists("brf_runs"):
        return {"skipped": "brief is not enabled"}
    from clefs import registry

    registry.load_module("brief")
    brief = ctx.module_api("brief").demo_seed_door()
    hotel = ctx.hotel
    out: dict[str, Any] = {"weekly": []}
    last_monday = ctx.anchor - timedelta(days=ctx.anchor.weekday() or 7)
    for monday in (last_monday - timedelta(days=7), last_monday):
        when = ctx.at(monday, 8)
        with at(when):
            run = brief.generate(ctx.cur, hotel, "weekly", day=monday, trigger="schedule", scheduled_local=when)
        out["weekly"].append({"day": monday.isoformat(), "status": run.get("status"),
                              "items": len(run.get("items") or [])})
        ctx.ids.setdefault("brief_runs", {})[f"weekly:{monday.isoformat()}"] = run.get("id")
    when = ctx.at(ctx.anchor, 7)
    run = brief.generate(ctx.cur, hotel, "daily", day=ctx.anchor, now=ctx.now, trigger="schedule",
                         scheduled_local=when)
    ctx.ids.setdefault("brief_runs", {})["daily"] = run.get("id")
    out["daily"] = {"status": run.get("status"), "headline": run.get("headline"),
                    "at_stake_minor": run.get("at_stake_minor"), "items": len(run.get("items") or []),
                    "verified": (run.get("verified") or {}).get("text")}
    brief_plants(ctx)
    return out


def brief_plants(ctx: Any) -> None:
    plant(ctx, detector="brief.daily", key=ctx.anchor.isoformat(), klass="ROW", module="brief",
          subjects={"cadence": "daily", "top": 5, "currency": "AED"},
          note="the anchor's daily brief ranks the top 5 by AED at stake")
    last_monday = ctx.anchor - timedelta(days=ctx.anchor.weekday() or 7)
    plant(ctx, detector="brief.weekly_report", key=last_monday.isoformat(), klass="ROW", module="brief",
          subjects={"cadence": "weekly"}, note="the weekly verified-value report")


# ─── Part entry ───


# Section order matters: the brief ranks what the other three raised. Tests
# narrow SECTIONS to one module to keep a seed within a few seconds.
SECTIONS = ("demand", "visibility", "finance", "brief")


# name, table, extra WHERE: the flat counts the CLI prints (it skips the
# nested per-section reports, which stay in the result for tests).
COUNTS = (
    ("events_visible", "dr_event_relevance", ""),
    ("comp_rates", "dr_comp_rates", ""),
    ("bar_nights", "dr_bar_calendar", ""),
    ("rate_recs", "dr_rate_recommendations", " AND status = 'PROPOSED'"),
    ("google_obs", "vis_google_hotels_obs", ""),
    ("gbp_kpi_months", "vis_gbp_kpis_monthly", ""),
    ("ai_runs", "vis_ai_runs", " AND status = 'ok'"),
    ("ai_fact_errors", "vis_ai_facts", ""),
    ("pl_months", "fin_monthly_summary", ""),
    ("fin_anomalies", "fin_anomalies", ""),
    ("brief_runs", "brf_runs", ""),
)


def summary_counts(ctx: Any) -> dict:
    out = {}
    for name, table, where in COUNTS:
        if ctx.table_exists(table):
            out[name] = _one(ctx.cur, f"SELECT count(*) FROM {table} WHERE hotel_slug = %s{where}", (ctx.slug,))
    return out


def seed_part(ctx: Any) -> dict:
    fns = {"demand": seed_demand, "visibility": seed_visibility, "finance": seed_finance, "brief": seed_brief}
    with demo_ai():
        out: dict[str, Any] = {name: fns[name](ctx) for name in SECTIONS}
    return summary_counts(ctx) | out


# ─── Surfaced: what each plant must have produced (tests, demo-check) ───


def _one(cur: Any, sql: str, params: tuple) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return next(iter(row.values())) if row else None


def _open_signal(cur: Any, slug: str, key: str) -> bool:
    return bool(_one(cur, "SELECT count(*) FROM signals WHERE hotel_slug = %s AND dedupe_key = %s "
                          "AND status NOT IN ('resolved', 'dismissed', 'superseded', 'expired')", (slug, key)))


def _comp_median(cur: Any, slug: str, start: date, end: date) -> float | None:
    """Median of the comps' latest (anchor-morning) prices over [start, end]."""
    return _one(cur, """
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY lowest_minor) FROM dr_comp_rates r
        WHERE hotel_slug = %s AND stay_date BETWEEN %s AND %s AND lowest_minor IS NOT NULL
          AND captured_on = (SELECT max(captured_on) FROM dr_comp_rates WHERE hotel_slug = r.hotel_slug)""",
                (slug, start, end))


def _uplift_seen(cur: Any, slug: str, span: tuple[date, date], lo: float, hi: float) -> bool:
    """Event nights priced lo-hi times the same month's ordinary nights."""
    ev = _comp_median(cur, slug, *span)
    month = span[0].replace(day=1)
    base = _one(cur, """
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY lowest_minor) FROM dr_comp_rates r
        WHERE hotel_slug = %s AND date_trunc('month', stay_date) = %s AND stay_date NOT BETWEEN %s AND %s
          AND EXTRACT(ISODOW FROM stay_date) BETWEEN 1 AND 3 AND lowest_minor IS NOT NULL
          AND captured_on = (SELECT max(captured_on) FROM dr_comp_rates WHERE hotel_slug = r.hotel_slug)""",
                (slug, month, span[0], span[1]))
    return bool(ev and base) and lo <= float(ev) / float(base) <= hi


def _demand_surfaced(cur: Any, slug: str, p: dict) -> bool:
    det, key = p["detector"], p["key"]
    if det == "dr.event":
        return bool(_one(cur, "SELECT count(*) FROM dr_events WHERE hotel_slug = %s AND title = %s "
                              "AND start_date = %s AND end_date = %s",
                         (slug, SUMMER_EXPO["title"], SUMMER_EXPO["start"], SUMMER_EXPO["end"])))
    if det == "dr.calibration":
        eid = _one(cur, "SELECT id::text FROM dr_events WHERE hotel_slug = %s AND title = %s",
                   (slug, SUMMER_EXPO["title"]))
        detail = _one(cur, "SELECT detail FROM dr_calibration WHERE hotel_slug = %s AND category = %s", (slug, key))
        need_lift = bool((p.get("subjects") or {}).get("positive_lift"))
        return any(d.get("event_id") == eid and (not need_lift or float(d.get("lift") or 0) > 0)
                   for d in detail or [])
    if det == "dr.comp_sold_out":
        n = _one(cur, """SELECT count(DISTINCT stay_date) FROM dr_comp_rates r
                         WHERE hotel_slug = %s AND comp_id = 'demo-trade-axis' AND sold_out
                           AND stay_date BETWEEN %s AND %s
                           AND captured_on = (SELECT max(captured_on) FROM dr_comp_rates WHERE hotel_slug = %s)""",
                 (slug, GITEX[0], GITEX[1], slug))
        return n == (GITEX[1] - GITEX[0]).days + 1
    if det == "dr.comp_uplift":
        return _uplift_seen(cur, slug, GITEX, 1.45, 1.75) if key.startswith("gitex") \
            else _uplift_seen(cur, slug, CABSAT, 1.08, 1.25)
    if det == "dr.recommendation":
        return bool(_one(cur, "SELECT count(*) FROM dr_rate_recommendations WHERE hotel_slug = %s "
                              "AND stay_date BETWEEN %s AND %s AND status = 'PROPOSED' "
                              "AND recommended_minor > current_bar_minor", (slug, CABSAT[0], CABSAT[1])))
    return False


def _visibility_surfaced(cur: Any, slug: str, p: dict) -> bool:
    det, subj = p["detector"], p.get("subjects") or {}
    if p.get("dedupe_key"):
        return _open_signal(cur, slug, p["dedupe_key"])
    if det == "vis.ai_fact_error":
        fp = _one(cur, "SELECT fingerprint FROM vis_ai_facts WHERE hotel_slug = %s AND fact_key = %s "
                       "AND claimed_value = %s AND error_type = %s AND status IN ('open', 'recurred')",
                  (slug, subj["fact_key"], subj["claimed"], subj["error_type"]))
        return bool(fp) and _open_signal(cur, slug, f"vis.ai_fact_error:{fp}")
    discovery = "prompt_intent IN ('generic', 'persona', 'event') AND status = 'ok'"
    if det == "vis.ai_mention":
        n = _one(cur, f"SELECT count(*) FILTER (WHERE self_mentioned) * 1.0 / NULLIF(count(*), 0) FROM vis_ai_runs "
                      f"WHERE hotel_slug = %s AND {discovery}", (slug,))
        return n is not None and 0.2 <= float(n) <= 0.6
    if det == "vis.ai_competitor_win":
        return bool(_one(cur, f"SELECT count(*) FROM vis_ai_runs WHERE hotel_slug = %s AND {discovery} "
                              "AND NOT self_mentioned AND jsonb_array_length(entities) > 0", (slug,)))
    if det == "vis.ai_model_change":
        return (_one(cur, "SELECT count(DISTINCT version_key) FROM vis_engine_versions WHERE hotel_slug = %s "
                          "AND engine = %s", (slug, p["key"])) or 0) >= 2
    return False


def _finance_surfaced(cur: Any, slug: str, p: dict) -> bool:
    det, key = p["detector"], p["key"]
    if p.get("dedupe_key"):
        return _open_signal(cur, slug, p["dedupe_key"])
    if det == "fin.anomaly" and p["klass"] == "DECOY":
        # Anything flagged outside the two planted months is a false positive.
        return bool(_one(cur, "SELECT count(*) FROM fin_anomalies WHERE hotel_slug = %s "
                              "AND period_month NOT IN (%s, %s)", (slug, LABOUR_MONTH, BOOKED_NET_MONTH)))
    if det == "fin.anomaly":
        kind, month = key.split(":")
        fp = _one(cur, "SELECT fingerprint FROM fin_anomalies WHERE hotel_slug = %s AND kind = %s "
                       "AND to_char(period_month, 'YYYY-MM') = %s", (slug, kind, month))
        return bool(fp) and _open_signal(cur, slug, f"fin.anomaly:{fp}")
    return False


def _brief_surfaced(cur: Any, slug: str, p: dict) -> bool:
    if p["detector"] == "brief.daily":
        cur.execute("""SELECT i.item_no, i.currency, i.impact_minor FROM brf_runs r JOIN brf_items i ON i.run_id = r.id
                       WHERE r.hotel_slug = %s AND r.cadence = 'daily' AND r.brief_date = %s AND i.kind = 'ranked'
                       ORDER BY i.item_no""", (slug, p["key"]))
        items = [dict(r) for r in cur.fetchall()]
        return len(items) == 5 and all((i["currency"] or "").strip() in ("AED", "") for i in items) \
            and any(i["impact_minor"] for i in items)
    if p["detector"] == "brief.weekly_report":
        return bool(_one(cur, "SELECT count(*) FROM brf_runs WHERE hotel_slug = %s AND cadence = 'weekly' "
                              "AND brief_date = %s", (slug, p["key"]))) \
            and bool(_one(cur, "SELECT count(*) FROM brf_reports WHERE hotel_slug = %s", (slug,)))
    return False


def surfaced(cur: Any, slug: str, p: dict) -> bool:
    """True when the planted case produced what it should (for a DECOY: the
    thing it must not produce exists, so True is a failure)."""
    fn = {"demand": _demand_surfaced, "visibility": _visibility_surfaced, "finance": _finance_surfaced,
          "brief": _brief_surfaced}.get(p.get("module") or "")
    return bool(fn and fn(cur, slug, p))


# ─── demo-check ───


def expected_plants(anchor: date, *, scale: float = 1.0) -> list[dict]:
    """The growth plants for an anchor, without a database: every plant is a
    constant of the seed (names, dates, amounts), so demo-check can rebuild
    them when the manifest on disk predates this part."""
    from types import SimpleNamespace

    ctx = SimpleNamespace(plants=[], scale=scale, anchor=anchor)
    for make in (demand_plants, visibility_plants, finance_plants, brief_plants):
        make(ctx)
    return ctx.plants


def growth_checks(cur: Any, slug: str) -> list[tuple[str, bool, str]]:
    """Read-only: every growth plant surfaced and no decoy fired. Plants come
    from expected_plants.json when it lists them, else they are rebuilt for
    the seeded anchor (the last derived occupancy day) at full scale."""
    import json as _json
    import os

    from clefs.seed.demo_uae import MANIFEST_PATH

    mods = ("demand", "visibility", "finance", "brief")
    plants: list[dict] = []
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            plants = [p for p in _json.load(fh).get("plants") or [] if p.get("module") in mods]
    if not plants:
        anchor = _one(cur, "SELECT max(date) FROM occupancy_daily WHERE hotel_slug = %s AND source = 'DERIVED'",
                      (slug,))
        if anchor is None:
            return [("growth plants", False, "no derived occupancy (stays part not run)")]
        plants = expected_plants(anchor)
    missing = [f"{p['detector']}:{p['key']}" for p in plants if p["klass"] != "DECOY" and not surfaced(cur, slug, p)]
    fired = [f"{p['detector']}:{p['key']}" for p in plants if p["klass"] == "DECOY" and surfaced(cur, slug, p)]
    real = sum(1 for p in plants if p["klass"] != "DECOY")
    detail = f"{real - len(missing)}/{real}" + (f" missing {missing}" if missing else "")
    return [("growth plants surfaced", not missing, detail),
            ("growth decoys quiet", not fired, f"fired {fired}" if fired else "0 fired")]


CHECKS = [growth_checks]
