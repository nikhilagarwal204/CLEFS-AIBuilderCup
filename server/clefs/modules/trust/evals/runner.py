"""
Scenario runner (03 spec 5.4).

Primary path: the EXACT session the guest would get,
concierge.api.build_surface_session(surface="eval", dry_run=True), driven
turn by turn through concierge.api.respond_text, so the same prompt, model
and tools are tested and the dry-run executor returns realistic outputs
without filing anything.

Fallback ("responses"): while Concierge's text turn is not built
(respond_text answers implemented=False), the runner sends the same
instructions and tool schemas to the Responses API through clefs.ai and
records the tool calls the model makes. There is no executor on this path,
so room-binding and result checks are skipped rather than guessed.

Flaky scenarios run up to 3 times and are decided by majority.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from clefs import ai
from clefs.settings import ai_model_for
from clefs.modules.concierge import api as concierge_api
from clefs.modules.upsell import api as upsell_api
from clefs.core import rooms
from clefs.modules.trust.evals import dsl, judge, scenarios, textcheck
from clefs.modules.trust.evals.sp import savepoint
from clefs.modules.trust.evals.graders import GradeContext, Transcript, grade, escalated_as_unknown, normalize_call

FALLBACK_TASK = "trust.eval_turn"
MAX_TOOL_ROUNDS = 2
MAJORITY_OF = 3

# ─── Session ───


def eval_room(cur: Any, hotel_slug: str) -> str:
    """The room an eval session binds. Scenarios are written for room 504;
    a hotel whose register has no 504 gets its highest real room instead
    (never 101 or 502, which the injection scenarios name as someone
    else's), so the concierge's room checks see a room that exists."""
    if not rooms.has_register(cur, hotel_slug) or rooms.get_room(cur, hotel_slug, scenarios.DEFAULT_ROOM):
        return scenarios.DEFAULT_ROOM
    names = [r["room_number"] for r in rooms.list_rooms(cur, hotel_slug)]
    others = [n for n in names if n not in ("101", "502")]
    return (others or names or [scenarios.DEFAULT_ROOM])[-1]


def localize(cur: Any, hotel_slug: str, scenario: dict) -> dict:
    """The scenario with the placeholder room 504 mapped to eval_room(), in
    the session context and in bound_room alike."""
    ctx = dict(scenario.get("context") or {})
    if ctx.get("room", scenarios.DEFAULT_ROOM) != scenarios.DEFAULT_ROOM:
        return scenario
    room = eval_room(cur, hotel_slug)
    if room == scenarios.DEFAULT_ROOM:
        return scenario
    exp = dict(scenario.get("expectations") or {})
    if exp.get("bound_room") == scenarios.DEFAULT_ROOM:
        exp["bound_room"] = room
    return {**scenario, "context": {**ctx, "room": room}, "expectations": exp}


def build_spec(cur: Any, hotel_slug: str, scenario: dict) -> Any:
    ctx = scenario.get("context") or {}
    return concierge_api.build_surface_session(
        hotel_slug, surface="eval", room=ctx.get("room"), location=ctx.get("location"),
        language=scenario.get("language") or "en", dry_run=True, cur=cur,
    )


def _responses_tools(tools: list[dict]) -> list[dict]:
    # Realtime and Responses function tools share {type, name, description,
    # parameters}; anything else (a hosted tool) is not replayed here.
    out = []
    for t in tools or []:
        if t.get("type", "function") == "function" and t.get("name"):
            out.append({"type": "function", "name": t["name"], "description": t.get("description", ""),
                        "parameters": t.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _parse_raw(body: dict) -> tuple[str, list[dict]]:
    """(assistant text, function calls) from a Responses body."""
    text_parts: list[str] = []
    calls: list[dict] = []
    for item in body.get("output") or []:
        if item.get("type") == "function_call":
            calls.append({"name": item.get("name"), "arguments": item.get("arguments"), "call_id": item.get("call_id")})
        elif item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    text_parts.append(c["text"])
    if not text_parts and body.get("output_text"):
        text_parts.append(str(body["output_text"]))
    return "\n".join(text_parts).strip(), calls


# ─── Conversation ───


def _fallback_turn(spec: Any, history: list[dict], hotel_slug: str) -> tuple[str, list[dict]]:
    """One guest turn through the Responses API with the spec's own prompt
    and tools. Tool calls get a neutral dry-run output so the model can
    finish its reply; nothing is executed."""
    tools = _responses_tools(spec.tools)
    items: list[dict] = [{"role": "system", "content": spec.instructions}]
    items += [{"role": m["role"], "content": m["text"]} for m in history]
    calls: list[dict] = []
    text = ""
    for _ in range(MAX_TOOL_ROUNDS):
        body = ai.responses_raw(FALLBACK_TASK, input=items, tools=tools, hotel_slug=hotel_slug, timeout_s=30)
        text, new_calls = _parse_raw(body)
        if not new_calls:
            break
        calls += new_calls
        for c in new_calls:
            items.append({"type": "function_call", "name": c["name"], "arguments": c["arguments"] or "{}",
                          "call_id": c.get("call_id") or c["name"]})
            items.append({"type": "function_call_output", "call_id": c.get("call_id") or c["name"],
                          "output": json.dumps({"ok": True, "dry_run": True})})
        if text:
            break
    return text, calls


def converse(cur: Any, hotel_slug: str, scenario: dict) -> Transcript:
    """Play the scenario's user turns and return what the concierge did."""
    try:
        spec = build_spec(cur, hotel_slug, scenario)
    except Exception as exc:  # noqa: BLE001 - a broken session is a failed scenario, not a crashed run
        return Transcript(runner="none", implemented=False, error=f"session_error: {type(exc).__name__}: {str(exc)[:200]}")
    t = Transcript(runner="concierge_text", prompt_version=getattr(spec, "prompt_version", None),
                   model=getattr(spec, "model", None))
    history: list[dict] = []
    for turn in scenario.get("turns") or []:
        history.append({"role": "user", "text": turn["text"]})
        t.messages.append({"role": "user", "text": turn["text"]})
        reply, calls = "", []
        try:
            if t.runner == "concierge_text":
                out = concierge_api.respond_text(spec, [{"role": m["role"], "content": m["text"]} for m in history])
                if getattr(out, "implemented", True) is False:
                    t.runner = "responses"
                else:
                    reply, calls = out.text or "", list(out.tool_calls or [])
                    t.handoff = t.handoff or bool(getattr(out, "handoff", False))
            if t.runner == "responses":
                reply, calls = _fallback_turn(spec, history, hotel_slug)
                t.model = ai_model_for(FALLBACK_TASK)
        except (ai.AIDisabled, ai.AIError) as exc:
            t.error = f"runner_unavailable: {exc.code}"
            break
        t.tool_calls += [normalize_call(c) for c in calls]
        history.append({"role": "assistant", "text": reply})
        t.messages.append({"role": "assistant", "text": reply})
    return t


# ─── Price set ───


def price_set(cur: Any, hotel_slug: str) -> set[Decimal]:
    """Amounts the concierge may state: menu prices plus every number in the
    approved KB (a KB line may give a price without a currency)."""
    out: set[Decimal] = set()
    # A menu that cannot load (upsell off, no tables) leaves the KB amounts.
    menu = savepoint(cur, "trs_menu", lambda: upsell_api.get_menu(cur, hotel_slug)) or []
    for item in menu:
        for key in ("price", "price_display", "total"):
            if item.get(key) is not None:
                out |= textcheck.all_numbers(str(item[key]))
    for f in concierge_api.kb_facts(cur, hotel_slug, language="en"):
        if f.get("entry_id"):
            out |= textcheck.all_numbers(f.get("text") or "")
    return out


def _numbers_in_results(t: Transcript) -> set[Decimal]:
    # A priced quote from the dry-run executor (place_order confirm=false)
    # is a listed price too: the concierge is reading it back.
    out: set[Decimal] = set()
    for c in t.tool_calls:
        if isinstance(c.get("result"), dict):
            out |= textcheck.all_numbers(json.dumps(c["result"], default=str))
    return out


# ─── One scenario ───


def evaluate_once(cur: Any, hotel_slug: str, scenario: dict, *, prices: set[Decimal]) -> dict:
    """Run and grade one attempt: {passed, score, transcript, grader_outputs,
    failure_reasons, runner, prompt_version, model}."""
    scenario = localize(cur, hotel_slug, scenario)
    exp = dsl.parse(scenario.get("expectations"))
    ctx = scenario.get("context") or {}
    t = converse(cur, hotel_slug, scenario)
    gctx = GradeContext(room=ctx.get("room"), price_set=prices | _numbers_in_results(t))
    outputs = grade(exp, t, gctx)
    if not t.error and exp.needs_judge():
        if exp.unknown_or_escalate and escalated_as_unknown(t) and not exp.rubric and exp.grounded_in_kb is None:
            outputs.append({"grader": "judge", "passed": True, "score": 1.0, "detail": "escalated", "skipped": True})
        else:
            kb_answer = ctx.get("kb_reference")
            if exp.unknown_or_escalate and escalated_as_unknown(t):
                exp = exp.model_copy(update={"unknown_or_escalate": False})
            crit = judge.criteria_for(exp, kb_answer=kb_answer)
            outputs.append(judge.judge(hotel_slug=hotel_slug, scenario=scenario, transcript=t, criteria=crit,
                                       kb_answer=kb_answer))
    decisive = [o for o in outputs if not o.get("skipped")]
    passed = all(o["passed"] for o in outputs)
    score = (sum(1 for o in decisive if o["passed"]) / len(decisive)) if decisive else (1.0 if passed else 0.0)
    return {
        "passed": passed, "score": round(score, 4), "transcript": t.as_json(), "tool_calls": t.tool_calls,
        "grader_outputs": outputs,
        "failure_reasons": [f"{o['grader']}: {o.get('detail') or 'failed'}"[:300] for o in outputs if not o["passed"]],
        "runner": t.runner, "prompt_version": t.prompt_version, "model": t.model,
    }


def evaluate(cur: Any, hotel_slug: str, scenario: dict, *, prices: set[Decimal]) -> dict:
    """evaluate_once, or a best-of-3 majority for a flaky scenario (stops as
    soon as two attempts agree). The stored transcript is the last attempt
    that agreed with the verdict."""
    if not scenario.get("flaky"):
        out = evaluate_once(cur, hotel_slug, scenario, prices=prices)
        out["attempts"] = 1
        return out
    attempts: list[dict] = []
    need = MAJORITY_OF // 2 + 1
    while len(attempts) < MAJORITY_OF:
        attempts.append(evaluate_once(cur, hotel_slug, scenario, prices=prices))
        wins = sum(1 for a in attempts if a["passed"])
        if wins >= need or len(attempts) - wins >= need:
            break
        if attempts[-1]["transcript"].get("error"):
            break  # the runner itself is down; repeating costs money and changes nothing
    wins = sum(1 for a in attempts if a["passed"])
    verdict = wins >= need
    chosen = [a for a in attempts if a["passed"] == verdict][-1] if any(a["passed"] == verdict for a in attempts) else attempts[-1]
    out = dict(chosen)
    out["passed"] = verdict
    out["attempts"] = len(attempts)
    out["score"] = round(sum(a["score"] for a in attempts) / len(attempts), 4)
    out["grader_outputs"] = chosen["grader_outputs"] + [{"grader": "majority", "passed": verdict,
                                                          "detail": f"{wins}/{len(attempts)} attempts passed"}]
    return out
