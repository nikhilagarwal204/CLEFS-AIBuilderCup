"""J6 gates and J7 feedback (04 spec 3.9): recovery first, and the same
review links for every guest (no sentiment routing, no incentives)."""

from __future__ import annotations

import inspect
import itertools
from pathlib import Path

import pytest

from clefs.core import signals
from clefs.core.hotels import set_module_settings
from clefs.modules.guests.journeys import dispatch, feedback, gates
from clefs.modules.guests.journeys import tokens as jtokens
from clefs.modules.service import api as service_api

from tests.modules.guests.test_journeys_support import at, event, make_res, plans

GOOGLE = "https://g.page/r/abc123/review"
TA = "https://www.tripadvisor.ae/Hotel_Review-g295424-d1-Reviews-Test.html"


def _links(cur, s):
    set_module_settings(cur, s, "guests", {"review_links": {"google_url": GOOGLE, "tripadvisor_url": TA}}, actor=None)


def _checked_out(cur, s, clock, *, rid=None):
    clock.freeze(at(2026, 10, 23, 11))
    rid = rid or make_res(cur, s, status="CHECKED_OUT", checked_out_at=at(2026, 10, 23, 11), room="504")
    event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])
    return rid


def test_review_links_signature_reads_settings_only():
    assert str(inspect.signature(feedback.review_links)) == "(cur: 'Any', hotel_slug: 'str') -> 'ReviewLinks'"
    assert list(inspect.signature(feedback.review_links).parameters) == ["cur", "hotel_slug"]


def test_j6_never_touches_feedback_plans():
    src = Path(gates.__file__).read_text(encoding="utf-8")
    assert "j7" not in src.lower() and "feedback" not in src.split('"""', 2)[2].lower()


@pytest.mark.parametrize("overall,j5_negative,open_tickets", list(itertools.product(range(1, 6), (True, False), (True, False))))
def test_no_sentiment_based_review_routing(cur, hotel_uae, clock, monkeypatch, overall, j5_negative, open_tickets):
    s = hotel_uae.slug
    _links(cur, s)
    monkeypatch.setattr(service_api, "open_items_for_stay",
                        lambda *a, **k: [{"id": 1, "request_type": "ac", "severity": "L2", "status": "open"}]
                        if open_tickets else [])
    monkeypatch.setattr(service_api, "create_followup", lambda *a, **k: {"id": 5, "action_id": 5, "kind": k["kind"],
                                                                       "status": "proposed"})
    rid = make_res(cur, s, status="CHECKED_OUT", checked_out_at=at(2026, 10, 23, 11), room="504")
    if j5_negative:
        cur.execute("INSERT INTO gst_journey_plans (hotel_slug, reservation_id, step, due_at, valid_until, status) "
                    "VALUES (%s, %s, 'j5', now(), now(), 'replied') RETURNING id", (s, rid))
        pid = cur.fetchone()["id"]
        cur.execute("INSERT INTO gst_journey_replies (hotel_slug, plan_id, reservation_id, classification, outcome) "
                    "VALUES (%s, %s, %s, '{\"negative\": true}', 'none')", (s, pid, rid))
    _checked_out(cur, s, clock, rid=rid)
    j7 = plans(cur, s, rid)[("j7", 1)]
    assert j7["due_at"] == at(2026, 10, 25, 11)  # identical for every guest
    clock.freeze(at(2026, 10, 23, 11, 1))
    dispatch.dispatch_hotel(cur, s)  # runs J6_post
    token = jtokens.mint(cur, s, rid, "feedback", expires_at=j7["valid_until"], ref={"plan_id": j7["id"]})
    row = jtokens.lookup(cur, token)
    before = feedback.review_links(cur, s).model_dump()
    out = feedback.submit(cur, row, feedback.FeedbackIn(overall=overall, improve="noisy AC" if overall < 3 else None))
    assert out == {"completion_key": "thanks", "review_links": {"google_url": GOOGLE, "tripadvisor_url": TA}}
    assert before == out["review_links"]
    assert plans(cur, s, rid)[("j7", 1)]["due_at"] == at(2026, 10, 25, 11)


def test_low_rating_files_a_followup_only_after_the_response(cur, hotel_uae, clock, monkeypatch):
    s = hotel_uae.slug
    calls = []
    monkeypatch.setattr(service_api, "create_followup",
                        lambda *a, **k: calls.append(k) or {"id": 7, "action_id": 7, "kind": k["kind"], "status": "proposed"})
    rid = make_res(cur, s)
    token = jtokens.mint(cur, s, rid, "feedback", expires_at=at(2027, 1, 1))
    feedback.submit(cur, jtokens.lookup(cur, token), feedback.FeedbackIn(overall=2))
    assert calls[0]["kind"] == "guest_feedback" and calls[0]["source"] == "j7_feedback"
    assert "review" not in calls[0]["summary"].lower()
    feedback.submit(cur, jtokens.lookup(cur, token), feedback.FeedbackIn(overall=1))
    assert len(calls) == 1  # one feedback per stay


def test_j6_pre_creates_recovery_task_with_l1_cadence(cur, hotel_uae, clock, monkeypatch):
    s = hotel_uae.slug
    monkeypatch.setattr(service_api, "open_items_for_stay",
                        lambda *a, **k: [{"id": 9, "request_type": "ac", "severity": "L1", "status": "open"}])
    clock.freeze(at(2026, 9, 1, 10))
    rid = make_res(cur, s, status="IN_HOUSE", room="504")
    event(cur, s, rid, "created")
    clock.freeze(at(2026, 10, 23, 9, 1))
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j6_pre", 1)]["status"] == "done"
    cur.execute("SELECT * FROM actions WHERE hotel_slug = %s AND type = 'guests.checkout_recovery'", (s,))
    task = dict(cur.fetchone())
    assert task["title"].startswith("Room 504 departs today with 1 open ac ticket")
    assert any("review" in r.lower() for r in task["params"]["rules"])  # printed on the card: never mention reviews
    sig = [x for x in signals.list_signals(cur, s, modules=["guests"]) if x["kind"] == "guests.checkout_open_issues"]
    assert sig and sig[0]["cadence"] == "daily"


def test_review_link_click_is_counted_without_identity(cur, hotel_uae, client):
    s = hotel_uae.slug
    _links(cur, s)
    rid = make_res(cur, s)
    token = jtokens.mint(cur, s, rid, "feedback", expires_at=at(2027, 1, 1))
    r = client.get(f"/public/journeys/{token}/go/google", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == GOOGLE
    cur.execute("SELECT platform, clicks FROM gst_review_link_clicks WHERE hotel_slug = %s", (s,))
    assert [dict(x) for x in cur.fetchall()] == [{"platform": "google", "clicks": 1}]
    page = client.get(f"/public/journeys/{token}").json()
    assert page["review_links"] == {"google_url": GOOGLE, "tripadvisor_url": TA}
    a = client.post(f"/public/journeys/{token}/feedback", json={"overall": 5}).json()
    b = client.post(f"/public/journeys/{token}/feedback", json={"overall": 1, "improve": "dirty"}).json()
    assert a == b
