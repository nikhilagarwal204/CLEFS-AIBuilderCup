"""
Integrator lane (wave 0b): the foundation requests applied after every other
lane finished.

  - New hotels have no dial rows (04 spec 1.4; the refix reversed the old
    pinning, see test_w0b_refix_servercontracts.py).
  - Accepting a promotion over HTTP follows accept_perm_for(to_level): a GM
    (core.autonomy.manage) accepts up to approve, only raise_auto above it.
  - GET /hotels/{slug}/autonomy/{action_type}/events is the dial's history.
  - core.serpapi.purge is registered by jobs.load_all() on its own.
  - A malformed Decimal setting fails at startup naming the variable.
  - The worker logs the same production gaps as create_app.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess

import pytest

from clefs import jobs
from clefs.core import actions
from clefs.core.actions import SYSTEM, ActionType
from clefs.settings import reload_settings
from clefs.worker import loop as worker_loop


# ─── Fixtures and helpers ───


@pytest.fixture
def dial_types():
    """Human-task types (no executor, so nothing queues): one promoted from
    suggest to approve, one from approve to auto (no limits_model, so the
    promotion skips auto_within_limits)."""
    types = [
        ActionType(key="core.w0b_int_suggest", module="core", label="Draft", default_level="suggest"),
        ActionType(key="core.w0b_int_auto", module="core", label="Send"),
    ]
    for t in types:
        actions.register_action_type(t)
    yield types
    for t in types:
        actions.unregister_action_type(t.key)


def _approve_many(cur, slug: str, actor, type_: str, n: int = 50) -> None:
    for i in range(n):
        a = actions.create_action(cur, slug, type=type_, title=f"T{i}", params={"n": i}, actor=SYSTEM)
        actions.decide(cur, a["id"], "approve", actor=actor)


def _suggestion(cur, slug: str, type_: str) -> dict:
    [s] = [s for s in actions.review_promotions(cur, slug) if s["action_type"] == type_]
    return s


# ─── Accepting a promotion over HTTP (04 spec 1.5 / 7.1) ───


def test_a_gm_accepts_a_promotion_to_approve_over_http(cur, hotel_uae, client_as, owner_ctx, dial_types):
    slug = hotel_uae.slug
    _approve_many(cur, slug, owner_ctx, "core.w0b_int_suggest")
    s = _suggestion(cur, slug, "core.w0b_int_suggest")
    assert s["to_level"] == "approve"
    res = client_as("gm").post(f"/hotels/{slug}/autonomy/suggestions/{s['event_id']}/accept")
    assert res.status_code == 200, res.json()
    assert res.json()["level"] == "approve" and res.json()["open_suggestion"] is None


def test_accepting_a_promotion_above_approve_still_needs_the_owner(cur, hotel_uae, client_as, owner_ctx, dial_types):
    slug = hotel_uae.slug
    _approve_many(cur, slug, owner_ctx, "core.w0b_int_auto")
    s = _suggestion(cur, slug, "core.w0b_int_auto")
    assert s["to_level"] == "auto"  # no limits_model: auto_within_limits is skipped
    path = f"/hotels/{slug}/autonomy/suggestions/{s['event_id']}/accept"
    assert client_as("revenue").post(path).status_code == 403  # no core.autonomy.manage at all
    denied = client_as("gm").post(path)
    assert denied.status_code == 403
    assert actions.get_autonomy(cur, slug, "core.w0b_int_auto")["open_suggestion"]["event_id"] == s["event_id"]
    ok = client_as("owner").post(path)
    assert ok.status_code == 200 and ok.json()["level"] == "auto"


# ─── The dial's history ───


def test_autonomy_history_lists_a_dials_events_newest_first(cur, hotel_uae, client_as, owner_ctx, dial_types):
    slug = hotel_uae.slug
    owner = client_as("owner")
    base = f"/hotels/{slug}/autonomy/core.w0b_int_suggest"
    assert owner.put(base, json={"level": "approve", "reason": "trial"}).status_code == 200
    actions.demote(cur, slug, "core.w0b_int_suggest", trigger="guest_complaint", evidence=[{"quote": "guest words"}])
    events = client_as("revenue").get(f"{base}/events").json()  # core.autonomy.view is enough
    assert [(e["kind"], e["from_level"], e["to_level"]) for e in events] == [
        ("demoted", "approve", "approve"), ("set", "suggest", "approve"), ("seed", None, "suggest")]
    assert set(events[0]) == set(actions.AUTONOMY_EVENT_FIELDS)
    assert events[0]["trigger"] == "guest_complaint" and events[1]["actor_type"] == "user"
    assert "evidence" not in events[0]  # a demotion's evidence can quote a guest
    assert len(owner.get(f"{base}/events", params={"limit": 1}).json()) == 1
    assert client_as("front_desk").get(f"{base}/events").status_code == 403
    unknown = owner.get(f"/hotels/{slug}/autonomy/core.no_such_type/events")
    assert unknown.status_code == 400 and unknown.json()["code"] == "unknown_action_type"


# ─── Jobs, settings, worker ───


def test_load_all_registers_the_serpapi_purge():
    jobs.load_all()
    assert "clefs.core.serpapi" in jobs.CORE_REGISTRANTS
    assert "core.serpapi.purge" in {j.name for j in jobs.registered_jobs()}


def test_a_malformed_decimal_setting_fails_naming_the_variable(env):
    # env() reloads the settings itself, which is where the value is parsed.
    with pytest.raises(ValueError, match="SERPAPI_COST_PER_SEARCH_USD"):
        env(SERPAPI_COST_PER_SEARCH_USD="0,015")
    with pytest.raises(ValueError, match="SERPAPI_COST_PER_SEARCH_USD"):
        reload_settings()  # never cached: every reader fails the same way
    env(SERPAPI_COST_PER_SEARCH_USD="0.02")
    from clefs.core import serpapi

    assert serpapi.cost_per_search_micros() == 20_000


def test_the_worker_logs_production_gaps_at_startup(env, monkeypatch, caplog):
    env(ENV="production", SECRET_KEY="w" * 40, COOKIE_SECURE="true", CONTACT_HMAC_PEPPER=None, PII_ENC_KEY=None)

    class Stop(Exception):
        pass

    def stop(*_a, **_k):
        raise Stop

    monkeypatch.setattr(worker_loop.jobs, "load_all", stop)
    monkeypatch.setattr(logging, "basicConfig", lambda **_k: None)
    with caplog.at_level(logging.WARNING), pytest.raises(Stop):
        worker_loop.main(["--once"])
    assert any("CONTACT_HMAC_PEPPER is not set" in r.getMessage() for r in caplog.records)
    assert any("PII_ENC_KEY is not set" in r.getMessage() for r in caplog.records)


# ─── Client mirrors of the above (client/app/lib/types.ts, settings/autonomy) ───

HISTORY_HARNESS = r"""
const fs = require("fs");
const ts = require(process.env.TS_PATH);
for (const ext of [".ts", ".tsx"]) {
  require.extensions[ext] = (m, filename) => {
    const out = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      fileName: filename,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    });
    m._compile(out.outputText, filename);
  };
}
const d = require(process.env.DIAL_TS);
const input = JSON.parse(fs.readFileSync(process.env.INPUT, "utf8"));
console.log(JSON.stringify({
  acceptPerms: input.levels.map(l => d.acceptPermFor(l)),
  gmAccepts: input.levels.map(l => d.canAcceptSuggestion(l, true, false)),
  ownerAccepts: input.levels.map(l => d.canAcceptSuggestion(l, true, true)),
  viewerAccepts: input.levels.map(l => d.canAcceptSuggestion(l, false, false)),
  history: input.events.map(e => d.describeAutonomyEvent(e)),
}));
"""


def test_types_ts_mirrors_the_history_route(cur, hotel_uae, owner_ctx, dial_types):
    from tests.foundation.test_w0b_client import check_literals, ts_interface, ts_union

    actions.set_autonomy(cur, hotel_uae.slug, "core.w0b_int_suggest", level="approve", actor=owner_ctx)
    row, seed = actions.list_autonomy_events(cur, hotel_uae.slug, "core.w0b_int_suggest")
    assert (row["kind"], seed["kind"]) == ("set", "seed")  # the first set logs the default it left
    fields = ts_interface("AutonomyEvent")
    assert set(fields) == set(row) == set(actions.AUTONOMY_EVENT_FIELDS)
    assert not any(fields.values()), "every AutonomyEvent field is always sent"
    assert ts_union("AutonomySuggestionStatus") == check_literals(cur, "action_autonomy_events", "status")


def test_autonomy_page_gates_accept_by_target_and_leaves_limits_to_the_server():
    from tests.foundation.test_w0b_client import AUTONOMY_PAGE

    page = AUTONOMY_PAGE.read_text()
    assert "canAcceptSuggestion(s.to_level, canManage, canRaiseAuto)" in page
    assert "disabled={!canRaiseAuto || busy" not in page  # the old owner-only Accept
    assert re.search(r"const limitsLocked = !canManage \|\| saving;", page)
    assert "/autonomy/${encodeURIComponent(row.action_type)}/events" in page


def test_dial_helpers_mirror_accept_perm_for_and_describe_history(cur, hotel_uae, owner_ctx, dial_types, tmp_path):
    from tests.foundation.test_w0b_client import CLIENT, DIAL_TS, _node

    node = _node()
    ts_path = CLIENT / "node_modules" / "typescript"
    if node is None or not ts_path.exists():
        pytest.skip("node 18+ and client/node_modules/typescript are needed to run dial.ts")
    slug, key = hotel_uae.slug, "core.w0b_int_suggest"
    actions.set_autonomy(cur, slug, key, level="approve", actor=owner_ctx)  # the first row: seed + set
    actions.demote(cur, slug, key, trigger="optout_spike")
    events = json.loads(json.dumps(actions.list_autonomy_events(cur, slug, key), default=str))
    (tmp_path / "input.json").write_text(json.dumps({"levels": list(actions.LEVELS), "events": events}))
    (tmp_path / "harness.js").write_text(HISTORY_HARNESS)
    proc = subprocess.run(
        [node, str(tmp_path / "harness.js")],
        capture_output=True, text=True, timeout=90, cwd=CLIENT,
        env={"PATH": "/usr/bin:/bin", "TS_PATH": str(ts_path), "DIAL_TS": str(DIAL_TS), "INPUT": str(tmp_path / "input.json")},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["acceptPerms"] == [actions.accept_perm_for(level) for level in actions.LEVELS]
    assert out["gmAccepts"] == [actions.level_rank(level) <= actions.level_rank("approve") for level in actions.LEVELS]
    assert out["ownerAccepts"] == [True] * len(actions.LEVELS)
    assert out["viewerAccepts"] == [False] * len(actions.LEVELS)
    assert out["history"] == [
        "Lowered from Approve to Approve after an opt-out spike",
        "Changed from Suggest to Approve",
        "Started at the default, Suggest",
    ]
