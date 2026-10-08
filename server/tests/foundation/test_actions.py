"""
Actions ledger (Canon 7): the main path, illegal transitions, the dispute
path, snooze and wake, adjust, nonces, permissions, execution through the
queue, verification (including pending and reversal) and the HTTP routes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from clefs import jobs
from clefs.auth.deps import Principal
from clefs.core import actions, signals
from clefs.core.actions import ActionType, ExecResult, VerifyResult
from clefs.core.errors import ClefsError
from clefs.core.signals import Evidence, SignalIn

SYSTEM = Principal(kind="service", label="test")


class Recorder:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.fail = False
        self.decline = False
        self.verify: VerifyResult | None = None

    def execute(self, ctx: actions.ActionCtx) -> ExecResult:
        self.calls.append(ctx.action["id"])
        if self.fail:
            raise RuntimeError("provider down")
        if self.decline:
            return ExecResult(ok=False, error="guest opted out")
        return ExecResult(data={"ok": 1}, template_id="tpl@3", template_category="UTILITY", consent_basis="contract:reservation:9",
                          reversal_handle={"kind": "none", "reason": "message_sent"})

    def verifier(self, ctx: actions.ActionCtx) -> VerifyResult:
        return self.verify or VerifyResult(status="pending")


def _check_rate(ctx: actions.ActionCtx, params: dict) -> None:
    if params.get("rate", 0) > 500:
        raise ValueError("rate above the ceiling of 500")


@pytest.fixture
def rec():
    r = Recorder()
    types = [
        ActionType(key="core.test_task", module="core", label="Test task", default_owner_role="gm", validate_params=_check_rate),
        ActionType(key="core.test_exec", module="core", label="Test exec", executor=r.execute, verifier=r.verifier,
                   verify_after=timedelta(hours=1), verify_every=timedelta(days=1), verify_until=timedelta(days=3),
                   counts_toward_verified_profit=True),
        ActionType(key="core.test_owner_only", module="core", label="Owner only", decide_perm="core.autonomy.raise_auto"),
    ]
    for t in types:
        actions.register_action_type(t)
    yield r
    for t in types:
        actions.unregister_action_type(t.key)


def _new(cur, slug, type_="core.test_task", **kw):
    kw.setdefault("title", "Do the thing")
    kw.setdefault("actor", SYSTEM)
    return actions.create_action(cur, slug, type=type_, **kw)


def _events(cur, action_id):
    return [e["event"] for e in actions.list_events(cur, action_id)]


def test_main_path_for_a_human_task(cur, hotel_uae, rec, owner_ctx, ctx_for):
    gm_ctx, gm = ctx_for("gm")
    a = _new(cur, hotel_uae.slug, params={"rate": 400}, expected_value=Decimal("250.5"))
    assert a["status"] == "proposed" and a["surface"] == "brief" and a["created"]
    assert a["expected_value"] == "250.50" and a["currency"] == "AED" and a["owner_role"] == "gm"
    a = actions.decide(cur, a["id"], "approve", actor=owner_ctx, via="console")
    assert a["status"] == "approved" and a["decided_by"] == owner_ctx.principal.user_id and a["human_decided"]
    a = actions.decide(cur, a["id"], "assign", actor=owner_ctx, assignee_user_id=gm["id"])
    assert a["status"] == "assigned" and a["assignee_user_id"] == gm["id"]
    a = actions.start(cur, a["id"], actor=gm_ctx)
    assert a["status"] == "in_progress"
    a = actions.mark_done(cur, a["id"], actor=gm_ctx, note="done by hand")
    assert a["status"] == "done" and a["executed_at"] is not None
    with pytest.raises(ClefsError):
        actions.record_verification(cur, a["id"], status="bogus")  # type: ignore[arg-type]
    a = actions.record_verification(cur, a["id"], verified=True, outcome_value=Decimal("250"), evidence=[Evidence(kind="row", label="Folio")])
    assert a["status"] == "verified" and a["outcome_value"] == "250.00" and a["terminal"]
    assert a["counts_toward_verified_profit"] is False  # the type does not count toward verified profit
    assert _events(cur, a["id"]) == ["created", "approve", "assign", "start", "done", "verified"]
    cur.execute("SELECT action FROM audit_log WHERE entity_type = 'action' AND entity_id = %s ORDER BY id", (str(a["id"]),))
    assert [r["action"] for r in cur.fetchall()] == [
        "core.actions.create", "core.actions.approve", "core.actions.assign", "core.actions.start", "core.actions.done",
        "core.actions.verify",
    ]


def test_illegal_transitions_are_refused(cur, hotel_uae, rec, owner_ctx):
    a = _new(cur, hotel_uae.slug)
    for bad in (
        lambda: actions.mark_done(cur, a["id"], actor=owner_ctx),
        lambda: actions.start(cur, a["id"], actor=owner_ctx),
        lambda: actions.record_verification(cur, a["id"], verified=True),
        lambda: actions.give_up(cur, a["id"]),
        lambda: actions.await_counterparty(cur, a["id"]),
    ):
        with pytest.raises(ClefsError) as exc:
            bad()
        assert exc.value.status == 409 and exc.value.code == "illegal_transition"
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    for decision in ("ignore", "dispute", "approve"):
        with pytest.raises(ClefsError) as exc:
            actions.decide(cur, a["id"], decision, actor=owner_ctx)
        assert exc.value.code == "illegal_transition"
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "teleport", actor=owner_ctx)
    assert exc.value.code == "invalid_decision"
    with pytest.raises(ClefsError) as exc:
        actions.submit(cur, a["id"])
    assert exc.value.code == "illegal_transition"  # not a dispute
    r = _new(cur, hotel_uae.slug)
    actions.decide(cur, r["id"], "ignore", actor=owner_ctx, note="not now")
    with pytest.raises(ClefsError):
        actions.decide(cur, r["id"], "approve", actor=owner_ctx)
    with pytest.raises(ClefsError):
        actions.cancel(cur, r["id"])


def test_dispute_path(cur, hotel_uae, rec, owner_ctx):
    a = _new(cur, hotel_uae.slug, expected_minor=84700)
    a = actions.decide(cur, a["id"], "dispute", actor=owner_ctx, note="GDT 2.3.2")
    assert a["status"] == "approved" and a["subtype"] == "dispute"
    a = actions.submit(cur, a["id"], data={"packet": "p1"})
    assert a["status"] == "submitted"
    a = actions.await_counterparty(cur, a["id"])
    assert a["status"] == "awaiting_counterparty"
    a = actions.record_verification(cur, a["id"], verified=False, evidence=[Evidence(kind="quote", label="OTA said no")])
    assert a["status"] == "rejected_by_counterparty" and not a["terminal"]
    a = actions.submit(cur, a["id"])  # a second attempt
    assert a["status"] == "submitted"
    a = actions.record_verification(cur, a["id"], verified=True, outcome_minor=84700)
    assert a["status"] == "verified" and a["outcome_value"] == "847.00"
    assert _events(cur, a["id"]) == [
        "created", "dispute", "submit", "await_counterparty", "rejected_by_counterparty", "submit", "verified",
    ]
    b = _new(cur, hotel_uae.slug)
    actions.decide(cur, b["id"], "dispute", actor=owner_ctx)
    actions.submit(cur, b["id"])
    actions.record_verification(cur, b["id"], verified=False)
    b = actions.give_up(cur, b["id"], note="not worth chasing")
    assert b["status"] == "unresolved" and b["terminal"]


def test_snooze_and_wake(cur, hotel_uae, rec, owner_ctx, clock):
    now = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    s = signals.upsert_signal(cur, hotel_uae.slug, SignalIn(module="core", kind="core.x", dedupe_key="s1", title="S",
                                                            confidence=0.9, section="money_available", cadence="daily"))
    a = _new(cur, hotel_uae.slug, signal_id=s["id"])
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "snooze", actor=owner_ctx, snooze_until=now - timedelta(minutes=1))
    assert exc.value.code == "invalid_snooze"
    a = actions.decide(cur, a["id"], "snooze", actor=owner_ctx, snooze_until=now + timedelta(hours=2))
    assert a["status"] == "snoozed" and a["snoozed_from"] == "proposed"
    assert signals.get_signal(cur, hotel_uae.slug, s["id"])["status"] == "snoozed"
    a = actions.decide(cur, a["id"], "snooze", actor=owner_ctx, snooze_until=now + timedelta(hours=3))
    assert a["snoozed_from"] == "proposed"  # extending keeps the original state
    a = actions.decide(cur, a["id"], "approve", actor=owner_ctx)  # any decision wakes it first
    assert a["status"] == "approved"
    assert _events(cur, a["id"])[-2:] == ["unsnooze", "approve"]

    b = _new(cur, hotel_uae.slug)
    actions.decide(cur, b["id"], "approve", actor=owner_ctx)
    actions.decide(cur, b["id"], "snooze", actor=owner_ctx, snooze_until=now + timedelta(hours=1))
    clock.advance(minutes=30)
    assert actions.wake_due(cur) == 0
    clock.advance(minutes=31)
    assert actions.wake_due(cur) == 1
    assert actions.get_action(cur, hotel_uae.slug, b["id"])["status"] == "approved"


def test_adjust_records_the_diff_and_enforces_bounds(cur, hotel_uae, rec, owner_ctx):
    a = _new(cur, hotel_uae.slug, params={"rate": 400, "plan": "BAR"})
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "adjust", actor=owner_ctx)
    assert exc.value.code == "params_patch_required"
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "adjust", actor=owner_ctx, params_patch={"rate": 520})
    assert exc.value.status == 422 and exc.value.code == "invalid_params"
    a = actions.decide(cur, a["id"], "adjust", actor=owner_ctx, params_patch={"rate": 420})
    assert a["status"] == "approved" and a["edited"] and a["params"] == {"rate": 420, "plan": "BAR"}
    ev = actions.list_events(cur, a["id"])[-1]
    assert ev["event"] == "adjust" and ev["data"]["params_diff"] == {"rate": [400, 420]}
    with pytest.raises(ClefsError) as exc:
        _new(cur, hotel_uae.slug, params={"rate": 900})
    assert exc.value.code == "invalid_params"


def test_signal_follows_the_decision(cur, hotel_uae, rec, owner_ctx):
    def sig(key):
        return signals.upsert_signal(cur, hotel_uae.slug, SignalIn(module="core", kind="core.x", dedupe_key=key, title="S",
                                                                   confidence=0.5, section="money_lost", cadence="daily"))

    s1, s2 = sig("a"), sig("b")
    a1 = _new(cur, hotel_uae.slug, signal_id=s1["id"])
    a2 = _new(cur, hotel_uae.slug, signal_id=s2["id"])
    assert signals.list_signals(cur, hotel_uae.slug)[0]["action_id"] in (a1["id"], a2["id"])
    actions.decide(cur, a1["id"], "approve", actor=owner_ctx)
    actions.decide(cur, a2["id"], "ignore", actor=owner_ctx)
    assert signals.get_signal(cur, hotel_uae.slug, s1["id"])["status"] == "decided"
    assert signals.get_signal(cur, hotel_uae.slug, s2["id"])["status"] == "ignored"
    actions.mark_done(cur, a1["id"], actor=owner_ctx)
    actions.record_verification(cur, a1["id"], verified=True)
    assert signals.get_signal(cur, hotel_uae.slug, s1["id"])["status"] == "resolved"
    with pytest.raises(ClefsError) as exc:
        _new(cur, hotel_uae.slug, signal_id=999999)
    assert exc.value.code == "signal_not_found"


def test_decide_permissions(cur, hotel_uae, rec, owner_ctx, ctx_for):
    staff_ctx, _ = ctx_for("staff")
    gm_ctx, _ = ctx_for("gm")
    a = _new(cur, hotel_uae.slug)
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "approve", actor=staff_ctx)
    assert exc.value.status == 403
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "approve", actor=Principal(kind="user", user_id=1))  # no hotel context
    assert exc.value.status == 403
    o = _new(cur, hotel_uae.slug, "core.test_owner_only")
    with pytest.raises(ClefsError):
        actions.decide(cur, o["id"], "approve", actor=gm_ctx)
    assert actions.decide(cur, o["id"], "approve", actor=owner_ctx)["status"] == "approved"
    assert actions.decide(cur, a["id"], "approve", actor=SYSTEM)["status"] == "approved"


def test_the_assignee_can_finish_their_own_task(cur, hotel_uae, rec, owner_ctx, ctx_for):
    staff_ctx, staff = ctx_for("staff")
    other_ctx, _ = ctx_for("front_desk")
    a = _new(cur, hotel_uae.slug)
    actions.decide(cur, a["id"], "assign", actor=owner_ctx, assignee_user_id=staff["id"])
    with pytest.raises(ClefsError):
        actions.mark_done(cur, a["id"], actor=other_ctx)
    assert actions.mark_done(cur, a["id"], actor=staff_ctx)["status"] == "done"
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, _new(cur, hotel_uae.slug)["id"], "assign", actor=owner_ctx, assignee_user_id=999999)
    assert exc.value.code == "invalid_assignee"


def test_a_button_nonce_is_single_use(cur, hotel_uae, rec, owner_ctx):
    a = _new(cur, hotel_uae.slug)
    actions.decide(cur, a["id"], "remind", actor=owner_ctx, via="whatsapp", nonce="n-1")
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "approve", actor=owner_ctx, via="whatsapp", nonce="n-1")
    assert exc.value.code == "nonce_replayed"
    assert actions.decide(cur, a["id"], "approve", actor=owner_ctx, via="whatsapp", nonce="n-2")["status"] == "approved"
    assert actions.get_action(cur, hotel_uae.slug, a["id"])["reminders_sent"] == 1


def test_approval_runs_the_executor_through_the_queue(cur, hotel_uae, rec, owner_ctx, clock):
    clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    a = _new(cur, hotel_uae.slug, "core.test_exec", expected_minor=5000)
    assert a["status"] == "proposed" and rec.calls == []
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    cur.execute("SELECT status FROM job_queue WHERE name = 'core.actions.execute' AND payload->>'action_id' = %s", (str(a["id"]),))
    assert cur.fetchone()["status"] == "queued"
    report = jobs.run_pending(only=["core.actions.execute"])
    assert report.tasks_run == 1 and rec.calls == [a["id"]]
    a = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert a["status"] == "done" and a["executed_at"] is not None
    assert a["dial_level_at_execution"] == "approve" and a["policy_version"] == 0
    assert (a["template_id"], a["template_category"], a["consent_basis"]) == ("tpl@3", "UTILITY", "contract:reservation:9")
    assert a["reversal_handle"] == {"kind": "none", "reason": "message_sent"}
    assert a["next_verify_at"] == datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)
    jobs.run_pending(only=["core.actions.execute"])  # idempotent: nothing left, and a re-run is skipped
    assert actions.execute_action(cur, a["id"]) == {"skipped": "done"}
    assert rec.calls == [a["id"]]


def test_executor_failure_is_recorded_and_retried(cur, hotel_uae, rec, owner_ctx, clock):
    clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    rec.fail = True
    a = _new(cur, hotel_uae.slug, "core.test_exec")
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    jobs.run_pending(only=["core.actions.execute"])
    a = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert a["status"] == "approved" and a["execution_attempts"] == 1 and "provider down" in a["execution_error"]
    assert _events(cur, a["id"])[-1] == "execution_failed"
    cur.execute(
        "SELECT run_at FROM job_queue WHERE name = 'core.actions.execute' AND status = 'queued' AND payload->>'action_id' = %s",
        (str(a["id"]),),
    )
    assert cur.fetchone()["run_at"] == datetime(2026, 10, 1, 6, 1, tzinfo=timezone.utc)
    rec.fail = False
    clock.advance(minutes=2)
    jobs.run_pending(only=["core.actions.execute"])
    assert actions.get_action(cur, hotel_uae.slug, a["id"])["status"] == "done"


def test_an_action_snoozed_before_its_executor_ran_runs_after_it_wakes(cur, hotel_uae, rec, owner_ctx, clock):
    # The executor task found the action snoozed and skipped (its job is done);
    # waking it must queue the run again, or it sits in approved forever.
    clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    a = _new(cur, hotel_uae.slug, "core.test_exec")
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    actions.decide(cur, a["id"], "snooze", actor=owner_ctx, snooze_until=clock.now() + timedelta(hours=1))
    jobs.run_pending(only=["core.actions.execute"])
    assert rec.calls == [] and actions.get_action(cur, hotel_uae.slug, a["id"])["status"] == "snoozed"
    clock.advance(hours=2)
    assert actions.wake_due(cur) == 1
    jobs.run_pending(only=["core.actions.execute"])
    got = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert rec.calls == [a["id"]] and got["status"] == "done" and got["executed_at"] is not None

    # Woken by a decision instead of the timer: same outcome.
    b = _new(cur, hotel_uae.slug, "core.test_exec")
    actions.decide(cur, b["id"], "approve", actor=owner_ctx)
    actions.decide(cur, b["id"], "snooze", actor=owner_ctx, snooze_until=clock.now() + timedelta(hours=1))
    jobs.run_pending(only=["core.actions.execute"])
    assert rec.calls == [a["id"]] and actions.get_action(cur, hotel_uae.slug, b["id"])["status"] == "snoozed"
    c = actions.decide(cur, b["id"], "assign", actor=owner_ctx, assignee_role="gm")
    assert c["status"] == "assigned"
    jobs.run_pending(only=["core.actions.execute"])
    assert rec.calls == [a["id"], b["id"]]
    assert actions.get_action(cur, hotel_uae.slug, b["id"])["status"] == "done"


def test_a_declined_or_exhausted_executor_leaves_the_action_failed(cur, hotel_uae, rec, owner_ctx, clock):
    """Canon K1: ok=false is final; an exception is retried with backoff and
    the last attempt's failure is final too. failed is terminal and the
    signal reopens so the brief shows the problem again."""
    clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    s = signals.upsert_signal(cur, hotel_uae.slug, SignalIn(module="core", kind="core.x", dedupe_key="f1", title="S",
                                                            confidence=0.9, section="money_available", cadence="daily"))
    rec.decline = True
    a = _new(cur, hotel_uae.slug, "core.test_exec", signal_id=s["id"])
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    assert signals.get_signal(cur, hotel_uae.slug, s["id"])["status"] == "decided"
    jobs.run_pending(only=["core.actions.execute"])
    got = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert (got["status"], got["terminal"], got["execution_error"]) == ("failed", True, "guest opted out")
    assert _events(cur, a["id"])[-1] == "execution_failed"
    assert signals.get_signal(cur, hotel_uae.slug, s["id"])["status"] == "open"
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    assert exc.value.code == "illegal_transition"

    rec.decline, rec.fail = False, True
    b = _new(cur, hotel_uae.slug, "core.test_exec")
    actions.decide(cur, b["id"], "approve", actor=owner_ctx)
    for attempt in range(1, actions.EXECUTION_MAX_ATTEMPTS + 1):
        jobs.run_pending(only=["core.actions.execute"])
        got = actions.get_action(cur, hotel_uae.slug, b["id"])
        assert got["execution_attempts"] == attempt
        assert got["status"] == ("failed" if attempt == actions.EXECUTION_MAX_ATTEMPTS else "approved")
        clock.advance(hours=1)
    cur.execute("SELECT COUNT(*) AS n FROM job_queue WHERE name = 'core.actions.execute' AND status = 'queued'")
    assert cur.fetchone()["n"] == 0
    assert len([c for c in rec.calls if c == b["id"]]) == actions.EXECUTION_MAX_ATTEMPTS


def test_actions_expire_at_expires_at(cur, hotel_uae, rec, owner_ctx, clock):
    """Canon K1: still proposed or approved at expires_at -> expired (the
    core.actions.expire job); a decision after the lapse is refused even
    before the job runs, and an approved action never executes."""
    now = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    s = signals.upsert_signal(cur, hotel_uae.slug, SignalIn(module="core", kind="core.x", dedupe_key="q1", title="Quote",
                                                            confidence=0.9, section="money_available", cadence="daily"))
    quote = _new(cur, hotel_uae.slug, signal_id=s["id"], expires_at=now + timedelta(hours=2))
    assert quote["expires_at"] == now + timedelta(hours=2)
    approved = _new(cur, hotel_uae.slug, "core.test_exec", expires_at=now + timedelta(hours=2))
    actions.decide(cur, approved["id"], "approve", actor=owner_ctx)
    actions.decide(cur, approved["id"], "snooze", actor=owner_ctx, snooze_until=now + timedelta(hours=1))
    working = _new(cur, hotel_uae.slug, expires_at=now + timedelta(hours=2))
    actions.decide(cur, working["id"], "assign", actor=owner_ctx, assignee_role="gm")  # assigned: not expirable
    lasting = _new(cur, hotel_uae.slug)
    with pytest.raises(ValueError):
        _new(cur, hotel_uae.slug, expires_at=datetime(2026, 10, 2))

    clock.advance(hours=1, minutes=5)
    assert actions.wake_due(cur) == 1  # back to approved, its run re-queued
    clock.advance(hours=1)
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, quote["id"], "approve", actor=owner_ctx)
    assert exc.value.status == 409 and exc.value.code == "action_expired"
    jobs.tick(only=["core.actions.expire"], queue=False)
    assert actions.get_action(cur, hotel_uae.slug, quote["id"])["status"] == "expired"
    got = actions.get_action(cur, hotel_uae.slug, approved["id"])
    assert got["status"] == "expired" and got["terminal"]
    assert _events(cur, approved["id"])[-1] == "expire"
    assert signals.get_signal(cur, hotel_uae.slug, s["id"])["status"] == "expired"
    assert actions.get_action(cur, hotel_uae.slug, working["id"])["status"] == "assigned"
    assert actions.get_action(cur, hotel_uae.slug, lasting["id"])["status"] == "proposed"
    jobs.run_pending(only=["core.actions.execute"])
    assert approved["id"] not in rec.calls  # expired before its executor ran


def test_verification_in_another_currency_is_converted_to_the_action_currency(cur, hotel_uae, rec, owner_ctx):
    a = _new(cur, hotel_uae.slug, expected_minor=36725)  # AED 367.25
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    actions.mark_done(cur, a["id"], actor=owner_ctx)
    got = actions.record_verification(cur, a["id"], verified=True, outcome_value=Decimal("100"), currency="USD")
    # USD 100 at the 3.6725 peg; expected_minor keeps its AED label.
    assert (got["outcome_minor"], got["currency"], got["expected_value"]) == (36725, "AED", "367.25")
    assert got["verification"]["original_outcome"] == {"outcome_minor": 10000, "currency": "USD"}


def test_verifier_job_pending_then_verified_or_expired(cur, hotel_uae, rec, owner_ctx, clock):
    start = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    a = _new(cur, hotel_uae.slug, "core.test_exec")
    b = _new(cur, hotel_uae.slug, "core.test_exec")
    for x in (a, b):
        actions.decide(cur, x["id"], "approve", actor=owner_ctx)
    jobs.run_pending(only=["core.actions.execute"])
    clock.advance(hours=2)
    jobs.tick(only=["core.actions.verify"], queue=False)
    a2 = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert a2["status"] == "done" and a2["next_verify_at"] == start + timedelta(hours=2, days=1)
    assert "verify_pending" in _events(cur, a["id"])
    rec.verify = VerifyResult(status="verified", outcome_minor=12345, evidence=[Evidence(kind="row", label="Credit note")])
    clock.advance(days=1, hours=1)
    rec_b = actions.get_action(cur, hotel_uae.slug, b["id"])
    assert rec_b["status"] == "done"
    jobs.tick(only=["core.actions.verify"], queue=False)
    a3 = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert a3["status"] == "verified" and a3["outcome_value"] == "123.45" and a3["counts_toward_verified_profit"]

    c = _new(cur, hotel_uae.slug, "core.test_exec")
    actions.decide(cur, c["id"], "approve", actor=owner_ctx)
    rec.verify = None  # pending forever
    jobs.run_pending(only=["core.actions.execute"])
    for _ in range(5):
        clock.advance(days=1, hours=1)
        jobs.tick(only=["core.actions.verify"], queue=False)
    assert actions.get_action(cur, hotel_uae.slug, c["id"])["status"] == "not_verified"


def test_reverse_verification_nets_the_outcome_to_zero(cur, hotel_uae, rec, owner_ctx):
    a = _new(cur, hotel_uae.slug)
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    actions.mark_done(cur, a["id"], actor=owner_ctx)
    actions.record_verification(cur, a["id"], verified=True, outcome_minor=50000)
    with pytest.raises(ClefsError):
        actions.reverse_verification(cur, _new(cur, hotel_uae.slug)["id"], reason="x")
    r = actions.reverse_verification(cur, a["id"], reason="refunded", evidence=[Evidence(kind="row", label="Refund")])
    assert r["status"] == "verified" and r["outcome_minor"] == 0 and r["reversed_at"] is not None
    ev = actions.list_events(cur, a["id"])[-1]
    assert ev["event"] == "verification_reversed" and ev["data"]["previous_outcome_minor"] == 50000
    with pytest.raises(ClefsError) as exc:
        actions.reverse_verification(cur, a["id"], reason="again")
    assert exc.value.code == "already_reversed"


def test_dedupe_key_returns_the_existing_action(cur, hotel_uae, rec):
    a = _new(cur, hotel_uae.slug, dedupe_key="pg:L3:123")
    b = _new(cur, hotel_uae.slug, dedupe_key="pg:L3:123", title="Other title")
    assert b["id"] == a["id"] and not b["created"]
    with pytest.raises(ClefsError) as exc:
        _new(cur, hotel_uae.slug, "core.nope")
    assert exc.value.code == "unknown_action_type"


def test_cancel_supersedes(cur, hotel_uae, rec, owner_ctx):
    s = signals.upsert_signal(cur, hotel_uae.slug, SignalIn(module="core", kind="core.x", dedupe_key="c", title="S",
                                                            confidence=0.5, section="money_lost", cadence="daily"))
    a = _new(cur, hotel_uae.slug, signal_id=s["id"])
    assert actions.cancel(cur, a["id"], reason="superseded")["status"] == "cancelled"
    assert signals.get_signal(cur, hotel_uae.slug, s["id"])["status"] == "superseded"


# ─── Routes ───


def test_action_routes(cur, client, client_as, hotel_uae, rec):
    owner = client_as("owner")
    base = f"/hotels/{hotel_uae.slug}/actions"
    r = owner.post(base, json={"type": "core.task", "title": "Call the OTA", "params": {"ota": "BOOKING"}, "expected_value": "150.5"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["status"] == "proposed" and a["expected_value"] == "150.50" and a["currency"] == "AED"
    assert owner.post(base, json={"type": "core.nope", "title": "x"}).json()["code"] == "unknown_action_type"
    listed = owner.get(base).json()
    assert [x["id"] for x in listed] == [a["id"]]
    assert owner.get(f"{base}?status=verified").json() == []
    one = owner.get(f"{base}/{a['id']}").json()
    assert [e["event"] for e in one["events"]] == ["created"]
    d = owner.post(f"{base}/{a['id']}/decide", json={"decision": "approve", "note": "go"})
    assert d.status_code == 200 and d.json()["status"] == "approved"
    again = owner.post(f"{base}/{a['id']}/decide", json={"decision": "ignore"})
    assert again.status_code == 409 and again.json()["code"] == "illegal_transition"
    done = owner.post(f"{base}/{a['id']}/done", json={"note": "called"})
    assert done.status_code == 200 and done.json()["status"] == "done"
    assert owner.get(f"{base}/999999").status_code == 404

    staff = client_as("staff")
    assert staff.get(base).status_code == 403
    assert staff.post(base, json={"type": "core.task", "title": "x"}).status_code == 403
    b = owner.post(base, json={"type": "core.task", "title": "Second"}).json()
    assert staff.post(f"{base}/{b['id']}/decide", json={"decision": "approve"}).status_code == 403
    assert client.post(f"{base}/{b['id']}/decide", json={"decision": "approve"}).status_code == 401
    snooze = owner.post(f"{base}/{b['id']}/decide", json={"decision": "snooze", "snooze_until": "2099-01-01T00:00:00Z"})
    assert snooze.json()["status"] == "snoozed"


def test_actions_of_another_hotel_are_not_found(cur, client_as, hotel_uae, rec, make_user, login, app):
    from fastapi.testclient import TestClient

    from clefs.core import hotels

    hotels.create_hotel(cur, slug="other-dubai", name="Other")
    a = _new(cur, "other-dubai")
    owner = client_as("owner")
    assert owner.post(f"/hotels/{hotel_uae.slug}/actions/{a['id']}/decide", json={"decision": "approve"}).status_code == 404
    assert owner.get(f"/hotels/{hotel_uae.slug}/actions/{a['id']}").status_code == 404
    other_owner = TestClient(app)
    login(other_owner, make_user("owner", "other-dubai"))
    assert other_owner.post(f"/hotels/other-dubai/actions/{a['id']}/decide", json={"decision": "approve"}).status_code == 200


def test_magic_session_can_decide(cur, app, make_user, hotel_uae, rec):
    from fastapi.testclient import TestClient

    from clefs.auth import magic

    u = make_user("owner", hotel_uae.slug)
    a = _new(cur, hotel_uae.slug)
    c = TestClient(app)
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="brief")
    assert c.post("/auth/magic/exchange", json={"token": token}).status_code == 200
    assert c.get(f"/hotels/{hotel_uae.slug}/signals").status_code == 200
    r = c.post(f"/hotels/{hotel_uae.slug}/actions/{a['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert c.get(f"/hotels/{hotel_uae.slug}/autonomy").status_code == 403
