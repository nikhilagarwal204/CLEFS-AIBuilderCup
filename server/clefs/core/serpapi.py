"""
core.serpapi: the one SerpApi client in the product (04 spec F5, Canon K3).

    from clefs.core import serpapi

    params = serpapi.google_hotels_area_params(hotel, q="hotels in Deira Dubai",
                                               check_in=d, check_out=d + timedelta(days=1))
    try:
        data = serpapi.search("google_hotels", params, hotel_slug=hotel.slug, purpose="demand.comp_rates")
    except serpapi.BudgetExhausted:
        ...  # skip this fetch

Why one client:
  - Sharing. The cache key is sha1(engine + sorted normalised params, minus
    api_key and no_cache), so Demand's 04:00 comp-rate fetch and Visibility's
    05:30 Google Hotels job, building identical params through
    google_hotels_area_params, pay for one call between them.
  - One budget. Every call (cache hits included) lands in the serpapi_calls
    ledger, so SERPAPI_MONTHLY_BUDGET_USD caps the whole product, and the public
    lead magnet (purpose 'public_check') has its own PUBLIC_SERPAPI_MONTHLY_CAP.
    No lane calls SerpApi directly.
  - Vendor risk (Canon 20). SerpApi fetches Google's consumer pages; a kill
    switch is simply unsetting SERPAPI_KEY (every caller then sees
    NotConfigured and degrades).

Money: costs are micro-units (K3), cost_micros BIGINT + cost_currency 'USD'.
The price of one search is SERPAPI_COST_PER_SEARCH_USD (default 0.015, the
Developer plan's $75 per 5,000); SerpApi does not charge failed searches, so a
failed call is settled to billed=false.

Tests and demos: `fake = serpapi.use_fake({...}, {...})` installs an httpx
MockTransport that answers from a queue and records each request's params
(api_key masked); `serpapi.set_transport(None)` restores the real network.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from clefs import db, jobs, logsafe
from clefs.core import clock
from clefs.core.errors import ClefsError, NotConfigured
from clefs.settings import get_settings

log = logging.getLogger("clefs.serpapi")

# The api key rides in the query string and httpx logs every request URL at
# INFO: redact it on the "httpx" logger whichever process imports us (the
# API and the worker also quiet httpx entirely; see clefs.logsafe).
logsafe.install()

API_URL = "https://serpapi.com/search.json"
DEFAULT_MAX_AGE = timedelta(hours=20)
# How long a cached response is kept for any caller (freshness is each call's
# own max_age); the daily purge drops rows past it.
CACHE_RETENTION = timedelta(days=7)
# The ledger is the evidence for vendor-cost reports; keep 13 months so a
# year-on-year month is always there.
LEDGER_RETENTION = timedelta(days=400)
# Google Hotels searches regularly take 5-15 s upstream.
TIMEOUT = httpx.Timeout(30.0, connect=5.0)

PUBLIC_PURPOSE = "public_check"
# Billed public_check searches per calendar month when PUBLIC_SERPAPI_MONTHLY_CAP
# is unset. The lead magnet is anonymous, so its spend is bounded by default
# (fails closed); 0 switches public SerpApi use off.
DEFAULT_PUBLIC_MONTHLY_CAP = 300
MICROS_PER_USD = 1_000_000
CURRENCY = "USD"

# Never part of a cache key: the credential, and the "skip SerpApi's own cache"
# switch (a forced refresh must overwrite the entry everyone else reads).
EXCLUDED_PARAMS = frozenset({"api_key", "no_cache"})
# Transport-level params this client always sets itself (engine is the first
# argument; every call is a synchronous JSON search), so they never split keys.
_TRANSPORT_PARAMS = frozenset({"engine", "output", "async"})
_ENGINE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_PURPOSE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
_BUDGET_LOCK = "clefs_serpapi_budget"


# ─── Errors ───


class BudgetExhausted(ClefsError):
    """The month's SerpApi budget (global, or the public_check cap) is spent.
    Callers treat it as a skipped fetch; the public lead magnet lets it
    through as 503 budget_exhausted (04 spec 5.9)."""

    def __init__(self, scope: str, *, limit: int, used: int):
        what = "public check" if scope == PUBLIC_PURPOSE else "SerpApi"
        super().__init__(503, f"The monthly {what} budget is used up", "budget_exhausted")
        self.scope = scope
        self.limit = limit
        self.used = used


class SerpApiError(ClefsError):
    """SerpApi failed or refused the search (not billed). The message never
    carries the api key or the request URL."""

    def __init__(self, detail: str, *, status_code: int | None = None):
        super().__init__(502, f"SerpApi: {detail}", "serpapi_error")
        self.status_code = status_code


# ─── Params and cache keys ───


def _norm_value(v: Any) -> str:
    """One canonical string per value, the way SerpApi receives it in the
    query string: 2 == "2" == 2.0, True == "true", date(2026, 10, 2) ==
    "2026-10-02". Whitespace inside text is collapsed, case is kept
    (property_token and next_page_token are case-sensitive)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v))
        if d == d.to_integral_value():
            return str(int(d))
        return format(d.normalize(), "f")
    if isinstance(v, (list, tuple)):
        return ",".join(_norm_value(x) for x in v)
    return " ".join(str(v).split())


def normalize_params(params: Mapping[str, Any]) -> dict[str, str]:
    """Sorted, stringified params without api_key, no_cache, the transport
    params (engine, output, async) and None values. This is what the cache row
    stores and what the key hashes."""
    out: dict[str, str] = {}
    for k, v in params.items():
        key = str(k).strip().lower()
        if not key or key in EXCLUDED_PARAMS or key in _TRANSPORT_PARAMS or v is None:
            continue
        out[key] = _norm_value(v)
    return dict(sorted(out.items()))


def _check_engine(engine: str, params: Mapping[str, Any]) -> str:
    engine = (engine or "").strip().lower()
    if not _ENGINE_RE.match(engine):
        raise ValueError(f"not a SerpApi engine name: {engine!r}")
    inner = params.get("engine")
    if inner is not None and str(inner).strip().lower() != engine:
        raise ValueError(f"params say engine={inner!r} but search() was called with {engine!r}")
    for bad in ("async", "output"):
        if bad in params and _norm_value(params[bad]) not in ("false", "json"):
            raise ValueError(f"core.serpapi only makes synchronous JSON searches ({bad}={params[bad]!r})")
    return engine


def cache_key(engine: str, params: Mapping[str, Any]) -> str:
    """sha1 hex of the engine plus the sorted, normalised params (F5)."""
    engine = _check_engine(engine, params)
    return hashlib.sha1(f"{engine}?{urlencode(list(normalize_params(params).items()))}".encode()).hexdigest()


def _check_purpose(purpose: str) -> str:
    if not isinstance(purpose, str) or len(purpose) > 64 or not _PURPOSE_RE.match(purpose):
        raise ValueError(f"purpose must look like '<module>.<what>' or 'public_check', got {purpose!r}")
    return purpose


def _truthy(v: Any) -> bool:
    return _norm_value(v).lower() in ("true", "1", "yes", "on") if v is not None else False


# ─── Shared parameter builder ───


def google_hotels_area_params(hotel: Any, *, q: str, check_in: date, check_out: date) -> dict[str, Any]:
    """The one way to build a Google Hotels area search (F5). Demand and
    Visibility must both use it, so identical requests share one billed call.
    `hotel` is a clefs.core.hotels.Hotel (or anything with a currency)."""
    if not isinstance(check_in, date) or not isinstance(check_out, date):
        raise ValueError("check_in and check_out must be dates")
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")
    currency = hotel.get("currency") if isinstance(hotel, Mapping) else getattr(hotel, "currency", None)
    if not currency:
        raise ValueError("the hotel has no currency")
    return {
        "engine": "google_hotels",
        "q": " ".join(str(q).split()),
        "check_in_date": check_in.isoformat(),
        "check_out_date": check_out.isoformat(),
        "currency": str(currency).upper(),
        "gl": "ae",
        "hl": "en",
        "adults": 2,
    }


# ─── Money ───


def usd_to_micros(amount: Decimal | int | str) -> int:
    return int((Decimal(str(amount)) * MICROS_PER_USD).to_integral_value(rounding=ROUND_HALF_UP))


def cost_per_search_micros() -> int:
    # A Settings field (Decimal, default 0.015): a malformed value already
    # failed at startup, so there is nothing to second-guess here.
    return max(0, usd_to_micros(get_settings().SERPAPI_COST_PER_SEARCH_USD))


def public_monthly_cap() -> int:
    cap = get_settings().PUBLIC_SERPAPI_MONTHLY_CAP
    return DEFAULT_PUBLIC_MONTHLY_CAP if cap is None else max(0, int(cap))


def month_bounds(at: datetime | None = None) -> tuple[datetime, datetime]:
    """[first instant, first instant of next month) of `at`'s UTC calendar
    month. The budget is global, so it follows UTC, not a hotel's timezone."""
    t = (at or clock.now()).astimezone(timezone.utc)
    start = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def month_spend_micros(cur: Any, *, hotel_slug: str | None = None, purpose: str | None = None, at: datetime | None = None) -> int:
    """Billed SerpApi spend in USD micros for `at`'s month (default now),
    optionally for one hotel or purpose. Reserved (in-flight) calls count, so
    this never under-reports. Visibility's budget check (04 spec 5.10) adds it
    to its own vis_api_costs."""
    start, end = month_bounds(at)
    sql = "SELECT COALESCE(SUM(cost_micros), 0) AS s FROM serpapi_calls WHERE billed AND at >= %s AND at < %s AND cost_currency = %s"
    args: list[Any] = [start, end, CURRENCY]
    if hotel_slug is not None:
        sql += " AND hotel_slug = %s"
        args.append(hotel_slug)
    if purpose is not None:
        sql += " AND purpose = %s"
        args.append(purpose)
    cur.execute(sql, args)
    return int(cur.fetchone()["s"])


def _check_budget(cur: Any, purpose: str, cost: int, now: datetime) -> None:
    start, end = month_bounds(now)
    budget = get_settings().SERPAPI_MONTHLY_BUDGET_USD
    if budget is not None:
        limit = usd_to_micros(budget)
        used = month_spend_micros(cur, at=now)
        if used + cost > limit:
            raise BudgetExhausted("global", limit=limit, used=used)
    if purpose == PUBLIC_PURPOSE:
        cap = public_monthly_cap()
        cur.execute(
            "SELECT COUNT(*) AS n FROM serpapi_calls WHERE billed AND purpose = %s AND at >= %s AND at < %s",
            (PUBLIC_PURPOSE, start, end),
        )
        used_n = int(cur.fetchone()["n"])
        if used_n + 1 > cap:
            raise BudgetExhausted(PUBLIC_PURPOSE, limit=cap, used=used_n)


# ─── Transport ───

_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | FakeSerpApi | None) -> None:
    """Route SerpApi HTTP through `transport` (tests, demos); None = network."""
    global _transport
    _transport = httpx.MockTransport(transport.handle) if isinstance(transport, FakeSerpApi) else transport


class FakeSerpApi:
    """Answers SerpApi requests from a queue. Each item is a dict (200 JSON;
    search_metadata.status 'Success' is added when missing), a (status, dict)
    tuple, an exception to raise (httpx.ConnectTimeout(...)), or a callable
    taking the request params and returning one of those. An empty queue
    answers `default` (a minimal successful result when None).

    `requests` holds each request's query params with api_key masked, so a
    test can assert what was sent without the key ever being stored."""

    def __init__(self, *responses: Any, default: dict | None = None) -> None:
        self.queue: list[Any] = list(responses)
        self.default = default
        self.requests: list[dict[str, str]] = []

    def push(self, *responses: Any) -> FakeSerpApi:
        self.queue.extend(responses)
        return self

    @property
    def calls(self) -> int:
        return len(self.requests)

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append({k: ("***" if k == "api_key" else v) for k, v in params.items()})
        if self.queue:
            item = self.queue.pop(0)
        elif self.default is not None:
            item = self.default
        else:
            item = {"search_parameters": {k: v for k, v in params.items() if k != "api_key"}}
        if callable(item) and not isinstance(item, (dict, tuple)):
            item = item(params)
        if isinstance(item, BaseException):
            raise item
        status, body = item if isinstance(item, tuple) else (200, item)
        if status == 200 and isinstance(body, dict):
            body = {**body, "search_metadata": {"status": "Success", **(body.get("search_metadata") or {})}}
        return httpx.Response(status, json=body, request=request)


def use_fake(*responses: Any, default: dict | None = None) -> FakeSerpApi:
    """Install a FakeSerpApi and return it (undo with set_transport(None))."""
    fake = FakeSerpApi(*responses, default=default)
    set_transport(fake)
    return fake


def _fetch(engine: str, norm: dict[str, str], api_key: str, force: bool) -> tuple[dict | None, str | None, int | None]:
    """(body, error, http_status). Billing follows SerpApi: a 200 whose
    search_metadata.status is not 'Error' is charged, anything else is not."""
    query = {"engine": engine, **norm, "output": "json", "api_key": api_key}
    if force:
        query["no_cache"] = "true"
    try:
        with httpx.Client(timeout=TIMEOUT, transport=_transport, follow_redirects=False) as client:
            r = client.get(API_URL, params=query, headers={"Accept": "application/json"})
    except httpx.TimeoutException:
        return None, "timed out", None
    except httpx.HTTPError as exc:
        # str(exc) can quote the request URL, which carries the api key.
        return None, f"network error ({type(exc).__name__})", None
    try:
        body = r.json()
    except ValueError:
        body = None
    if r.status_code != 200 or not isinstance(body, dict):
        msg = body.get("error") if isinstance(body, dict) else None
        return None, f"HTTP {r.status_code}: {msg or 'no JSON body'}"[:500], r.status_code
    if (body.get("search_metadata") or {}).get("status") == "Error":
        return None, str(body.get("error") or "SerpApi reported an error")[:500], r.status_code
    return body, None, r.status_code


# ─── Search ───


def _ledger(
    cur: Any,
    *,
    hotel_slug: str | None,
    purpose: str,
    engine: str,
    key: str,
    cache_hit: bool,
    billed: bool,
    cost: int,
    status: str,
    now: datetime,
) -> int:
    cur.execute(
        """
        INSERT INTO serpapi_calls (hotel_slug, purpose, engine, cache_key, cache_hit, billed, cost_micros,
                                   cost_currency, status, at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (hotel_slug, purpose, engine, key, cache_hit, billed, cost, CURRENCY, status, now),
    )
    return int(cur.fetchone()["id"])


def _cached(cur: Any, key: str, max_age: timedelta, now: datetime) -> dict | None:
    cur.execute(
        "SELECT response FROM serpapi_cache WHERE cache_key = %s AND fetched_at > %s",
        (key, now - max_age),
    )
    row = cur.fetchone()
    return row["response"] if row else None


def search(
    engine: str,
    params: Mapping[str, Any],
    *,
    hotel_slug: str | None,
    purpose: str,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> dict:
    """Run (or reuse) one SerpApi search and return its JSON.

    A cached response fetched within `max_age` is returned without a call and
    ledgered as a free cache hit (even when the budget is spent or no key is
    set). Otherwise the call is budget-checked and reserved in its own
    transaction, made over HTTP outside any transaction, then cached and
    settled. `params["no_cache"]` truthy skips the cache read (and asks SerpApi
    for a fresh page) but still refreshes the shared entry.

    Raises NotConfigured (no SERPAPI_KEY), BudgetExhausted, SerpApiError,
    ValueError (bad engine, purpose or params). Opens its own db() blocks, so
    it takes no cursor: a caller's transaction never waits on SerpApi."""
    engine = _check_engine(engine, params)
    purpose = _check_purpose(purpose)
    norm = normalize_params(params)
    key = cache_key(engine, params)
    force = _truthy(params.get("no_cache"))
    now = clock.now()

    if not force and max_age > timedelta(0):
        with db.db() as cur:
            hit = _cached(cur, key, max_age, now)
            if hit is not None:
                _ledger(cur, hotel_slug=hotel_slug, purpose=purpose, engine=engine, key=key, cache_hit=True,
                        billed=False, cost=0, status="cache_hit", now=now)
                return hit

    api_key = get_settings().SERPAPI_KEY
    if not api_key:
        raise NotConfigured("SerpApi (SERPAPI_KEY)")

    cost = cost_per_search_micros()
    with db.db() as cur:
        # Budget check and reservation under one lock, so two workers at the
        # edge of the budget cannot both spend the last search.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_BUDGET_LOCK,))
        _check_budget(cur, purpose, cost, now)
        call_id = _ledger(cur, hotel_slug=hotel_slug, purpose=purpose, engine=engine, key=key, cache_hit=False,
                          billed=True, cost=cost, status="reserved", now=now)

    t0 = time.monotonic()
    body: dict | None = None
    error: str | None = None
    http_status: int | None = None
    try:
        body, error, http_status = _fetch(engine, norm, api_key, force)
        body = _strip_nul(body)
    except BaseException:
        error = "internal error during the SerpApi call"
        raise
    finally:
        latency = int((time.monotonic() - t0) * 1000)
        _settle(call_id, key=key, engine=engine, norm=norm, body=body, error=error, latency=latency)

    if error is not None or body is None:
        log.warning("serpapi %s for %s failed: %s", engine, purpose, error)
        raise SerpApiError(error or "no response", status_code=http_status)
    return body


def _strip_nul(v: Any) -> Any:
    """JSONB refuses the NUL character, and scraped page text can carry one;
    without this a billed search would fail to cache."""
    if isinstance(v, str):
        return v.replace("\x00", "") if "\x00" in v else v
    if isinstance(v, dict):
        return {_strip_nul(k): _strip_nul(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip_nul(x) for x in v]
    return v


def _settle(call_id: int, *, key: str, engine: str, norm: dict[str, str], body: dict | None, error: str | None,
            latency: int) -> None:
    fetched = clock.now()
    with db.db() as cur:
        if body is not None and error is None:
            status = "empty" if body.get("error") else "ok"
            cur.execute(
                """
                INSERT INTO serpapi_cache (cache_key, engine, params, response, status, fetched_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cache_key) DO UPDATE SET
                    engine = EXCLUDED.engine, params = EXCLUDED.params, response = EXCLUDED.response,
                    status = EXCLUDED.status, fetched_at = EXCLUDED.fetched_at, expires_at = EXCLUDED.expires_at
                """,
                (key, engine, db.Json(norm), db.Json(body), status, fetched, fetched + CACHE_RETENTION),
            )
            cur.execute("UPDATE serpapi_calls SET status = 'ok', latency_ms = %s WHERE id = %s", (latency, call_id))
        else:
            cur.execute(
                "UPDATE serpapi_calls SET status = 'error', billed = FALSE, cost_micros = 0, error = %s, latency_ms = %s "
                "WHERE id = %s",
                ((error or "no response")[:500], latency, call_id),
            )


# ─── Housekeeping ───


def purge(cur: Any, *, now: datetime | None = None) -> dict:
    """Drop cache rows past expires_at and ledger rows past LEDGER_RETENTION."""
    t = now or clock.now()
    cur.execute("DELETE FROM serpapi_cache WHERE expires_at < %s", (t,))
    cache = cur.rowcount
    cur.execute("DELETE FROM serpapi_calls WHERE at < %s", (t - LEDGER_RETENTION,))
    return {"cache_deleted": cache, "calls_deleted": cur.rowcount}


# Registered whenever the worker or the cron tick runs: clefs.core.serpapi is
# in jobs.CORE_REGISTRANTS, so the purge runs even before Demand or Visibility
# import this module.
@jobs.scheduled("core.serpapi.purge", jobs.daily_at("03:50", tz="utc"), per_hotel=False)
def _purge_job(ctx: jobs.JobCtx) -> dict:
    return purge(ctx.cur, now=ctx.now)
