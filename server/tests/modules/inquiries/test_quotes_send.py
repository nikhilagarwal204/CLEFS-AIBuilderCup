"""Auto-send rule (04 spec 4.7), the send executor, staff Send and the
public quote page with accept (4.6 steps 7-8)."""

from __future__ import annotations

from clefs.core import actions
from clefs.core.actions import SYSTEM
from clefs.modules.inquiries import quotes, store
from tests.modules.inquiries.test_quotes_support import CHECK_IN, demand, hotel, make_inquiry, now  # noqa: F401

RUN = ["core.actions.execute", "core.notify.deliver"]


def dial(cur, slug, level, **limits):
    actions.set_autonomy(cur, slug, "inquiries.send_quote", level=level, limits=limits or None, actor=SYSTEM)


def send(cur, hotel, q):
    return quotes.request_send(cur, hotel, q, actor=SYSTEM, via="system")


def test_quote_at_bar_sends_automatically(cur, hotel, demand, outbox, run_jobs):
    dial(cur, hotel.slug, "auto_within_limits")
    inq = make_inquiry(cur, hotel)
    q = quotes.build(cur, hotel, inq)
    act = send(cur, hotel, q)
    assert act["status"] == "approved" and act["decided_via"] == "dial"
    run_jobs(RUN)
    q = quotes.quote_by_id(cur, q["id"])
    assert q["status"] == "sent" and q["sent_at"] is not None and q["public_token_hash"] is not None
    row = store.get_row(cur, inq["id"])
    assert row["stage"] == "quoted" and row["first_response_at"] is not None
    mails = outbox.captured("email")
    assert mails and "total AED 1,008.68" in str(mails[-1])  # no approved template: email
    assert actions.get_action(cur, hotel.slug, act["id"])["status"] == "done"


def test_one_fil_below_bar_goes_to_approval(cur, hotel, demand, run_jobs):
    dial(cur, hotel.slug, "auto_within_limits")
    demand.recommended["STD"] = "399.99"
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    assert "below_bar" in q["auto_send_reasons"]
    act = send(cur, hotel, q)
    assert act["status"] == "proposed"
    assert quotes.quote_by_id(cur, q["id"])["status"] == "awaiting_approval"


def test_discount_goes_to_approval_and_raises_signal_for_group(cur, hotel, demand):
    dial(cur, hotel.slug, "auto_within_limits", segments=["fit", "corporate", "group"], max_rooms=20,
         max_nights=7, max_total_minor=10_000_000, max_bar_age_days=7)
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, segment="group", rooms=10))
    act = send(cur, hotel, q)
    assert act["status"] == "proposed" and "discount" in q["auto_send_reasons"]
    cur.execute("SELECT kind, status FROM signals WHERE hotel_slug = %s AND kind = 'inq.quote_awaiting_approval'",
                (hotel.slug,))
    assert [dict(r) for r in cur.fetchall()] == [{"kind": "inq.quote_awaiting_approval", "status": "open"}]


def test_hand_set_auto_cannot_exceed_the_ceiling(cur, hotel, demand):
    # A row written straight to the dial (as an old or hand-edited setting would be).
    cur.execute("INSERT INTO action_autonomy (hotel_slug, action_type, level, limits) VALUES (%s, %s, 'auto', '{}')",
                (hotel.slug, "inquiries.send_quote"))
    demand.recommended["STD"] = "399.99"
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    act = send(cur, hotel, q)
    assert act["status"] == "proposed"


def test_default_limits_apply(cur, hotel, demand):
    dial(cur, hotel.slug, "auto_within_limits")
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, rooms=3))
    assert "too_many_rooms" in q["auto_send_reasons"]
    assert send(cur, hotel, q)["status"] == "proposed"
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, nights=8, email="n@example.com"))
    assert q["auto_send_reasons"] == ["too_many_nights"]
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, confidence="0.70", email="c@example.com"))
    assert q["auto_send_reasons"] == ["low_confidence"]
    demand.bar["STD"] = "2100.00"  # 2 nights x 2,100 = 5,295.52 incl. taxes > AED 5,000
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, email="t@example.com"))
    assert q["auto_send_reasons"] == ["total_over_limit"]


def test_default_dial_is_approve(cur, hotel, demand):
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    assert q["auto_send_eligible"] is True
    assert send(cur, hotel, q)["status"] == "proposed"


def test_no_channel_fails_template_missing(cur, hotel, demand, run_jobs):
    dial(cur, hotel.slug, "auto_within_limits")
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, email=None))
    act = send(cur, hotel, q)
    run_jobs(RUN)
    got = actions.get_action(cur, hotel.slug, act["id"])
    assert got["status"] == "failed"
    assert quotes.quote_by_id(cur, q["id"])["status"] == "approved"


def test_edit_after_request_cancels_the_pending_action(cur, hotel, demand):
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    act = send(cur, hotel, q)
    q2 = quotes.revise(cur, hotel, quotes.quote_by_id(cur, q["id"]),
                       edits=[{"stay_date": CHECK_IN.isoformat(), "rate_minor": 45000}], reason=None, can_approve=False)
    assert q2["status"] == "draft" and q2["action_id"] is None
    assert actions.get_action(cur, hotel.slug, act["id"])["status"] == "cancelled"


def test_public_page_and_accept(cur, hotel, demand, run_jobs):
    from clefs.modules.inquiries.payments import service as pay

    dial(cur, hotel.slug, "auto_within_limits")
    pay.connect(cur, hotel.slug, provider="ngenius", config={"outlet_ref": "OUT-1"}, secret={"api_key": "k"}, actor=SYSTEM)
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    send(cur, hotel, q)
    run_jobs(RUN)
    q = quotes.quote_by_id(cur, q["id"])
    token = quotes.public_token(q)
    view = quotes.public_view(cur, token)
    assert view["taxes"]["total"] == "1008.68" and view["can_accept"] is True and view["noindex"] is True
    flat = str(view)
    assert "bar_minor" not in flat and "floor" not in flat and "guest@example.com" not in flat
    out = quotes.accept(cur, token)
    assert out["status"] == "accepted" and out["payment_action_status"] == "proposed"
    link_act = actions.get_action(cur, hotel.slug, out["payment_action_id"])
    assert link_act["type"] == "inquiries.send_payment_link" and link_act["params"]["amount_minor"] == 50434
    assert store.get_row(cur, q["inquiry_id"])["guest_engaged"] is True
    assert quotes.accept(cur, token)["replayed"] is True


def test_expired_quote_cannot_be_accepted(cur, hotel, demand, run_jobs, now):
    dial(cur, hotel.slug, "auto_within_limits")
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    send(cur, hotel, q)
    run_jobs(RUN)
    token = quotes.public_token(quotes.quote_by_id(cur, q["id"]))
    now.advance(hours=49)
    assert quotes.public_view(cur, token)["status"] == "expired"
    import pytest

    from clefs.core.errors import ClefsError

    with pytest.raises(ClefsError) as e:
        quotes.accept(cur, token)
    assert e.value.code == "quote_expired"


def test_accept_without_a_provider_still_accepts(cur, hotel, demand, run_jobs):
    dial(cur, hotel.slug, "auto_within_limits")
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    send(cur, hotel, q)
    run_jobs(RUN)
    out = quotes.accept(cur, quotes.public_token(quotes.quote_by_id(cur, q["id"])))
    assert out["status"] == "accepted" and out["payment_action_id"] is None
    assert out["payment_reason"] == "no_payment_provider"


def test_bar_change_after_send_is_honoured(cur, hotel, demand, run_jobs):
    dial(cur, hotel.slug, "auto_within_limits")
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    send(cur, hotel, q)
    run_jobs(RUN)
    demand.bar["STD"] = "550.00"  # Demand moves BAR after the quote went out
    token = quotes.public_token(quotes.quote_by_id(cur, q["id"]))
    assert quotes.public_view(cur, token)["taxes"]["total"] == "1008.68"
    assert quotes.accept(cur, token)["status"] == "accepted"
