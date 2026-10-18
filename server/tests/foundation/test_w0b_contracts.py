"""
Wave 0b fixes for the contracts lane.

  - Migration 0013 brings Canon K1 (failed / expired, actions.expires_at) and
    the service_requests_v1 floor fix to databases that applied the first
    0011 and 0012, is idempotent where 0011 already carried K1, and leaves a
    view the Service module replaced alone.
  - K1 edges: an executor never runs after expires_at, a snooze does not
    extend a quote's validity, and start / done refuse a lapsed action.
  - A failed or expired action is not a signal's live action.
  - Staff SMS from public guest requests is capped per assignee.
  - The 01 spec H fixtures (outbox, fake_ai, run_jobs) come from the root
    conftest, so every module lane has them.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest

import clefs
from clefs import jobs
from clefs.auth.deps import Principal
from clefs.core import actions, signals
from clefs.core.actions import ActionType, ExecResult
from clefs.core.errors import ClefsError
from clefs.core.signals import SignalIn

SYSTEM = Principal(kind="service", label="test")
MIGRATIONS = Path(clefs.__file__).resolve().parent / "migrations"
ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"

# The actions.status CHECK exactly as the first 0011 created it (no K1).
PRE_K1_STATUSES = (
    "proposed", "approved", "assigned", "in_progress", "done", "verified", "not_verified", "snoozed", "rejected",
    "cancelled", "observed", "submitted", "awaiting_counterparty", "rejected_by_counterparty", "unresolved",
)
LONG_ROOM = "9" * 23  # valid by rooms.ROOM_RE; a BIGINT cast of its floor overflows


# ─── Helpers ───


def _sql(name: str) -> str:
    return (MIGRATIONS / name).read_text()


def _status_checks(cur) -> list[str]:
    cur.execute(
        """
        SELECT pg_get_constraintdef(con.oid) AS def
        FROM pg_constraint con
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attname = 'status'
        WHERE con.conrelid = 'actions'::regclass AND con.contype = 'c' AND con.conkey = ARRAY[att.attnum]
        """
    )
    return [r["def"] for r in cur.fetchall()]


def _view_def(cur) -> str:
    cur.execute("SELECT pg_get_viewdef('service_requests_v1'::regclass) AS d")
    return cur.fetchone()["d"]


def _file_long_room_request(cur, slug: str) -> None:
    cur.execute(
        "INSERT INTO staff_requests (hotel_slug, room, type, category, priority, status, timestamp) "
        "VALUES (%s, %s, 'Water', 'Housekeeping', 'medium', 'new', 1)",
        (slug, LONG_ROOM),
    )


def _long_room_floor(cur, slug: str) -> str | None:
    cur.execute("SELECT floor FROM service_requests_v1 WHERE hotel_slug = %s AND room = %s", (slug, LONG_ROOM))
    return cur.fetchone()["floor"]


def _to_pre_k1_shape(cur) -> None:
    """Make this (rolled-back) transaction look like a database that applied
    the first 0011 and 0012: the narrow status CHECK, no expires_at, and the
    BIGINT floor view (0012 as it is on disk, which is its applied content)."""
    cur.execute("ALTER TABLE actions DROP CONSTRAINT actions_status_check")
    cur.execute(
        "ALTER TABLE actions ADD CONSTRAINT actions_status_check CHECK (status IN ("
        + ", ".join(f"'{s}'" for s in PRE_K1_STATUSES)
        + "))"
    )
    cur.execute("DROP INDEX IF EXISTS actions_expiry_idx")
    cur.execute("ALTER TABLE actions DROP COLUMN expires_at")
    cur.execute(_sql("0012_core_contract_views.sql"))


# ─── Migration 0013 ───


def test_the_migrated_database_has_the_k1_states_and_the_numeric_floor(cur, hotel_uae):
    checks = _status_checks(cur)
    assert len(checks) == 1 and "'failed'" in checks[0] and "'expired'" in checks[0]
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'actions' AND column_name = 'expires_at'")
    assert cur.fetchone() is not None
    cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'actions_expiry_idx'")
    assert "snoozed" in cur.fetchone()["indexdef"]
    assert "::numeric" in _view_def(cur) and "::bigint" not in _view_def(cur)
    _file_long_room_request(cur, hotel_uae.slug)
    assert _long_room_floor(cur, hotel_uae.slug) == "9" * 21


def test_0013_upgrades_a_database_that_applied_the_first_0011_and_0012(cur, hotel_uae):
    _to_pre_k1_shape(cur)
    _file_long_room_request(cur, hotel_uae.slug)
    assert "'failed'" not in _status_checks(cur)[0]
    cur.execute("SAVEPOINT w0b_overflow")
    with pytest.raises(psycopg2.errors.NumericValueOutOfRange):
        _long_room_floor(cur, hotel_uae.slug)
    cur.execute("ROLLBACK TO SAVEPOINT w0b_overflow")

    for _ in range(2):  # idempotent: a second run changes nothing
        cur.execute(_sql("0013_core_action_states.sql"))
        checks = _status_checks(cur)
        assert len(checks) == 1 and "'failed'" in checks[0] and "'expired'" in checks[0]
        assert _long_room_floor(cur, hotel_uae.slug) == "9" * 21

    # The K1 path works end to end on the upgraded schema.
    a = actions.create_action(
        cur, hotel_uae.slug, type="core.task", title="Quote", actor=SYSTEM,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert actions.expire_due_actions(cur) >= 1
    assert actions.get_action(cur, hotel_uae.slug, a["id"])["status"] == "expired"


def test_0013_finds_a_status_check_whatever_it_was_named(cur):
    cur.execute("ALTER TABLE actions DROP CONSTRAINT actions_status_check")
    cur.execute(
        "ALTER TABLE actions ADD CONSTRAINT legacy_status_rule CHECK (status IN ("
        + ", ".join(f"'{s}'" for s in PRE_K1_STATUSES)
        + "))"
    )
    cur.execute(_sql("0013_core_action_states.sql"))
    checks = _status_checks(cur)
    assert len(checks) == 1 and "'expired'" in checks[0]
    cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'legacy_status_rule'")
    assert cur.fetchone() is None


def test_0013_leaves_a_view_the_service_module_replaced(cur):
    # Service may CREATE OR REPLACE the view from its own range (0100-0199).
    # If that ran before 0013 reached a database, 0013 must not undo it.
    replaced = _sql("0012_core_contract_views.sql").replace("'legacy'::TEXT AS source", "'service'::TEXT AS source")
    assert replaced != _sql("0012_core_contract_views.sql")
    cur.execute(replaced)
    cur.execute(_sql("0013_core_action_states.sql"))
    assert "'service'::text AS source" in _view_def(cur)


# ─── K1 edges ───


class _Exec:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.decline = False

    def __call__(self, ctx: actions.ActionCtx) -> ExecResult:
        self.calls.append(ctx.action["id"])
        if self.decline:
            return ExecResult(ok=False, error="guest opted out")
        return ExecResult(data={"sent": True})


@pytest.fixture
def exec_type():
    ex = _Exec()
    types = [
        ActionType(key="core.w0b_exec", module="core", label="W0b exec", executor=ex),
        ActionType(key="core.w0b_task", module="core", label="W0b task"),
    ]
    for t in types:
        actions.register_action_type(t)
    yield ex
    for t in types:
        actions.unregister_action_type(t.key)


def _signal(cur, slug: str, key: str) -> dict:
    return signals.upsert_signal(
        cur, slug, SignalIn(module="core", kind="core.x", dedupe_key=key, title="S", confidence=0.9, section="money_available", cadence="daily")
    )


def test_an_executor_never_runs_after_expires_at(cur, hotel_uae, owner_ctx, exec_type, clock):
    """The execute task can come due after the lapse (a retry backoff, a late
    worker) before core.actions.expire has run: it expires the action."""
    now = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    s = _signal(cur, hotel_uae.slug, "w0b-exec")
    a = actions.create_action(cur, hotel_uae.slug, type="core.w0b_exec", title="Send quote", signal_id=s["id"],
                              actor=SYSTEM, expires_at=now + timedelta(minutes=1))
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    clock.advance(minutes=2)
    jobs.run_pending(only=["core.actions.execute"])
    got = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert exec_type.calls == [] and got["status"] == "expired" and got["executed_at"] is None
    assert got["events"][-1]["event"] == "expire"
    assert signals.get_signal(cur, hotel_uae.slug, s["id"])["status"] == "expired"


def test_a_snooze_does_not_extend_a_quote(cur, hotel_uae, owner_ctx, exec_type, clock):
    now = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    # Snoozed past its expiry: the expire job takes it while still snoozed.
    a = actions.create_action(cur, hotel_uae.slug, type="core.w0b_task", title="Quote A", actor=SYSTEM,
                              expires_at=now + timedelta(hours=1))
    actions.decide(cur, a["id"], "snooze", actor=owner_ctx, snooze_until=now + timedelta(hours=3))
    # Approved, then snoozed: the wake job meets it first and expires it
    # instead of queueing its executor.
    b = actions.create_action(cur, hotel_uae.slug, type="core.w0b_exec", title="Quote B", actor=SYSTEM,
                              expires_at=now + timedelta(minutes=30))
    actions.decide(cur, b["id"], "approve", actor=owner_ctx)
    actions.decide(cur, b["id"], "snooze", actor=owner_ctx, snooze_until=now + timedelta(hours=1))
    # Still snoozed and lapsed: neither a re-snooze nor a decision revives it.
    c = actions.create_action(cur, hotel_uae.slug, type="core.w0b_task", title="Quote C", actor=SYSTEM,
                              expires_at=now + timedelta(minutes=30))
    actions.decide(cur, c["id"], "snooze", actor=owner_ctx, snooze_until=now + timedelta(hours=5))

    clock.advance(hours=2)
    for decision, kw in (("snooze", {"snooze_until": now + timedelta(hours=9)}), ("approve", {})):
        cur.execute("SAVEPOINT w0b_decide")
        with pytest.raises(ClefsError) as exc:
            actions.decide(cur, c["id"], decision, actor=owner_ctx, **kw)
        assert exc.value.code == "action_expired"
        cur.execute("ROLLBACK TO SAVEPOINT w0b_decide")

    assert actions.wake_due(cur) == 0  # b was due to wake, and lapsed: expired, not woken
    got_b = actions.get_action(cur, hotel_uae.slug, b["id"])
    assert (got_b["status"], got_b["snoozed_until"], got_b["snoozed_from"]) == ("expired", None, None)
    jobs.run_pending(only=["core.actions.execute"])
    assert exec_type.calls == []

    assert actions.expire_due_actions(cur) == 2  # a and c, straight from snoozed
    got_a = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert got_a["status"] == "expired" and got_a["events"][-1]["from_status"] == "snoozed"
    clock.advance(hours=4)
    assert actions.wake_due(cur) == 0
    assert actions.get_action(cur, hotel_uae.slug, a["id"])["status"] == "expired"


def test_start_and_done_refuse_a_lapsed_action_but_cancel_works(cur, hotel_uae, owner_ctx, exec_type, clock):
    now = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    a = actions.create_action(cur, hotel_uae.slug, type="core.w0b_task", title="Plan", actor=SYSTEM,
                              expires_at=now + timedelta(hours=1))
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    clock.advance(hours=2)
    for call in (actions.start, actions.mark_done):
        with pytest.raises(ClefsError) as exc:
            call(cur, a["id"], actor=owner_ctx)
        assert exc.value.status == 409 and exc.value.code == "action_expired"
    assert actions.cancel(cur, a["id"], reason="superseded")["status"] == "cancelled"

    # An assigned action is past the undecided stage: expires_at no longer applies.
    b = actions.create_action(cur, hotel_uae.slug, type="core.w0b_task", title="Plan B", actor=SYSTEM,
                              expires_at=clock.now() + timedelta(hours=1))
    actions.decide(cur, b["id"], "assign", actor=owner_ctx, assignee_role="gm")
    clock.advance(hours=2)
    assert actions.mark_done(cur, b["id"], actor=owner_ctx)["status"] == "done"


def test_a_failed_or_expired_action_is_not_the_signals_live_action(cur, hotel_uae, owner_ctx, exec_type, clock):
    now = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    s1 = _signal(cur, hotel_uae.slug, "w0b-failed")
    exec_type.decline = True
    a = actions.create_action(cur, hotel_uae.slug, type="core.w0b_exec", title="Send", signal_id=s1["id"], actor=SYSTEM)
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    jobs.run_pending(only=["core.actions.execute"])
    assert actions.get_action(cur, hotel_uae.slug, a["id"])["status"] == "failed"

    s2 = _signal(cur, hotel_uae.slug, "w0b-expired")
    b = actions.create_action(cur, hotel_uae.slug, type="core.w0b_task", title="Quote", signal_id=s2["id"], actor=SYSTEM,
                              expires_at=now + timedelta(minutes=5))
    clock.advance(minutes=10)
    actions.expire_due_actions(cur)
    assert actions.get_action(cur, hotel_uae.slug, b["id"])["status"] == "expired"

    rows = {r["id"]: r for r in signals.list_signals(cur, hotel_uae.slug, status=signals.SIGNAL_STATUSES)}
    assert rows[s1["id"]]["status"] == "open" and rows[s1["id"]]["action_id"] is None
    assert rows[s2["id"]]["action_id"] is None


# ─── Public guest writes (security) ───


def test_staff_sms_from_public_requests_is_capped_per_assignee(client, cur, legacy_slug, monkeypatch):
    """Rotating the (attacker-chosen) room gets past the per-room limit, and
    every request auto-assigns to the same on-shift person: the per-assignee
    cap is what keeps that from becoming a flood of paid SMS carrying the
    attacker's text. The requests themselves are still filed."""
    from clefs.modules.service import legacy as service_legacy

    sent: list[str] = []
    monkeypatch.setattr(service_legacy.msg91, "sms_enabled", lambda: True)
    monkeypatch.setattr(service_legacy.msg91, "send_sms_alert", lambda **kw: sent.append(kw["room"]))
    monkeypatch.setattr(service_legacy, "SMS_PER_ASSIGNEE_LIMIT", 2)
    cur.execute(
        "INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, phone_number) "
        "VALUES (%s, 'W0b Attendant', 'Attendant', 'Housekeeping', TRUE, TRUE, '+919800000001')",
        (legacy_slug,),
    )
    rooms = ["801", "802", "803"]
    for room in rooms:
        r = client.post(f"/hotels/{legacy_slug}/pending", json={"data": {"room": room, "type": "Water"}})
        assert r.status_code == 200, r.text
    cur.execute(
        "SELECT room, assigned_to FROM staff_requests WHERE hotel_slug = %s AND room = ANY(%s) ORDER BY room",
        (legacy_slug, rooms),
    )
    assert [(r["room"], r["assigned_to"]) for r in cur.fetchall()] == [(room, "W0b Attendant") for room in rooms]
    assert sent == ["801", "802"]


# ─── 01 spec H fixtures ───


@pytest.mark.parametrize("name", ["outbox", "fake_ai", "run_jobs"])
def test_the_h_fixtures_come_from_the_root_conftest(request, name):
    # Defined in tests/conftest.py (visible under tests/modules/* too), not in
    # a foundation-only conftest that module lanes cannot reach.
    defs = request._fixturemanager.getfixturedefs(name, request.node)
    assert defs, name
    assert Path(inspect.getsourcefile(defs[-1].func)).resolve() == ROOT_CONFTEST


def test_the_h_fixtures_work(cur, hotel_uae, outbox, fake_ai, run_jobs):
    from clefs import ai, notify

    # outbox: captured() as documented, plus the transport's own knobs.
    assert outbox.captured() == [] and outbox.sent == []
    r = notify.send_email("guest@example.test", "Hi", "<p>Hi</p>", hotel_slug=hotel_uae.slug, purpose="core.test", cur=cur)
    assert r.ok
    run_jobs(["core.notify.deliver"])
    assert [m["channel"] for m in outbox.captured("email")] == ["email"]
    assert outbox.captured("sms") == []
    outbox.fail_next(500, "boom")  # delegated to the transport
    assert outbox._failures

    # fake_ai answers from its queue even with AI switched off.
    fake_ai.push("hello")
    assert ai.text("core.test", system="s", input="i") == "hello"
    assert len(fake_ai.calls) == 1

    # run_jobs drains due queued tasks and returns a TickReport.
    assert run_jobs().tasks_run == 0
