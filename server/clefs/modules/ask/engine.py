"""
The Ask Clefs pipeline (02 spec 7):

    guardrails -> rate limit -> plan (<= 3 tool calls over registry-generated
    strict tools) -> governed queries -> answer prose -> grounding check
    (one regeneration, then the table-only fallback) -> ask_logs

The model chooses KEYS (metric, dimensions, filters, period preset). The
server validates them against this viewer's catalog, runs them through
clefs.core.metrics, and checks every number the model writes back against
the results. Saved views skip the model entirely.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from clefs import ai, ratelimit
from clefs.core.errors import ClefsError
from clefs.core.time import local_date
from clefs.modules.ask import catalog, execute, grounding, guardrails, present, store, summary, tools, values
from clefs.modules.ask.constants import (
    MAX_QUERIES,
    MAX_TOOL_CALLS,
    QUESTIONS_PER_HOUR,
    RATE_WINDOW_S,
    TASK_ANSWER,
    TASK_PLAN,
)

log = logging.getLogger(__name__)

# ─── Prompts ───

PLAN_RULES = """You are Ask Clefs, the numbers assistant of a hotel's owner console.
Answer ONLY by calling tools. You never write SQL and never guess numbers.
- Pick the metric whose label, description or synonyms best fit the question.
- Break down with `dimensions` (at most 2) only when the question asks "by", "which", "top" or "per".
- Put named values (a channel, an amenity, a room type) in `filters`, op eq for one value, in for several.
- Use a period preset whenever one fits (last_month, mtd, last_7d, wtd, ytd, next_30d ...). Otherwise give
  explicit hotel-local dates. A named month means that whole month; "this week" is wtd.
- "vs last year" is compare_to same_period_last_year; "vs last month/week/period" is previous_period.
- Use grain day/week/month only when the question asks for a trend or a series.
- Call answer_unsupported for: a guest's personal details, predictions about other hotels, opinions,
  or anything no listed metric measures. Never stretch a metric to fit.
- At most 3 tool calls in total."""

ANSWER_RULES = """You write the one-paragraph answer shown above a table of results.
- Use ONLY numbers that appear in the results JSON (values, totals, comparisons, change_pct). Never compute
  new figures other than a difference or percentage change between two given numbers; never estimate.
- Lead with the direct answer, then at most two short supporting facts. No more than 60 words.
- Write money with its currency code (e.g. AED 12,345). Percentages with one decimal.
- If the results are empty, say that no data was recorded for the period.
- Plain text, no markdown, no em dashes."""


class AnswerDraft(BaseModel):
    answer_text: str = Field(description="The answer paragraph")


# ─── Plan state ───


@dataclass
class Query:
    spec: tools.QuerySpec
    entry: dict
    res: dict


@dataclass
class Plan:
    queries: list[Query] = field(default_factory=list)
    tool_log: list[dict] = field(default_factory=list)
    unsupported: dict | None = None  # {reason, suggestions}
    error: str | None = None


def _events(cur: Any, hotel_slug: str) -> list[dict]:
    """Named events the planner may translate into dates ("GITEX week").
    Demand owns them; its stub answers [] and a failure costs only context."""
    # A savepoint: a failing read in another module (its tables not migrated
    # yet, a bug) must not abort the caller's transaction with it.
    cur.execute("SAVEPOINT ask_events")
    try:
        from clefs.modules.demand import api as demand_api

        evs = demand_api.upcoming_events(cur, hotel_slug, days=180, min_score=0.0) or []
        cur.execute("RELEASE SAVEPOINT ask_events")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT ask_events")
        cur.execute("RELEASE SAVEPOINT ask_events")
        return []
    out = []
    for e in evs[:12]:
        if e.get("name") and e.get("start"):
            out.append({"name": str(e["name"])[:80], "start": str(e["start"])[:10], "end": str(e.get("end") or e["start"])[:10]})
    return out


def plan_system(cur: Any, ctx: Any, entries: list[dict]) -> str:
    hotel = ctx.hotel
    today = local_date(hotel)
    header = {
        "hotel": hotel.name, "today": today.isoformat(), "weekday": today.strftime("%A"),
        "timezone": hotel.timezone, "currency": hotel.currency, "rooms": hotel.rooms_count,
        "viewer_role": ctx.role, "events": _events(cur, hotel.slug),
    }
    return (f"{PLAN_RULES}\n\nContext: {json.dumps(header, default=str)}\n"
            f"Metrics: {json.dumps(catalog.compact(entries), default=str)}")


# ─── Planning: at most MAX_TOOL_CALLS calls, executed on the server ───


def _describe(ctx: Any, entry: dict) -> dict:
    """describe_metric output: the catalog entry plus known values per
    dimension, so the model can pick a real filter value."""
    known: dict[str, list[str]] = {}
    num = catalog.derived(entry["key"])
    for d in entry["dimensions"]:
        try:
            vals = values.allowed(ctx, num.num if num else entry["key"], d["key"])
        except ClefsError:
            vals = None
        if vals is not None:
            known[d["key"]] = vals[:25]
    return {**entry, "known_values": known}


def _handle(ctx: Any, plan: Plan, call: ai.ToolCall, cat: dict[str, dict], entries: list[dict]) -> dict:
    """Run one tool call; returns the output the model sees next round."""
    name, args = call.name, call.arguments if isinstance(call.arguments, dict) else {}
    if name == "list_metrics":
        return {"metrics": catalog.compact(entries)}
    if name == "describe_metric":
        e = cat.get(args.get("key"))
        if e is None:
            raise tools.ToolArgError(f"unknown metric {args.get('key')!r}")
        return _describe(ctx, e)
    if name == "answer_unsupported":
        sugg = [str(s)[:160] for s in (args.get("suggestions") or [])][:4]
        plan.unsupported = {"reason": str(args.get("reason") or "")[:500], "suggestions": sugg}
        return {"ok": True}
    if name == "query_metric":
        if len(plan.queries) >= MAX_QUERIES:
            raise tools.ToolArgError(f"at most {MAX_QUERIES} queries per question")
        spec = tools.validate_query(ctx, args, cat)
        try:
            res = execute.run(ctx, spec)
        except ClefsError as exc:  # timeout, permission, range: the model may adjust
            raise tools.ToolArgError(exc.detail) from exc
        plan.queries.append(Query(spec, cat[spec.metric], res))
        return {"ok": True, "rows": len(res["rows"]), "total": res.get("total")}
    raise tools.ToolArgError(f"unknown tool {name!r}")


def run_plan(cur: Any, ctx: Any, question: str, entries: list[dict]) -> Plan:
    plan = Plan()
    cat = {e["key"]: e for e in entries}
    tool_defs = tools.build_tools(entries)
    system = plan_system(cur, ctx, entries)
    items: list[dict] = [{"role": "user", "content": question}]
    used = 0
    while used < MAX_TOOL_CALLS:
        calls = ai.tool_call(TASK_PLAN, system=system, input=list(items), tools=tool_defs, hotel_slug=ctx.hotel.slug)
        if not calls:
            break
        for call in calls:
            if used >= MAX_TOOL_CALLS:
                plan.tool_log.append({"name": call.name, "arguments": call.arguments, "ok": False,
                                      "error": "tool_call_budget"})
                continue
            used += 1
            call_id = call.call_id or f"call_{used}"
            items.append({"type": "function_call", "call_id": call_id, "name": call.name,
                          "arguments": json.dumps(call.arguments, default=str)})
            try:
                out = _handle(ctx, plan, call, cat, entries)
                plan.tool_log.append({"name": call.name, "arguments": call.arguments, "ok": True})
            except tools.ToolArgError as exc:
                out = {"error": str(exc)}
                plan.tool_log.append({"name": call.name, "arguments": call.arguments, "ok": False,
                                      "error": str(exc)[:300]})
            except (ai.AIError, ai.AIDisabled):
                raise
            except Exception:  # a broken metric in another module: one failed call, not a 500
                log.exception("ask tool %s failed at %s", call.name, ctx.hotel.slug)
                out = {"error": "that metric could not be computed right now"}
                plan.tool_log.append({"name": call.name, "arguments": call.arguments, "ok": False,
                                      "error": "metric_failed"})
            items.append({"type": "function_call_output", "call_id": call_id,
                          "output": json.dumps(out, default=str)})
        if plan.queries or plan.unsupported:
            break  # answered (or declined): no further planning round
    if not plan.queries and not plan.unsupported:
        plan.error = "tool_call_budget" if used >= MAX_TOOL_CALLS else "no_tool_call"
    return plan


# ─── Answer prose + grounding ───


def _compact(q: Query) -> dict:
    """What the answer model sees of one result (no SQL, no internals)."""
    res, spec = q.res, q.spec
    rows = []
    for r in res["rows"][:50]:
        row = {**{k: present.safe_label(v) for k, v in r["group"].items()}, "value": r["value"]}
        if r.get("bucket"):
            row["bucket"] = r["bucket"]
        if spec.compare:
            row["compare_value"] = r["compare_value"]
            row["change_pct"] = present.change_pct(r["value"], r["compare_value"])
        rows.append(row)
    out = {
        "metric": res.get("label"), "unit": res.get("unit"), "currency": res.get("currency"),
        "period": spec.period.to_api()["label"], "grain": spec.grain, "dimensions": spec.dimensions,
        "filters": spec.filters, "compare": spec.compare, "rows": rows, "total": res.get("total"),
        "row_count": res.get("row_count"),
    }
    if res.get("compare_total") is not None:
        out["compare_total"] = res["compare_total"]
        out["change_pct"] = present.change_pct(res.get("total"), res["compare_total"])
    return out


@dataclass
class Composed:
    text: str
    status: str  # answered | table_only
    grounded: bool | None
    regenerated: bool
    ungrounded: list[str] = field(default_factory=list)


def compose(ctx: Any, question: str, queries: list[Query]) -> Composed:
    """Model prose checked number by number; one regeneration naming what
    failed; else the deterministic summary with status table_only."""
    bag = grounding.allowed([dict(q.res, limit=q.spec.limit) for q in queries], question=question)
    payload = {"question": question, "results": [_compact(q) for q in queries]}
    fallback = " ".join(summary.summarize(q.res, q.spec, locale=ctx.hotel.locale) for q in queries)
    feedback = None
    bad: list[str] = []
    for attempt in (0, 1):
        body = dict(payload)
        if feedback:
            body["correction"] = feedback
        try:
            draft = ai.structured(TASK_ANSWER, system=ANSWER_RULES, input=json.dumps(body, default=str),
                                  schema=AnswerDraft, hotel_slug=ctx.hotel.slug, max_output_tokens=600)
        except (ai.AIError, ai.AIDisabled):
            return Composed(fallback, "table_only", None, attempt == 1, bad)
        text = (draft.answer_text or "").strip().replace(chr(0x2014), "-")  # the house style has no em dash
        report = grounding.check(text, bag)
        if report.ok and text:
            return Composed(text, "answered", True if report.numbers else None, attempt == 1)
        bad = report.ungrounded
        feedback = (f"Your previous answer used numbers that are not in the results: {', '.join(bad[:10])}. "
                    "Rewrite it using only numbers present in the results.")
    return Composed(fallback, "table_only", False, True, bad)


# ─── Public pipeline ───

_ERROR_TEXT = {
    "ai_disabled": "Ask Clefs needs the AI service, which is switched off right now. Saved views still work.",
    "ai_error": "Ask Clefs could not reach the AI service. Please try again in a minute.",
    "no_metrics": "There are no metrics you can ask about at this hotel yet.",
    "no_tool_call": "I could not match that to any Clefs metric. Try naming what to measure and a period.",
    "tool_call_budget": "I could not build a valid query for that in three tries. Try rephrasing it.",
}


def _output(*, question: str, status: str, text: str, queries: list[Query], reason: str | None = None,
            suggestions: list[str] | None = None, grounded: bool | None = None, regenerated: bool = False) -> dict:
    blocks = [{**present.result_block(q.res, q.spec, q.entry), "query": q.spec.args} for q in queries]
    first = blocks[0] if blocks else {}
    return {
        "question": question,
        "status": status,
        "answer_text": text,
        "table": first.get("table"),
        "chart_spec": first.get("chart_spec"),
        "trace": first.get("trace"),
        "query": first.get("query"),
        "results": blocks,
        "unsupported": status == "unsupported",
        "reason": reason,
        "suggestions": suggestions or [],
        "grounded": grounded,
        "regenerated": regenerated,
    }


def _finish(cur: Any, ctx: Any, out: dict, *, channel: str, tool_log: list, t0: float, ai_before: int | None,
            saved_view_id: int | None = None) -> dict:
    latency = int((time.monotonic() - t0) * 1000)
    tokens = store.tokens_since(cur, ctx.hotel.slug, ai_before) if ai_before is not None else None
    log_id = store.insert_log(
        cur, ctx.hotel.slug, user_id=ctx.principal.user_id, channel=channel, question=out["question"],
        tool_calls=tool_log, answer={k: v for k, v in out.items() if k != "question"}, status=out["status"],
        reason=out.get("reason"), grounded=out.get("grounded"), regenerated=bool(out.get("regenerated")),
        latency_ms=latency, tokens=tokens, saved_view_id=saved_view_id,
    )
    return {"log_id": log_id, **out, "tool_calls": len(tool_log), "latency_ms": latency, "tokens": tokens}


def check_rate(cur: Any, user_id: int | None) -> None:
    """30 questions per hour per user (spec 7), web and WhatsApp together."""
    if user_id is None:
        return
    if not ratelimit.hit(cur, f"ask:q:{user_id}", limit=QUESTIONS_PER_HOUR, window_s=RATE_WINDOW_S):
        raise ClefsError(429, f"You can ask {QUESTIONS_PER_HOUR} questions an hour. Please try again later.",
                         "rate_limited")


def ask(cur: Any, ctx: Any, question: str, *, channel: str = "WEB") -> dict:
    """Answer one question for the viewer in `ctx` (a HotelCtx). Raises 429
    rate_limited; every other outcome is an answer with a status."""
    t0 = time.monotonic()
    question = (question or "").strip()
    check_rate(cur, ctx.principal.user_id)
    verdict = guardrails.check_question(question)
    if not verdict.ok:
        out = _output(question=question[:500] or "(empty)", status="refused", text=verdict.message, queries=[],
                      reason=verdict.code, suggestions=verdict.suggestions)
        return _finish(cur, ctx, out, channel=channel, tool_log=[], t0=t0, ai_before=None)
    entries = catalog.entries(ctx)
    if not entries:
        out = _output(question=question, status="unsupported", text=_ERROR_TEXT["no_metrics"], queries=[],
                      reason="no_metrics")
        return _finish(cur, ctx, out, channel=channel, tool_log=[], t0=t0, ai_before=None)
    ai_before = store.last_ai_call_id(cur)
    try:
        plan = run_plan(cur, ctx, question, entries)
    except ai.AIDisabled:
        out = _output(question=question, status="error", text=_ERROR_TEXT["ai_disabled"], queries=[],
                      reason="ai_disabled")
        return _finish(cur, ctx, out, channel=channel, tool_log=[], t0=t0, ai_before=ai_before)
    except ai.AIError:
        out = _output(question=question, status="error", text=_ERROR_TEXT["ai_error"], queries=[], reason="ai_error")
        return _finish(cur, ctx, out, channel=channel, tool_log=[], t0=t0, ai_before=ai_before)
    if plan.unsupported and not plan.queries:
        u = plan.unsupported
        text = u["reason"] or "Clefs has no metric that answers that."
        out = _output(question=question, status="unsupported", text=text, queries=[], reason="model_unsupported",
                      suggestions=u["suggestions"])
    elif not plan.queries:
        out = _output(question=question, status="error", text=_ERROR_TEXT[plan.error or "no_tool_call"],
                      queries=[], reason=plan.error)
    else:
        c = compose(ctx, question, plan.queries)
        out = _output(question=question, status=c.status, text=c.text, queries=plan.queries,
                      reason="ungrounded" if c.status == "table_only" else None, grounded=c.grounded,
                      regenerated=c.regenerated)
        if c.ungrounded:
            out["ungrounded"] = c.ungrounded
    return _finish(cur, ctx, out, channel=channel, tool_log=plan.tool_log, t0=t0, ai_before=ai_before)


def run_query(cur: Any, ctx: Any, query: dict, *, title: str, saved_view_id: int | None = None) -> dict:
    """Re-run stored tool arguments WITHOUT the LLM (saved views): validated
    against today's catalog (a role change or a retired metric is a 400),
    answered with the deterministic summary."""
    t0 = time.monotonic()
    entries = catalog.entries(ctx)
    cat = {e["key"]: e for e in entries}
    try:
        spec = tools.validate_query(ctx, query, cat)
    except tools.ToolArgError as exc:
        raise ClefsError(400, str(exc), "invalid_view_query") from exc
    res = execute.run(ctx, spec)
    q = Query(spec, cat[spec.metric], res)
    out = _output(question=title, status="answered", text=summary.summarize(res, spec, locale=ctx.hotel.locale),
                  queries=[q], grounded=True)
    log = [{"name": "query_metric", "arguments": spec.args, "ok": True, "source": "saved_view"}]
    return _finish(cur, ctx, out, channel="VIEW", tool_log=log, t0=t0, ai_before=None, saved_view_id=saved_view_id)
