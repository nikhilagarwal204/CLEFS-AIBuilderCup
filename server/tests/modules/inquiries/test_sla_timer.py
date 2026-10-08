"""SLA timer (04 spec 4.5, 4.12): breach raises inq.unanswered, a segment
change re-arms the timer, the ack does not stop the clock, a first response
does."""

from __future__ import annotations

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    NOW,
    hotel,
    inquiries,
    now,
    queued,
    send_wa,
    set_settings,
)


def _signal(cur, slug, inquiry_id):
    cur.execute("SELECT * FROM signals WHERE hotel_slug = %s AND dedupe_key = %s", (slug, f"inq.unanswered:{inquiry_id}"))
    return cur.fetchone()


def test_unanswered_past_due_raises_the_signal(cur, hotel, now, run_jobs):
    send_wa("Hello, a room for tonight?")
    run_jobs(["core.actions.execute", "core.notify.deliver"])  # the ack goes out
    r = inquiries(cur, hotel.slug)[0]
    now.advance(minutes=16)
    run_jobs("inquiries.sla_check")
    r = inquiries(cur, hotel.slug)[0]
    assert r["sla_breached"] is True
    sig = _signal(cur, hotel.slug, r["id"])
    assert sig is not None and sig["section"] == "money_available"
    assert sig["recommended_action"]["type"] == "core.task"


def test_first_response_stops_the_clock(cur, hotel, now, run_jobs):
    from clefs.modules.inquiries import sla

    send_wa("Hello")
    r = inquiries(cur, hotel.slug)[0]
    now.advance(minutes=5)
    assert sla.record_first_response(cur, hotel, r, by="staff:1")
    assert r["first_response_business_min"] == 5
    now.advance(minutes=20)
    run_jobs("inquiries.sla_check")
    r = inquiries(cur, hotel.slug)[0]
    assert r["sla_breached"] is False and _signal(cur, hotel.slug, r["id"]) is None


def test_segment_change_rearms_the_timer(cur, hotel, now, run_jobs):
    from clefs.modules.inquiries import sla, store

    send_wa("Hello")
    r = inquiries(cur, hotel.slug)[0]
    first_due = r["first_response_due_at"]
    # Extraction decides it is a group (240 min): the due time moves.
    r = store.update_inquiry(cur, r["id"], {"segment": "group"})
    sla.arm(cur, hotel, r)
    assert r["first_response_due_at"] > first_due
    now.advance(minutes=16)
    run_jobs("inquiries.sla_check")  # the old timer fires, sees the move, exits
    r = inquiries(cur, hotel.slug)[0]
    assert r["sla_breached"] is False
    assert any(j["run_at"] == r["first_response_due_at"] for j in queued(cur, "inquiries.sla_check"))
    now.advance(minutes=240)
    run_jobs("inquiries.sla_check")
    assert inquiries(cur, hotel.slug)[0]["sla_breached"] is True


def test_breach_evidence_names_the_ctwa_free_window(cur, hotel, now, run_jobs):
    send_wa("Offer?", referral={"source_type": "ad", "source_id": "ad-1", "ctwa_clid": "clid-9"})
    now.advance(minutes=20)
    run_jobs("inquiries.sla_check")
    r = inquiries(cur, hotel.slug)[0]
    sig = _signal(cur, hotel.slug, r["id"])
    labels = [e["label"] for e in sig["evidence"]]
    assert any(label.startswith("ad lead: free window closes in 23h") for label in labels), labels


def test_future_remind_brings_the_inquiry_back_with_a_clock(cur, hotel, now):
    from datetime import date

    from clefs.modules.inquiries import jobs as inq_jobs
    from clefs.modules.inquiries import service

    send_wa("Maybe in November")
    r = inquiries(cur, hotel.slug)[0]
    service.transition(cur, r, "future", remind_on=date(2026, 10, 1), hotel=hotel)
    assert inq_jobs.remind_due(cur, hotel) == 0
    now.advance(days=2)
    assert inq_jobs.remind_due(cur, hotel) == 1
    r = inquiries(cur, hotel.slug)[0]
    assert r["stage"] == "new" and r["first_response_due_at"] is not None and r["first_response_at"] is None


def test_purge_redacts_spam_and_lost_contacts_after_365_days(cur, hotel, now):
    from clefs.modules.inquiries import jobs as inq_jobs
    from clefs.modules.inquiries import service

    send_wa("Hello", mid="p1", sender="971500000011")
    send_wa("Hi", mid="p2", sender="971500000022")
    a, b = inquiries(cur, hotel.slug)
    service.transition(cur, a, "spam", hotel=hotel)
    now.advance(days=366)
    assert inq_jobs.purge_closed_contacts(cur, hotel.slug) == 1
    a, b = inquiries(cur, hotel.slug)
    assert a["contact_phone_hmac"] is None and a["contact_phone_enc"] is None and a["contact_name"] is None
    assert b["contact_phone_hmac"] is not None  # still open: the 730 d rule (Trust) applies
