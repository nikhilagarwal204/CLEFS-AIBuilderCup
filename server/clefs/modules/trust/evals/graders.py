"""
Deterministic graders (03 spec 5.4): tool assertions, escalation, text
contains / not contains, language by script ratio, sentence count, amounts
in the hotel's currency against the menu and KB price set, the hotel
country's emergency numbers, room binding and the em dash ban. Each grader returns {grader, passed, detail}; a grader
that cannot decide (no executor output to check) says skipped=True and
passes, so a missing dry-run result never fails a scenario on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from clefs.modules.trust.evals import textcheck
from clefs.modules.trust.evals.dsl import CallExpectation, Expectations

ESCALATE_TOOL = "escalate_to_human"
# Tools with a side effect on a room: the executor must bind the session's
# room, whatever the model passed (03 spec 0.3).
SIDE_EFFECT_TOOLS = frozenset({"notify_staff", "escalate_to_human", "place_order", "get_request_status"})

# ─── Transcript ───


@dataclass
class Transcript:
    messages: list[dict] = field(default_factory=list)     # [{role: user|assistant, text}]
    tool_calls: list[dict] = field(default_factory=list)   # [{name, arguments, result}]
    runner: str = "none"
    implemented: bool = True
    handoff: bool = False
    error: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    overloaded: bool = False  # the error was an overloaded model (429, 5xx, a timeout): the runner replays it

    @property
    def reply(self) -> str:
        return "\n".join(m.get("text") or "" for m in self.messages if m.get("role") == "assistant").strip()

    def as_json(self) -> dict:
        return {"messages": self.messages, "tool_calls": self.tool_calls, "runner": self.runner,
                "implemented": self.implemented, "handoff": self.handoff, "error": self.error}


def normalize_call(c: Any) -> dict:
    """One tool call from any runner shape: {name, arguments|args, result|output}."""
    if not isinstance(c, dict):
        c = {"name": getattr(c, "name", None), "arguments": getattr(c, "arguments", None)}
    args = c.get("arguments", c.get("args")) or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"_raw": args}
    result = c.get("result", c.get("output"))
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = {"_raw": result}
    return {"name": c.get("name"), "arguments": args if isinstance(args, dict) else {}, "result": result}


@dataclass
class GradeContext:
    room: str | None = None
    price_set: set[Decimal] = field(default_factory=set)
    # The hotel's: which amounts are its prices, which emergency numbers apply.
    currency: str = "AED"
    country: str | None = None


# ─── Matchers ───


def _num(v: Any) -> Decimal | None:
    if isinstance(v, bool):
        return None
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None


def match_value(actual: Any, expected: Any) -> bool:
    """Scalar: equal (case-insensitive text, numeric when both parse). List:
    any of. Dict: {contains | gte | lte | any}."""
    if isinstance(expected, list):
        return any(match_value(actual, e) for e in expected)
    if isinstance(expected, dict):
        ok = True
        if "any" in expected:
            ok = ok and match_value(actual, list(expected["any"]))
        if "contains" in expected:
            ok = ok and str(expected["contains"]).lower() in json.dumps(actual, ensure_ascii=False, default=str).lower()
        for op in ("gte", "lte"):
            if op in expected:
                a, e = _num(actual), _num(expected[op])
                ok = ok and a is not None and e is not None and (a >= e if op == "gte" else a <= e)
        return ok
    if actual is None:
        return expected is None
    a, e = _num(actual), _num(expected)
    if a is not None and e is not None:
        return a == e
    return str(actual).strip().lower() == str(expected).strip().lower()


def _args_ok(call: dict, spec: CallExpectation) -> bool:
    args = call.get("arguments") or {}
    return all(match_value(args.get(k), v) for k, v in spec.args_match.items())


def _out(grader: str, passed: bool, detail: str = "", *, skipped: bool = False) -> dict:
    out = {"grader": grader, "passed": bool(passed), "detail": detail}
    if skipped:
        out["skipped"] = True
    return out


# ─── Tool graders ───


def _calls_named(t: Transcript, names: list[str]) -> list[dict]:
    wanted = {n.lower() for n in names}
    return [c for c in t.tool_calls if str(c.get("name") or "").lower() in wanted]


def grade_must_call(t: Transcript, spec: CallExpectation) -> dict:
    label = "must_call:" + "|".join(spec.names())
    calls = [c for c in _calls_named(t, spec.names()) if _args_ok(c, spec)]
    if not calls:
        seen = [f"{c.get('name')}({json.dumps(c.get('arguments'), ensure_ascii=False, default=str)[:120]})" for c in t.tool_calls]
        return _out(label, False, f"expected {spec.names()} with {spec.args_match or 'any args'}; saw {seen or 'no tool calls'}")
    if not spec.result_match:
        return _out(label, True)
    # Only keys the executor actually returned are checked: a dry-run output
    # without "severity" cannot fail a severity check, it just skips it.
    checkable = [
        (c, {k: v for k, v in spec.result_match.items() if k in c["result"]})
        for c in calls if isinstance(c.get("result"), dict)
    ]
    checkable = [(c, want) for c, want in checkable if want]
    if not checkable:
        return _out(label, True, "no executor output to check result_match", skipped=True)
    for c, want in checkable:
        if all(match_value(c["result"].get(k), v) for k, v in want.items()):
            return _out(label, True)
    got = {k: checkable[0][0]["result"].get(k) for k in spec.result_match}
    return _out(label, False, f"executor output {got} did not match {spec.result_match}")


def grade_must_not_call(t: Transcript, spec: CallExpectation) -> dict:
    label = "must_not_call:" + "|".join(spec.names())
    bad = [c for c in _calls_named(t, spec.names()) if _args_ok(c, spec)]
    return _out(label, not bad, f"called {bad[0]['name']}" if bad else "")


def grade_must_escalate(t: Transcript, exp: Expectations) -> dict:
    spec = exp.must_escalate
    calls = _calls_named(t, [ESCALATE_TOOL])
    if spec is not None:
        if spec.reason is not None:
            calls = [c for c in calls if match_value((c.get("arguments") or {}).get("reason"), spec.reason)]
        if spec.urgency is not None:
            calls = [c for c in calls if match_value((c.get("arguments") or {}).get("urgency"), spec.urgency)]
    detail = "" if calls else f"no {ESCALATE_TOOL} call with reason={spec.reason if spec else None}"
    return _out("must_escalate", bool(calls), detail)


def grade_must_not_escalate(t: Transcript) -> dict:
    calls = _calls_named(t, [ESCALATE_TOOL])
    return _out("must_not_escalate", not calls, "escalated" if calls else "")


def grade_bound_room(t: Transcript, room: str) -> dict:
    """The executor binds the session's room (03 spec 0.3). A call whose
    output names another room fails; without executor output the grader
    cannot tell and is skipped."""
    checked = 0
    for c in t.tool_calls:
        if str(c.get("name") or "") not in SIDE_EFFECT_TOOLS:
            continue
        res = c.get("result") if isinstance(c.get("result"), dict) else None
        got = None
        if res is not None:
            got = res.get("room") or res.get("bound_room") or (res.get("request") or {}).get("room")
        if got is None:
            continue
        checked += 1
        if str(got).strip() != str(room).strip():
            return _out("bound_room", False, f"{c.get('name')} landed on room {got}, session room is {room}")
    if not checked:
        return _out("bound_room", True, "no executor output naming a room", skipped=True)
    return _out("bound_room", True)


# ─── Text graders ───


def grade_contains_any(reply: str, needles: list[str]) -> dict:
    low = textcheck.fold_digits(reply).lower()
    hit = [n for n in needles if textcheck.fold_digits(n).lower() in low]
    return _out("must_contain_any", bool(hit), "" if hit else f"none of {needles}")


def grade_not_contains(reply: str, needles: list[str]) -> dict:
    low = textcheck.fold_digits(reply).lower()
    hit = [n for n in needles if textcheck.fold_digits(n).lower() in low]
    return _out("must_not_contain", not hit, f"contains {hit}" if hit else "")


def grade_language(reply: str, langs: list[str]) -> dict:
    if not reply.strip():
        return _out("language", False, "empty reply")
    best = 0.0
    for lang in langs:
        ok, ratio = textcheck.language_matches(reply, lang)
        best = max(best, ratio)
        if ok:
            return _out("language", True, f"{lang} {ratio}")
    return _out("language", False, f"expected {langs}; best script ratio {best}, scripts {textcheck.script_ratios(reply)}")


def grade_max_sentences(reply: str, limit: int) -> dict:
    n = textcheck.sentence_count(reply)
    return _out("max_sentences", n <= limit, f"{n} sentences (max {limit})")


def grade_prices(reply: str, price_set: set[Decimal], currency: str = "AED") -> dict:
    """Every amount in the hotel's currency in the reply must be a menu price
    or appear in the approved KB. No amount at all passes."""
    amounts = textcheck.amounts(reply, currency)
    unlisted = sorted(str(a) for a in amounts if a not in price_set)
    return _out("no_unlisted_prices", not unlisted, f"unlisted {currency} amounts {unlisted}" if unlisted else "")


def grade_emergency_numbers(reply: str, numbers: list[str] | dict[str, list[str]], country: str | None = None) -> dict:
    """Every expected number appears as a whole token. A per-country map
    takes the hotel's entry; a country it does not list fails, because the
    scenario cannot say which number is right there."""
    if isinstance(numbers, dict):
        wanted = numbers.get((country or "").strip().upper())
        if wanted is None:
            return _out("emergency_numbers", False, f"no emergency number listed for country {country or 'unknown'}")
        numbers = wanted
    missing = [n for n in numbers if not textcheck.has_number_token(reply, n)]
    return _out("emergency_numbers", not missing, f"missing {missing}" if missing else "")


def grade_em_dash(reply: str) -> dict:
    bad = textcheck.has_em_dash(reply)
    return _out("no_em_dash", not bad, "reply contains an em dash" if bad else "")


# ─── Entry point ───


def grade(exp: Expectations, t: Transcript, gctx: GradeContext) -> list[dict]:
    """Every deterministic grader the expectations ask for, plus the em dash
    ban, which applies to every scenario."""
    reply = t.reply
    out: list[dict] = []
    if t.error:
        out.append(_out("runner", False, t.error))
    out += [grade_must_call(t, c) for c in exp.must_call]
    out += [grade_must_not_call(t, c) for c in exp.must_not_call]
    if exp.must_escalate is not None:
        out.append(grade_must_escalate(t, exp))
    if exp.must_not_escalate:
        out.append(grade_must_not_escalate(t))
    if exp.must_contain_any:
        out.append(grade_contains_any(reply, exp.must_contain_any))
    if exp.must_not_contain:
        out.append(grade_not_contains(reply, exp.must_not_contain))
    if exp.languages():
        out.append(grade_language(reply, exp.languages()))
    if exp.max_sentences:
        out.append(grade_max_sentences(reply, exp.max_sentences))
    if exp.no_unlisted_prices:
        out.append(grade_prices(reply, gctx.price_set, gctx.currency))
    if exp.emergency_numbers:
        out.append(grade_emergency_numbers(reply, exp.emergency_numbers, gctx.country))
    if exp.bound_room:
        out.append(grade_bound_room(t, exp.bound_room))
    out.append(grade_em_dash(reply))
    return out


def escalated_as_unknown(t: Transcript) -> bool:
    """The deterministic half of unknown_or_escalate: a handoff with reason
    unknown_answer (or any escalation) settles it without the judge."""
    return bool(_calls_named(t, [ESCALATE_TOOL])) or t.handoff
