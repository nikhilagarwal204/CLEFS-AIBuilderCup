"""Delivery status, Meta 131049, cost, verifiers and plan states (04 spec
3.3, 3.5, 3.7, 3.10)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from clefs import notify
from clefs.core import actions
from clefs.modules.guests.journeys import dispatch, executor, status

from tests.modules.guests.test_journeys_support import (
    EMAIL, PHONE, add_contact, approve_templates, at, enable_whatsapp, event, grant, make_offer, make_res, plans,
    sends,
)


def wa_status(wamid, st, *, code=None, recipient="971501234567"):
    from clefs.core import clock

    s = {"id": wamid, "status": st, "timestamp": str(int(clock.now().timestamp())), "recipient_id": recipient}
    if code:
        s["errors"] = [{"code": int(code), "title": "held"}]
    return notify.process_whatsapp({"object": "whatsapp_business_account", "entry": [{"id": "W", "changes": [
        {"field": "messages", "value": {"messaging_product": "whatsapp", "metadata": {"phone_number_id": "1234567890"},
                                        "statuses": [s]}}]}]})


def _j3_on_whatsapp(cur, s, clock, *, email_consent=False):
    enable_whatsapp(cur, s)
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s)
    h = add_contact(cur, s, rid, PHONE)
    grant(cur, s, h, "marketing_whatsapp", "whatsapp_updates")
    if email_consent:
        he = add_contact(cur, s, rid, EMAIL, kind="email")
        grant(cur, s, he, "marketing_email")
    event(cur, s, rid, "created")
    make_offer(cur, s, "j3")
    approve_templates(cur, s, step_list=("j3",))
    actions.set_autonomy(cur, s, "guests.journey_j3_offer", level="auto_within_limits",
                         limits={"max_sends_per_day": 10}, actor=actions.SYSTEM)
    clock.freeze(at(2026, 10, 17, 11))
    dispatch.dispatch_hotel(cur, s)
    return rid, plans(cur, s, rid)[("j3", 1)]


def test_131049_retries_once_under_the_same_action_then_skips(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    rid, j3 = _j3_on_whatsapp(cur, s, clock)
    assert j3["status"] == "sent" and j3["attempt"] == 1
    wa_status(j3["wamid"], "failed", code=131049)
    p = plans(cur, s, rid)[("j3", 1)]
    assert (p["status"], p["error_code"], p["attempt"]) == ("deferred", "131049", 2)
    assert p["due_at"] == at(2026, 10, 18, 11)  # now + 24 h, inside 09:00-18:00
    cur.execute("SELECT status FROM outbound_messages WHERE purpose = 'guests.journey.j3' AND idempotency_key LIKE %s",
                ("%:retry:%",))
    assert [r["status"] for r in cur.fetchall()] == ["cancelled"]  # the outbox's own copy is taken over
    clock.freeze(at(2026, 10, 18, 11, 1))
    dispatch.dispatch_hotel(cur, s)
    p = plans(cur, s, rid)[("j3", 1)]
    assert p["status"] == "sent" and p["action_id"] == j3["action_id"]
    cur.execute("SELECT idempotency_key FROM outbound_messages WHERE id = %s", (p["outbound_message_id"],))
    assert cur.fetchone()["idempotency_key"] == f"gst:{j3['id']}:a2"
    cur.execute("SELECT COUNT(*) AS n FROM actions WHERE hotel_slug = %s AND type = 'guests.journey_j3_offer'", (s,))
    assert cur.fetchone()["n"] == 1  # no new action, no new approval
    wa_status(p["wamid"], "failed", code=131049)
    p = plans(cur, s, rid)[("j3", 1)]
    assert (p["status"], p["skip_reason"]) == ("skipped", "meta_131049")


def test_second_131049_falls_back_to_email_with_marketing_email_consent(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    rid, j3 = _j3_on_whatsapp(cur, s, clock, email_consent=True)
    assert j3["channel"] == "whatsapp"
    wa_status(j3["wamid"], "failed", code=131049)
    clock.freeze(at(2026, 10, 18, 11, 1))
    dispatch.dispatch_hotel(cur, s)
    p = plans(cur, s, rid)[("j3", 1)]
    wa_status(p["wamid"], "failed", code=131049)
    p = plans(cur, s, rid)[("j3", 1)]
    assert p["channel"] == "email" and p["status"] in ("sent", "delivered")


def test_utility_delivery_within_48h_verifies(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    enable_whatsapp(cur, s)
    approve_templates(cur, s)
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s, booked_at=at(2026, 9, 1, 10))
    h = add_contact(cur, s, rid, PHONE)
    grant(cur, s, h, "whatsapp_updates")
    event(cur, s, rid, "created")
    dispatch.dispatch_hotel(cur, s)
    j1 = plans(cur, s, rid)[("j1", 1)]
    assert actions.run_verifier(cur, j1["action_id"])["status"] == "done"  # pending: nothing delivered yet
    clock.freeze(at(2026, 9, 1, 10, 7))
    wa_status(j1["wamid"], "delivered")
    assert plans(cur, s, rid)[("j1", 1)]["status"] == "delivered"
    assert actions.run_verifier(cur, j1["action_id"])["status"] == "verified"
    cur.execute("SELECT charged_category FROM gst_message_costs WHERE plan_id = %s", (j1["id"],))
    assert cur.fetchone()["charged_category"] == "utility"  # a template outside any window


@pytest.mark.parametrize("step,category,window,day,expected", [
    ("j7", "utility", False, date(2026, 9, 20), "marketing"),
    ("j3", "marketing", True, date(2026, 10, 5), "marketing"),
    ("j1", "utility", False, date(2026, 10, 5), "utility"),
    ("j1", "utility", True, date(2026, 9, 20), "free"),
    ("j1", "utility", True, date(2026, 10, 5), "service"),
])
def test_cost_category_before_and_after_2026_10_01(cur, monkeypatch, step, category, window, day, expected):
    monkeypatch.setattr(status, "_window_open_at_send", lambda *a: window)
    sent_at = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    got = status.charged_category(cur, {"step": step}, {"category": category, "kind": "template"}, sent_at)
    assert got == expected


def test_claimed_rows_are_released_after_ten_minutes(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s)
    event(cur, s, rid, "created")
    j1 = plans(cur, s, rid)[("j1", 1)]
    executor.set_plan(cur, j1["id"], status="claimed", claimed_at=at(2026, 9, 1, 9, 50))
    assert dispatch.release_stale(cur, s, at(2026, 9, 1, 10, 5)) == 1
    assert plans(cur, s, rid)[("j1", 1)]["status"] == "planned"


def _proposed_j8(cur, s, clock):
    clock.freeze(at(2026, 10, 23, 11))
    rid = make_res(cur, s, status="CHECKED_OUT", checked_out_at=at(2026, 10, 23, 11))
    h = add_contact(cur, s, rid, EMAIL, kind="email")
    grant(cur, s, h, "marketing_email")
    event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])
    make_offer(cur, s, "j8", title="10% off", price_minor=None, discount_pct=10)
    approve_templates(cur, s, step_list=("j8",))
    clock.freeze(at(2026, 11, 22, 11))
    dispatch.dispatch_hotel(cur, s)  # no limits set: auto_within_limits falls back to approve
    return rid, plans(cur, s, rid)[("j8", 1)]


def test_rejected_proposal_ends_skipped_rejected(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    rid, j8 = _proposed_j8(cur, s, clock)
    assert j8["status"] == "proposed"
    actions.decide(cur, j8["action_id"], "ignore", actor=actions.SYSTEM, hotel_slug=s)
    dispatch.dispatch_hotel(cur, s)
    p = plans(cur, s, rid)[("j8", 1)]
    assert (p["status"], p["skip_reason"]) == ("skipped", "rejected")
    assert sends(cur, s) == []


def test_unapproved_proposal_expires_at_valid_until(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    rid, j8 = _proposed_j8(cur, s, clock)
    clock.freeze(j8["valid_until"] + timedelta(minutes=1))
    actions.expire_due_actions(cur)
    dispatch.dispatch_hotel(cur, s)
    p = plans(cur, s, rid)[("j8", 1)]
    assert (p["status"], p["skip_reason"]) == ("skipped", "approval_expired")


def test_approved_proposal_sends_under_the_same_action(cur, hotel_uae, clock, outbox, client_as):
    s = hotel_uae.slug
    rid, j8 = _proposed_j8(cur, s, clock)
    owner = client_as("owner")
    r = owner.post(f"/hotels/{s}/actions/{j8['action_id']}/decide", json={"decision": "approve"},
                   headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200, r.text
    actions.execute_action(cur, j8["action_id"])
    p = plans(cur, s, rid)[("j8", 1)]
    assert p["status"] in ("sent", "delivered") and p["action_id"] == j8["action_id"]
    assert len(sends(cur, s, category="marketing")) == 1
