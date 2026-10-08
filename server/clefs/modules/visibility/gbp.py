"""
Google Business Profile: connection state, the location picker, daily
Performance metrics, derived monthly KPIs, monthly search keywords and the
UTM websiteUri patch (04 spec 5.4). Lodging lives in lodging.py.

Access. GBP API approval belongs to Clefs' Google Cloud project, not to each
hotel: 0 QPM means not approved, 300 QPM approved. Until then every tile
reads "not connected (project not approved, 0 QPM)". The project quota is
read from GBP_PROJECT_QPM (an ops setting mirroring the Cloud console).
# FOUNDATION-REQUEST: add GBP_PROJECT_QPM (default 0) to Settings and .env.example.

The OAuth connection is core.oauth provider `google_business`, shared with
Reviews, so an owner connects Google once. No Q&A: that API was discontinued
on 2025-11-03.

Retention. Raw GBP API rows (vis_gbp_daily, vis_gbp_keywords) expire 30 days
after fetch; vis_gbp_kpis_monthly holds Clefs' derived KPIs and is kept.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from clefs.core import clock
from clefs.settings import env

PROVIDER = "google_business"
ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
INFO_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1"
PERF_BASE = "https://businessprofileperformance.googleapis.com/v1"
RAW_TTL = timedelta(days=30)
REFETCH_DAYS = 10          # the data lag is undocumented, so each run re-reads 10 days
WINDOW_DAYS = 28

IMPRESSIONS = (
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS", "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS", "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
)
ACTIONS = ("BUSINESS_CONVERSATIONS", "BUSINESS_DIRECTION_REQUESTS", "CALL_CLICKS", "WEBSITE_CLICKS")
# BUSINESS_BOOKINGS (Reserve with Google only) and BUSINESS_FOOD_ORDERS
# (deprecated) are deliberately excluded.
DAILY_METRICS = IMPRESSIONS + ACTIONS

UTM = {"utm_source": "google", "utm_medium": "organic", "utm_campaign": "gbp_listing"}
REACH_LABEL = "user-days"


# ─── Connection state ───


def project_qpm() -> int:
    try:
        return int(env("GBP_PROJECT_QPM", "0") or 0)
    except (TypeError, ValueError):
        return 0


def connection_state(cur: Any, hotel_slug: str) -> dict:
    """{status, qpm, location, message}: status is project_not_approved,
    not_connected, pick_location or connected (checked in that order: without
    project approval nothing a hotel does can help)."""
    from clefs.core import oauth
    from clefs.modules.visibility import store

    qpm = project_qpm()
    conn = oauth.get_connection(cur, hotel_slug, PROVIDER)
    location = store.get_profile(cur, hotel_slug).get("gbp_location")
    if qpm <= 0:
        status, msg = "project_not_approved", "not connected (project not approved, 0 QPM)"
    elif not conn or conn.get("status") != "active":  # expired/revoked need a reconnect
        status, msg = "not_connected", "Connect GBP (OAuth); the location must be verified"
    elif not location:
        status, msg = "pick_location", "Pick location"
    else:
        status, msg = "connected", None
    return {"status": status, "qpm": qpm, "location": location, "message": msg,
            "connection": None if not conn else {"status": conn.get("status"), "connected_at":
                                                 _iso(conn.get("connected_at"))}}


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def ready(cur: Any, hotel_slug: str) -> tuple[bool, dict]:
    st = connection_state(cur, hotel_slug)
    return st["status"] == "connected", st


def call(cur: Any, hotel_slug: str, method: str, url: str, **kw: Any) -> dict:
    """An authorised GBP call. get_access_token refreshes near expiry and
    raises NotConnected when the grant was revoked."""
    from clefs.core import oauth
    from clefs.modules.visibility import http

    token = oauth.get_access_token(cur, hotel_slug, PROVIDER)
    headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {token}"}
    return http.request_json("gbp", method, url, headers=headers, **kw)


# ─── Location picker ───


def list_locations(cur: Any, hotel_slug: str) -> list[dict]:
    """accounts.list then accounts.locations.list for each account (5.4).
    [{location, title, address, website_uri, account}]."""
    out: list[dict] = []
    accounts = call(cur, hotel_slug, "GET", ACCOUNTS_URL).get("accounts") or []
    for acc in accounts[:20]:
        name = acc.get("name")
        if not name:
            continue
        token = None
        for _ in range(10):  # 100 per page, 1,000 locations is plenty for a hotel account
            params = {"readMask": "name,title,storefrontAddress,websiteUri", "pageSize": 100}
            if token:
                params["pageToken"] = token
            page = call(cur, hotel_slug, "GET", f"{INFO_BASE}/{name}/locations", params=params)
            for loc in page.get("locations") or []:
                addr = loc.get("storefrontAddress") or {}
                out.append({"location": loc.get("name"), "title": loc.get("title"),
                            "address": ", ".join([*(addr.get("addressLines") or []), addr.get("locality") or ""]
                                                 ).strip(", ") or None,
                            "website_uri": loc.get("websiteUri"), "account": acc.get("accountName") or name})
            token = page.get("nextPageToken")
            if not token:
                break
    return out


# ─── Performance: daily metrics ───


def _range_params(prefix: str, start: date, end: date) -> list[tuple[str, Any]]:
    return [(f"{prefix}.startDate.year", start.year), (f"{prefix}.startDate.month", start.month),
            (f"{prefix}.startDate.day", start.day), (f"{prefix}.endDate.year", end.year),
            (f"{prefix}.endDate.month", end.month), (f"{prefix}.endDate.day", end.day)]


def parse_daily(body: dict) -> dict[tuple[date, str], int]:
    """{(date, metric): value}. A dated value without `value` is a zero day
    (the API omits zeros)."""
    out: dict[tuple[date, str], int] = {}
    for multi in body.get("multiDailyMetricTimeSeries") or []:
        for series in multi.get("dailyMetricTimeSeries") or []:
            metric = series.get("dailyMetric")
            if metric not in DAILY_METRICS:
                continue
            for dv in (series.get("timeSeries") or {}).get("datedValues") or []:
                d = dv.get("date") or {}
                try:
                    day = date(int(d["year"]), int(d["month"]), int(d["day"]))
                except (KeyError, TypeError, ValueError):
                    continue
                out[(day, metric)] = int(dv.get("value") or 0)
    return out


def fetch_daily(cur: Any, hotel_slug: str, location: str, start: date, end: date) -> dict[tuple[date, str], int]:
    params = [("dailyMetrics", m) for m in DAILY_METRICS] + _range_params("dailyRange", start, end)
    body = call(cur, hotel_slug, "GET", f"{PERF_BASE}/{location}:fetchMultiDailyMetricsTimeSeries", params=params)
    return parse_daily(body)


def run_daily(cur: Any, hotel: Any) -> dict:
    """visibility.gbp_daily (06:00): re-fetch the last 10 days and upsert.
    Each row expires 30 days after THIS fetch, so a re-fetched day lives on
    only while Google still serves it."""
    from clefs.core.time import local_date

    ok, st = ready(cur, hotel.slug)
    if not ok:
        return {"skipped": st["status"]}
    end = local_date(hotel) - timedelta(days=1)
    start = end - timedelta(days=REFETCH_DAYS - 1)
    values = fetch_daily(cur, hotel.slug, st["location"], start, end)
    now = clock.now()
    for (day, metric), v in values.items():
        cur.execute(
            """
            INSERT INTO vis_gbp_daily (hotel_slug, date, metric, value, fetched_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, date, metric) DO UPDATE SET value = EXCLUDED.value,
                fetched_at = EXCLUDED.fetched_at, expires_at = EXCLUDED.expires_at
            """,
            (hotel.slug, day, metric, v, now, now + RAW_TTL),
        )
    return {"rows": len(values), "start": start.isoformat(), "end": end.isoformat()}


def expire_raw(cur: Any, *, now: datetime | None = None) -> dict:
    """visibility.gbp_raw_expire: delete raw GBP rows past expires_at. The
    derived vis_gbp_kpis_monthly rows are Clefs' own and stay."""
    now = now or clock.now()
    out = {}
    for table in ("vis_gbp_daily", "vis_gbp_keywords", "vis_lodging_snapshots"):
        cur.execute(f"DELETE FROM {table} WHERE expires_at <= %s", (now,))
        out[table] = cur.rowcount
    return out


# ─── Derived monthly KPIs ───


def _q(v: Decimal, places: str) -> Decimal:
    return v.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def summarise(values: dict[tuple[date, str], int], start: date, end: date) -> dict:
    """Impressions (reach, user-days) and actions over [start, end]."""
    impressions = sum(v for (d, m), v in values.items() if start <= d <= end and m in IMPRESSIONS)
    actions = sum(v for (d, m), v in values.items() if start <= d <= end and m in ACTIONS)
    website = sum(v for (d, m), v in values.items() if start <= d <= end and m == "WEBSITE_CLICKS")
    return {"impressions": impressions, "actions": actions, "website_clicks": website}


def compute_kpis(cur: Any, hotel: Any, month: date) -> dict:
    """visibility.gbp_kpis_monthly (day 3, 06:30) for `month` (the previous
    one). Reach = impressions over the 28 days ending on the month's last day
    vs the 28 days before; year-over-year re-fetches the year-ago window live
    (no raw history is stored). Action rate = (calls + website + directions +
    conversations) / impressions."""
    ok, st = ready(cur, hotel.slug)
    if not ok:
        return {"skipped": st["status"]}
    month = month.replace(day=1)
    month_end = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    win_start = month_end - timedelta(days=WINDOW_DAYS - 1)
    prior_end = win_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=WINDOW_DAYS - 1)
    values = fetch_daily(cur, hotel.slug, st["location"], min(prior_start, month), month_end)
    cur_w = summarise(values, win_start, month_end)
    prior = summarise(values, prior_start, prior_end)
    whole = summarise(values, month, month_end)
    try:
        yoy_end = month_end.replace(year=month_end.year - 1)
    except ValueError:  # 29 Feb
        yoy_end = month_end - timedelta(days=366)
    yoy_vals = fetch_daily(cur, hotel.slug, st["location"], yoy_end - timedelta(days=WINDOW_DAYS - 1), yoy_end)
    yoy = sum(v for (_, m), v in yoy_vals.items() if m in IMPRESSIONS) if yoy_vals else None
    rate = _q(Decimal(cur_w["actions"]) / cur_w["impressions"], "0.0001") if cur_w["impressions"] else None
    cur.execute(
        """
        INSERT INTO vis_gbp_kpis_monthly (hotel_slug, month, reach_user_days, reach_prior_user_days,
            reach_yoy_user_days, actions, action_rate, website_clicks, computed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, month) DO UPDATE SET reach_user_days = EXCLUDED.reach_user_days,
            reach_prior_user_days = EXCLUDED.reach_prior_user_days, reach_yoy_user_days = EXCLUDED.reach_yoy_user_days,
            actions = EXCLUDED.actions, action_rate = EXCLUDED.action_rate, website_clicks = EXCLUDED.website_clicks,
            computed_at = EXCLUDED.computed_at
        """,
        (hotel.slug, month, cur_w["impressions"], prior["impressions"], yoy, cur_w["actions"], rate,
         whole["website_clicks"], clock.now()),
    )
    return {"month": month.isoformat(), "reach_user_days": cur_w["impressions"], "action_rate": _s(rate),
            "website_clicks": whole["website_clicks"]}


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


# ─── Monthly search keywords (thresholds never summed) ───


def parse_keywords(body: dict) -> list[dict]:
    """[{keyword, value, threshold}] with exactly one of value/threshold set.
    Google hides small counts behind `threshold` ("fewer than N"): those rows
    are kept apart and never added to anything."""
    out = []
    for row in body.get("searchKeywordsCounts") or []:
        kw = (row.get("searchKeyword") or "").strip()
        iv = row.get("insightsValue") or {}
        if not kw:
            continue
        if iv.get("value") is not None:
            out.append({"keyword": kw, "value": int(iv["value"]), "threshold": None})
        elif iv.get("threshold") is not None:
            out.append({"keyword": kw, "value": None, "threshold": int(iv["threshold"])})
    return out


def _norm(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode() or (s or "")
    return " ".join(s.lower().replace("-", " ").split())


def is_branded(keyword: str, names: list[str]) -> bool:
    """A keyword naming the hotel (or an alias). "hotel" alone is generic, so
    the generic words are dropped from the brand before matching."""
    k = _norm(keyword)
    for n in names:
        brand = " ".join(w for w in _norm(n).split() if w not in {"hotel", "hotels", "the", "dubai", "(demo)"})
        if brand and brand in k:
            return True
    return False


def keyword_summary(rows: list[dict]) -> dict:
    """Branded share from exact values only, with the under-threshold count
    as a footnote: values 120 (branded), 45, 30 and 5 threshold rows show as
    "195 + 5 under threshold", branded share 120/195."""
    exact = [r for r in rows if r["value"] is not None]
    total = sum(r["value"] for r in exact)
    branded = sum(r["value"] for r in exact if r.get("is_branded"))
    under = sum(1 for r in rows if r["value"] is None)
    share = _q(Decimal(branded) / total, "0.0001") if total else None
    return {"total_exact": total, "branded_exact": branded, "under_threshold": under, "branded_share": share,
            "display": f"{total:,} + {under} under threshold" if under else f"{total:,}"}


def run_keywords(cur: Any, hotel: Any, month: date) -> dict:
    """visibility.gbp_keywords_monthly (day 5, 06:00) for `month`."""
    from clefs.modules.visibility import store

    ok, st = ready(cur, hotel.slug)
    if not ok:
        return {"skipped": st["status"]}
    month = month.replace(day=1)
    rng = [("monthlyRange.startMonth.year", month.year), ("monthlyRange.startMonth.month", month.month),
           ("monthlyRange.endMonth.year", month.year), ("monthlyRange.endMonth.month", month.month)]
    rows: list[dict] = []
    token = None
    for _ in range(50):
        params = [*rng, ("pageSize", 100)] + ([("pageToken", token)] if token else [])
        body = call(cur, hotel.slug, "GET", f"{PERF_BASE}/{st['location']}/searchkeywords/impressions/monthly",
                    params=params)
        rows += parse_keywords(body)
        token = body.get("nextPageToken")
        if not token:
            break
    profile = store.get_profile(cur, hotel.slug)
    names = [hotel.name, *(profile.get("brand_aliases") or [])]
    now = clock.now()
    for r in rows:
        r["is_branded"] = is_branded(r["keyword"], names)
        cur.execute(
            """
            INSERT INTO vis_gbp_keywords (hotel_slug, month, keyword, value, threshold, is_branded, fetched_at,
                                          expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, month, keyword) DO UPDATE SET value = EXCLUDED.value,
                threshold = EXCLUDED.threshold, is_branded = EXCLUDED.is_branded, fetched_at = EXCLUDED.fetched_at,
                expires_at = EXCLUDED.expires_at
            """,
            (hotel.slug, month, r["keyword"][:500], r["value"], r["threshold"], r["is_branded"], now, now + RAW_TTL),
        )
    summary = keyword_summary(rows)
    # The derived branded share is retained on the KPI row (the raw keywords
    # expire); the KPI job may not have run for this month yet.
    cur.execute(
        """
        INSERT INTO vis_gbp_kpis_monthly (hotel_slug, month, branded_share, keywords_under_threshold, computed_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, month) DO UPDATE SET branded_share = EXCLUDED.branded_share,
            keywords_under_threshold = EXCLUDED.keywords_under_threshold, computed_at = EXCLUDED.computed_at
        """,
        (hotel.slug, month, summary["branded_share"], summary["under_threshold"], now),
    )
    return {"month": month.isoformat(), "keywords": len(rows), **{k: _s(v) if isinstance(v, Decimal) else v
                                                                  for k, v in summary.items()}}


# ─── UTM websiteUri patch (visibility.gbp_utm_patch) ───


def merge_utm(uri: str) -> str:
    """Add utm_source=google&utm_medium=organic&utm_campaign=gbp_listing,
    keeping the scheme, host, path, fragment and every other parameter (in
    their order). Our three keys replace any existing values for them."""
    parts = urlsplit(uri)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in UTM]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([*kept, *UTM.items()]), parts.fragment))


def has_utm(uri: str | None) -> bool:
    if not uri:
        return False
    q = dict(parse_qsl(urlsplit(uri).query))
    return all(q.get(k) == v for k, v in UTM.items())


def read_website_uri(cur: Any, hotel_slug: str, location: str) -> str | None:
    body = call(cur, hotel_slug, "GET", f"{INFO_BASE}/{location}", params={"readMask": "websiteUri"})
    return body.get("websiteUri")


def utm_execute(ctx: Any) -> dict:
    """Executor: GET websiteUri, merge the UTM, PATCH ?updateMask=websiteUri.
    The previous URI goes in reversal_handle so a person can undo it."""
    from clefs.core.actions import ExecResult

    ok, st = ready(ctx.cur, ctx.hotel_slug)
    if not ok:
        return ExecResult(ok=False, error=f"GBP {st['status']}")
    location = st["location"]
    previous = read_website_uri(ctx.cur, ctx.hotel_slug, location)
    if not previous:
        return ExecResult(ok=False, error="the profile has no website to tag")
    new = merge_utm(previous)
    if new != previous:
        call(ctx.cur, ctx.hotel_slug, "PATCH", f"{INFO_BASE}/{location}", params={"updateMask": "websiteUri"},
             json={"websiteUri": new})
    return ExecResult(ok=True, data={"location": location, "previous": previous, "new": new},
                      reversal_handle={"kind": "gbp_patch", "field": "websiteUri", "location": location,
                                       "previous": previous})


def utm_verify(ctx: Any) -> dict:
    """1 h after: the GET must show the tagged URI."""
    from clefs.core.actions import VerifyResult

    data = ctx.action.get("execution_result") or {}
    location = data.get("location") or ctx.params.get("location")
    ok, st = ready(ctx.cur, ctx.hotel_slug)
    if not ok or not location:
        return VerifyResult(status="pending", evidence=[{"kind": "row", "label": "GBP", "value": st["status"]}])
    now_uri = read_website_uri(ctx.cur, ctx.hotel_slug, location)
    expected = data.get("new")
    good = has_utm(now_uri) and (expected is None or now_uri == expected)
    return VerifyResult(status="verified" if good else "not_verified", attribution="caused",
                        evidence=[{"kind": "link", "label": "websiteUri now", "value": now_uri},
                                  {"kind": "link", "label": "expected", "value": expected}])


# ─── Overview (GET /visibility/gbp) ───


def overview(cur: Any, hotel: Any) -> dict:
    from clefs.modules.reviews import api as reviews_api
    from clefs.modules.visibility import xmod

    st = connection_state(cur, hotel.slug)
    cur.execute("SELECT month, reach_user_days, reach_prior_user_days, reach_yoy_user_days, actions, action_rate, "
                "website_clicks, branded_share, keywords_under_threshold, computed_at FROM vis_gbp_kpis_monthly "
                "WHERE hotel_slug = %s ORDER BY month DESC LIMIT 4", (hotel.slug,))
    kpis = [{k: (_iso(v) if isinstance(v, (date, datetime)) else _s(v) if isinstance(v, Decimal) else v)
             for k, v in dict(r).items()} for r in cur.fetchall()]
    cur.execute("SELECT max(month) AS m FROM vis_gbp_keywords WHERE hotel_slug = %s", (hotel.slug,))
    kw_month = cur.fetchone()["m"]
    keywords: list[dict] = []
    if kw_month:
        cur.execute("SELECT keyword, value, threshold, is_branded FROM vis_gbp_keywords WHERE hotel_slug = %s "
                    "AND month = %s ORDER BY value DESC NULLS LAST, threshold DESC, keyword", (hotel.slug, kw_month))
        keywords = [dict(r) for r in cur.fetchall()]
    summary = keyword_summary(keywords) if keywords else None
    # A Reviews failure hides the tile (None); it never breaks this page.
    replies = xmod.call(cur, None, reviews_api.reply_stats, hotel.slug, platform="google", days=90)
    return {
        "state": st,
        "kpis": kpis,
        "reach_label": REACH_LABEL,
        "keywords": {"month": _iso(kw_month), "rows": [
            {**k, "display": str(k["value"]) if k["value"] is not None else f"< {k['threshold']}"} for k in keywords],
            "summary": None if not summary else {**summary, "branded_share": _s(summary["branded_share"])}},
        "review_replies": replies,
        "utm": utm_status(cur, hotel.slug),
    }


def utm_status(cur: Any, hotel_slug: str) -> dict:
    cur.execute("SELECT id, status, created_at FROM actions WHERE hotel_slug = %s AND type = 'visibility.gbp_utm_patch' "
                "ORDER BY created_at DESC, id DESC LIMIT 1", (hotel_slug,))
    r = cur.fetchone()
    return {"action_id": r["id"], "status": r["status"], "created_at": _iso(r["created_at"])} if r else \
        {"action_id": None, "status": None, "created_at": None}


# ─── Draft replies (POST /visibility/gbp/draft-replies, 04 spec 5.4) ───

# One click drafts at most this many (newest first). Each new draft is an AI
# call made inside the request, so the batch stays small; a review that
# already has a live draft comes back as is (no second AI call), so a repeat
# click is cheap. The rest wait in the Reviews console.
DRAFT_BATCH = 5
REPLY_WINDOW_DAYS = 90  # the reply-rate tile's window (reviews.api.reply_stats)


def draft_replies(cur: Any, hotel_slug: str) -> dict:
    """Draft (never post) replies to the newest unanswered Google reviews via
    reviews.api.draft_reply. Visibility never posts a reply itself (04 spec
    5.4): each draft lands in Reviews' approval queue with its reply action,
    and the owner approves or edits it there (`reviews_tab`)."""
    from clefs.modules.reviews import api as reviews_api
    from clefs.modules.visibility import xmod

    ids = xmod.unanswered_google_review_ids(cur, hotel_slug, days=REPLY_WINDOW_DAYS, limit=DRAFT_BATCH)
    drafts = []
    for rid in ids:
        # None (Reviews off, review gone, no AI) skips that review; one
        # review's failure never loses the drafts already made.
        d = xmod.call(cur, None, reviews_api.draft_reply, hotel_slug, rid)
        if d:
            drafts.append({k: d.get(k) for k in ("review_id", "reply_id", "status", "needs_approval", "language")})
    stats = xmod.call(cur, None, reviews_api.reply_stats, hotel_slug, platform="google", days=REPLY_WINDOW_DAYS)
    return {"drafted": len(drafts), "drafts": drafts, "considered": len(ids),
            "unanswered": (stats or {}).get("unanswered"), "reviews_tab": "replies"}
