"""The magic-link mobile brief: GET /public/brief/{token} and one-tap
decisions, scoped to the link's hotel and surfaced actions, never consuming
a use of the link, expiring with it."""

from __future__ import annotations

import pytest

from clefs.core import actions as A
from clefs.modules.brief import brief


@pytest.fixture
def linked(cur, hotel_uae, frozen, owner, sig, act):
    """A stored daily run with one surfaced action and the owner's raw token."""
    s = sig("leak", section="money_lost", aed=1020, hours=10, title="Booking no-show commission")
    a = act("Charge the no-show", signal_id=s["id"], aed=1020, due_hours=10,
            params={"amount_minor": 102000, "min_amount_minor": 50000, "max_amount_minor": 150000})
    run = brief.generate(cur, hotel_uae, "daily", trigger="send_now")
    other = act("Not in the brief")  # proposed after the run: the link never covered it
    url = brief.mint_link(cur, run, owner["id"])
    return {"token": url.rsplit("/", 1)[-1], "action": a, "other": other, "run": run}


def test_get_brief_reads_without_consuming_the_link(client, cur, linked):
    r = client.get(f"/public/brief/{linked['token']}")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store" and "noindex" in r.headers["x-robots-tag"]
    body = r.json()
    assert body["hotel"]["slug"] == "test-dubai" and body["user"]["name"] == "Omar Owner"
    item = body["run"]["items"][0]
    assert item["title"] == "Booking no-show commission"
    assert item["action"]["id"] == linked["action"]["id"] and item["action"]["can_decide"]
    assert set(item["action"]["decisions"]) >= {"approve", "adjust", "snooze", "ignore"}
    assert item["action"]["adjust"] == {"field": "amount_minor", "label": "amount", "currency": "AED",
                                        "min": "500.00", "max": "1500.00"}
    cur.execute("SELECT uses FROM magic_links WHERE hotel_slug = 'test-dubai'")
    assert cur.fetchone()["uses"] == 0


def test_decide_surfaced_action_only(client, cur, linked):
    base = f"/public/brief/{linked['token']}/actions"
    r = client.post(f"{base}/{linked['other']['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403 and r.json()["code"] == "not_surfaced"
    r = client.post(f"{base}/{linked['action']['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["action"]["status"] == "approved"
    cur.execute("SELECT meta FROM audit_log WHERE action = 'core.actions.approve' ORDER BY id DESC LIMIT 1")
    assert cur.fetchone()["meta"]["via"] == "magic"
    r = client.post(f"{base}/{linked['action']['id']}/decide", json={"decision": "ignore"})
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"


def test_adjust_within_bounds(client, cur, linked):
    base = f"/public/brief/{linked['token']}/actions/{linked['action']['id']}/decide"
    r = client.post(base, json={"decision": "adjust", "params": {"amount_minor": "2000"}})
    assert r.status_code == 400 and r.json()["code"] == "out_of_bounds"
    r = client.post(base, json={"decision": "adjust", "params": {"amount": "900.50"}})
    assert r.status_code == 200, r.text
    assert r.json()["action"]["params"]["amount_minor"] == 90050


def test_snooze_days(client, linked, frozen):
    r = client.post(f"/public/brief/{linked['token']}/actions/{linked['action']['id']}/decide",
                    json={"decision": "snooze", "snooze_days": 7})
    assert r.status_code == 200 and r.json()["action"]["status"] == "snoozed"


def test_link_expiry_and_bad_tokens(client, linked, frozen):
    assert client.get("/public/brief/not-a-token").status_code == 401
    frozen.advance(hours=73)
    r = client.get(f"/public/brief/{linked['token']}")
    assert r.status_code == 401 and r.json()["code"] == "magic_link_invalid"
    r = client.post(f"/public/brief/{linked['token']}/actions/{linked['action']['id']}/decide",
                    json={"decision": "approve"})
    assert r.status_code == 401


def test_member_who_left_loses_the_link(client, cur, linked, owner):
    cur.execute("UPDATE hotel_memberships SET is_active = FALSE WHERE user_id = %s", (owner["id"],))
    assert client.get(f"/public/brief/{linked['token']}").status_code == 401


def test_role_without_the_decide_permission_sees_no_buttons(client, cur, hotel_uae, frozen, make_user, sig, act):
    fin = make_user("finance", hotel_uae.slug, name="Fin")
    s = sig("leak", section="money_lost", aed=1020, hours=10)
    a = act("Charge", signal_id=s["id"], aed=1020, due_hours=10)
    run = brief.generate(cur, hotel_uae, "daily", trigger="send_now")
    token = brief.mint_link(cur, run, fin["id"]).rsplit("/", 1)[-1]
    item = client.get(f"/public/brief/{token}").json()["run"]["items"][0]
    assert item["action"]["can_decide"] is False and item["action"]["decisions"] == []
    r = client.post(f"/public/brief/{token}/actions/{a['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403
    assert A.get_action(cur, hotel_uae.slug, a["id"])["status"] == "proposed"
