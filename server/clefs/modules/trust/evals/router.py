"""
Evals and readiness routes (03 spec 5.4), under /hotels/{slug}/trust/.
The HTTP contract is listed in the module README.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.modules.trust.evals import autokb, readiness, runs, scenarios

router = APIRouter(prefix="/hotels/{slug}/trust", tags=["trust"])

VIEW = "trust.evals.view"
MANAGE = "trust.evals.manage"
OVERRIDE = "trust.readiness.override"


# ─── Serializers ───


def _dec(v: Any) -> str | None:
    return None if v is None else str(v)


def run_out(r: dict) -> dict:
    out = dict(r)
    out["pass_rate"] = _dec(r.get("pass_rate"))
    out["cost_usd"] = _dec(Decimal(r.get("cost_micros") or 0) / Decimal(1_000_000))
    return out


def scenario_out(s: dict) -> dict:
    out = dict(s)
    out["read_only"] = s.get("suite") != "custom"
    return out


# ─── Readiness ───


@router.get("/readiness")
def get_readiness(ctx: HotelCtx = Depends(require_perm(VIEW, "concierge.manage"))) -> dict:
    with db.db() as cur:
        return readiness.evaluate(cur, ctx.hotel.slug)


class OverrideIn(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)


@router.post("/readiness/override", status_code=201)
def post_override(body: OverrideIn, ctx: HotelCtx = Depends(require_perm(OVERRIDE))) -> dict:
    with db.db() as cur:
        return readiness.create_override(cur, ctx, ctx.hotel.slug, reason=body.reason)


@router.delete("/readiness/override/{override_id}", status_code=204)
def delete_override(override_id: int, ctx: HotelCtx = Depends(require_perm(OVERRIDE))) -> None:
    with db.db() as cur:
        readiness.revoke_override(cur, ctx, ctx.hotel.slug, override_id)


# ─── Scenarios ───


class ScenarioIn(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=200)
    category: str = "general"
    surface: str = "web_room"
    language: str = Field(default="en", max_length=10)
    turns: list[str | dict] = Field(min_length=1, max_length=10)
    context: dict = Field(default_factory=dict)
    expectations: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=20)
    flaky: bool = False


class ScenarioPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    category: str | None = None
    surface: str | None = None
    language: str | None = Field(default=None, max_length=10)
    turns: list[str | dict] | None = Field(default=None, min_length=1, max_length=10)
    context: dict | None = None
    expectations: dict | None = None
    tags: list[str] | None = None
    flaky: bool | None = None
    active: bool | None = None


@router.get("/evals/scenarios")
def get_scenarios(
    suite: Literal["standard", "auto_kb", "custom"] | None = None,
    include_inactive: bool = False,
    ctx: HotelCtx = Depends(require_perm(VIEW)),
) -> list[dict]:
    with db.db() as cur:
        scenarios.sync_standard(cur)
        rows = scenarios.list_scenarios(cur, ctx.hotel.slug, suites=[suite] if suite else scenarios.SUITES,
                                        include_inactive=include_inactive)
    return [scenario_out(r) for r in rows]


@router.post("/evals/scenarios", status_code=201)
def post_scenario(body: ScenarioIn, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        return scenario_out(scenarios.create_custom(cur, ctx.hotel.slug, body.model_dump()))


@router.patch("/evals/scenarios/{scenario_id}")
def patch_scenario(scenario_id: int, body: ScenarioPatch, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        data = body.model_dump(exclude_none=True)
        return scenario_out(scenarios.update_custom(cur, ctx.hotel.slug, scenario_id, data))


@router.delete("/evals/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: int, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> None:
    with db.db() as cur:
        scenarios.deactivate_custom(cur, ctx.hotel.slug, scenario_id)


@router.post("/evals/auto-kb/sync")
def post_autokb_sync(ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        return autokb.sync(cur, ctx.hotel.slug)


# ─── Runs ───


class RunIn(BaseModel):
    suites: list[Literal["standard", "auto_kb", "custom"]] | None = None
    note: str | None = Field(default=None, max_length=500)


@router.get("/evals/runs")
def get_runs(limit: int = Query(default=20, ge=1, le=100), ctx: HotelCtx = Depends(require_perm(VIEW))) -> list[dict]:
    with db.db() as cur:
        return [run_out(r) for r in runs.list_runs(cur, ctx.hotel.slug, limit=limit)]


@router.post("/evals/runs", status_code=202)
def post_run(body: RunIn | None = None, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    body = body or RunIn()
    with db.db() as cur:
        run = runs.create_run(cur, ctx.hotel.slug, trigger="manual", suites=body.suites,
                              requested_by=ctx.principal.user_id, note=body.note)
    return run_out(run)


@router.get("/evals/runs/{run_id}")
def get_run(run_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    with db.db() as cur:
        run = runs.get_run(cur, ctx.hotel.slug, run_id)
        prev = runs.last_completed(cur, ctx.hotel.slug, before_id=run_id)
    out = run_out(run)
    out["previous_run_id"] = prev["id"] if prev else None
    out["previous_pass_rate"] = _dec(prev["pass_rate"]) if prev else None
    return out


@router.get("/evals/runs/{run_id}/results")
def get_results(run_id: int, failed_only: bool = False, ctx: HotelCtx = Depends(require_perm(VIEW))) -> list[dict]:
    with db.db() as cur:
        rows = runs.list_results(cur, ctx.hotel.slug, run_id, failed_only=failed_only)
    for r in rows:
        r["score"] = _dec(r.get("score"))
    return rows


@router.get("/evals/budget")
def get_budget(ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    with db.db() as cur:
        spent = runs.spent_today_micros(cur, ctx.hotel.slug)
    cap = runs.daily_cap_micros()
    return {"cap_usd": _dec(Decimal(cap) / Decimal(1_000_000)), "spent_today_usd": _dec(Decimal(spent) / Decimal(1_000_000)),
            "remaining_usd": _dec(Decimal(max(cap - spent, 0)) / Decimal(1_000_000))}
