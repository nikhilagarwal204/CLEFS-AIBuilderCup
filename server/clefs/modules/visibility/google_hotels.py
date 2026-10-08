"""
Google Hotels observations: organic rank, official price presence and parity
(04 spec 5.2).

Every call goes through core.serpapi.search with params built by
google_hotels_area_params, and `q` comes only from demand.api.area_queries.
The two date pairs (next Friday + 1 night, today + 30 + 1 night) sit inside
Demand's daily 0-30 day fetch, so Demand's 04:00 dr.comp_rates_fetch and this
05:30 job share one billed page-1 call per query (the cache key ignores
`purpose`). Extra pages and property details are Visibility's own spend.

Prices are indicative third-party data (SerpApi, Canon 20) and are labelled
so everywhere they are shown.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock, money
from clefs.core.time import local_date
from clefs.modules.visibility import xmod

PURPOSE = "visibility.google_hotels"
MAX_QUERIES = 2          # area queries per run; each adds up to 3 pages
MAX_EXTRA_PAGES = 2      # when the hotel is not on page 1 (5.2)
DISCLOSURE = ("Google Hotels data comes from SerpApi, a third-party service; treat prices as indicative.")
PARITY_LABEL = "indicative, third-party data"
PRICE_ACCURACY_NOTE = ("Your booking-engine landing price and taxes must match the price Google shows "
                       "(Google hotel price accuracy policy 6064419).")

# Google Hotels source names -> channels.code. A source not listed, or whose
# code the hotel has no channel for, is "unmapped" and its exposure uses all
# OTA rooms on the books (L7's rule).
SOURCE_CHANNELS = {
    "booking.com": "BOOKING",
    "agoda": "AGODA",
    "expedia": "EXPEDIA",
    "expedia.com": "EXPEDIA",
    "hotels.com": "HOTELS_COM",
    "trip.com": "TRIP_COM",
}


# ─── Date pairs ───


def date_pairs(today: date) -> list[tuple[date, date]]:
    """(next Friday, +1) and (today + 30, +1). "Next Friday" is strictly after
    today, so a Friday run looks a week ahead like every other day does."""
    days = (4 - today.weekday()) % 7 or 7
    fri = today + timedelta(days=days)
    far = today + timedelta(days=30)
    return [(fri, fri + timedelta(days=1)), (far, far + timedelta(days=1))]


# ─── Organic rank ───


def organic_properties(page: dict) -> tuple[list[dict], int]:
    """(organic properties, sponsored count) of one search page. Sponsored
    properties and every `ads[]` entry are dropped from the ranking."""
    props = [p for p in (page.get("properties") or []) if isinstance(p, dict)]
    organic = [p for p in props if not p.get("sponsored")]
    sponsored = (len(props) - len(organic)) + len([a for a in (page.get("ads") or []) if isinstance(a, dict)])
    return organic, sponsored


def rank_in(organic: list[dict], property_token: str | None, *, offset: int = 0) -> int | None:
    """1-based organic rank of the hotel's property token, or None."""
    if not property_token:
        return None
    for i, p in enumerate(organic, start=1):
        if p.get("property_token") == property_token:
            return offset + i
    return None


def next_page_token(page: dict) -> str | None:
    pag = page.get("serpapi_pagination") or {}
    return pag.get("next_page_token") or None


# ─── Prices and parity ───


def _num(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except Exception:  # noqa: BLE001  (a string like "$412" is not extracted: skip it)
        return None
    return d if d > 0 else None


def price_entries(details: dict) -> list[dict]:
    """Every price entry on the property page (featured first), each tagged
    official or not."""
    out = []
    for src in ("featured_prices", "prices"):
        for e in details.get(src) or []:
            if isinstance(e, dict):
                out.append(e)
    return out


def _field(entry: dict, basis: str) -> Decimal | None:
    return _num((entry.get(basis) or {}).get("extracted_lowest"))


def choose_basis(entries: list[dict]) -> str | None:
    """Like for like (5.2): total_rate when the official entry carries it (or,
    without an official entry, when any entry does), else rate_per_night."""
    official = [e for e in entries if e.get("official")]
    pool = official or entries
    for basis in ("total_rate", "rate_per_night"):
        if any(_field(e, basis) is not None for e in pool):
            return basis
    return None


def analyse_prices(details: dict, currency: str) -> dict:
    """Official presence and the like-for-like gap on one property page.

    gap = official - min(non-official) and gap_pct = gap / official x 100,
    the same direction as Profit Guard L7 (it compares against the direct
    price): a positive gap means an OTA undercuts the hotel's own site."""
    entries = price_entries(details)
    official_present = any(bool(e.get("official")) for e in entries)
    basis = choose_basis(entries)
    evidence = []
    official_minor = None
    others: list[tuple[int, str]] = []
    for e in entries:
        amount = _field(e, basis) if basis else None
        minor = money.to_minor(amount, currency) if amount is not None else None
        src = str(e.get("source") or "")
        evidence.append({"source": src, "official": bool(e.get("official")), "price_minor": minor,
                         "currency": currency, "channel": channel_for_source(src)})
        if minor is None:
            continue
        if e.get("official"):
            official_minor = minor if official_minor is None else min(official_minor, minor)
        else:
            others.append((minor, src))
    min_other = min(others, default=None, key=lambda t: t[0])
    out = {
        "official_present": official_present, "official_price_minor": official_minor,
        "min_other_price_minor": min_other[0] if min_other else None,
        "min_other_source": min_other[1] if min_other else None,
        "price_basis": basis, "parity_gap_minor": None, "parity_gap_pct": None, "prices": evidence,
    }
    if official_minor and min_other:
        gap = official_minor - min_other[0]
        out["parity_gap_minor"] = gap
        out["parity_gap_pct"] = gap_pct(gap, official_minor)
    return out


def gap_pct(gap_minor: int, official_minor: int) -> Decimal:
    """Exact percentage (quantized for storage at 3 dp by the caller)."""
    return Decimal(gap_minor) * 100 / Decimal(official_minor)


def is_breach(pct: Decimal | None, tolerance_pct: Decimal) -> bool:
    """Strictly greater than the tolerance: exactly 1.00% is parity (5.2)."""
    return pct is not None and pct > Decimal(tolerance_pct)


def q3(pct: Decimal | None) -> Decimal | None:
    return None if pct is None else pct.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def channel_for_source(source: str) -> str | None:
    s = " ".join((source or "").lower().split())
    return SOURCE_CHANNELS.get(s) or SOURCE_CHANNELS.get(s.replace(" ", ""))


# ─── Exposure (L7's formula) ───


def otb_rooms(cur: Any, hotel_slug: str, stay_date: date, channel: str | None) -> tuple[int, str]:
    """Rooms on the books for the stay date on `channel` from the latest OTB
    snapshot, or on every OTA channel when the source is unmapped or the
    hotel has no such channel. Returns (rooms, basis)."""
    from clefs.core import stays

    snap = stays.latest_snapshot_date(cur, hotel_slug)
    if snap is None:
        return 0, "no_otb"
    rows = stays.get_otb(cur, hotel_slug, snapshot_date=snap, start=stay_date, end=stay_date)
    by_channel = (rows[0].get("by_channel") or {}) if rows else {}
    cur.execute("SELECT code, kind FROM channels WHERE hotel_slug = %s", (hotel_slug,))
    kinds = {r["code"]: r["kind"] for r in cur.fetchall()}
    if channel and channel in kinds:
        return int(by_channel.get(channel) or 0), channel
    ota = [c for c, k in kinds.items() if k == "ota"]
    return sum(int(by_channel.get(c) or 0) for c in ota), "all_ota"


# ─── Fetching (core.serpapi only) ───


def search_rank(hotel: Any, q: str, check_in: date, check_out: date, token: str | None) -> dict:
    """Page 1 (shared with Demand), then up to 2 more pages while the hotel
    is not found. Returns {organic_rank, organic_total, sponsored_count,
    pages_fetched, cache_key}."""
    from clefs.core import serpapi

    params = serpapi.google_hotels_area_params(hotel, q=q, check_in=check_in, check_out=check_out)
    key = serpapi.cache_key("google_hotels", params)
    page = serpapi.search("google_hotels", params, hotel_slug=hotel.slug, purpose=PURPOSE)
    organic, sponsored = organic_properties(page)
    total, rank, pages = len(organic), rank_in(organic, token), 1
    nxt = next_page_token(page)
    # Pages 2-3 are Visibility-only spend: fetched only when there is a token
    # to look for and it was not on page 1.
    while token and rank is None and nxt and pages < 1 + MAX_EXTRA_PAGES:
        page = serpapi.search("google_hotels", {**params, "next_page_token": nxt}, hotel_slug=hotel.slug,
                              purpose=PURPOSE)
        organic, extra = organic_properties(page)
        rank = rank_in(organic, token, offset=total)
        total += len(organic)
        sponsored += extra
        pages += 1
        nxt = next_page_token(page)
    return {"organic_rank": rank, "organic_total": total, "sponsored_count": sponsored, "pages_fetched": pages,
            "cache_key": key}


def fetch_details(hotel: Any, q: str, token: str, check_in: date, check_out: date, *,
                  max_age: timedelta | None = None) -> dict:
    """The hotel's property page for one date pair (its price list)."""
    from clefs.core import serpapi

    params = {**serpapi.google_hotels_area_params(hotel, q=q, check_in=check_in, check_out=check_out),
              "property_token": token}
    kw = {"max_age": max_age} if max_age is not None else {}
    return serpapi.search("google_hotels", params, hotel_slug=hotel.slug, purpose=PURPOSE, **kw)


# ─── Daily job ───


def run_daily(cur: Any, hotel: Any) -> dict:
    """visibility.google_hotels_daily: observe, store, hand parity evidence to
    Profit Guard (emit_signal=False, so L7 never signals the same gap) and
    raise Visibility's own signals."""
    from clefs.core import serpapi
    from clefs.core.errors import NotConfigured
    from clefs.modules.demand import api as demand_api
    from clefs.modules.visibility import store

    profile = store.get_profile(cur, hotel.slug)
    if not profile["serpapi_enabled"]:
        return {"skipped": "serpapi_disabled"}
    if store.budget_state(cur, hotel.slug, profile=profile)["exhausted"]:
        return {"skipped": "budget_exhausted"}
    fallback = [f"hotels in {hotel.city}"] if hotel.city else []  # the demand stub's answer
    queries = [q for q in (xmod.call(cur, fallback, demand_api.area_queries, hotel.slug) or []) if q][:MAX_QUERIES]
    token = profile.get("gh_property_token")
    if not queries and not token:
        return {"skipped": "no_queries"}
    detail_q = queries[0] if queries else f"{hotel.name} {hotel.city or ''}".strip()
    now = clock.now()
    report: dict[str, Any] = {"observations": 0, "errors": 0, "breaches": 0}
    analyses: list[tuple[date, date, dict | None]] = []
    try:
        for ci, co in date_pairs(local_date(hotel)):
            analysis = None
            if token:
                try:
                    analysis = analyse_prices(fetch_details(hotel, detail_q, token, ci, co), hotel.currency)
                except serpapi.SerpApiError:
                    report["errors"] += 1
            analyses.append((ci, co, analysis))
            for q in queries or [None]:
                ranked: dict[str, Any] = {}
                if q:
                    try:
                        ranked = search_rank(hotel, q, ci, co, token)
                    except serpapi.SerpApiError:
                        report["errors"] += 1
                        continue
                _insert_obs(cur, hotel, now, q, ci, co, ranked, analysis)
                report["observations"] += 1
    except NotConfigured:
        report["skipped"] = "not_configured"
    except serpapi.BudgetExhausted:
        report["skipped"] = "budget_exhausted"
    report["breaches"] = _parity_followups(cur, hotel, analyses, now)
    return report


def _insert_obs(cur: Any, hotel: Any, now: Any, q: str | None, ci: date, co: date, ranked: dict,
                analysis: dict | None) -> None:
    from clefs.db import Json

    a = analysis or {}
    cur.execute(
        """
        INSERT INTO vis_google_hotels_obs (hotel_slug, observed_at, query, check_in, check_out, pages_fetched,
            organic_rank, organic_total, sponsored_count, official_present, official_price_minor,
            min_other_price_minor, min_other_source, min_other_channel, price_basis, parity_gap_minor,
            parity_gap_pct, prices, currency, serp_cache_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (hotel.slug, now, q, ci, co, ranked.get("pages_fetched"), ranked.get("organic_rank"),
         ranked.get("organic_total"), ranked.get("sponsored_count"), a.get("official_present"),
         a.get("official_price_minor"), a.get("min_other_price_minor"), a.get("min_other_source"),
         channel_for_source(a.get("min_other_source") or ""), a.get("price_basis"), a.get("parity_gap_minor"),
         q3(a.get("parity_gap_pct")), Json(a.get("prices")) if analysis else None, hotel.currency,
         ranked.get("cache_key")),
    )


def _parity_followups(cur: Any, hotel: Any, analyses: list[tuple[date, date, dict | None]], now: Any) -> int:
    """Evidence to Profit Guard, the daily parity signal per stay date and the
    weekly official-price-missing signal. Returns the number of breaches."""
    from clefs.core import signals
    from clefs.modules.profit_guard import api as pg_api
    from clefs.modules.visibility import vsignals

    tolerance = xmod.call(cur, Decimal("1.0"), pg_api.parity_tolerance_pct, hotel.slug)
    breaches = 0
    missing: list[str] = []
    for ci, co, a in analyses:
        if a is None:
            continue
        if not a["official_present"]:
            missing.append(ci.isoformat())
        key = f"vis.parity_gap_google:{ci.isoformat()}"
        if a["parity_gap_minor"] is None:
            signals.resolve_signal(cur, hotel.slug, key, resolution="no_comparison")
            continue
        channel = channel_for_source(a["min_other_source"] or "")
        rooms, rooms_basis = otb_rooms(cur, hotel.slug, ci, channel)
        exposure = max(a["parity_gap_minor"], 0) * rooms
        xmod.call(cur, 0, pg_api.record_rate_evidence, hotel.slug, emit_signal=False, evidence={
            "source": "SERPAPI", "stay_date": ci.isoformat(), "check_out": co.isoformat(),
            "official_minor": a["official_price_minor"], "lowest_minor": a["min_other_price_minor"],
            "lowest_source": a["min_other_source"], "lowest_channel": channel, "gap_minor": a["parity_gap_minor"],
            "gap_pct": str(q3(a["parity_gap_pct"])), "price_basis": a["price_basis"], "currency": hotel.currency,
            "observed_at": now.isoformat(), "prices": a["prices"], "label": PARITY_LABEL,
        })
        if is_breach(a["parity_gap_pct"], tolerance):
            breaches += 1
            vsignals.parity_gap(cur, hotel, ci, co, a, exposure_minor=exposure, rooms=rooms,
                                rooms_basis=rooms_basis, channel=channel, tolerance=tolerance)
        else:
            signals.resolve_signal(cur, hotel.slug, key, resolution="within_tolerance")
    if any(a is not None for _, _, a in analyses):
        if missing:
            vsignals.official_price_missing(cur, hotel, missing)
        else:
            signals.resolve_signal(cur, hotel.slug, "vis.official_price_missing", resolution="present")
    return breaches


# ─── Reads ───


def latest_parity(cur: Any, hotel_slug: str, stay_date: date) -> dict | None:
    """visibility.api.parity_status: the newest priced observation for the
    night (check_in = stay_date)."""
    cur.execute(
        """
        SELECT official_present, official_price_minor, min_other_price_minor, parity_gap_minor, parity_gap_pct,
               currency, observed_at
        FROM vis_google_hotels_obs
        WHERE hotel_slug = %s AND check_in = %s AND official_present IS NOT NULL
        ORDER BY observed_at DESC, id DESC LIMIT 1
        """,
        (hotel_slug, stay_date),
    )
    r = cur.fetchone()
    if not r:
        return None
    return {"official_present": r["official_present"], "official_minor": r["official_price_minor"],
            "lowest_ota_minor": r["min_other_price_minor"], "gap_minor": r["parity_gap_minor"],
            "gap_pct": None if r["parity_gap_pct"] is None else str(r["parity_gap_pct"]),
            "currency": r["currency"], "checked_at": r["observed_at"].isoformat()}


VENDOR_RISK_URL = "/subprocessors#serpapi"


def overview(cur: Any, hotel: Any) -> dict:
    """GET /visibility/google: presence, the parity table (latest per stay
    date, with the price list as evidence), rank history and the labels the
    tab must show (indicative, third-party SerpApi data, Canon 20)."""
    from clefs.modules.profit_guard import api as pg_api
    from clefs.modules.visibility import attribution, store
    from clefs.settings import get_settings

    profile = store.get_profile(cur, hotel.slug)
    tolerance = xmod.call(cur, Decimal("1.0"), pg_api.parity_tolerance_pct, hotel.slug)
    today = local_date(hotel)
    cur.execute(
        """
        SELECT DISTINCT ON (check_in) check_in, check_out, official_present, official_price_minor,
               min_other_price_minor, min_other_source, min_other_channel, price_basis, parity_gap_minor,
               parity_gap_pct, prices, currency, observed_at
        FROM vis_google_hotels_obs
        WHERE hotel_slug = %s AND check_in >= %s AND official_present IS NOT NULL
        ORDER BY check_in, observed_at DESC, id DESC
        """,
        (hotel.slug, today),
    )
    parity = []
    for r in cur.fetchall():
        pct = r["parity_gap_pct"]
        parity.append({
            "check_in": r["check_in"].isoformat(), "check_out": r["check_out"].isoformat(),
            "official_present": r["official_present"], "official_minor": r["official_price_minor"],
            "lowest_minor": r["min_other_price_minor"], "lowest_source": r["min_other_source"],
            "lowest_channel": r["min_other_channel"], "price_basis": r["price_basis"],
            "gap_minor": r["parity_gap_minor"], "gap_pct": None if pct is None else str(pct),
            "breach": is_breach(pct, tolerance), "prices": r["prices"] or [], "currency": r["currency"],
            "observed_at": r["observed_at"].isoformat()})
    cur.execute(
        """
        SELECT (observed_at AT TIME ZONE %s)::date AS day, MIN(organic_rank) AS rank, MAX(organic_total) AS total,
               BOOL_OR(official_present) AS official
        FROM vis_google_hotels_obs WHERE hotel_slug = %s AND observed_at >= %s
        GROUP BY 1 ORDER BY 1
        """,
        (hotel.timezone, hotel.slug, clock.now() - timedelta(days=30)),
    )
    history = [{"day": r["day"].isoformat(), "organic_rank": r["rank"], "organic_total": r["total"],
                "official_present": r["official"]} for r in cur.fetchall()]
    # The 30-day rate counts stay-date checks, as vis.official_presence_rate
    # does. WHY: it used BOOL_OR per observation day, so a day where one of
    # several checked stays showed the official price counted as "shown":
    # the tile read 100% above rows saying "No official price" (QA OWN-16).
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE official_present) AS shown, COUNT(official_present) AS checks,
               COUNT(DISTINCT (observed_at AT TIME ZONE %s)::date) FILTER (WHERE official_present IS NOT NULL) AS days
        FROM vis_google_hotels_obs WHERE hotel_slug = %s AND observed_at >= %s
        """,
        (hotel.timezone, hotel.slug, clock.now() - timedelta(days=30)),
    )
    pres = cur.fetchone()
    shown, checks = int(pres["shown"] or 0), int(pres["checks"] or 0)
    setup = []
    if not profile.get("gh_property_token"):
        setup.append("property_token")
    if not profile["serpapi_enabled"]:
        setup.append("serpapi_enabled")
    if not get_settings().SERPAPI_KEY:
        setup.append("serpapi_key")
    att = attribution.recent(cur, hotel.slug, months=1)
    return {
        "serpapi_enabled": profile["serpapi_enabled"], "setup_missing": setup,
        "presence": {"latest": parity[0]["official_present"] if parity else None,
                     "rate_30d": f"{100 * shown / checks:.1f}" if checks else None,
                     "shown_30d": shown, "checks_30d": checks, "days_30d": int(pres["days"] or 0)},
        "parity": parity, "tolerance_pct": str(tolerance), "rank_history": history,
        "attribution": att[0] if att else None,
        "labels": {"disclosure": DISCLOSURE, "parity": PARITY_LABEL, "price_accuracy": PRICE_ACCURACY_NOTE,
                   "vendor_risk_url": VENDOR_RISK_URL},
    }
