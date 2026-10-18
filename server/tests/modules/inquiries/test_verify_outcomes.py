"""Verification (04 spec 4.9): the golden 139.06 gross / 126.45 net, one
carrier per inquiry, stay_completed influence, the review band, refunds and
PMS cancellations, and paid-not-in-PMS."""

from __future__ import annotations

from datetime import timedelta

from clefs.core.actions import SYSTEM
from clefs.modules.inquiries import quotes, store, verify
from tests.modules.inquiries.test_payments_support import (  # noqa: F401
    connect,
    pay_link,
    pg,
    reservation,
    send_link,
    sent_quote,
    transport,
)
from tests.modules.inquiries.test_quotes_support import CHECK_IN, demand, hotel, make_inquiry, now  # noqa: F401


def inquiry_actions(cur, inquiry_id):
    cur.execute("SELECT id, type, status, outcome_minor, counts_toward_verified_profit, attribution, reversed_at "
                "FROM actions WHERE (params->>'inquiry_id')::bigint = %s AND type LIKE 'inquiries.%%' ORDER BY id",
                (inquiry_id,))
    return [dict(r) for r in cur.fetchall()]


def test_golden_payment_and_pms(cur, hotel, demand, transport, pg, run_jobs):
    connect(cur, hotel.slug)
    inq, q = sent_quote(cur, hotel, run_jobs)
    send_link(cur, hotel, run_jobs, q)
    pay_link(cur, hotel, inq["id"])
    res = reservation(cur, hotel)
    got = verify.verify_inquiry(cur, hotel, inq["id"])
    assert got["status"] == "verified" and got["path"] == "payment_and_pms"
    out = verify.get_outcome(cur, inq["id"])
    assert (out["commission_avoided_minor"], out["payment_fee_minor"], out["outcome_net_minor"]) == (13906, 1261, 12645)
    assert out["verified_revenue_minor"] == 80000 and out["verified_reservation_id"] == res["id"]
    assert out["attribution"] == "caused" and out["counts_toward_verified_profit"] is True
    assert out["match_score"] == 1  # identity, name, dates, nights, amount
    assert store.get_row(cur, inq["id"])["stage"] == "confirmed_verified"
    acts = {a["type"]: a for a in inquiry_actions(cur, inq["id"])}
    link_act, quote_act = acts["inquiries.send_payment_link"], acts["inquiries.send_quote"]
    assert (link_act["status"], link_act["outcome_minor"], link_act["counts_toward_verified_profit"]) == (
        "verified", 12645, True)
    assert (quote_act["status"], quote_act["outcome_minor"], quote_act["counts_toward_verified_profit"]) == (
        "verified", 0, False)


def test_two_versions_two_links_count_once(cur, hotel, demand, transport, pg, run_jobs):
    connect(cur, hotel.slug)
    inq, q1 = sent_quote(cur, hotel, run_jobs)
    q2 = quotes.build(cur, hotel, store.get_row(cur, inq["id"]))
    quotes.request_send(cur, hotel, q2, actor=SYSTEM, via="system")
    run_jobs(["core.actions.execute", "core.notify.deliver"])
    q2 = quotes.quote_by_id(cur, q2["id"])
    send_link(cur, hotel, run_jobs, q2)
    pay_link(cur, hotel, inq["id"])
    send_link(cur, hotel, run_jobs, q2, purpose="balance")
    pay_link(cur, hotel, inq["id"])
    reservation(cur, hotel)
    verify.verify_inquiry(cur, hotel, inq["id"])
    acts = inquiry_actions(cur, inq["id"])
    counted = [a for a in acts if a["counts_toward_verified_profit"]]
    assert len([a for a in acts if a["type"] in verify.SEND_TYPES]) == 4
    assert len(counted) == 1 and counted[0]["type"] == "inquiries.send_payment_link"
    out = verify.get_outcome(cur, inq["id"])
    assert counted[0]["outcome_minor"] == out["outcome_net_minor"] == 13906 - out["payment_fee_minor"]
    assert sum(int(a["outcome_minor"] or 0) for a in acts) == out["outcome_net_minor"]
    verify.run(cur, hotel)  # a second pass records nothing twice
    assert inquiry_actions(cur, inq["id"]) == acts


def test_stay_completed_without_clefs_outbound_is_influenced(cur, hotel, demand, pg, run_jobs):
    inq = make_inquiry(cur, hotel)
    reservation(cur, hotel, status="CONFIRMED")
    assert verify.verify_inquiry(cur, hotel, inq["id"])["status"] == "pending"  # no payment, no stay yet
    reservation(cur, hotel, status="IN_HOUSE")
    got = verify.verify_inquiry(cur, hotel, inq["id"])
    assert got["path"] == "stay_completed"
    out = verify.get_outcome(cur, inq["id"])
    assert out["attribution"] == "influenced" and out["counts_toward_verified_profit"] is False
    assert out["payment_fee_minor"] == 0 and out["outcome_net_minor"] == 13906
    assert store.get_row(cur, inq["id"])["stage"] == "confirmed_verified"
    run_jobs(["core.actions.execute"])
    verify.run(cur, hotel)
    (a,) = inquiry_actions(cur, inq["id"])
    assert a["type"] == "inquiries.direct_booking" and a["status"] == "verified"
    assert a["counts_toward_verified_profit"] is False and a["attribution"] == "influenced"


def test_review_band_and_confirmation(cur, hotel, demand, pg, run_jobs):
    inq = make_inquiry(cur, hotel)
    # Identity + nights, arrival 2 days off, no quote to compare amounts: 0.55.
    res = reservation(cur, hotel, name="Someone Else", check_in=CHECK_IN + timedelta(days=2), status="IN_HOUSE")
    got = verify.verify_inquiry(cur, hotel, inq["id"])
    assert got == {"status": "review", "reviews_opened": 1}
    (rv,) = verify.list_reviews(cur, hotel.slug)
    assert rv["reservation_id"] == res["id"] and str(rv["score"]) == "0.550"
    out = verify.decide_review(cur, hotel, rv["id"], confirm=True, actor=SYSTEM)
    assert out["verification"]["status"] == "verified"


def test_rejected_review_is_not_proposed_again(cur, hotel, demand, pg):
    inq = make_inquiry(cur, hotel)
    reservation(cur, hotel, name="Someone Else", check_in=CHECK_IN + timedelta(days=2), status="IN_HOUSE")
    verify.verify_inquiry(cur, hotel, inq["id"])
    (rv,) = verify.list_reviews(cur, hotel.slug)
    verify.decide_review(cur, hotel, rv["id"], confirm=False, actor=SYSTEM)
    assert verify.verify_inquiry(cur, hotel, inq["id"])["status"] == "unmatched"


def test_ota_reservation_is_never_a_direct_match(cur, hotel, demand, pg):
    inq = make_inquiry(cur, hotel)
    reservation(cur, hotel, channel="BOOKING", status="IN_HOUSE")
    assert verify.verify_inquiry(cur, hotel, inq["id"])["status"] == "unmatched"


def test_refund_reverses_the_verification(cur, hotel, demand, transport, pg, run_jobs):
    from clefs.modules.inquiries.payments import service as pay
    from clefs.modules.inquiries.payments.base import PaymentEvent
    from tests.modules.inquiries.test_quotes_support import NOW

    connect(cur, hotel.slug)
    inq, q = sent_quote(cur, hotel, run_jobs)
    send_link(cur, hotel, run_jobs, q)
    link = pay_link(cur, hotel, inq["id"])
    reservation(cur, hotel)
    verify.verify_inquiry(cur, hotel, inq["id"])
    link = pay.get_link(cur, hotel.slug, link["id"])
    ev = PaymentEvent(provider="ngenius", provider_event_id="r", provider_ref=link["provider_ref"], kind="refunded",
                      amount_minor=link["amount_minor"], currency="AED", occurred_at=NOW)
    assert pay.apply_status(cur, hotel, link, ev, config={})
    out = verify.get_outcome(cur, inq["id"])
    assert out["outcome"] == "cancelled_after_confirm" and out["counts_toward_verified_profit"] is False
    carrier = [a for a in inquiry_actions(cur, inq["id"]) if a["type"] == "inquiries.send_payment_link"][0]
    assert carrier["reversed_at"] is not None and carrier["outcome_minor"] == 0
    assert store.get_row(cur, inq["id"])["stage"] == "confirmed_verified"  # stage unchanged (4.2)


def test_pms_cancellation_reverses(cur, hotel, demand, pg, run_jobs):
    inq = make_inquiry(cur, hotel)
    res = reservation(cur, hotel, status="IN_HOUSE")
    verify.verify_inquiry(cur, hotel, inq["id"])
    verify.on_stay_event(cur, {"id": 1, "event": "cancelled", "hotel_slug": hotel.slug, "reservation_id": res["id"]})
    assert verify.get_outcome(cur, inq["id"])["outcome"] == "cancelled_after_confirm"


def test_paid_not_in_pms_after_48h(cur, hotel, demand, transport, pg, run_jobs, now):
    connect(cur, hotel.slug)
    inq, q = sent_quote(cur, hotel, run_jobs)
    send_link(cur, hotel, run_jobs, q)
    pay_link(cur, hotel, inq["id"])
    assert verify.verify_inquiry(cur, hotel, inq["id"])["status"] == "unmatched"
    cur.execute("SELECT COUNT(*) AS n FROM actions WHERE type = 'inquiries.create_pms_reservation'")
    assert cur.fetchone()["n"] == 0
    now.advance(hours=49)
    verify.verify_inquiry(cur, hotel, inq["id"])
    cur.execute("SELECT status, owner_role FROM actions WHERE type = 'inquiries.create_pms_reservation'")
    assert dict(cur.fetchone()) == {"status": "proposed", "owner_role": "front_desk"}
    cur.execute("SELECT impact_minor, status FROM signals WHERE kind = 'inq.paid_not_in_pms'")
    assert dict(cur.fetchone()) == {"impact_minor": 50434, "status": "open"}
    reservation(cur, hotel, booked_at=None)
    assert verify.verify_inquiry(cur, hotel, inq["id"])["status"] == "verified"
    cur.execute("SELECT status FROM signals WHERE kind = 'inq.paid_not_in_pms'")
    assert cur.fetchone()["status"] == "resolved"


def test_reservation_made_before_payment(cur, hotel, demand, transport, pg, run_jobs):
    connect(cur, hotel.slug)
    inq, q = sent_quote(cur, hotel, run_jobs)
    reservation(cur, hotel)  # the front desk booked it before the guest paid
    assert verify.verify_inquiry(cur, hotel, inq["id"])["status"] == "pending"
    send_link(cur, hotel, run_jobs, q)
    pay_link(cur, hotel, inq["id"])
    got = verify.verify_inquiry(cur, hotel, inq["id"])
    assert got["status"] == "verified" and got["path"] == "payment_and_pms"


# ─── One reservation verifies one inquiry (double-count regression) ───


def test_one_reservation_verifies_only_one_inquiry(cur, hotel, demand, pg, run_jobs):
    # The same guest asked twice without a duplicate_of link (web form, then
    # WhatsApp with another email). Both score the one booking >= 0.75.
    first = make_inquiry(cur, hotel)
    second = make_inquiry(cur, hotel, source="whatsapp", email="sara.other@example.com")
    res = reservation(cur, hotel, status="IN_HOUSE")
    assert verify.verify_inquiry(cur, hotel, first["id"])["status"] == "verified"
    assert verify.verify_inquiry(cur, hotel, second["id"])["status"] == "unmatched"
    assert verify.get_outcome(cur, second["id"]) is None
    assert store.get_row(cur, second["id"])["stage"] != "confirmed_verified"
    cur.execute("SELECT COUNT(*) AS n FROM inq_outcomes WHERE verified_reservation_id = %s "
                "AND outcome = 'verified_direct'", (res["id"],))
    assert cur.fetchone()["n"] == 1
    # The job pass (every due inquiry) records nothing more.
    verify.run(cur, hotel)
    cur.execute("SELECT COUNT(*) AS n FROM actions WHERE type = 'inquiries.direct_booking'")
    assert cur.fetchone()["n"] == 1


def test_claimed_reservation_is_not_offered_for_review(cur, hotel, demand, pg, run_jobs):
    first = make_inquiry(cur, hotel)
    # A weaker sibling (arrival 2 days off) would sit in the review band.
    second = make_inquiry(cur, hotel, source="whatsapp", check_in=CHECK_IN + timedelta(days=2))
    reservation(cur, hotel, status="IN_HOUSE")
    verify.verify_inquiry(cur, hotel, first["id"])
    assert verify.verify_inquiry(cur, hotel, second["id"]) == {"status": "unmatched", "reviews_opened": 0}
    assert verify.list_reviews(cur, hotel.slug) == []


def test_claimed_by_other_sees_a_concurrent_claim(cur, hotel, demand, pg, run_jobs):
    first = make_inquiry(cur, hotel)
    second = make_inquiry(cur, hotel, source="whatsapp")
    res = reservation(cur, hotel, status="IN_HOUSE")
    assert not verify.claimed_by_other(cur, res["id"], second["id"])
    verify.verify_inquiry(cur, hotel, first["id"])
    assert verify.claimed_by_other(cur, res["id"], second["id"])
    assert not verify.claimed_by_other(cur, res["id"], first["id"])  # its own claim never blocks a re-run


def test_unique_index_backstops_a_second_verified_outcome(cur, hotel, demand, pg, run_jobs):
    import psycopg2
    import pytest

    first = make_inquiry(cur, hotel)
    second = make_inquiry(cur, hotel, source="whatsapp")
    res = reservation(cur, hotel, status="IN_HOUSE")
    verify.verify_inquiry(cur, hotel, first["id"])
    cur.execute("SAVEPOINT dup")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("INSERT INTO inq_outcomes (inquiry_id, hotel_slug, outcome, verified_reservation_id, currency) "
                    "VALUES (%s, %s, 'verified_direct', %s, 'AED')", (second["id"], hotel.slug, res["id"]))
    cur.execute("ROLLBACK TO SAVEPOINT dup")
    # A reversed outcome frees the reservation (partial index).
    verify.reverse(cur, hotel, first["id"], reason="test")
    cur.execute("INSERT INTO inq_outcomes (inquiry_id, hotel_slug, outcome, verified_reservation_id, currency) "
                "VALUES (%s, %s, 'verified_direct', %s, 'AED')", (second["id"], hotel.slug, res["id"]))


# ─── Public api delegates (Trust pilot report, Finance) ───


def test_api_verified_outcomes_returns_the_verified_booking(cur, hotel, demand, pg, run_jobs):
    from clefs.core.hotels import set_module_settings
    from clefs.core.time import local_date
    from clefs.modules.inquiries import api

    inq = make_inquiry(cur, hotel)
    reservation(cur, hotel, status="IN_HOUSE")
    verify.verify_inquiry(cur, hotel, inq["id"])
    day = local_date(hotel)
    (row,) = api.verified_outcomes(cur, hotel.slug, start=day, end=day)
    assert row["inquiry_ref"] == inq["ref"] and row["outcome_minor"] == 13906 and row["currency"] == "AED"
    assert row["attribution"] == "influenced" and row["action_id"] and row["verified_at"]
    assert api.verified_outcomes(cur, hotel.slug, start=day - timedelta(days=9), end=day - timedelta(days=1)) == []
    assert api.verified_outcomes(cur, "no-such-hotel", start=day, end=day) == []
    set_module_settings(cur, hotel.slug, "inquiries", {}, actor=None, enabled=False)
    assert api.verified_outcomes(cur, hotel.slug, start=day, end=day) == []
    set_module_settings(cur, hotel.slug, "inquiries", {}, actor=None, enabled=True)
    verify.reverse(cur, hotel, inq["id"], reason="test")  # a reversed booking drops out
    assert api.verified_outcomes(cur, hotel.slug, start=day, end=day) == []
