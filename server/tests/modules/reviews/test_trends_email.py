"""Weekly theme trends, forwarded review emails, the purge job, metrics."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from clefs.core import signals
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.reviews import store, trends
from clefs.modules.reviews.sources import email_forward
from tests.foundation import resend_mail


def _themed(cur, slug, ext, posted, key="hvac", polarity="negative"):
    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id=ext, rating=2, text="x",
                                                          posted_at=posted))
    cur.execute("INSERT INTO rev_derived (review_id, hotel_slug, themes, severity, negative) VALUES (%s, %s, %s, 'low', TRUE)",
                (row["id"], slug, Json([{"key": key, "department": "engineering", "polarity": polarity,
                                         "summary_en": "x"}])))


def test_theme_trend_up_fires_on_a_50pct_rise_with_three_mentions(cur, slug):
    hotel = get_hotel(cur, slug)
    monday = date(2026, 9, 28)  # the job runs this Monday; the week before is 21-27 Sep
    last_week = datetime(2026, 9, 23, 8, tzinfo=timezone.utc)
    for i in range(4):
        _themed(cur, slug, f"n{i}", last_week + timedelta(hours=i))
    for w in range(1, 5):  # baseline: 2 a week over the four weeks before
        for j in range(2):
            _themed(cur, slug, f"b{w}{j}", last_week - timedelta(weeks=w, hours=j))
    _themed(cur, slug, "wifi1", last_week, key="wifi")
    _themed(cur, slug, "wifi2", last_week, key="wifi")  # 2 < 3 mentions: nothing
    out = trends.detect(cur, hotel, today=monday)
    assert out == {"week": "2026-09-21", "trending": 1, "resolved": 0}
    sig = signals.list_signals(cur, slug, modules=["reviews"])
    assert len(sig) == 1 and sig[0]["kind"] == trends.SIGNAL_TREND and "up 100%" in sig[0]["title"]
    assert sig[0]["cadence"] == "weekly" and sig[0]["section"] == "reviews"
    # The next week, no rise: last week's signal resolves.
    assert trends.detect(cur, hotel, today=monday + timedelta(weeks=1))["resolved"] == 1


def test_weekly_series_buckets_in_hotel_time(cur, slug):
    hotel = get_hotel(cur, slug)
    # Sunday 21:00 UTC is Monday 01:00 in Dubai: it belongs to the new week.
    _themed(cur, slug, "edge", datetime(2026, 9, 27, 21, tzinfo=timezone.utc))
    series = trends.weekly_series(cur, hotel, weeks=2, today=date(2026, 9, 29))
    assert series["weeks"] == ["2026-09-21", "2026-09-28"]
    assert series["themes"]["hvac"]["negative"] == [0, 1]


def _spf_pass(envelope_from):
    # A passing Received-SPF that names its envelope domain, stamped by our verifier.
    return {"Received-SPF": f"Pass (sender SPF authorized) identity=mailfrom; client-ip=198.51.100.7; "
                            f"envelope-from={envelope_from}; receiver=inbound.resend.com"}


def _email(cur, slug, *, sender, subject, text, message_id="m1", headers=None):
    # Default: SPF passed for the sender's own domain (a real, authenticated
    # message), so the tests below exercise the allowlist, not the auth gate.
    headers = _spf_pass(sender) if headers is None else headers
    spf = next((v.split()[0].lower() for k, v in headers.items() if k.lower() == "received-spf"), None)
    return SimpleNamespace(cur=cur, hotel_slug=slug, sender=sender, subject=subject, text=text, html=None,
                           stripped_reply=None, message_id=message_id, headers=headers, spf=spf)


def test_forwarded_booking_email_heuristic_without_ai(cur, slug, make_user):
    u = make_user("gm", slug)
    body = "---------- Forwarded message ---------\nFrom: Booking.com <noreply@booking.com>\n" \
           "Your property received a review score of 6,5/10. The AC was noisy."
    out = email_forward.handle(_email(cur, slug, sender=u["email"], subject="Fwd: New guest review", text=body))
    assert out["handled"] and out["via"] == "heuristic"
    r = store.get_review(cur, slug, out["review_id"])
    assert r["platform"] == "booking" and r["rating_scale"] == 10 and float(r["rating_norm_5"]) == 3.3
    again = email_forward.handle(_email(cur, slug, sender=u["email"], subject="Fwd: New guest review", text=body))
    assert again["review_id"] == out["review_id"] and not again["created"]


def test_forwarded_email_from_a_stranger_is_refused(cur, slug):
    out = email_forward.handle(_email(cur, slug, sender="someone@evil.example", subject="Review",
                                      text="5 stars, amazing"))
    assert out == {"handled": False, "reason": "sender_not_allowed"}


def test_forwarded_email_llm_extraction(cur, slug, fake_ai, set_settings):
    set_settings(forward_senders=["@hotel-dubai.ae"])
    fake_ai.push({"is_review": True, "platform": "tripadvisor", "rating": 2, "rating_scale": 5, "author": "Ken",
                  "title": "Tired", "text": "Carpet stained in room 811", "posted_date": "2026-09-25",
                  "language": "en"})
    out = email_forward.handle(_email(cur, slug, sender="gr@hotel-dubai.ae", subject="Fwd: New review",
                                      text="Tripadvisor ..."))
    r = store.get_review(cur, slug, out["review_id"])
    assert out["via"] == "llm" and r["platform"] == "tripadvisor" and r["room_hint"] == "811"


def test_raw_purge_job_and_metrics(app, cur, slug, now, run_jobs):
    from clefs.core.metrics import registered_metrics

    keys = {m.key for m in registered_metrics()}
    assert {"reviews.avg_rating_30d", "reviews.response_rate", "reviews.negative_share"} <= keys
    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="p", rating=3,
                                                          author_display="Ann", text="meh"))
    now.advance(days=31)
    run_jobs(only=["reviews.raw_purge"], scheduled=True)
    assert store.get_review(cur, slug, row["id"])["raw_text"] is None


def test_email_webhook_routes_reviews_mail_to_the_module(client, cur, slug, env, make_user):
    env(INBOUND_EMAIL_ENABLED="true", RESEND_WEBHOOK_SECRET=resend_mail.SECRET, INBOUND_EMAIL_DOMAIN=None)
    cur.execute("UPDATE hotels SET inbound_email_alias = 'testdubai' WHERE slug = %s", (slug,))
    user = make_user("gm", slug)
    to = "testdubai+reviews@in.clefs.world"
    body = {"From": user["email"], "FromFull": {"Email": user["email"], "Name": "GM"}, "To": to,
            "ToFull": [{"Email": to, "Name": ""}], "CcFull": [], "Subject": "Fwd: New review on Tripadvisor",
            "MessageID": "rv-1", "TextBody": "From: Tripadvisor <noreply@tripadvisor.com>\nrated 2 of 5 bubbles. "
            "Dirty bathroom.", "Attachments": [],
            # What from_resend writes from Resend's API: no domain, so the DMARC pass proves From.
            "Headers": [{"Name": "Received-SPF", "Value": "pass receiver=inbound.resend.com"},
                        {"Name": "Authentication-Results", "Value": "inbound.resend.com; spf=pass; dkim=pass; dmarc=pass"}]}
    r = resend_mail.post(client, body)
    assert r.status_code == 200 and r.json()["status"] == "handled" and r.json()["purpose"] == "reviews"
    cur.execute("SELECT platform, rating_norm_5 FROM rev_reviews WHERE hotel_slug = %s", (slug,))
    row = cur.fetchone()
    assert row["platform"] == "tripadvisor" and float(row["rating_norm_5"]) == 2.0


def test_scheduled_analyze_and_sync_jobs_run_per_hotel(app, cur, slug, now, run_jobs):
    store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="s1", rating=1,
                                                 text="Rude staff at check-in"))
    report = run_jobs(only=["reviews.analyze", "reviews.sync", "reviews.theme_trends"], scheduled=True)
    cur.execute("SELECT analyzed_at FROM rev_reviews WHERE hotel_slug = %s", (slug,))
    assert cur.fetchone()["analyzed_at"] is not None
    cur.execute("SELECT job, status FROM job_runs WHERE hotel_slug = %s AND job LIKE 'reviews.%%'", (slug,))
    runs = {r["job"]: r["status"] for r in cur.fetchall()}
    assert runs.get("reviews.analyze") and runs.get("reviews.sync"), (runs, report)


# ─── Forwarded email: sender allowlist and authentication ───


def test_allowlist_matches_registrable_platform_domains_only(cur, slug):
    ok = email_forward.sender_allowed
    assert ok(cur, slug, "noreply@tripadvisor.com", [])
    assert ok(cur, slug, "MemberServices@e.tripadvisor.com", [])
    assert ok(cur, slug, "noreply@tripadvisor.co.uk", [])
    assert ok(cur, slug, "noreply@booking.com", [])
    # Look-alike domains anyone can register.
    for bad in ("x@evil-tripadvisor.example", "x@tripadvisor.attacker.net", "x@nottripadvisor.com",
                "x@booking.com.attacker.net", "x@evilbooking.com"):
        assert not ok(cur, slug, bad, []), bad


def test_lookalike_platform_sender_cannot_plant_a_review(cur, slug):
    for sender in ("x@evil-tripadvisor.example", "x@tripadvisor.attacker.net"):
        out = email_forward.handle(_email(cur, slug, sender=sender, subject="New review",
                                          text="rated 1 of 5 bubbles. Awful."))
        assert out == {"handled": False, "reason": "sender_not_allowed"}, sender
    cur.execute("SELECT count(*) AS n FROM rev_reviews WHERE hotel_slug = %s", (slug,))
    assert cur.fetchone()["n"] == 0


def test_spoofed_from_without_spf_or_dkim_is_refused(cur, slug, make_user):
    u = make_user("gm", slug)
    body = "From: Booking.com <noreply@booking.com>\nYour property received a review score of 2/10."
    # Allowlisted From (a member, a platform) but no authentication headers.
    for sender in (u["email"], "noreply@booking.com"):
        out = email_forward.handle(_email(cur, slug, sender=sender, subject="Fwd: review", text=body, headers={}))
        assert out == {"handled": False, "reason": "sender_unauthenticated"}, sender
    # SPF failed.
    bad = {"Received-SPF": "Fail (sender not authorized) identity=mailfrom; envelope-from=noreply@booking.com"}
    out = email_forward.handle(_email(cur, slug, sender="noreply@booking.com", subject="r", text=body, headers=bad))
    assert out["reason"] == "sender_unauthenticated"
    # SPF passed, but for the attacker's own envelope domain: not aligned.
    out = email_forward.handle(_email(cur, slug, sender="noreply@booking.com", subject="r", text=body,
                                      headers=_spf_pass("bounce@attacker.net")))
    assert out["reason"] == "sender_unauthenticated"
    # DKIM passed for a different domain: not aligned either.
    dk = {"Authentication-Results": "mx.clefs; dkim=pass header.d=attacker.net; spf=none"}
    out = email_forward.handle(_email(cur, slug, sender="noreply@booking.com", subject="r", text=body, headers=dk))
    assert out["reason"] == "sender_unauthenticated"
    # DMARC passed for another From domain, or failed: nothing proven either.
    for ar in ("inbound.resend.com; dmarc=pass header.from=attacker.net", "inbound.resend.com; spf=pass; dmarc=fail"):
        out = email_forward.handle(_email(cur, slug, sender="noreply@booking.com", subject="r", text=body,
                                          headers={"Authentication-Results": ar}))
        assert out["reason"] == "sender_unauthenticated", ar
    cur.execute("SELECT count(*) AS n FROM rev_reviews WHERE hotel_slug = %s", (slug,))
    assert cur.fetchone()["n"] == 0


def test_aligned_dkim_or_subdomain_spf_is_accepted(cur, slug):
    body = "Your property received a review score of 9/10. Lovely stay."
    dk = {"Authentication-Results": "mx.clefs; spf=softfail smtp.mailfrom=x@fwd.example; "
                                    "dkim=pass (2048-bit key) header.d=booking.com header.b=abc"}
    out = email_forward.handle(_email(cur, slug, sender="noreply@booking.com", subject="New review", text=body,
                                      headers=dk, message_id="dk1"))
    assert out["handled"], out
    # Relaxed alignment: a bounce subdomain of the From domain passes SPF for it.
    out = email_forward.handle(_email(cur, slug, sender="noreply@booking.com", subject="New review", text=body,
                                      headers=_spf_pass("bounces@mail.booking.com"), message_id="spf1"))
    assert out["handled"], out


def test_org_domain_alignment_helpers():
    assert email_forward._aligned("mail.booking.com", "booking.com")
    assert email_forward._aligned("e.tripadvisor.co.uk", "tripadvisor.co.uk")
    assert not email_forward._aligned("attacker.co.uk", "tripadvisor.co.uk")
    assert not email_forward._aligned("booking.com.attacker.net", "booking.com")
    assert not email_forward._aligned(None, "booking.com")
