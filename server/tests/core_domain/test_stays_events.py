"""Stay-event outbox: emission contract, per-consumer cursors, failure
handling and dead letters (04 spec D1; names frozen in core/stay_events)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clefs.core import stays
from clefs.core.stay_events import CHECKED_OUT, DISPATCH_JOB
from clefs.core.stays import events as ev_mod

NOW = datetime(2025, 10, 20, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def slug(cur, hotel_uae, clock):
    clock.freeze(NOW)
    return hotel_uae.slug


@pytest.fixture
def consumer():
    """consumer(name, fail_on=set()) -> list of received events."""
    names: list[str] = []

    def _make(name, fail_on=frozenset(), fail_always=False):
        seen: list[dict] = []

        def handler(cur, ev):
            if fail_always or ev["event"] in fail_on:
                raise RuntimeError(f"boom on {ev['id']}")
            cur.execute("SELECT 1")  # handlers get a live cursor
            seen.append(ev)

        handler.__module__ = f"tests.consumer.{name}"
        stays.on_stay_event(name)(handler)
        names.append(name)
        return seen

    yield _make
    for n in names:
        stays.unregister_consumer(n)


def book(cur, slug, pms_id, **kw):
    data = {"pms_res_id": pms_id, "check_in": "2025-10-10", "check_out": "2025-10-12", "room_number": "101"} | kw
    return stays.upsert_reservation(cur, slug, data)


def test_emit_checks_names_fields_and_instants(cur, slug):
    rid = book(cur, slug, "E1")["id"]
    with pytest.raises(ValueError):
        stays.emit(cur, slug, rid, "renamed", {})
    with pytest.raises(ValueError):
        stays.emit(cur, slug, rid, "dates_changed", {"check_in": ("2025-10-10", "2025-10-11")})
    with pytest.raises(ValueError):
        stays.emit(cur, slug, rid, "no_show", {"status": ("CONFIRMED", "NO_SHOW")}, occurred_at=datetime(2025, 1, 1))
    with pytest.raises(ValueError):
        stays.on_stay_event("NoDot")


def test_dispatch_delivers_in_order_once_per_consumer(cur, slug, consumer):
    a, b = consumer("tests.alpha"), consumer("tests.beta")
    r1 = book(cur, slug, "E2")["id"]
    stays.upsert_reservation(cur, slug, {"pms_res_id": "E2", "status": "CHECKED_OUT"})
    report = stays.dispatch(cur)
    assert report["tests.alpha"]["delivered"] == 3 and report["tests.beta"]["delivered"] == 3
    assert [e["event"] for e in a] == ["created", "checked_in", CHECKED_OUT]
    assert [e["id"] for e in a] == [e["id"] for e in b] == sorted(e["id"] for e in a)
    assert a[0]["reservation_id"] == r1 and a[0]["occurred_at"].tzinfo is not None
    assert stays.dispatch(cur)["tests.alpha"]["delivered"] == 0  # the cursor moved
    book(cur, slug, "E3")
    assert stays.dispatch(cur, consumer="tests.beta") == {"tests.beta": {"delivered": 1, "failed": 0, "dead": 0}}
    assert len(b) == 4 and len(a) == 3


def test_a_failing_consumer_holds_its_place_then_dead_letters(cur, slug, consumer):
    good, bad = consumer("tests.good"), consumer("tests.bad", fail_on={"checked_in"})
    book(cur, slug, "E4", status="IN_HOUSE")
    book(cur, slug, "E5")
    first = stays.dispatch(cur)
    assert first["tests.good"]["delivered"] == 3
    assert first["tests.bad"] == {"delivered": 1, "failed": 1, "dead": 0}  # stops at the failure
    cur.execute("SELECT failures, last_error FROM stay_event_cursors WHERE consumer = 'tests.bad'")
    row = cur.fetchone()
    assert row["failures"] == 1 and "boom" in row["last_error"]
    for _ in range(ev_mod.MAX_FAILURES - 1):
        out = stays.dispatch(cur, consumer="tests.bad")
    assert out["tests.bad"]["dead"] == 1 and out["tests.bad"]["delivered"] == 1
    assert [e["event"] for e in bad] == ["created", "created"]
    cur.execute("SELECT event_id, attempts FROM stay_event_failures WHERE consumer = 'tests.bad'")
    dead = cur.fetchone()
    assert dead["attempts"] == ev_mod.MAX_FAILURES
    assert len(good) == 3


def test_replay_moves_a_dead_letter_back(cur, slug, consumer):
    calls = consumer("tests.flaky", fail_always=True)
    book(cur, slug, "E6")
    for _ in range(ev_mod.MAX_FAILURES):
        stays.dispatch(cur, consumer="tests.flaky")
    cur.execute("SELECT event_id FROM stay_event_failures WHERE consumer = 'tests.flaky'")
    event_id = cur.fetchone()["event_id"]
    assert stays.replay_failure(cur, "tests.flaky", event_id) is False
    stays.unregister_consumer("tests.flaky")
    ok = consumer("tests.flaky")
    assert stays.replay_failure(cur, "tests.flaky", event_id) is True
    assert len(ok) == 1 and calls == []


def test_dispatch_job_is_registered_every_minute(cur, slug, consumer, run_jobs, clock):
    from clefs import jobs

    job = next(j for j in jobs.registered_jobs() if j.name == DISPATCH_JOB)
    assert job.per_hotel is False and job.module == "core"
    got = consumer("tests.viajob")
    book(cur, slug, "E7")
    run_jobs(DISPATCH_JOB, scheduled=True)
    assert [e["event"] for e in got] == ["created"]
