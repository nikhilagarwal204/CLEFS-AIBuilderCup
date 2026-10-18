"""Signals into proposed actions (review completeness-01): every Profit
Guard money signal carries a live pg.* action, the set follows the group,
a person's decision is never undone, and a dispute proposal gets its
packet from the brief's Dispute verb and is verified once."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from clefs.core import actions as core_actions
from clefs.core import signals as core_signals
from clefs.modules.profit_guard import actions as pg_actions  # noqa: F401  (registers the types)
from clefs.modules.profit_guard import detectors, disputes
from tests.modules.profit_guard.fixtures import pg_data as pg
from tests.modules.profit_guard.test_detectors_store import findings, no_shows, signal

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
GROUP = "pg:L3:BOOKING:doc:INV-1"


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def acts(cur, slug, *, type_=None):
    cur.execute("SELECT * FROM actions WHERE hotel_slug = %s AND module = 'profit_guard' "
                "AND (%s::text IS NULL OR type = %s) ORDER BY id", (slug, type_, type_))
    return [dict(r) for r in cur.fetchall()]


def live(rows):
    return [a for a in rows if a["status"] not in core_actions.TERMINAL]


def test_a_money_signal_gets_one_proposed_action(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, 3)
    out = detectors.run(cur, s, trigger="manual")
    assert out["signals"]["proposed"] >= 1
    (a,) = acts(cur, s, type_="pg.dispute")
    sig = signal(cur, s, GROUP)
    assert (a["status"], a["signal_id"]) == ("proposed", sig["id"])
    assert sorted(a["params"]["finding_ids"]) == sorted(r["id"] for r in findings(cur, s, "L3"))
    assert a["expected_minor"] == sig["impact_minor"]
    # the brief reads the live action off the signal
    (row,) = [r for r in core_signals.list_signals(cur, s, limit=50) if r["id"] == sig["id"]]
    assert row["action_id"] == a["id"]
    detectors.run(cur, s, trigger="manual")
    assert [x["id"] for x in live(acts(cur, s))] == [a["id"]]   # a re-run never duplicates


def test_the_proposal_follows_the_group_and_retires_with_it(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, 3)
    detectors.run(cur, s, trigger="manual")
    (first,) = acts(cur, s, type_="pg.dispute")
    one = findings(cur, s, "L3")[0]
    cur.execute("UPDATE pfg_findings SET status = 'DISMISSED' WHERE id = %s", (one["id"],))
    detectors.refresh_signals_for(cur, s, [one["id"]])
    old, new = acts(cur, s, type_="pg.dispute")
    assert old["id"] == first["id"] and old["status"] == "cancelled"
    assert new["status"] == "proposed" and one["id"] not in new["params"]["finding_ids"]
    cur.execute("UPDATE pfg_findings SET status = 'DISMISSED' WHERE hotel_slug = %s", (s,))
    detectors.refresh_signals_for(cur, s, [r["id"] for r in findings(cur, s, "L3")])
    assert live(acts(cur, s)) == []
    assert signal(cur, s, GROUP)["status"] == "resolved"


def test_a_decided_action_is_never_cancelled_or_duplicated(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, 3)
    detectors.run(cur, s, trigger="manual")
    one = findings(cur, s, "L3")[0]
    cur.execute("UPDATE pfg_findings SET status = 'DISMISSED' WHERE id = %s", (one["id"],))
    detectors.refresh_signals_for(cur, s, [one["id"]])
    (a,) = live(acts(cur, s, type_="pg.dispute"))
    core_actions.decide(cur, a["id"], "approve", actor=core_actions.SYSTEM, via="system", hotel_slug=s)
    # the finding comes back into the group: only it needs a new proposal
    cur.execute("UPDATE pfg_findings SET status = 'DETECTED' WHERE id = %s", (one["id"],))
    detectors.refresh_signals_for(cur, s, [one["id"]])
    rows = live(acts(cur, s, type_="pg.dispute"))
    assert next(x for x in rows if x["id"] == a["id"])["status"] == "approved"
    (extra,) = [x for x in rows if x["id"] != a["id"]]
    assert extra["params"]["finding_ids"] == [one["id"]]


def test_a_rejected_proposal_is_not_proposed_again(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, 3)
    detectors.run(cur, s, trigger="manual")
    (a,) = acts(cur, s, type_="pg.dispute")
    core_actions.decide(cur, a["id"], "ignore", actor=core_actions.SYSTEM, via="system", hotel_slug=s)
    detectors.run(cur, s, trigger="manual")
    assert [x["id"] for x in acts(cur, s)] == [a["id"]]


def test_vcc_cards_get_charge_actions_with_their_cards(cur, hotel_uae):
    s = hotel_uae.slug
    c1 = pg.vcc(cur, s, "V1", deadline=date(2026, 10, 20))
    c2 = pg.vcc(cur, s, "V2", deadline=date(2026, 10, 21), balance=30000)
    urgent = pg.vcc(cur, s, "V3", deadline=date(2026, 10, 3), balance=20000)
    detectors.run(cur, s, trigger="deadline")
    rows = live(acts(cur, s, type_="pg.vcc_charge"))
    by_cards = {tuple(sorted(a["params"]["card_ids"])): a for a in rows}
    assert set(by_cards) == {(c1, c2), (urgent,)}          # the group and the one-day item
    assert by_cards[(urgent,)]["params"]["card_id"] == urgent
    group = by_cards[(c1, c2)]
    assert group["expected_minor"] == 80000 and group["due_at"] is not None
    # both group cards charged on the next report -> verified with the sum
    core_actions.decide(cur, group["id"], "approve", actor=core_actions.SYSTEM, via="system", hotel_slug=s)
    ctx = core_actions._ctx_for(cur, core_actions._load(cur, group["id"], lock=False),
                                core_actions.get_action_type("pg.vcc_charge"))
    assert pg_actions._vcc_verify(ctx).status == "pending"


def test_brief_dispute_builds_the_packet_on_the_proposal(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, 3)
    detectors.run(cur, s, trigger="manual")
    (a,) = acts(cur, s, type_="pg.dispute")
    core_actions.decide(cur, a["id"], "dispute", actor=core_actions.SYSTEM, via="system", hotel_slug=s)
    got = disputes.packet_for_action(cur, s, action_id=a["id"])
    assert got is not None and got["file_id"]
    packet = disputes.get_packet(cur, s, got["packet_id"])
    assert packet["action_id"] == a["id"] and sorted(packet["finding_ids"]) == sorted(a["params"]["finding_ids"])
    # no second pg.dispute action, and a re-run proposes nothing new
    detectors.run(cur, s, trigger="manual")
    assert [x["id"] for x in live(acts(cur, s, type_="pg.dispute"))] == [a["id"]]
    assert disputes.packet_for_action(cur, s, action_id=a["id"])["packet_id"] == packet["id"]


def test_a_manual_packet_retires_the_group_proposal(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, 3)
    detectors.run(cur, s, trigger="manual")
    (a,) = acts(cur, s, type_="pg.dispute")
    packet = disputes.create_packet(cur, s, [r["id"] for r in findings(cur, s, "L3")])
    rows = acts(cur, s, type_="pg.dispute")
    assert next(x for x in rows if x["id"] == a["id"])["status"] == "cancelled"
    assert [x["id"] for x in live(rows)] == [packet["action_id"]]
    detectors.run(cur, s, trigger="manual")
    assert [x["id"] for x in live(acts(cur, s, type_="pg.dispute"))] == [packet["action_id"]]
