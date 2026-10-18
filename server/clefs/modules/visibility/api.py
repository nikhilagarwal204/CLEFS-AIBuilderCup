"""
Visibility public API (frozen signatures; other modules import only this file).

Canon 20/23: the free OTA audit report carries a small AI-snapshot section,
which Profit Guard's public audit gets from start_ai_snapshot /
get_ai_snapshot (04 spec 5.9). Results are simulations (API answers differ
from consumer apps) and are always labelled.

04 spec 7.2: every function ships as a working stub with its documented
safe default ("not connected" for callers). The Visibility lane replaces the
bodies, never the signatures (FROZEN_API).
"""

from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from pydantic import BaseModel

# ─── Shapes ───


class SnapshotSubject(BaseModel):
    """Who the public AI snapshot is about. There is no hotel row behind it
    (a prospect's hotel), so everything the prompts need is here."""

    hotel_name: str
    city: str
    area: str | None = None
    website: str | None = None
    star: int | None = None


class AiSnapshot(TypedDict):
    """get_ai_snapshot's answer. Mentions count prompts 1-3 only and are
    shown as counts ("mentioned in 4 of 9 answers"): n is below the minimum
    sample for a percentage."""

    status: str  # running | ready | failed
    per_engine: list[dict]  # [{engine, mentioned, runs, ci}]
    top_cited_domains: list[str]
    answers_about_you: list[dict]  # prompts 4-5, quoted
    label: str  # "API simulation" / third-party SerpApi data


# ─── AI snapshot (Profit Guard's public audit) ───


def ai_snapshot(cur: Any, hotel_slug: str | None, *, hotel_name: str, city: str, max_queries: int = 3) -> dict | None:
    """The latest completed snapshot for that subject:
    {queries[], mentioned, share_of_voice, sample_size, engines[], label},
    or None when no ready snapshot exists for it (hotel_slug is unused: a
    snapshot is about a prospect's hotel, which has no hotels row)."""
    from clefs.modules.visibility import snapshot

    return snapshot.latest_for_subject(cur, hotel_name=hotel_name, city=city, max_queries=max_queries)


def start_ai_snapshot(cur: Any, *, subject: SnapshotSubject, requester_ref: str) -> int | None:
    """Queue 5 prompts x the enabled engines x 3 runs as a kind='snapshot'
    batch and return its id. None when the module is disabled, the daily cap
    (SNAPSHOT_DAILY_CAP) is reached, no engine is available (keys, the
    Perplexity legal gate) or SNAPSHOT_MONTHLY_BUDGET_USD cannot cover it (the
    audit then shows no AI section). The runs execute in the worker
    (visibility.ai_batch_step); poll get_ai_snapshot."""
    from clefs.modules.visibility import ai_radar

    return ai_radar.start_snapshot(cur, subject=subject.model_dump(), requester_ref=requester_ref)


def get_ai_snapshot(cur: Any, batch_id: int) -> AiSnapshot | None:
    """The batch's result (see AiSnapshot), or None when unknown or purged."""
    from clefs.modules.visibility import snapshot

    return snapshot.get(cur, int(batch_id))  # type: ignore[return-value]


def purge_ai_snapshot(cur: Any, batch_id: int) -> int:
    """Delete a snapshot batch and its answers (Profit Guard's 30-day purge).
    Returns the rows removed (runs + the batch); 0 when unknown."""
    from clefs.modules.visibility import snapshot

    return snapshot.purge(cur, int(batch_id))


# ─── Parity, attribution, facts, Google Business Profile ───


def parity_status(cur: Any, hotel_slug: str, *, stay_date: date) -> dict | None:
    """Google Hotels price parity for one night: {official_present,
    official_minor, lowest_ota_minor, gap_minor, gap_pct, currency,
    checked_at}, or None (not connected, or the night was never observed).
    gap_pct is a Decimal string (gap / official x 100, 3 dp)."""
    from clefs.modules.visibility import google_hotels

    return google_hotels.latest_parity(cur, hotel_slug, stay_date)


def attribution(cur: Any, hotel_slug: str, *, month: date) -> dict | None:
    """Google-sourced direct demand for the month starting `month`:
    {month, gbp_website_clicks, direct_bookings, direct_room_revenue_minor,
    commission_avoided_minor, reference_ota, currency, label}, or None when
    the month was never computed (visibility.attribution_monthly)."""
    from clefs.modules.visibility import attribution as attr

    return attr.get_month(cur, hotel_slug, month.replace(day=1))


def open_fact_errors(cur: Any, hotel_slug: str) -> list[dict]:
    """Facts the web or Google states differently from the approved KB:
    [{fact_key, ours, theirs, source_url, seen_at}]. The website's JSON-LD
    (latest site-health check) and the GBP lodging record (latest snapshot)
    feed it, plus the AI radar's open vis_ai_facts (wrong values only; an
    'unverifiable' claim is a KB gap, not a contradiction)."""
    from clefs.modules.visibility import facts, lodging, site_health

    return (site_health.open_fact_errors(cur, hotel_slug) + lodging.lodging_fact_errors(cur, hotel_slug)
            + facts.open_ai_facts(cur, hotel_slug))


def gbp_location(cur: Any, hotel_slug: str) -> str | None:
    """The Google Business Profile location the owner picked
    ("locations/123..."), which Reviews reuses; None until picked."""
    cur.execute("SELECT gbp_location FROM vis_profile WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    return (row or {}).get("gbp_location") or None


# ─── Demo seed door ───
# WHY: the UAE demo seed (clefs.seed.demo_uae) is foundation code, which may
# never import a module's internals (tests/foundation/test_module_contracts).
# It loads this api by name, saves the profile through the writer the
# settings route calls, and replays vendor calls from fixtures by swapping
# the one function per surface that makes the call, so the registered jobs
# then run as shipped. The surface modules are handed over as modules
# because that swap patches their attributes. Nothing else calls this.


def demo_seed_door() -> Any:
    """google_hotels (fetch_details / search_rank, organic_properties,
    rank_in), gbp (connection_state / ready / call), ai_radar (ENGINES,
    Engine, CONCURRENCY, STEP_MAX_RUNS, enabled_engines) and costs
    (serpapi_budget_ok) as modules; the active prompts and the profile
    writer as functions."""
    from types import SimpleNamespace

    from clefs.modules.visibility import ai_radar, costs, gbp, google_hotels, prompts, store

    return SimpleNamespace(
        google_hotels=google_hotels,
        gbp=gbp,
        ai_radar=ai_radar,
        costs=costs,
        active_prompts=prompts.active_prompts,
        update_profile=store.update_profile,
    )
