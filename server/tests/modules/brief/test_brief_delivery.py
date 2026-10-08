"""Runs, magic links, delivery, push_now and the hotel-local schedule."""

from __future__ import annotations

from datetime import datetime, timezone

from clefs.auth import tokens
from clefs.modules.brief import api as brief_api
from clefs.modules.brief import brief, render


def test_generate_persists_items_and_marks_signals_surfaced(cur, hotel_uae, frozen, sig, act):
    s = sig("leak", section="money_lost", aed=1020, hours=10)
    a = act("Dispute", signal_id=s["id"])
    run = brief.generate(cur, hotel_uae, "daily", trigger="schedule")
    assert run["created"] and run["status"] == "generated"
    assert [(i["item_no"], i["signal_id"], i["action_id"]) for i in run["items"]] == [(1, s["id"], a["id"])]
    cur.execute("SELECT status FROM signals WHERE id = %s", (s["id"],))
    assert cur.fetchone()["status"] == "surfaced"
    again = brief.generate(cur, hotel_uae, "daily", trigger="schedule")
    assert again["created"] is False and again["id"] == run["id"]


def test_push_now_sends_email_with_a_scoped_72h_magic_link(cur, hotel_uae, frozen, outbox, sig, act, owner, gm):
    s = sig("leak", section="money_lost", aed=1020, hours=10, title="Booking no-show commission")
    a = act("Dispute", signal_id=s["id"])
    out = brief_api.push_now(cur, hotel_uae.slug)
    assert out["ok"] and out["brief_id"]
    assert sorted(x["user_id"] for x in out["sent_to"]) == sorted([owner["id"], gm["id"]])
    assert {x["channel"] for x in out["sent_to"]} == {"email"}  # no approved WhatsApp template yet
    mails = outbox.captured("email")
    assert len(mails) == 2
    cur.execute("SELECT * FROM brf_links WHERE run_id = %s ORDER BY user_id", (out["brief_id"],))
    links = cur.fetchall()
    assert len(links) == 2 and links[0]["scope"] == {"action_ids": [a["id"]]}
    assert (links[0]["expires_at"] - frozen.now()).total_seconds() == 72 * 3600
    cur.execute("SELECT scope, purpose, expires_at FROM magic_links WHERE token_hash = %s", (links[0]["token_hash"],))
    ml = cur.fetchone()
    assert ml["purpose"] == "brief" and ml["scope"]["action_ids"] == [a["id"]]
    cur.execute("SELECT status, delivery FROM brf_runs WHERE id = %s", (out["brief_id"],))
    row = cur.fetchone()
    assert row["status"] == "sent" and len(row["delivery"]) == 2


def test_push_now_to_one_user_and_no_recipients(cur, hotel_uae, frozen, outbox, owner, gm):
    out = brief_api.push_now(cur, hotel_uae.slug, user_id=owner["id"])
    assert out["ok"] and [x["user_id"] for x in out["sent_to"]] == [owner["id"]]
    cur.execute("UPDATE hotel_memberships SET is_active = FALSE WHERE hotel_slug = %s", (hotel_uae.slug,))
    none = brief_api.push_now(cur, hotel_uae.slug)
    assert none["ok"] is False and none["reason"] == "no_recipients"
    cur.execute("SELECT kind FROM signals WHERE hotel_slug = %s AND kind = 'brief.delivery.failed'", (hotel_uae.slug,))
    assert cur.fetchone() is None  # nobody to brief is configuration, not a failed delivery
    assert brief_api.push_now(cur, hotel_uae.slug, cadence="yearly")["reason"] == "invalid_cadence"


def test_render_lines_follow_the_template_format(cur, hotel_uae, frozen, sig, owner):
    s = sig("leak", section="money_lost", aed=1020, hours=24 * 13, title="Booking no-show commission", conf=0.9,
            owner_role="owner")
    run = brief.generate(cur, hotel_uae, "daily", trigger="send_now")
    assert run["items"][0]["signal_id"] == s["id"]
    line = render.run_lines(run, tz="Asia/Dubai")[0]
    assert line == "1) AED 1,020 · Booking no-show commission · 90% · by 14 Oct · Omar Owner"


def test_promo_lint_and_body_budget():
    assert render.lint("Close discount plans for Friday") == "restrict rate plans for Friday"
    assert "offer" not in render.lint("Special offer: 20% off deal").lower()
    run = {"items": [{"item_no": i, "title": "x" * 400, "impact_minor": 99_999_999, "currency": "AED",
                      "confidence": 0.9, "deadline_at": "2026-10-14T10:00:00+00:00", "responsible_label": "Finance team"}
                     for i in range(1, 6)], "overdue": []}
    lines = render.budget_lines(run, tz="Asia/Dubai", fixed=60)
    assert len(lines) == 5 and 60 + sum(len(x) for x in lines) <= render.BODY_LIMIT


def test_daily_job_runs_at_brief_time_local_once(cur, hotel_uae, clock, outbox, run_jobs, owner):
    from clefs.core import hotels

    hotels.update_hotel(cur, hotel_uae.slug, {"brief_time_local": datetime(2026, 1, 1, 8, 30).time()})
    clock.freeze(datetime(2026, 10, 1, 4, 20, tzinfo=timezone.utc))  # 08:20 Dubai: not yet
    run_jobs(["brief.daily"], scheduled=True)
    cur.execute("SELECT COUNT(*) AS n FROM brf_runs WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 0
    clock.freeze(datetime(2026, 10, 1, 4, 35, tzinfo=timezone.utc))  # 08:35 Dubai
    run_jobs(["brief.daily"], scheduled=True)
    run_jobs(["brief.daily"], scheduled=True)
    cur.execute("SELECT cadence, brief_date, trigger, status FROM brf_runs WHERE hotel_slug = %s", (hotel_uae.slug,))
    rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["trigger"] == "schedule" and str(rows[0]["brief_date"]) == "2026-10-01"
    assert rows[0]["status"] == "sent"


def test_token_hash_matches_the_foundation(cur, hotel_uae, frozen, owner):
    run = brief.generate(cur, hotel_uae, "daily", trigger="send_now")
    url = brief.mint_link(cur, run, owner["id"])
    token = url.rsplit("/", 1)[-1]
    cur.execute("SELECT run_id FROM brf_links WHERE token_hash = %s", (tokens.hash_token(token),))
    assert cur.fetchone()["run_id"] == run["id"]


def test_all_channels_failing_raises_delivery_failed(cur, hotel_uae, frozen, outbox, owner):
    cur.execute("UPDATE users SET email = NULL WHERE id = %s", (owner["id"],))
    out = brief_api.push_now(cur, hotel_uae.slug)
    assert out["ok"] is False and out["reason"] == "not_delivered"
    cur.execute("SELECT title FROM signals WHERE hotel_slug = %s AND kind = 'brief.delivery.failed'", (hotel_uae.slug,))
    assert cur.fetchone()["title"] == "The daily brief for 2026-10-01 was not delivered"


def test_scheduled_run_skips_hotels_without_recipients(cur, hotel_uae, frozen, run_jobs):
    frozen.advance(minutes=5)
    run_jobs(["brief.daily"], scheduled=True)
    cur.execute("SELECT COUNT(*) AS n FROM brf_runs WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 0


def test_jobs_are_registered_with_their_schedules():
    from clefs import jobs

    import clefs.modules.brief.module  # noqa: F401

    by_name = {j.name: j for j in jobs.registered_jobs()}
    assert by_name["brief.daily"].schedule.setting == "brief_time_local"
    assert by_name["brief.weekly"].schedule.weekday == 1 and by_name["brief.weekly"].schedule.hhmm == "08:00"
    assert by_name["brief.monthly"].schedule.day == 1
    assert by_name["brief.escalate"].schedule.seconds == 900
    assert by_name["brief.service_risk"].schedule.seconds == 300
    assert all(by_name[n].per_hotel for n in ("brief.daily", "brief.escalate", "brief.service_risk"))


def test_weekly_job_sends_the_weekly_tier_on_monday_morning(cur, hotel_uae, clock, outbox, run_jobs, owner):
    clock.freeze(datetime(2026, 10, 5, 4, 1, tzinfo=timezone.utc))  # Monday 08:01 Dubai
    run_jobs(["brief.weekly"], scheduled=True)
    cur.execute("SELECT cadence, status, period_start FROM brf_runs WHERE hotel_slug = %s", (hotel_uae.slug,))
    rows = cur.fetchall()
    assert [(r["cadence"], r["status"], str(r["period_start"])) for r in rows] == [("weekly", "sent", "2026-09-28")]
    cur.execute("SELECT period_key FROM brf_reports WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["period_key"] == "2026-W40"


def test_whatsapp_fallback_sends_the_template_only_when_meta_approved(cur, hotel_uae, frozen, outbox, owner,
                                                                      monkeypatch, sig):
    sig("leak", section="money_lost", aed=1020, hours=10, title="Close discount plans for Friday")
    cur.execute("UPDATE users SET phone_e164 = '+971501234567' WHERE id = %s", (owner["id"],))
    out = brief_api.push_now(cur, hotel_uae.slug)
    attempts = [a["channel"] + ":" + str(a["status"]) for a in brief.find_run(cur, hotel_uae.slug, "daily", frozen.now().date())["delivery"][0]["attempts"]]
    assert attempts[0] == "whatsapp:template_not_approved" and out["sent_to"][0]["channel"] == "email"

    from clefs import notify

    sent = []

    def fake_template(to, template, lang, params=None, **kw):
        sent.append({"to": to, "template": template, "params": params, **kw})
        return notify.NotifyResult(ok=True, channel="whatsapp", outbox_id=1, status="queued", error=None, deduped=False)

    monkeypatch.setattr(brief_api, "wa_template_info", lambda *a, **k: {"status": "APPROVED", "category": "UTILITY"})
    monkeypatch.setattr(notify, "send_whatsapp_template", fake_template)
    out = brief_api.push_now(cur, hotel_uae.slug)
    assert out["sent_to"][0]["channel"] == "whatsapp"
    msg = sent[0]
    assert msg["template"] == "owner_brief_v1" and msg["category"] == "utility" and msg["to"] == "+971501234567"
    assert msg["params"][:2] == ["1", "AED 1,020.00"] and len(msg["params"]) == 7
    assert "restrict rate plans" in msg["params"][2] and "discount" not in " ".join(msg["params"]).lower()
    assert msg["header"] == "Clefs brief · 2026-10-01"


# ─── Estimates say so (04 spec 4.5, 02 spec 4.3) ───


def test_estimated_impacts_carry_est_in_lines_headline_email_and_template(cur, hotel_uae, frozen, sig, owner):
    from clefs.modules.brief import fallback, templates

    sig("leak", section="money_lost", aed=1000, hours=10, title="Booking no-show commission", impact_kind="recoverable")
    sig("inq", section="money_available", aed=2500, hours=20, title="Unanswered inquiry ABC",
        kind="inq.unanswered", impact_kind="revenue_upside", conf=0.6)
    run = brief.generate(cur, hotel_uae, "daily", trigger="send_now")
    by_title = {i["title"]: i for i in run["items"]}
    assert by_title["Unanswered inquiry ABC"]["impact_estimated"] is True
    assert by_title["Booking no-show commission"]["impact_estimated"] is False
    lines = render.run_lines(run, tz="Asia/Dubai")
    inq = next(x for x in lines if "Unanswered inquiry ABC" in x)
    leak = next(x for x in lines if "Booking no-show" in x)
    assert "AED 2,500 est. · Unanswered inquiry ABC" in inq and "est." not in leak
    assert run["headline"] == "2 decisions pending today, AED 3,500.00 at stake (incl. est.)."
    assert "AED 2,500.00 est." in render.email_html(run, hotel_name="H", tz="Asia/Dubai")
    params = templates.owner_brief_variables(run, hotel_uae)["params"]
    assert params[1] == "AED 3,500.00 (incl. est.)" and any("AED 2,500 est." in p for p in params)
    assert "(incl. est.)" in fallback.sms_text(run["items"], hotel_uae)


def test_demand_opportunity_is_an_estimate_and_verified_money_never_is(cur, hotel_uae, frozen, sig):
    from clefs.core import signals as S

    S.upsert_signal(cur, hotel_uae.slug, S.SignalIn(
        module="demand", kind="dr.opportunity", dedupe_key="dr.opportunity:x", title="Event weekend demand",
        section="money_available", cadence="daily", confidence=0.7, impact_amount=4000, currency="AED"))
    sig("won", section="verified", aed=900, hours=10, title="Verified direct booking", impact_kind="revenue_upside")
    only_measured = brief.assemble(cur, hotel_uae, "daily")
    by_title = {i["title"]: i for i in only_measured["items"]}
    assert by_title["Event weekend demand"]["impact_estimated"] is True
    assert by_title["Verified direct booking"]["impact_estimated"] is False
    assert brief.impact_estimated({"section": "money_lost", "impact_kind": "recoverable"}, None, 100) is False
    assert brief.impact_estimated(None, {"type": "inquiries.send_quote"}, 100) is True
    assert brief.impact_estimated(None, {"type": "core.task"}, 100) is False
    assert brief.impact_estimated({"impact_kind": "revenue_upside"}, None, None) is False


def test_headline_without_estimates_is_unchanged(cur, hotel_uae, frozen, sig):
    sig("a", section="money_lost", aed=1020, hours=10, impact_kind="recoverable")
    out = brief.assemble(cur, hotel_uae, "daily")
    assert out["headline"] == "1 decision pending today, AED 1,020.00 at stake."
