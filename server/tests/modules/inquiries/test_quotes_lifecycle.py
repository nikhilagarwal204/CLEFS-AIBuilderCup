"""Quote lifecycle (04 spec 4.10, 4.11): expiry, follow-ups at +24 h and
+72 h, the lost-reason automation, the weighted pipeline, the weekly
pipeline signal, recalibration and the new metrics."""

from __future__ import annotations

from datetime import date

from clefs.core import actions
from clefs.core.actions import SYSTEM
from clefs.modules.inquiries import quotes, signals, store
from tests.modules.inquiries.test_payments_support import sent_quote
from tests.modules.inquiries.test_quotes_support import NOW, demand, hotel, make_inquiry, now  # noqa: F401

RUN = ["core.actions.execute", "core.notify.deliver"]


def follow_ups(cur, quote_id):
    cur.execute("SELECT params->>'n' AS n, status FROM actions WHERE type = 'inquiries.follow_up' "
                "AND (params->>'quote_id')::bigint = %s ORDER BY id", (quote_id,))
    return [(r["n"], r["status"]) for r in cur.fetchall()]


def test_follow_ups_are_proposed_at_24h_and_72h(cur, hotel, demand, run_jobs, now):
    inq, q = sent_quote(cur, hotel, run_jobs, segment="corporate")  # valid 72 h
    now.advance(hours=23)
    assert quotes.expire_due(cur, hotel)["follow_ups"] == 0
    now.advance(hours=2)
    assert quotes.expire_due(cur, hotel)["follow_ups"] == 1
    assert follow_ups(cur, q["id"]) == [("1", "proposed")]
    now.advance(hours=48)  # 73 h: the quote has expired, so no second nudge
    out = quotes.expire_due(cur, hotel)
    assert out["expired"] == 1 and out["follow_ups"] == 0
    assert quotes.quote_by_id(cur, q["id"])["status"] == "expired"


def test_follow_up_executes_inside_the_window(cur, hotel, demand, run_jobs, now, outbox):
    actions.set_autonomy(cur, hotel.slug, "inquiries.follow_up", level="auto_within_limits", actor=SYSTEM)
    inq, q = sent_quote(cur, hotel, run_jobs, segment="corporate")
    now.advance(hours=25)
    quotes.expire_due(cur, hotel)
    run_jobs(RUN)
    assert follow_ups(cur, q["id"]) == [("1", "done")]
    assert "is still available until" in str(outbox.captured("email")[-1])


def test_guest_reply_stops_follow_ups(cur, hotel, demand, run_jobs, now):
    inq, q = sent_quote(cur, hotel, run_jobs, segment="corporate")
    now.advance(hours=2)
    store.add_message(cur, store.get_row(cur, inq["id"]), direction="in", channel="email", body="Thanks, checking",
                      author_type="guest", provider_message_id="in-1")
    now.advance(hours=24)
    assert quotes.expire_due(cur, hotel)["follow_ups"] == 0


def test_unresponsive_after_expiry_is_lost(cur, hotel, demand, run_jobs, now):
    inq, q = sent_quote(cur, hotel, run_jobs)
    now.advance(hours=49)
    assert quotes.expire_due(cur, hotel)["lost"] == 0  # expired, but no follow-up went out: wait 7 d
    now.advance(days=7)
    assert quotes.expire_due(cur, hotel)["lost"] == 1
    row = store.get_row(cur, inq["id"])
    assert (row["stage"], row["lost_reason"]) == ("lost", "GUEST_UNRESPONSIVE")


def test_breached_sla_reads_slow_response(cur, hotel, demand, run_jobs, now):
    inq, q = sent_quote(cur, hotel, run_jobs)
    store.update_inquiry(cur, inq["id"], {"sla_breached": True})
    now.advance(days=10)
    quotes.expire_due(cur, hotel)
    assert store.get_row(cur, inq["id"])["lost_reason"] == "SLOW_RESPONSE"


def test_weighted_pipeline(cur, hotel, demand):
    make_inquiry(cur, hotel, money_at_risk_minor=10000, check_in=date(2026, 10, 20))
    make_inquiry(cur, hotel, money_at_risk_minor=25000, check_in=date(2026, 11, 5), segment="group",
                 email="b@example.com")
    make_inquiry(cur, hotel, money_at_risk_minor=99999, check_in=date(2026, 9, 1), email="past@example.com")
    make_inquiry(cur, hotel, money_at_risk_minor=77777, stage="lost", lost_reason="TRIP_CANCELLED",
                 email="lost@example.com")
    p = signals.pipeline(cur, hotel, today=NOW.date())
    assert p["estimated"] is True and p["label"] == "est."
    assert p["total_minor"] == 35000 and p["count"] == 2
    assert [(m["month"], m["weighted_minor"]) for m in p["months"]] == [("2026-10", 10000), ("2026-11", 25000)]
    assert {(r["month"], r["segment"]) for r in p["rows"]} == {("2026-10", "fit"), ("2026-11", "group")}


def test_pipeline_weekly_signal(cur, hotel, demand):
    make_inquiry(cur, hotel, money_at_risk_minor=2100000)
    sig = signals.pipeline_weekly(cur, hotel)
    assert sig["title"].startswith("1 inquiries (AED 21k potential, est.); last week 0 verified bookings")
    assert sig["section"] == "verified" and sig["cadence"] == "weekly"


def test_recalibrate_needs_30_closed(cur, hotel, demand):
    for i in range(30):
        inq = make_inquiry(cur, hotel, email=f"r{i}@example.com", phone=None,
                           stage="confirmed_verified" if i < 6 else "lost",
                           lost_reason=None if i < 6 else "TRIP_CANCELLED")
        cur.execute("INSERT INTO inq_stage_events (hotel_slug, inquiry_id, to_stage) VALUES (%s, %s, 'quoted')",
                    (hotel.slug, inq["id"]))
    out = signals.recalibrate(cur, hotel.slug)
    assert out["written"] == {"new": "0.200", "quoted": "0.200"}
    cur.execute("SELECT stage, p, n FROM inq_stage_probabilities WHERE hotel_slug = %s ORDER BY stage", (hotel.slug,))
    assert [(r["stage"], str(r["p"]), r["n"]) for r in cur.fetchall()] == [("new", "0.200", 30), ("quoted", "0.200", 30)]


def test_new_metrics_compile_and_run(cur, hotel, demand):
    from clefs.core.metrics import MetricQuery, compile_metric, registered_metrics

    keys = {m.key for m in registered_metrics()}
    new = {"inq.verified_revenue", "inq.commission_avoided", "inq.conversion_rate", "inq.lost_reason_mix",
           "inq.leak_share", "inq.weighted_pipeline"}
    assert new <= keys
    for k in sorted(new):
        sql, params = compile_metric(hotel, MetricQuery(metric=k, start=date(2026, 9, 1), end=date(2026, 12, 31),
                                                        grain="month"))
        cur.execute(sql, params)
        cur.fetchall()
