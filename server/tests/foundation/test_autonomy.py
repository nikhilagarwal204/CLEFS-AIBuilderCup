"""
The autonomy dial (Canon 17, 04 spec section 1): each level's effect on
create_action, ceilings (always_approve types never run automatically),
gates, limits, re-resolution at execution, promotion suggestions at
50 decisions / 95% unedited, demotion triggers, and the routes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from clefs import jobs
from clefs.auth.deps import Principal
from clefs.core import actions, signals
from clefs.core.actions import ActionType, Gate
from clefs.core.errors import ClefsError

SYSTEM = Principal(kind="service", label="test")


class Limits(BaseModel):
    max_amount_minor: int = 0


class Runs:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, ctx: actions.ActionCtx) -> dict:
        self.calls.append(ctx.action["id"])
        return {"sent": True}


def _gate(ctx: actions.ActionCtx) -> Gate:
    if not ctx.params.get("template_approved"):
        return Gate(max_level="observe", reasons=["template not approved"])
    return Gate(max_level="auto")


@pytest.fixture
def runs():
    r = Runs()
    types = [
        ActionType(key="core.test_auto", module="core", label="Auto thing", executor=r),
        ActionType(key="core.test_refund", module="core", label="Refund", executor=r, always_approve=True),
        ActionType(key="core.test_limited", module="core", label="Gesture", executor=r, default_level="suggest",
                   max_level="auto_within_limits", limits_model=Limits),
        ActionType(key="core.test_gated", module="core", label="Journey send", executor=r, default_level="auto", gate=_gate),
    ]
    for t in types:
        actions.register_action_type(t)
    yield r
    for t in types:
        actions.unregister_action_type(t.key)


def _create(cur, slug, type_="core.test_auto", **kw):
    kw.setdefault("title", "Thing")
    return actions.create_action(cur, slug, type=type_, actor=SYSTEM, **kw)


def _run_queue():
    return jobs.run_pending(only=["core.actions.execute"])


def test_each_level_does_what_it_says(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    actions.set_autonomy(cur, slug, "core.test_auto", level="off", actor=owner_ctx)
    off = _create(cur, slug)
    assert off == {"id": None, "status": "suppressed", "reason": "dial_off", "type": "core.test_auto", "created": False}
    cur.execute("SELECT COUNT(*) AS n FROM actions WHERE hotel_slug = %s", (slug,))
    assert cur.fetchone()["n"] == 0

    actions.set_autonomy(cur, slug, "core.test_auto", level="observe", actor=owner_ctx)
    obs = _create(cur, slug)
    assert obs["status"] == "observed" and obs["surface"] == "none" and obs["terminal"]

    actions.set_autonomy(cur, slug, "core.test_auto", level="suggest", actor=owner_ctx)
    sug = _create(cur, slug)
    assert (sug["status"], sug["surface"]) == ("proposed", "console")

    actions.set_autonomy(cur, slug, "core.test_auto", level="approve", actor=owner_ctx)
    app = _create(cur, slug)
    assert (app["status"], app["surface"]) == ("proposed", "brief")

    actions.set_autonomy(cur, slug, "core.test_auto", level="auto", actor=owner_ctx)
    auto = _create(cur, slug)
    assert auto["status"] == "approved" and auto["decided_via"] == "dial" and not auto["human_decided"]
    ev = actions.list_events(cur, auto["id"])
    assert [(e["event"], e["actor_type"]) for e in ev] == [("created", "service"), ("approve", "dial")]

    _run_queue()
    assert runs.calls == [auto["id"]]  # observe/suggest/approve never ran
    done = actions.get_action(cur, slug, auto["id"])
    assert done["status"] == "done" and done["dial_level_at_execution"] == "auto"
    assert done["policy_version"] == 5  # stamped from the dial row at execution


def test_always_approve_types_never_run_automatically(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    with pytest.raises(ClefsError) as exc:
        actions.set_autonomy(cur, slug, "core.test_refund", level="auto", actor=owner_ctx)
    assert exc.value.status == 422 and exc.value.code == "above_ceiling"
    # Even a row forced into the table cannot lift it above the ceiling.
    cur.execute(
        "INSERT INTO action_autonomy (hotel_slug, action_type, level) VALUES (%s, 'core.test_refund', 'auto')", (slug,)
    )
    res = actions.resolve_level(cur, slug, "core.test_refund")
    assert (res.configured, res.effective) == ("auto", "approve") and res.reasons == ["ceiling approve"]
    a = _create(cur, slug, "core.test_refund", expected_minor=1000)
    assert a["status"] == "proposed"
    _run_queue()
    assert runs.calls == []
    assert actions.get_action_type("core.test_refund").ceiling == "approve"


def test_a_gate_caps_the_level(cur, hotel_uae, runs):
    blocked = _create(cur, hotel_uae.slug, "core.test_gated", params={"template_approved": False})
    assert blocked["status"] == "observed" and "template not approved" in blocked["limit_reasons"]
    ok = _create(cur, hotel_uae.slug, "core.test_gated", params={"template_approved": True})
    assert ok["status"] == "approved"
    _run_queue()
    assert runs.calls == [ok["id"]]


def test_limits_fall_back_to_approve(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    default = _create(cur, slug, "core.test_limited", expected_minor=3000)
    assert default["status"] == "proposed" and default["surface"] == "console"  # suggest by default
    actions.set_autonomy(cur, slug, "core.test_limited", level="auto_within_limits", limits={"max_amount_minor": 0}, actor=owner_ctx)
    zero = _create(cur, slug, "core.test_limited", expected_minor=3000)
    assert zero["status"] == "proposed" and zero["limit_reasons"] == ["amount_minor 3000 exceeds max_amount_minor 0"]
    actions.set_autonomy(cur, slug, "core.test_limited", level="auto_within_limits", limits={"max_amount_minor": 5000}, actor=owner_ctx)
    within = _create(cur, slug, "core.test_limited", expected_minor=3000)
    over = _create(cur, slug, "core.test_limited", expected_minor=8000)
    no_amount = _create(cur, slug, "core.test_limited")
    assert within["status"] == "approved"
    assert over["status"] == "proposed" and over["surface"] == "brief"
    assert no_amount["status"] == "proposed" and no_amount["limit_reasons"] == ["no limits apply to this action"]
    _run_queue()
    assert runs.calls == [within["id"]]
    with pytest.raises(ClefsError) as exc:
        actions.set_autonomy(cur, slug, "core.test_limited", level="auto", actor=owner_ctx)
    assert exc.value.code == "above_ceiling"
    with pytest.raises(ClefsError) as exc:
        actions.set_autonomy(cur, slug, "core.test_limited", level="approve", limits={"max_amount_minor": "lots"}, actor=owner_ctx)
    assert exc.value.code == "invalid_limits"


def test_execution_re_resolves_after_a_demotion(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    actions.set_autonomy(cur, slug, "core.test_auto", level="auto", actor=owner_ctx)
    a = _create(cur, slug)
    assert a["status"] == "approved"
    actions.report_incident(cur, slug, kind="guest_complaint", action_types=["core.test_auto"], evidence=[])
    _run_queue()
    assert runs.calls == []
    back = actions.get_action(cur, slug, a["id"])
    assert back["status"] == "proposed" and actions.list_events(cur, a["id"])[-1]["event"] == "dial_revoked"
    # A person approving it now runs it: human decisions are not second-guessed by the dial.
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    _run_queue()
    assert runs.calls == [a["id"]]
    assert actions.get_action(cur, slug, a["id"])["dial_level_at_execution"] == "approve"


def test_set_autonomy_needs_the_right_permission(cur, hotel_uae, runs, ctx_for):
    gm_ctx, _ = ctx_for("gm")
    staff_ctx, _ = ctx_for("staff")
    owner_ctx, _ = ctx_for("owner")
    with pytest.raises(ClefsError):
        actions.set_autonomy(cur, hotel_uae.slug, "core.test_auto", level="suggest", actor=staff_ctx)
    out = actions.set_autonomy(cur, hotel_uae.slug, "core.test_auto", level="suggest", actor=gm_ctx, reason="trial")
    assert out["level"] == "suggest" and out["policy_version"] == 1 and out["source"] == "hotel"
    with pytest.raises(ClefsError):
        actions.set_autonomy(cur, hotel_uae.slug, "core.test_auto", level="auto", actor=gm_ctx)
    out = actions.set_autonomy(cur, hotel_uae.slug, "core.test_auto", level="auto", actor=owner_ctx)
    assert out["level"] == "auto" and out["policy_version"] == 2
    same = actions.set_autonomy(cur, hotel_uae.slug, "core.test_auto", level="auto", actor=owner_ctx)
    assert same["policy_version"] == 2  # unchanged settings do not bump the version


def _decide_many(cur, slug, owner_ctx, n_approve: int, n_adjust: int = 0) -> None:
    for i in range(n_approve + n_adjust):
        a = _create(cur, slug, params={"n": i})
        if i < n_approve:
            actions.decide(cur, a["id"], "approve", actor=owner_ctx)
        else:
            actions.decide(cur, a["id"], "adjust", actor=owner_ctx, params_patch={"n": -i})


def test_promotion_is_suggested_at_fifty_decisions_and_95_percent(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    _decide_many(cur, slug, owner_ctx, 49)
    stats = actions.promotion_stats(cur, slug, "core.test_auto")
    assert stats["decisions"] == 49 and not stats["meets_threshold"]
    assert actions.review_promotions(cur, slug) == []
    _decide_many(cur, slug, owner_ctx, 1)
    stats = actions.promotion_stats(cur, slug, "core.test_auto")
    assert stats == {"window_days": 30, "decisions": 50, "approved_unedited": 50, "approval_rate": 1.0, "meets_threshold": True}
    out = actions.review_promotions(cur, slug)
    # core.test_auto has no limits_model, so the step skips auto_within_limits (04 spec 1.5).
    assert [(o["action_type"], o["from_level"], o["to_level"]) for o in out] == [("core.test_auto", "approve", "auto")]
    s = signals.list_signals(cur, slug, sections=["compliance"])
    assert [x["kind"] for x in s] == ["core.autonomy_promotion"] and s[0]["cadence"] == "weekly"
    assert actions.review_promotions(cur, slug) == []  # one open suggestion at a time
    assert actions.get_autonomy(cur, slug, "core.test_auto")["open_suggestion"]["event_id"] == out[0]["event_id"]


def test_edited_approvals_keep_the_rate_below_the_bar(cur, hotel_uae, runs, owner_ctx):
    _decide_many(cur, hotel_uae.slug, owner_ctx, 47, n_adjust=3)
    stats = actions.promotion_stats(cur, hotel_uae.slug, "core.test_auto")
    assert stats["decisions"] == 50 and stats["approval_rate"] == 0.94 and not stats["meets_threshold"]
    assert actions.review_promotions(cur, hotel_uae.slug) == []


def test_accepting_and_dismissing_suggestions(cur, hotel_uae, runs, ctx_for):
    owner_ctx, _ = ctx_for("owner")
    gm_ctx, _ = ctx_for("gm")
    slug = hotel_uae.slug
    _decide_many(cur, slug, owner_ctx, 50)
    [s] = actions.review_promotions(cur, slug)
    with pytest.raises(ClefsError):
        actions.accept_suggestion(cur, slug, s["event_id"], actor=gm_ctx)
    out = actions.accept_suggestion(cur, slug, s["event_id"], actor=owner_ctx)
    assert out["level"] == "auto" and out["open_suggestion"] is None
    assert signals.list_signals(cur, slug, sections=["compliance"]) == []
    with pytest.raises(ClefsError) as exc:
        actions.accept_suggestion(cur, slug, s["event_id"], actor=owner_ctx)
    assert exc.value.code == "suggestion_stale"

    actions.set_autonomy(cur, slug, "core.test_auto", level="approve", actor=owner_ctx)
    [s2] = actions.review_promotions(cur, slug)
    actions.dismiss_suggestion(cur, slug, s2["event_id"], actor=gm_ctx)
    assert actions.review_promotions(cur, slug) == []  # quiet for 30 days after a dismissal
    cur.execute("SELECT status FROM signals WHERE hotel_slug = %s AND kind = 'core.autonomy_promotion'", (slug,))
    assert cur.fetchone()["status"] == "ignored"


def test_a_suggestion_after_a_dismissal_surfaces_again(cur, hotel_uae, runs, owner_ctx, clock):
    """Each suggestion is its own signal: the one a person ignored stays
    ignored, and the next one (after the 30-day quiet period) is open."""
    slug = hotel_uae.slug
    clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    _decide_many(cur, slug, owner_ctx, 50)
    [first] = actions.review_promotions(cur, slug)
    actions.dismiss_suggestion(cur, slug, first["event_id"], actor=owner_ctx)
    clock.advance(days=31)
    _decide_many(cur, slug, owner_ctx, 50)
    [second] = actions.review_promotions(cur, slug)
    assert second["event_id"] != first["event_id"]
    open_now = signals.list_signals(cur, slug, sections=["compliance"])
    assert [x["subject_id"] for x in open_now] == [str(second["event_id"])]

    # A manual dial change makes the open suggestion stale: it leaves the brief.
    actions.set_autonomy(cur, slug, "core.test_auto", level="suggest", actor=owner_ctx)
    assert signals.list_signals(cur, slug, sections=["compliance"]) == []


def test_a_second_demotion_surfaces_after_the_first_was_ignored(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    actions.set_autonomy(cur, slug, "core.test_auto", level="auto", actor=owner_ctx)
    actions.demote(cur, slug, "core.test_auto", trigger="guest_complaint")
    [first] = signals.list_signals(cur, slug, sections=["compliance"])
    signals.set_status(cur, first["id"], "ignored", resolution="seen")
    actions.set_autonomy(cur, slug, "core.test_auto", level="auto", actor=owner_ctx)
    actions.demote(cur, slug, "core.test_auto", trigger="optout_spike")
    [second] = signals.list_signals(cur, slug, sections=["compliance"])
    assert second["id"] != first["id"] and second["status"] == "open" and "opt" in second["title"].lower()


@pytest.mark.parametrize("trigger", ["guest_complaint", "optout_spike", "meta_quality", "eval_regression"])
def test_each_demotion_trigger(cur, hotel_uae, runs, owner_ctx, trigger):
    slug = hotel_uae.slug
    actions.set_autonomy(cur, slug, "core.test_auto", level="auto", actor=owner_ctx)
    [out] = actions.report_incident(
        cur, slug, kind=trigger, action_types=["core.test_auto"],
        evidence=[{"kind": "metric", "label": "Opt-outs (7 d)", "value": 4}],
    )
    assert (out["from_level"], out["to_level"], out["policy_version"]) == ("auto", "approve", 2)
    cur.execute(
        "SELECT kind, trigger, from_level, to_level FROM action_autonomy_events WHERE hotel_slug = %s ORDER BY id DESC LIMIT 1",
        (slug,),
    )
    assert dict(cur.fetchone()) == {"kind": "demoted", "trigger": trigger, "from_level": "auto", "to_level": "approve"}
    sig = signals.list_signals(cur, slug, sections=["compliance"])
    assert [x["kind"] for x in sig] == ["core.autonomy_demoted"] and sig[0]["evidence"][0]["value"] == 4
    low = actions.demote(cur, slug, "core.test_auto", trigger=trigger)
    assert low["to_level"] == "approve"  # never raises a level
    with pytest.raises(ValueError):
        actions.report_incident(cur, slug, kind="bad_vibes", action_types=["core.test_auto"])


def test_autonomy_routes(cur, client, client_as, hotel_uae, runs):
    base = f"/hotels/{hotel_uae.slug}/autonomy"
    owner, gm, staff = client_as("owner"), client_as("gm"), client_as("staff")
    listed = owner.get(base)
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r["action_type"] == "core.test_limited")
    assert row["level"] == "suggest" and row["max_level"] == "auto_within_limits"
    assert row["limits_schema"]["properties"]["max_amount_minor"]["type"] == "integer"
    assert staff.get(base).status_code == 403
    assert client.get(base).status_code == 401
    assert gm.put(f"{base}/core.test_auto", json={"level": "suggest"}).status_code == 200
    assert gm.put(f"{base}/core.test_auto", json={"level": "auto"}).status_code == 403
    r = owner.put(f"{base}/core.test_refund", json={"level": "auto"})
    assert r.status_code == 422 and r.json()["code"] == "above_ceiling"
    assert owner.put(f"{base}/core.test_auto", json={"level": "sideways"}).json()["code"] == "invalid_level"
    assert owner.put(f"{base}/core.nope", json={"level": "approve"}).json()["code"] == "unknown_action_type"
    ok = owner.put(f"{base}/core.test_limited", json={"level": "auto_within_limits", "limits": {"max_amount_minor": 20000}})
    assert ok.status_code == 200 and ok.json()["limits"] == {"max_amount_minor": 20000}
    assert owner.get(f"{base}/core.test_limited").json()["level"] == "auto_within_limits"


def test_autonomy_review_job_runs_per_hotel(cur, hotel_uae, runs, owner_ctx, clock):
    _decide_many(cur, hotel_uae.slug, owner_ctx, 50)
    clock.freeze(datetime(2026, 10, 2, 0, 15, tzinfo=timezone.utc))  # 04:15 in Dubai
    jobs.tick(only=["core.autonomy_review"], queue=False)
    cur.execute(
        "SELECT result FROM job_runs WHERE job = 'core.autonomy_review' AND hotel_slug = %s", (hotel_uae.slug,)
    )
    assert cur.fetchone()["result"]["suggested"][0]["action_type"] == "core.test_auto"
