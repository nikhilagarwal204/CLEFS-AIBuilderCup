"""
AI radar cost controls (04 spec 5.10), all in USD micros (Canon K3).

- Per-run estimates price a batch before it is planned; the real cost of
  each call is ledgered afterwards (vis_api_costs for Gemini and Perplexity,
  core's serpapi_calls for AI Mode) and the hotel budget check in
  store.budget_state sums both.
- The weekly batch gets this week's share of the remaining monthly budget.
  When the full plan does not fit, it degrades in the spec's order:
  1. drop the source-market prompts, 2. cut repeats to 3, 3. drop the AR
  prompts, 4. drop the most expensive engine (repeated down to one engine).
  If one engine still does not fit the batch is skipped, never run partially:
  a prompt x engine keeps all of its repeats or none (integrity rule).
- Snapshots and public checks have no hotel: they are budgeted globally
  (SNAPSHOT_DAILY_CAP, SNAPSHOT_MONTHLY_BUDGET_USD, PUBLIC_SERPAPI_MONTHLY_CAP).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from clefs.core import clock
from clefs.settings import get_settings

REPEATS = 4
DEGRADED_REPEATS = 3
SNAPSHOT_REPEATS = 3

# Per-run estimates (USD micros). Gemini: $14 per 1k Google Search grounding
# queries (ai.WEB_SEARCH_CALL_MICROS) plus ~6k input / 400 output tokens on
# gemini-2.5-flash. Perplexity: $5 per 1k
# requests at search_context_size='low' plus ~$1/M tokens. AI Mode: the
# SerpApi per-search price (1 credit per request assumed, UNVERIFIED). The
# parse/fact extraction adds ~1k per run on the runs that need it.
GEMINI_RUN_MICROS = 17_000
PERPLEXITY_RUN_MICROS = 6_000
PARSE_RUN_MICROS = 1_000


def run_estimate_micros(engine: str) -> int:
    from clefs.core import serpapi

    base = {
        "gemini_web_search": GEMINI_RUN_MICROS,
        "perplexity_sonar": PERPLEXITY_RUN_MICROS,
        "serpapi_ai_mode": serpapi.cost_per_search_micros(),
    }.get(engine, GEMINI_RUN_MICROS)
    return base + PARSE_RUN_MICROS


# ─── Weekly degradation ───


@dataclass
class Plan:
    prompts: list[dict]
    engines: list[str]
    repeats: int
    steps: list[str] = field(default_factory=list)
    est_micros: int = 0
    skipped: str | None = None

    def units(self) -> list[tuple[dict, str]]:
        """(prompt, engine) pairs that run. Perplexity has no localisation, so
        it runs home-market (UAE) prompts only."""
        return [(p, e) for p in self.prompts for e in self.engines
                if not (e == "perplexity_sonar" and str(p.get("market") or "").upper() != "AE")]

    def estimate(self) -> int:
        return sum(run_estimate_micros(e) * self.repeats for _, e in self.units())

    def as_dict(self) -> dict:
        return {"engines": self.engines, "repeats": self.repeats, "steps": self.steps,
                "est_micros": self.est_micros, "skipped": self.skipped, "prompts": len(self.prompts)}


def plan_weekly(prompts: list[dict], engines: list[str], *, allowance_micros: int | None,
                home_market: str = "AE") -> Plan:
    """The biggest plan that fits `allowance_micros` (None = no budget check
    possible: the full plan runs)."""
    plan = Plan(prompts=list(prompts), engines=list(engines), repeats=REPEATS)
    plan.est_micros = plan.estimate()

    def fits() -> bool:
        plan.est_micros = plan.estimate()
        return allowance_micros is None or plan.est_micros <= allowance_micros

    home = home_market.upper()
    ladder = [
        ("drop_source_markets", lambda: setattr(plan, "prompts",
                                                [p for p in plan.prompts if str(p.get("market")).upper() == home])),
        ("repeats_3", lambda: setattr(plan, "repeats", DEGRADED_REPEATS)),
        ("drop_ar", lambda: setattr(plan, "prompts", [p for p in plan.prompts if p.get("language") != "ar"])),
    ]
    for name, step in ladder:
        if fits():
            return plan
        step()
        plan.steps.append(name)
    while not fits() and len(plan.engines) > 1:
        dearest = max(plan.engines, key=lambda e: (run_estimate_micros(e), e))
        plan.engines = [e for e in plan.engines if e != dearest]
        plan.steps.append(f"drop_engine:{dearest}")
    if not fits():
        plan.skipped = "budget_exhausted"
    return plan


def weekly_allowance(remaining_micros: int | None, *, at: datetime | None = None) -> int | None:
    """This week's share of what is left this (UTC) month: remaining / the
    number of weekly batches still to come, this one included."""
    if remaining_micros is None:
        return None
    at = (at or clock.now()).astimezone(UTC)
    nxt = (at.replace(day=28) + timedelta(days=4)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    weeks_left = max(1, math.ceil((nxt - at).total_seconds() / (7 * 86400)))
    return max(0, remaining_micros) // weeks_left


# ─── Global budgets: snapshots and public checks ───

SNAPSHOT_SERP_PURPOSE = "visibility.snapshot"


def _utc_day_start(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def snapshot_month_spend_micros(cur: Any, *, at: datetime | None = None) -> int:
    """Every snapshot's spend this UTC month: its Gemini / Perplexity calls in
    vis_api_costs (purpose 'snapshot', no hotel) plus its billed AI Mode
    searches in serpapi_calls."""
    at = (at or clock.now()).astimezone(UTC)
    start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cur.execute(
        """
        SELECT COALESCE((SELECT SUM(cost_micros) FROM vis_api_costs
                         WHERE purpose = 'snapshot' AND at >= %(s)s AND at <= %(a)s), 0)
             + COALESCE((SELECT SUM(cost_micros) FROM serpapi_calls
                         WHERE purpose = %(p)s AND billed AND at >= %(s)s AND at <= %(a)s), 0) AS spend
        """,
        {"s": start, "a": at, "p": SNAPSHOT_SERP_PURPOSE},
    )
    return int(cur.fetchone()["spend"] or 0)


def snapshot_budget_micros() -> int:
    return int((Decimal(get_settings().SNAPSHOT_MONTHLY_BUDGET_USD) * 1_000_000).to_integral_value())


def snapshots_today(cur: Any, *, at: datetime | None = None) -> int:
    start = _utc_day_start(at or clock.now())
    cur.execute("SELECT COUNT(*) AS n FROM vis_ai_batches WHERE kind = 'snapshot' AND created_at >= %s", (start,))
    return int(cur.fetchone()["n"])


def snapshot_refusal(cur: Any, *, est_micros: int) -> str | None:
    """Why a new snapshot may not start ('daily_cap' | 'budget_exhausted'),
    or None. The estimate must fit, so the last snapshot of the month never
    overshoots the global budget by a whole batch."""
    if snapshots_today(cur) >= max(0, int(get_settings().SNAPSHOT_DAILY_CAP)):
        return "daily_cap"
    if snapshot_month_spend_micros(cur) + est_micros > snapshot_budget_micros():
        return "budget_exhausted"
    return None


def serpapi_available() -> bool:
    """AI Mode runs only with a key; the budget is checked per call by
    core.serpapi (BudgetExhausted), which the executor records as an error."""
    return bool(get_settings().SERPAPI_KEY)


def perplexity_available() -> bool:
    """The Canon 20 legal gate: a key AND PERPLEXITY_TERMS_APPROVED=true."""
    s = get_settings()
    return bool(s.PERPLEXITY_API_KEY) and bool(s.PERPLEXITY_TERMS_APPROVED)


def serpapi_budget_ok(cur: Any, *, searches: int = 1) -> bool:
    """False when core's global SerpApi budget (SERPAPI_MONTHLY_BUDGET_USD)
    cannot cover `searches` more calls: AI Mode is then dropped up front
    instead of every run failing with BudgetExhausted (5.9)."""
    from clefs.core import serpapi

    budget = get_settings().SERPAPI_MONTHLY_BUDGET_USD
    if budget is None:
        return True
    cost = serpapi.cost_per_search_micros() * max(1, searches)
    return serpapi.month_spend_micros(cur) + cost <= serpapi.usd_to_micros(budget)
