"""Weekly verified-value report: payload, HTML, PDF, the weekly brief link."""

from __future__ import annotations

from datetime import datetime, timezone

from clefs import files
from clefs.core import actions as A
from clefs.modules.brief import brief, weekly


def _verify(cur, a, minor, when, *, counts=True):
    cur.execute(
        """UPDATE actions SET status = 'verified', counts_toward_verified_profit = %s, outcome_minor = %s,
                  currency = 'AED', verified_at = %s, attribution = %s WHERE id = %s""",
        (counts, minor, when, "caused" if counts else "observed", a["id"]),
    )


def test_week_helpers():
    assert weekly.week_bounds("2026-W40") == (datetime(2026, 9, 28).date(), datetime(2026, 10, 4).date())
    assert weekly.previous_week(datetime(2026, 10, 5).date()) == "2026-W40"


def test_report_payload_html_and_pdf(cur, hotel_uae, clock, act, sig):
    clock.freeze(datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc))  # Tue of W40
    sig("found", section="money_lost", aed=2000, hours=48)
    a = act("Dispute no-show", aed=500)
    A.decide(cur, a["id"], "approve", actor=None, hotel_slug=hotel_uae.slug)
    b = act("Rate change", aed=900)
    _verify(cur, a, 45000, datetime(2026, 9, 30, 8, tzinfo=timezone.utc))
    _verify(cur, b, 90000, datetime(2026, 9, 30, 9, tzinfo=timezone.utc), counts=False)
    clock.freeze(datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc))  # Monday 08:00 Dubai
    rep = weekly.ensure_report(cur, hotel_uae)
    p = rep["payload"]
    assert rep["period_key"] == "2026-W40" and p["verified_minor"] == 45000
    assert p["identified_minor"] == 200000 and p["identified_signals"] == 1
    row = {r["type"]: r for r in p["by_type"]}["core.task"]
    assert row["verified_minor"] == 45000 and row["observed_minor"] == 90000 and row["actions"] == 2
    assert "AED 450.00 verified this week" in rep["html"]
    ref, data = files.get_file(cur, hotel_uae.slug, rep["pdf_file_id"])
    assert data[:5] == b"%PDF-" and ref.content_type == "application/pdf"
    assert weekly.ensure_report(cur, hotel_uae)["id"] == rep["id"]  # stored once


def test_weekly_brief_links_the_report(cur, hotel_uae, clock, outbox, owner):
    clock.freeze(datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc))
    run = brief.generate(cur, hotel_uae, "weekly", trigger="schedule")
    rep = run["blocks"]["report"]
    assert rep["period_key"] == "2026-W40" and rep["text"] == "Week 2026-W40: AED 0.00 verified of AED 0.00 identified."
    out = brief.deliver(cur, run)
    assert out["ok"]
    assert "Week 2026-W40" in outbox.captured("email")[0]["payload"].get("html", str(outbox.captured("email")[0]))
