"""Brief assembly: cadence tiers, caps, verified line, responsible person."""

from __future__ import annotations

from datetime import timedelta

from clefs.core import actions as A
from clefs.modules.brief import brief


def test_daily_brief_ranks_daily_signals_only_and_never_compliance(cur, hotel_uae, frozen, sig):
    sig("a", section="money_lost", aed=1020, hours=10)
    sig("b", section="money_available", aed=5400, hours=30)
    sig("w", section="money_lost", cadence="weekly", aed=90000, hours=1)
    sig("c", section="compliance", aed=99999, hours=1)
    out = brief.assemble(cur, hotel_uae, "daily")
    titles = [i["title"] for i in out["items"]]
    assert titles == ["b", "a"] or titles == ["a", "b"]
    assert "w" not in titles and "c" not in titles
    assert out["cadence"] == "daily" and out["brief_date"] == "2026-10-01"
    assert out["headline"] == "2 decisions pending today, AED 6,420.00 at stake."


def test_section_and_detector_caps_apply(cur, hotel_uae, frozen, sig):
    for i in range(4):
        sig(f"lost{i}", section="money_lost", aed=5000 + i, hours=5, kind="pg.no_show")
    for i in range(3):
        sig(f"vcc{i}", section="money_lost", aed=4000 + i, hours=5, kind="pg.vcc")
    sig("inq", section="money_available", aed=100, hours=500, kind="inq.unanswered")
    out = brief.assemble(cur, hotel_uae, "daily")
    kinds = [i["detector"] for i in out["items"]]
    assert sum(1 for i in out["items"] if i["section"] == "money_lost") == 3
    assert kinds.count("pg.no_show") <= 2 and kinds.count("pg.vcc") <= 2
    assert "inq.unanswered" in kinds


def test_top_five_limit_and_verified_line_is_extra(cur, hotel_uae, frozen, sig):
    for i, section in enumerate(["money_lost", "money_available", "service_risk", "reviews"] * 3):
        sig(f"s{i}", section=section, aed=100 * (i + 1), hours=5, kind=f"pg.k{i}")
    out = brief.assemble(cur, hotel_uae, "daily")
    assert len(out["items"]) == 5
    assert out["verified"]["text"].startswith("Verified results: AED 0.00 in the last 24 h")


def test_verified_line_sums_24h_and_month_to_date(cur, hotel_uae, frozen, act):
    a = act("Dispute no-show", aed=500)
    b = act("Charge VCC", aed=300)
    for x, when in ((a, frozen.now() - timedelta(hours=2)), (b, frozen.now() - timedelta(days=5))):
        cur.execute(
            """UPDATE actions SET status = 'verified', counts_toward_verified_profit = TRUE, outcome_minor = %s,
                      currency = 'AED', verified_at = %s WHERE id = %s""",
            (x["expected_minor"], when, x["id"]),
        )
    v = brief.verified_line(cur, hotel_uae, now=frozen.now())
    # b was verified on 26 Sep: last month in Dubai, so it is not month-to-date on 1 Oct
    assert v["last_24h_minor"] == 50000 and v["mtd_minor"] == 50000
    assert v["text"] == "Verified results: AED 500.00 in the last 24 h, AED 500.00 this month"


def test_pending_brief_approval_without_signal_reaches_the_daily_brief(cur, hotel_uae, frozen, act):
    a = act("Approve group quote", aed=2500, due_hours=20)
    assert a["status"] == "proposed" and a["surface"] == "brief"
    out = brief.assemble(cur, hotel_uae, "daily")
    assert [i["action_id"] for i in out["items"]] == [a["id"]]
    assert out["items"][0]["section"] == "money_available"


def test_signal_with_live_action_carries_it_and_responsible_person(cur, hotel_uae, frozen, sig, act, owner, gm):
    s = sig("leak", section="money_lost", aed=1020, hours=10)
    a = act("Dispute Booking no-show", signal_id=s["id"], owner_role="finance")
    out = brief.assemble(cur, hotel_uae, "daily")
    item = out["items"][0]
    assert item["signal_id"] == s["id"] and item["action_id"] == a["id"]
    # No finance member: the role label stands in for the person
    assert item["owner_role"] == "finance" and item["responsible_label"] == "Finance"
    A.decide(cur, a["id"], "assign", actor=None, assignee_user_id=gm["id"], hotel_slug=hotel_uae.slug)
    item = brief.assemble(cur, hotel_uae, "daily")
    assert item["items"] == [] or item["items"][0]["responsible_user_id"] == gm["id"]


def test_weekly_tier_has_autonomy_and_compliance_blocks(cur, hotel_uae, frozen, sig):
    sig("vis", section="money_available", cadence="weekly", aed=800, hours=100, kind="vis.rank_drop")
    sig("td", section="compliance", cadence="weekly", aed=1000, hours=100, kind="tax.td_remit")
    out = brief.assemble(cur, hotel_uae, "weekly")
    assert [i["title"] for i in out["items"]] == ["vis"]
    assert [c["title"] for c in out["blocks"]["compliance"]] == ["td"]
    assert out["blocks"]["autonomy"] == {"promotions": [], "demotions": []}
    assert (out["period_start"], out["period_end"]) == ("2026-09-21", "2026-09-27")


def test_monthly_tier_has_the_pl_slot_even_when_finance_is_unbuilt(cur, hotel_uae, frozen):
    out = brief.assemble(cur, hotel_uae, "monthly")
    fin = out["blocks"].get("finance")
    assert fin is None or (fin["kind"] == "pl_pulse" and fin["month"] == "2026-09-01")
    assert (out["period_start"], out["period_end"]) == ("2026-09-01", "2026-09-30")


def test_r_ref_uses_trailing_room_revenue(cur, hotel_uae, frozen):
    assert brief.r_ref_major(cur, hotel_uae, frozen.now().date()) == 20000.0
    for d in range(1, 11):
        cur.execute(
            """INSERT INTO occupancy_daily (hotel_slug, date, rooms_available, room_revenue_minor, currency)
               VALUES (%s, DATE '2026-09-20' + %s, 13, 1000000, 'AED')""",
            (hotel_uae.slug, d),
        )
    assert brief.r_ref_major(cur, hotel_uae, frozen.now().date()) == 20000.0  # 2 x AED 10,000


def test_weekly_autonomy_block_shows_open_promotions_and_recent_demotions(cur, hotel_uae, frozen):
    A.demote(cur, hotel_uae.slug, "core.task", trigger="guest_complaint")
    cur.execute(
        """INSERT INTO action_autonomy_events (hotel_slug, action_type, kind, from_level, to_level, status, at)
           VALUES (%s, 'core.task', 'promotion_suggested', 'suggest', 'approve', 'open', %s) RETURNING id""",
        (hotel_uae.slug, frozen.now()),
    )
    eid = cur.fetchone()["id"]
    block = brief.assemble(cur, hotel_uae, "weekly")["blocks"]["autonomy"]
    assert [p["event_id"] for p in block["promotions"]] == [eid]
    assert "Suggest suggest -> approve" in block["promotions"][0]["text"]
    assert block["demotions"][0]["trigger"] == "guest_complaint"
    assert block["demotions"][0]["text"].endswith("after guest complaint.")
    # The compliance digest leaves the autonomy signals to the autonomy block
    assert all(c["kind"] != "core.autonomy_demoted" for c in brief.assemble(cur, hotel_uae, "weekly")["blocks"]["compliance"])
    # Daily briefs never carry the autonomy block (Canon 18: weekly tier)
    assert "autonomy" not in brief.assemble(cur, hotel_uae, "daily")["blocks"]


def test_detector_cap_counts_profit_guard_detectors_not_the_shared_leak_kind(cur, hotel_uae, frozen, sig):
    # L1, L2 and L3 all file pg.leak.detected; each detector gets its own cap.
    sig("pg:L1:BOOKING:doc:1", section="money_lost", aed=9000, hours=5, kind="pg.leak.detected")
    sig("pg:L1:AGODA:doc:2", section="money_lost", aed=8800, hours=5, kind="pg.leak.detected")
    sig("pg:L2:BOOKING:doc:3", section="money_lost", aed=8600, hours=5, kind="pg.leak.detected")
    sig("inq", section="money_available", aed=50, hours=600, kind="inq.unanswered")
    out = brief.assemble(cur, hotel_uae, "daily")
    titles = [i["title"] for i in out["items"]]
    assert {"pg:L1:BOOKING:doc:1", "pg:L1:AGODA:doc:2", "pg:L2:BOOKING:doc:3"} <= set(titles)
    # The public detector stays the kind (the console's action-type fallback).
    assert {i["detector"] for i in out["items"] if i["section"] == "money_lost"} == {"pg.leak.detected"}


# ─── Final-fix regressions ───


def test_verified_items_rank_but_never_count_as_at_stake(cur, hotel_uae, frozen, sig):
    # A Booking.com credit already received is a win, not money at risk.
    sig("credit", section="verified", cadence="weekly", aed=3020, kind="pg.recovery.verified")
    sig("leak", section="money_lost", cadence="weekly", aed=1000, hours=10)
    out = brief.assemble(cur, hotel_uae, "weekly")
    assert {i["section"] for i in out["items"]} == {"verified", "money_lost"}
    assert out["at_stake_minor"] == 100000
    assert "AED 1,000.00 at stake" in out["headline"]
    assert brief.at_stake_minor([{"section": "verified", "impact_minor": 5}, {"section": "reviews", "impact_minor": None}]) == 0


def test_evidence_links_map_refs_and_subjects_before_falling_back_to_the_brief(cur, hotel_uae, frozen, sig):
    from clefs.core import signals as S

    slug = hotel_uae.slug
    f = sig("f", section="money_lost", aed=10, hours=5,
            evidence=[S.Evidence(kind="row", label="Finding 7", ref={"table": "pfg_findings", "id": 7})])
    q = sig("q", section="money_available", aed=10, hours=5, subject_type="inquiry", subject_id="42")
    m = sig("m", section="service_risk", hours=5)
    bad = sig("bad", section="service_risk", hours=5, subject_type="inquiry", subject_id="../x")
    assert brief._evidence_url(hotel_uae, f, None) == f"/app/{slug}/profit/findings/7"
    assert brief._evidence_url(hotel_uae, q, None) == f"/app/{slug}/inquiries?id=42"
    assert brief._evidence_url(hotel_uae, m, None) == f"/app/{slug}/profit"  # module console, not the brief
    assert brief._evidence_url(hotel_uae, bad, 9) == f"/app/{slug}/brief/actions/9"
    assert brief._evidence_url(hotel_uae, {"id": 3}, None) == f"/app/{slug}/brief?signal=3"


def test_daily_brief_keeps_decision_slots_for_live_approvals(cur, hotel_uae, frozen, sig, act):
    for i in range(3):
        sig(f"l1-{i}", section="service_risk", severity=4, hours=-900, kind=f"svc.k{i}")
    sig("ghc", section="money_available", aed=9000, hours=0, kind="dr.ghc")
    sig("vcc", section="money_lost", aed=3637, hours=0, kind="pg.vcc")
    a = act("Send quote", aed=13573.70, due_hours=200)
    out = brief.assemble(cur, hotel_uae, "daily")
    assert len(out["items"]) == 5
    assert a["id"] in [i["action_id"] for i in out["items"]]


def test_monthly_brief_drops_signals_about_earlier_months_and_labels_desk_todos(cur, hotel_uae, frozen, sig):
    # Brief on 1 Oct 2026: the period is September 2026.
    sig("fin.pl_pulse_ready:2025-08-01", section="verified", cadence="monthly", kind="fin.pl_pulse_ready")
    sig("fin.pl_pulse_ready:2026-09-01", section="verified", cadence="monthly", kind="fin.pl_pulse_ready")
    sig("fin.mapping_needed:2026-09-01", section="service_risk", cadence="monthly", kind="fin.mapping_needed")
    out = brief.assemble(cur, hotel_uae, "monthly")
    keys = [i["title"] for i in out["items"]]
    assert "fin.pl_pulse_ready:2025-08-01" not in keys
    assert "fin.pl_pulse_ready:2026-09-01" in keys and "fin.mapping_needed:2026-09-01" in keys
    todo = next(i for i in out["items"] if i["detector"] == "fin.mapping_needed")
    assert todo["section"] == "service_risk" and todo["section_label"] == "Finance to-do"
    assert brief.section_label("service_risk", "service.l1_or_ack_breach") is None
    assert brief.signal_month({"dedupe_key": "dr.rate_change:2026-12-10"}) is None
