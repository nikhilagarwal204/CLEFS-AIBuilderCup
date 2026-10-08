"""The eval runner against the real Concierge api (no scripted concierge).

Concierge is built in parallel by another lane, so this only pins what
Trust relies on: the eval session builds with a prompt_version, is a dry
run, and a scenario completes (on the Concierge text turn or the Responses
fallback) with every AI call answered by a fake backend.
"""

from __future__ import annotations

import pytest

from clefs import ai
from clefs.modules.trust.evals import autokb, readiness, runner, runs, scenarios


def _answer(req):
    if req.kind == "raw":
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Two towels are coming."}]}]}
    if req.kind == "tool_call":
        return [{"name": "notify_staff", "arguments": {"request_type": "towels", "quantity": 2}}]
    return "Two towels are coming."


class _Any:
    name = "fake"

    def respond(self, req):
        return ai._coerce(_answer(req), req)


def _concierge_migrated(cur):
    cur.execute("SELECT to_regclass('cc_kb_entries') IS NOT NULL AS ok")
    return cur.fetchone()["ok"]


def test_without_concierge_tables_trust_degrades_instead_of_failing(cur, hotel_uae):
    if _concierge_migrated(cur):
        pytest.skip("Concierge is migrated in this run")
    # No session, no KB: the gate is red, nothing raises, the transaction survives.
    assert runs.current_prompt_version(cur, hotel_uae.slug) is None
    state = readiness.evaluate(cur, hotel_uae.slug)
    assert state["ready"] is False and not {c["key"]: c for c in state["checks"]}["kb_sections"]["ok"]
    assert autokb.sync(cur, hotel_uae.slug)["entries"] == 0
    cur.execute("SELECT 1 AS ok")
    assert cur.fetchone()["ok"] == 1


def test_real_concierge_eval_session_and_one_scenario(cur, hotel_uae):
    if not _concierge_migrated(cur):
        pytest.skip("Concierge is not migrated in this run (CLEFS_ONLY_MODULES)")
    pv = runs.current_prompt_version(cur, hotel_uae.slug)
    assert isinstance(pv, str) and pv
    sc = next(s for s in scenarios.load_standard() if s["key"] == "routing.two_towels")
    spec = runner.build_spec(cur, hotel_uae.slug, runner.localize(cur, hotel_uae.slug, sc))
    assert spec.dry_run is True and spec.surface == "eval" and spec.room
    ai.set_backend(_Any())
    try:
        t = runner.converse(cur, hotel_uae.slug, sc)
    finally:
        ai.reset_backend()
    assert t.runner in ("concierge_text", "responses") and len(t.messages) == 2
