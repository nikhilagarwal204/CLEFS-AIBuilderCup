"""
Visibility metrics (04 spec 5.11), registered at import: the Google side
and the AI radar (vis.ai_mention_rate, vis.ai_share_of_voice,
vis.ai_fact_errors_open). The AI rate metrics apply the same minimum-sample
gate as the console (n >= 30 and Wilson half-width <= 0.15, through the SQL
function vis_wilson_halfwidth), so Ask never quotes a hidden number.

Money metrics use Metric(unit='money', currency='hotel') with no currency in
the key; GBP metrics read only the derived vis_gbp_kpis_monthly (the raw
rows expire after 30 days).
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

PERM = "visibility.view"

# ─── Google Hotels ───

_GH = dict(source="FROM vis_google_hotels_obs o", hotel_column="o.hotel_slug", time_column="o.observed_at",
           time_kind="timestamptz", permission=PERM,
           dimensions=(Dimension("query", "Search query", "o.query",
                                 "SELECT DISTINCT query FROM vis_google_hotels_obs WHERE hotel_slug = %(hotel)s"),))

register_metric(Metric(
    key="vis.gh_organic_rank", label="Google Hotels organic rank",
    description="Average organic position of the hotel in Google Hotels area searches (sponsored results "
                "excluded; third-party SerpApi data)",
    unit="count", measure="ROUND(AVG(o.organic_rank), 2)", where="o.organic_rank IS NOT NULL",
    higher_is_better=False, synonyms=("google hotels rank", "google hotels position"), **_GH,
))

register_metric(Metric(
    key="vis.official_presence_rate", label="Official price shown on Google",
    description="Share of Google Hotels checks where the hotel's own site price was shown",
    unit="percent", measure="100.0 * COUNT(*) FILTER (WHERE o.official_present) / NULLIF(COUNT(o.official_present), 0)",
    higher_is_better=True, synonyms=("free booking link", "official site price"), **_GH,
))

register_metric(Metric(
    key="vis.parity_gap", label="Google Hotels parity gap",
    description="Average gap between the hotel's own price and the cheapest OTA on Google Hotels (positive = an OTA "
                "undercuts the hotel; indicative third-party data)",
    unit="money", currency="hotel", measure="AVG(o.parity_gap_minor)", where="o.parity_gap_minor IS NOT NULL",
    higher_is_better=False, synonyms=("rate parity", "ota undercut"), **_GH,
))

# ─── Rank grid (per keyword, per entity: the hotel or a competitor) ───

_RANK_SOURCE = (
    "FROM vis_rank_runs r CROSS JOIN LATERAL ("
    "SELECT 'self'::text AS entity, r.solv AS solv, r.atrp AS atrp "
    "UNION ALL SELECT c.key, (c.value->>'solv')::numeric, (c.value->>'atrp')::numeric "
    "FROM jsonb_each(COALESCE(r.comp_metrics, '{}'::jsonb)) c) e"
)
_RANK = dict(
    source=_RANK_SOURCE, hotel_column="r.hotel_slug", time_column="r.run_at", time_kind="timestamptz",
    where="r.source = 'places_ids'", permission=PERM,
    dimensions=(
        Dimension("keyword", "Keyword", "r.keyword",
                  "SELECT DISTINCT keyword FROM vis_rank_runs WHERE hotel_slug = %(hotel)s"),
        Dimension("entity", "Hotel", "e.entity",
                  "SELECT 'self' UNION SELECT comp_id FROM vis_competitors WHERE hotel_slug = %(hotel)s"),
    ),
)

register_metric(Metric(
    key="vis.solv_pct", label="Share of local voice",
    description="Share of map-grid points where the hotel ranks in the top 3 (Places API, internal KPI)",
    unit="percent", measure="ROUND(100.0 * AVG(e.solv), 2)", higher_is_better=True,
    synonyms=("solv", "map rank", "local rank"), **_RANK,
))

register_metric(Metric(
    key="vis.atrp", label="Average total rank position",
    description="Mean map-grid rank with not-found points counted as 21 (lower is better)",
    unit="count", measure="ROUND(AVG(e.atrp), 2)", higher_is_better=False, synonyms=("atrp", "average rank"),
    **_RANK,
))

# ─── GBP (derived monthly KPIs only) ───

_GBP = dict(source="FROM vis_gbp_kpis_monthly k", hotel_column="k.hotel_slug", time_column="k.month",
            time_kind="date", permission=PERM)

register_metric(Metric(
    key="vis.gbp_reach_user_days", label="Google profile reach",
    description="Business Profile impressions over the 28 days ending each month, in user-days",
    unit="count", measure="SUM(k.reach_user_days)", higher_is_better=True,
    synonyms=("gbp impressions", "profile views"), **_GBP,
))

register_metric(Metric(
    key="vis.gbp_website_clicks", label="Google profile website clicks",
    description="Website clicks from the Google Business Profile in the month", unit="count",
    measure="SUM(k.website_clicks)", higher_is_better=True, synonyms=("gbp clicks",), **_GBP,
))

register_metric(Metric(
    key="vis.gbp_action_rate", label="Google profile action rate",
    description="(calls + website clicks + directions + messages) / impressions over the 28-day window",
    unit="percent", measure="ROUND(100.0 * AVG(k.action_rate), 2)", higher_is_better=True,
    synonyms=("gbp conversion",), **_GBP,
))

# ─── Attribution and site health ───

_ATTR = dict(source="FROM vis_attribution_monthly a", hotel_column="a.hotel_slug", time_column="a.month",
             time_kind="date", permission=PERM)

register_metric(Metric(
    key="vis.google_direct_bookings", label="Direct bookings from Google",
    description="Direct reservations tagged utm_source=google (profile link or free booking links); bookings that "
                "lost the tag are not counted", unit="count", measure="SUM(a.direct_bookings)",
    higher_is_better=True, synonyms=("google bookings",), **_ATTR,
))

register_metric(Metric(
    key="vis.google_commission_avoided", label="Commission avoided via Google",
    description="What the reference OTA would have charged on the Google-tagged direct bookings (attribution, not "
                "ledger-verified profit)", unit="money", currency="hotel", measure="SUM(a.commission_avoided_minor)",
    higher_is_better=True, synonyms=("google savings",), **_ATTR,
))

register_metric(Metric(
    key="vis.cwv_lcp_p75_ms", label="Website LCP (p75, phones)",
    description="Largest Contentful Paint at the 75th percentile of real Chrome users on phones (CrUX); good is "
                "2,500 ms or less", unit="count",
    measure="ROUND(AVG((s.crux->'metrics'->>'lcp_ms')::numeric), 0)",
    source="FROM vis_site_health s", hotel_column="s.hotel_slug", time_column="s.checked_at",
    time_kind="timestamptz", where="s.crux->'metrics'->>'lcp_ms' IS NOT NULL", permission=PERM,
    higher_is_better=False, synonyms=("page speed", "core web vitals"),
))


# ─── AI radar (window 1, the 'self' cells; discovery prompts only) ───

_AI_GATE = "SUM(w.runs) >= 30 AND vis_wilson_halfwidth(SUM(w.mentions), SUM(w.runs)) <= 0.15"
_AI = dict(source="FROM vis_ai_weekly w", hotel_column="w.hotel_slug", time_column="w.week", time_kind="date",
           where="w.window_weeks = 1 AND w.entity = 'self'", permission=PERM,
           dimensions=(Dimension("engine", "AI engine", "w.engine"), Dimension("market", "Market", "w.market"),
                       Dimension("language", "Language", "w.language")))

register_metric(Metric(
    key="vis.ai_mention_rate", label="AI answer mention rate",
    description="Share of AI discovery answers that name the hotel (API simulation for Gemini and Perplexity; "
                "Google AI Mode via SerpApi is third-party and indicative); hidden below n=30 or when the 95% "
                "interval is wider than +/-15 points", unit="ratio",
    measure=f"CASE WHEN {_AI_GATE} THEN SUM(w.mentions)::numeric / SUM(w.runs) END",
    higher_is_better=True, synonyms=("gemini mentions", "ai visibility", "ai answers"), **_AI,
))

register_metric(Metric(
    key="vis.ai_share_of_voice", label="AI share of voice",
    description="The hotel's share of hotel mentions in AI discovery answers against its comp set (same sample "
                "gate as the mention rate)", unit="ratio",
    measure=f"CASE WHEN {_AI_GATE} THEN ROUND(AVG(w.sov), 4) END",
    higher_is_better=True, synonyms=("ai sov", "share of voice"), **_AI,
))

register_metric(Metric(
    key="vis.ai_fact_errors_open", label="Wrong facts in AI answers",
    description="Open fact errors AI answers state about the hotel (check-in, parking, pool, stars, price level)",
    unit="count", measure="COUNT(*)", source="FROM vis_ai_facts f", hotel_column="f.hotel_slug",
    time_column="f.last_seen", time_kind="timestamptz", where="f.status IN ('open', 'recurred')", permission=PERM,
    dimensions=(Dimension("fact_key", "Fact", "f.fact_key"),), higher_is_better=False,
    synonyms=("ai fact errors", "wrong facts"),
))
