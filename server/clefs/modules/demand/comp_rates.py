"""
Demand Radar comp set and indicative competitor rates (02 spec 4.1-4.2).

One writer (record_rates) for every rate feed:
- SerpApi Google Hotels (adapters/serpapi_hotels.py, the default): one area
  query per night through clefs.core.serpapi.search with the shared
  google_hotels_area_params builder, so Visibility's identical requests reuse
  the same billed call (04 spec F5, Canon K3). BudgetExhausted skips the run.
- Lighthouse / RateGain CSV (adapters/csv_rates.py) through core ingest kind
  COMP_RATES or the direct import route.
- Xotelo (demo only, USD converted and flagged) and MakCorps (stub).

Every rate is "Indicative": a public shopping price, never a contract rate.
Sold out is stored as sold_out=True with no price (a strong demand signal,
never a zero price). Cadence: every night 0-30 days out daily, nights
31-120 every third day (about 60 area calls a day for one query).
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from clefs import db as clefs_db
from clefs import jobs
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.money import money_str
from clefs.core.time import local_date

log = logging.getLogger("clefs.demand.rates")

SOURCES = ("SERPAPI", "MAKCORPS", "XOTELO", "CSV_LIGHTHOUSE", "CSV_RATEGAIN")
# Per capture day, a hand-uploaded rate shop outranks a scraped page.
SOURCE_RANK = {"CSV_LIGHTHOUSE": 0, "CSV_RATEGAIN": 0, "MAKCORPS": 1, "SERPAPI": 2, "XOTELO": 3}
INDICATIVE_LABEL = "Indicative"
MAX_COMPS = 25
NEAR_DAYS = 30        # fetched every day
FAR_DAYS = 120        # 31..FAR_DAYS fetched every FAR_EVERY days
FAR_EVERY = 3
FALLBACK_HORIZON = 14  # per-property fallback calls only for these nights
RETENTION_DAYS = 400


# ─── Types ───


@dataclass
class CompRate:
    """One comp's indicative price for one night, as an adapter reports it."""

    comp_id: str
    stay_date: date
    lowest_minor: int | None
    currency: str
    source: str
    captured_at: datetime
    los: int = 1
    adults: int = 2
    sold_out: bool = False
    by_source: list[dict] = field(default_factory=list)
    fx_converted: bool = False
    original_minor: int | None = None
    original_currency: str | None = None
    query: str | None = None
    import_batch_id: int | None = None


class RateFeedAdapter(Protocol):
    """02 spec 4.2: fetch(hotel, comp_ids, dates, los=1, adults=2,
    currency="AED") -> list[CompRate]. Adapters never write rows."""

    source: str

    def fetch(self, hotel: Any, comp_ids: list[str], dates: list[date], los: int = 1, adults: int = 2,
              currency: str = "AED") -> list[CompRate]: ...


# ─── Settings ───

SETTINGS_DEFAULTS = {"area_queries": [], "self_property_token": None, "reference_room_type": None,
                     "comp_fetch_enabled": True, "fallback_calls_max": 5}
_SETTINGS_COLS = tuple(SETTINGS_DEFAULTS)


def get_rate_settings(cur: Any, hotel_slug: str) -> dict:
    row = clefs_db.fetch_one("SELECT * FROM dr_rate_settings WHERE hotel_slug = %s", (hotel_slug,), cur=cur)
    out = {k: (row[k] if row else v) for k, v in SETTINGS_DEFAULTS.items()}
    out["area_queries"] = list(out["area_queries"] or [])
    out["updated_at"] = row["updated_at"].isoformat() if row else None
    return out


def put_rate_settings(cur: Any, hotel_slug: str, changes: dict, *, user_id: int | None) -> dict:
    """Partial update. Area queries are trimmed, de-duplicated and capped at
    3: each one is a billed SerpApi call per fetched night."""
    cur_vals = get_rate_settings(cur, hotel_slug)
    vals = {k: changes.get(k, cur_vals[k]) for k in _SETTINGS_COLS}
    queries: list[str] = []
    for q in vals["area_queries"] or []:
        q = " ".join(str(q).split())[:120]
        if q and q.lower() not in {x.lower() for x in queries}:
            queries.append(q)
    if len(queries) > 3:
        raise ClefsError(400, "At most 3 area queries", "invalid_settings")
    vals["area_queries"] = queries
    rt = vals["reference_room_type"]
    vals["reference_room_type"] = normalize_code(rt) if rt else None
    vals["fallback_calls_max"] = max(0, min(20, int(vals["fallback_calls_max"] or 0)))
    cols = ", ".join(_SETTINGS_COLS)
    ph = ", ".join(["%s"] * len(_SETTINGS_COLS))
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in _SETTINGS_COLS)
    cur.execute(
        f"INSERT INTO dr_rate_settings (hotel_slug, {cols}, updated_by, updated_at) VALUES (%s, {ph}, %s, now()) "
        f"ON CONFLICT (hotel_slug) DO UPDATE SET {sets}, updated_by = EXCLUDED.updated_by, updated_at = now()",
        (hotel_slug, *[vals[c] for c in _SETTINGS_COLS], user_id),
    )
    return get_rate_settings(cur, hotel_slug)


def normalize_code(code: Any) -> str:
    """Room type codes as core stores them: trimmed, upper case."""
    c = str(code or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_\-]{0,31}|\*", c):
        raise ClefsError(400, f"Invalid room type code {code!r}", "invalid_room_type")
    return c


def area_queries(cur: Any, hotel: Any) -> list[str]:
    """The single source of Google Hotels `q` strings (04 spec F5): the
    owner's queries, else "hotels in <city>", else [] (a query on nothing
    would bill a useless call)."""
    queries = get_rate_settings(cur, hotel.slug)["area_queries"]
    if queries:
        return queries
    city = (getattr(hotel, "city", None) or "").strip()
    return [f"hotels in {city}"] if city else []


# ─── Comp set ───

_COMP_FIELDS = ("name", "property_token", "place_id", "xotelo_key", "makcorps_id", "lat", "lon", "stars", "rooms",
                "aliases", "is_demo", "active")
_GENERIC = {"hotel", "hotels", "the", "and", "by", "a", "an", "llc", "resort", "spa"}


def norm_name(name: Any) -> str:
    """Loose name key for matching a CSV or SerpApi name to a comp: lower
    case, alphanumerics only, generic words dropped ("The Hotel X" = "x")."""
    words = re.findall(r"[a-z0-9؀-ۿ]+", str(name or "").lower().replace("&", " and "))
    return " ".join(w for w in words if w not in _GENERIC)


def _comp_id_for(cur: Any, hotel_slug: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:40] or "comp"
    cid, n = base, 1
    while clefs_db.fetch_one("SELECT 1 FROM dr_comp_set WHERE hotel_slug = %s AND comp_id = %s",
                             (hotel_slug, cid), cur=cur):
        n += 1
        cid = f"{base}-{n}"
    return cid


def _comp_out(r: dict) -> dict:
    return {
        "comp_id": r["comp_id"], "name": r["name"], "property_token": r["property_token"],
        "place_id": r["place_id"], "xotelo_key": r["xotelo_key"], "makcorps_id": r["makcorps_id"],
        "lat": float(r["lat"]) if r["lat"] is not None else None,
        "lng": float(r["lon"]) if r["lon"] is not None else None,
        "stars": float(r["stars"]) if r["stars"] is not None else None, "rooms": r["rooms"],
        "aliases": list(r["aliases"] or []), "is_demo": r["is_demo"], "active": r["active"], "origin": r["origin"],
        "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
    }


def list_comps(cur: Any, hotel_slug: str, *, include_inactive: bool = False) -> list[dict]:
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_comp_set WHERE hotel_slug = %s AND (active OR %s) ORDER BY name, comp_id",
        (hotel_slug, include_inactive), cur=cur)
    return [_comp_out(r) for r in rows]


def get_comp(cur: Any, hotel_slug: str, comp_id: str) -> dict:
    r = clefs_db.fetch_one("SELECT * FROM dr_comp_set WHERE hotel_slug = %s AND comp_id = %s",
                           (hotel_slug, comp_id), cur=cur)
    if r is None:
        raise ClefsError(404, "No such competitor", "comp_not_found")
    return _comp_out(r)


def _clean_comp(data: dict) -> dict:
    data = dict(data)
    if "lng" in data:  # the API says lat/lng (comp_set contract); the column is lon
        data["lon"] = data.pop("lng")
    out: dict = {}
    for k in _COMP_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k in ("name", "property_token", "place_id", "xotelo_key", "makcorps_id"):
            v = " ".join(str(v).split())[:200] if v not in (None, "") else None
        elif k == "aliases":
            v = [" ".join(str(a).split())[:120] for a in (v or []) if str(a).strip()][:10]
        out[k] = v
    if "name" in out and not out["name"]:
        raise ClefsError(400, "A competitor needs a name", "invalid_comp")
    if "stars" in data and out.get("stars") is not None and not 1 <= float(out["stars"]) <= 5:
        raise ClefsError(400, "Stars must be between 1 and 5", "invalid_comp")
    return out


def upsert_comp(cur: Any, hotel_slug: str, data: dict, *, comp_id: str | None = None,
                origin: str = "manual") -> tuple[dict, bool]:
    """Create (comp_id None) or patch one comp. Returns (comp, created). A
    property_token already used by another comp of the hotel is 409."""
    vals = _clean_comp(data)
    tok = vals.get("property_token")
    if tok:
        other = clefs_db.fetch_one(
            "SELECT comp_id FROM dr_comp_set WHERE hotel_slug = %s AND property_token = %s AND comp_id <> %s",
            (hotel_slug, tok, comp_id or ""), cur=cur)
        if other:
            raise ClefsError(409, f"That Google Hotels property is already {other['comp_id']}", "comp_exists")
    if comp_id is None:
        if not vals.get("name"):
            raise ClefsError(400, "A competitor needs a name", "invalid_comp")
        n = clefs_db.fetch_one("SELECT count(*) AS n FROM dr_comp_set WHERE hotel_slug = %s AND active",
                               (hotel_slug,), cur=cur)["n"]
        if n >= MAX_COMPS:
            raise ClefsError(409, f"A comp set holds at most {MAX_COMPS} active hotels", "comp_set_full")
        cid = _comp_id_for(cur, hotel_slug, vals["name"])
        cols = ["hotel_slug", "comp_id", "origin", *vals]
        cur.execute(
            f"INSERT INTO dr_comp_set ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
            (hotel_slug, cid, origin, *vals.values()))
        return get_comp(cur, hotel_slug, cid), True
    get_comp(cur, hotel_slug, comp_id)  # 404 first
    if vals:
        sets = ", ".join(f"{k} = %s" for k in vals)
        cur.execute(f"UPDATE dr_comp_set SET {sets}, updated_at = now() WHERE hotel_slug = %s AND comp_id = %s",
                    (*vals.values(), hotel_slug, comp_id))
    return get_comp(cur, hotel_slug, comp_id), False


def deactivate_comp(cur: Any, hotel_slug: str, comp_id: str) -> dict:
    """Soft delete: the rate history stays for measurement."""
    get_comp(cur, hotel_slug, comp_id)
    cur.execute("UPDATE dr_comp_set SET active = FALSE, updated_at = now() WHERE hotel_slug = %s AND comp_id = %s",
                (hotel_slug, comp_id))
    return get_comp(cur, hotel_slug, comp_id)


def match_comp(comps: list[dict], *, token: str | None = None, name: str | None = None) -> dict | None:
    """Token first, then the loose name key against name and aliases."""
    if token:
        for c in comps:
            if c.get("property_token") == token:
                return c
    key = norm_name(name) if name else ""
    if key:
        for c in comps:
            if key in {norm_name(c["name"]), *(norm_name(a) for a in c.get("aliases") or [])}:
                return c
    return None


# ─── Store ───


def record_rates(cur: Any, hotel: Any, rates: list[CompRate]) -> dict:
    """The one writer. Upserts per (comp, night, los, adults, source,
    hotel-local capture day): a second fetch the same day replaces the first
    rather than stacking rows. Rates for unknown comps or in another
    currency than the hotel's are dropped (counted), never guessed."""
    written = skipped = 0
    known = {r["comp_id"] for r in clefs_db.fetch_all(
        "SELECT comp_id FROM dr_comp_set WHERE hotel_slug = %s", (hotel.slug,), cur=cur)}
    for r in rates:
        if r.comp_id not in known or r.source not in SOURCES or (r.lowest_minor is None and not r.sold_out):
            skipped += 1
            continue
        if r.currency.upper() != str(hotel.currency).upper():
            skipped += 1
            continue
        cur.execute(
            """
            INSERT INTO dr_comp_rates (hotel_slug, comp_id, stay_date, los, adults, captured_at, captured_on, source,
                                       lowest_minor, currency, sold_out, by_source, indicative, fx_converted,
                                       original_minor, original_currency, query, import_batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, comp_id, stay_date, los, adults, source, captured_on) DO UPDATE SET
                captured_at = EXCLUDED.captured_at, lowest_minor = EXCLUDED.lowest_minor,
                sold_out = EXCLUDED.sold_out, by_source = EXCLUDED.by_source,
                fx_converted = EXCLUDED.fx_converted, original_minor = EXCLUDED.original_minor,
                original_currency = EXCLUDED.original_currency, query = EXCLUDED.query,
                import_batch_id = EXCLUDED.import_batch_id
            """,
            (hotel.slug, r.comp_id, r.stay_date, r.los, r.adults, r.captured_at, local_date(hotel, r.captured_at),
             r.source, None if r.sold_out else r.lowest_minor, r.currency.upper(), r.sold_out,
             clefs_db.Json(r.by_source[:20]), r.fx_converted, r.original_minor, r.original_currency, r.query,
             r.import_batch_id),
        )
        written += 1
    return {"written": written, "skipped": skipped}


def purge_old(cur: Any, *, today: date) -> int:
    """Comp rates for nights more than RETENTION_DAYS past (enough for a
    same-season year-on-year look)."""
    cur.execute("DELETE FROM dr_comp_rates WHERE stay_date < %s", (today - timedelta(days=RETENTION_DAYS),))
    return cur.rowcount


# ─── Reads ───

_RANK_SQL = ("CASE r.source WHEN 'CSV_LIGHTHOUSE' THEN 0 WHEN 'CSV_RATEGAIN' THEN 0 WHEN 'MAKCORPS' THEN 1 "
             "WHEN 'SERPAPI' THEN 2 ELSE 3 END")


def latest_rows(cur: Any, hotel_slug: str, start: date, end: date, *, los: int = 1, adults: int = 2,
                max_age_days: int | None = 14) -> list[dict]:
    """The current view per active comp and night in [start, end]: the most
    recent capture day, then the most trusted source that day. Captures
    older than max_age_days are ignored (a stale price is not a signal)."""
    since = clock.now() - timedelta(days=max_age_days) if max_age_days else None
    return clefs_db.fetch_all(
        f"""
        SELECT DISTINCT ON (r.stay_date, r.comp_id)
               r.*, c.name AS comp_name, c.is_demo
          FROM dr_comp_rates r
          JOIN dr_comp_set c ON c.hotel_slug = r.hotel_slug AND c.comp_id = r.comp_id AND c.active
         WHERE r.hotel_slug = %s AND r.stay_date BETWEEN %s AND %s AND r.los = %s AND r.adults = %s
           AND (%s::timestamptz IS NULL OR r.captured_at >= %s::timestamptz)
         ORDER BY r.stay_date, r.comp_id, r.captured_on DESC, {_RANK_SQL}, r.captured_at DESC
        """,
        (hotel_slug, start, end, los, adults, since, since), cur=cur)


def _cheapest_channel(by_source: list[dict] | None) -> str | None:
    priced = [s for s in by_source or [] if s.get("minor")]
    return min(priced, key=lambda s: s["minor"]).get("source") if priced else None


def rate_out(r: dict) -> dict:
    return {
        "competitor": r["comp_name"], "comp_id": r["comp_id"], "stay_date": r["stay_date"].isoformat(),
        "channel": _cheapest_channel(r["by_source"]), "rate": money_str(r["lowest_minor"], r["currency"]),
        "currency": r["currency"], "sold_out": r["sold_out"], "source": r["source"],
        "fetched_at": r["captured_at"].isoformat(), "indicative": True, "label": INDICATIVE_LABEL,
        "fx_converted": r["fx_converted"], "is_demo": r["is_demo"],
        "by_source": [{"source": s.get("source"), "rate": money_str(s.get("minor"), r["currency"]),
                       "official": bool(s.get("official"))} for s in r["by_source"] or []],
    }


def rates_for_night(cur: Any, hotel_slug: str, stay_date: date) -> list[dict]:
    """demand.api.comp_rates: one row per comp for the night, cheapest first,
    sold-out comps last."""
    rows = [rate_out(r) for r in latest_rows(cur, hotel_slug, stay_date, stay_date)]
    return sorted(rows, key=lambda x: (x["sold_out"], Decimal(x["rate"] or 0), x["competitor"]))


def snapshot(cur: Any, hotel_slug: str, stay_dates: list[date]) -> dict:
    """demand.api.comp_rates_snapshot: {iso date: [{competitor, rate,
    currency, fetched_at, sold_out, source}]}; nights with no data are left
    out (a quote shows what was seen, never an empty strip)."""
    if not stay_dates:
        return {}
    wanted = set(stay_dates)
    out: dict[str, list[dict]] = {}
    for r in latest_rows(cur, hotel_slug, min(wanted), max(wanted)):
        if r["stay_date"] in wanted:
            row = rate_out(r)
            out.setdefault(row["stay_date"], []).append(
                {k: row[k] for k in ("competitor", "comp_id", "rate", "currency", "fetched_at", "sold_out", "source")})
    return out


def night_summary(rows: list[dict]) -> dict:
    """Min / median / max of the priced comps plus the sold-out count for one
    night (the CompRateStrip band and the comp_z input)."""
    priced = sorted(r["lowest_minor"] for r in rows if not r["sold_out"] and r["lowest_minor"])
    ccy = rows[0]["currency"] if rows else None
    return {
        "comps": len(rows), "priced": len(priced), "sold_out": sum(1 for r in rows if r["sold_out"]),
        "min": money_str(priced[0], ccy) if priced else None,
        "median": money_str(int(round(statistics.median(priced))), ccy) if priced else None,
        "max": money_str(priced[-1], ccy) if priced else None,
        "median_minor": int(round(statistics.median(priced))) if priced else None,
        "currency": ccy, "captured_at": max(r["captured_at"] for r in rows).isoformat() if rows else None,
        "sources": sorted({r["source"] for r in rows}), "indicative": True, "label": INDICATIVE_LABEL,
    }


def calendar(cur: Any, hotel_slug: str, start: date, end: date) -> list[dict]:
    """Per night in [start, end] with any comp data: the summary band."""
    by_night: dict[date, list[dict]] = {}
    for r in latest_rows(cur, hotel_slug, start, end):
        by_night.setdefault(r["stay_date"], []).append(r)
    return [{"stay_date": d.isoformat(), **night_summary(rows)} for d, rows in sorted(by_night.items())]


def night_history(cur: Any, hotel_slug: str, stay_date: date) -> list[dict]:
    """Every capture for one night (oldest first): how each comp moved."""
    rows = clefs_db.fetch_all(
        """SELECT r.*, c.name AS comp_name, c.is_demo FROM dr_comp_rates r
             JOIN dr_comp_set c ON c.hotel_slug = r.hotel_slug AND c.comp_id = r.comp_id
            WHERE r.hotel_slug = %s AND r.stay_date = %s ORDER BY r.captured_at, r.comp_id""",
        (hotel_slug, stay_date), cur=cur)
    return [rate_out(r) for r in rows]


# ─── Fetch plan and run ───


def fetch_dates(today: date, *, near: int = NEAR_DAYS, far: int = FAR_DAYS, every: int = FAR_EVERY) -> list[date]:
    """Nights 0..near daily; near+1..far when (lead % every == 0). As the
    lead time of a night drops by one a day, each far night comes up every
    `every` days: 31 + 90/3 = 61 area calls a day per query."""
    out = [today + timedelta(days=i) for i in range(near + 1)]
    out += [today + timedelta(days=i) for i in range(near + 1, far + 1) if i % every == 0]
    return out


def _adapters(hotel: Any, comps: list[dict], settings: dict, queries: list[str]) -> list[Any]:
    from clefs.modules.demand.adapters import makcorps, serpapi_hotels, xotelo

    out: list[Any] = [serpapi_hotels.SerpApiGoogleHotels(
        comps, queries=queries, self_token=settings["self_property_token"],
        fallback_max=settings["fallback_calls_max"], fallback_horizon=FALLBACK_HORIZON)]
    if xotelo.enabled_for(hotel) and any(c.get("xotelo_key") for c in comps):
        out.append(xotelo.Xotelo(comps))
    if makcorps.configured() and any(c.get("makcorps_id") for c in comps):
        out.append(makcorps.MakCorps(comps))
    return out


def run_fetch(cur: Any, hotel: Any, *, dates: list[date], budget_s: float = 90.0) -> dict:
    """Fetch every adapter for `dates` until the time budget runs out. The
    nights left over are returned in `remaining` (the job re-queues them),
    so a slow vendor never holds one job slot past its timeout."""
    import time as _time

    settings = get_rate_settings(cur, hotel.slug)
    if not settings["comp_fetch_enabled"]:
        return {"skipped": "disabled"}
    comps = list_comps(cur, hotel.slug)
    if not comps:
        return {"skipped": "no_comp_set"}
    queries = area_queries(cur, hotel)
    report: dict = {"nights": 0, "written": 0, "skipped_rows": 0, "adapters": {}, "remaining": []}
    deadline = _time.monotonic() + budget_s
    adapters = _adapters(hotel, comps, settings, queries)
    for i, d in enumerate(sorted(dates)):
        if _time.monotonic() > deadline:
            report["remaining"] = [x.isoformat() for x in sorted(dates)[i:]]
            break
        live = [a for a in adapters if not getattr(a, "stopped", None)]
        if not live:
            break
        for a in live:
            rates = a.fetch(hotel, [c["comp_id"] for c in comps], [d], currency=str(hotel.currency))
            res = record_rates(cur, hotel, rates)
            report["written"] += res["written"]
            report["skipped_rows"] += res["skipped"]
        report["nights"] += 1
    for a in adapters:
        report["adapters"][a.source] = getattr(a, "report", {})
    return report


# ─── Metrics ───

# dr.comp_median_rate_aed (02 spec 4.4): the median indicative comp price per
# stay night, over each comp's latest capture of that night (so re-fetching a
# night does not weight it twice). Sold-out captures carry no price.
_LATEST = ("FROM (SELECT DISTINCT ON (x.hotel_slug, x.stay_date, x.comp_id) x.* FROM dr_comp_rates x "
           "WHERE x.los = 1 AND x.adults = 2 ORDER BY x.hotel_slug, x.stay_date, x.comp_id, x.captured_at DESC) r "
           "JOIN dr_comp_set c ON c.hotel_slug = r.hotel_slug AND c.comp_id = r.comp_id")


def _register_metrics() -> None:
    from clefs.core.metrics import Dimension, Metric, register_metric

    register_metric(Metric(
        key="dr.comp_median_rate_aed", label="Comp set median rate (indicative)",
        description="Median lowest public price across the comp set per stay night, latest capture per comp; "
                    "indicative, not contract rates",
        unit="money", source=_LATEST, hotel_column="r.hotel_slug", time_column="r.stay_date", time_kind="date",
        measure="percentile_cont(0.5) WITHIN GROUP (ORDER BY r.lowest_minor)", where="NOT r.sold_out",
        dimensions=(Dimension("competitor", "Competitor", "c.name"), Dimension("source", "Source", "r.source")),
        permission="demand.view", currency="hotel", synonyms=("comp rates", "competitor prices", "compset rate"),
    ))


_register_metrics()


# ─── Jobs ───

FETCH_BUDGET_S = 90.0


def _continue(ctx: jobs.JobCtx, report: dict) -> dict:
    """Queue the nights a run had no time for as a follow-up task."""
    rest = report.get("remaining") or []
    if rest:
        jobs.enqueue("dr.comp_rates_fetch_more", {"dates": rest}, hotel_slug=ctx.hotel_slug,
                     dedupe_key=f"dr.comp_rates_fetch_more:{ctx.hotel_slug}:{rest[0]}", cur=ctx.cur)
        report["continued"] = len(rest)
        report["remaining"] = rest[:5]
    return report


@jobs.scheduled("dr.comp_rates_fetch", jobs.daily_at("04:00"), timeout_s=150)
def _comp_rates_fetch(ctx: jobs.JobCtx) -> dict:
    today = local_date(ctx.hotel)
    return _continue(ctx, run_fetch(ctx.cur, ctx.hotel, dates=fetch_dates(today), budget_s=FETCH_BUDGET_S))


@jobs.task("dr.comp_rates_fetch_more", max_attempts=3, timeout_s=150)
def _comp_rates_fetch_more(ctx: jobs.JobCtx, payload: dict) -> dict:
    today = local_date(ctx.hotel)
    dates = sorted({d for d in (date.fromisoformat(x) for x in payload.get("dates") or []) if d >= today})
    if not dates:
        return {"skipped": "nothing_left"}
    return _continue(ctx, run_fetch(ctx.cur, ctx.hotel, dates=dates, budget_s=FETCH_BUDGET_S))


@jobs.scheduled("dr.rates_retention", jobs.daily_at("03:45", tz="utc"), per_hotel=False)
def _rates_retention(ctx: jobs.JobCtx) -> dict:
    from clefs.modules.demand import weather

    today = clock.now().date()
    return {"comp_rates": purge_old(ctx.cur, today=today), "weather": weather.purge_old(ctx.cur, now=clock.now())}
