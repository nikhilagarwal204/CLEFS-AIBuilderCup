"""The detector runner end to end: storage by fingerprint (a re-run never
duplicates), the confidence formula and its Beta-prior feedback, grouped
signals and per-item urgency signals, auto-close, the weekly
feedback_tune rules and the jobs (02 spec 3.3-3.4, 3.9)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from clefs.modules.profit_guard import confidence as conf
from clefs.modules.profit_guard import detectors
from clefs.modules.profit_guard.detectors import tuning
from tests.modules.profit_guard.fixtures import pg_data as pg

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def no_shows(cur, slug, n=3, *, commission=15750, doc_no="INV-1", start=20):
    """n PMS no-shows each charged on one Booking invoice."""
    lines = []
    for i in range(start, start + n):
        ota = f"40000000{i:02d}"
        rid = pg.reservation(cur, slug, f"P{i}", ota_id=ota, status="NO_SHOW")
        lines.append({"ota_res_id": ota, "gross_minor": 105000, "commissionable_minor": 105000,
                      "commission_rate": Decimal("0.15"), "commission_minor": commission, "reservation_id": rid,
                      "match_status": "LINKED", "match_score": Decimal("1")})
    return pg.statement(cur, slug, lines, doc_no=doc_no)


def findings(cur, slug, detector=None):
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND (%s::text IS NULL OR detector = %s) ORDER BY id",
                (slug, detector, detector))
    return [dict(r) for r in cur.fetchall()]


def signal(cur, slug, key):
    cur.execute("SELECT * FROM signals WHERE hotel_slug = %s AND dedupe_key = %s", (slug, key))
    r = cur.fetchone()
    return dict(r) if r else None


def dismiss(cur, slug, finding_id, reason, *, at=None):
    cur.execute("UPDATE pfg_findings SET status = 'DISMISSED', dismiss_reason = %s WHERE id = %s", (reason, finding_id))
    cur.execute("INSERT INTO pfg_finding_events (finding_id, hotel_slug, at, from_status, to_status, actor) "
                "VALUES (%s, %s, %s, 'DETECTED', 'DISMISSED', 'owner')", (finding_id, slug, at or NOW))


# ─── Storage and idempotency ───


def test_run_stores_once_and_reruns_update(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s)
    first = detectors.run(cur, s, trigger="manual", detectors=["L3"], run_id="r1")
    assert first["detectors"]["L3"] == {"found": 3, "created": 3, "signalable": 3}
    again = detectors.run(cur, s, trigger="manual", detectors=["L3"], run_id="r2")
    assert again["created"] == 0 and again["findings"] == 3
    rows = findings(cur, s, "L3")
    assert len(rows) == 3 and {(r["first_run_id"], r["last_run_id"]) for r in rows} == {("r1", "r2")}
    assert rows[0]["subject_ref"] == "doc:INV-1|4000000020"
    assert rows[0]["calc"][-1]["label"] == "Confidence"
    cur.execute("SELECT COUNT(*) AS n FROM pfg_finding_events WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 3        # one DETECTED event each, none for the re-run


def test_unknown_detector_is_refused(cur, hotel_uae):
    with pytest.raises(ValueError):
        detectors.run(cur, hotel_uae.slug, detectors=["L99"])


# ─── Confidence ───


def test_confidence_formula_pieces():
    assert conf.completeness(0) == 1 and conf.completeness(2) == Decimal("0.70") and conf.completeness(9) == 0
    assert conf.feedback_from(0, 0) == Decimal("0.8")                       # (4 + 0) / (4 + 1)
    assert conf.feedback_from(2, 0) == Decimal(6) / Decimal(7)
    assert conf.feedback_from(0, 3) == Decimal(4) / Decimal(8)
    # shrunk toward a poor global record: centre 4/10 -> prior a = 2 of 5
    assert conf.feedback_from(0, 0, g_accepted=0, g_fp=5) == Decimal("0.4")
    assert conf.score("0.9", match_score="0.9", missing=1, feedback="0.8") == Decimal("0.551")
    assert conf.score(2, feedback=1) == 1


def test_stored_confidence_and_feedback_from_decisions(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, n=2)
    detectors.run(cur, s, detectors=["L3"])
    rows = findings(cur, s, "L3")
    assert {r["confidence"] for r in rows} == {Decimal("0.720")}          # 0.9 x 1 x 1 x 0.8
    cur.execute("UPDATE pfg_findings SET status = 'ACCEPTED' WHERE id = %s", (rows[0]["id"],))
    detectors.run(cur, s, detectors=["L3"])
    # hotel (4 + 1) / (5 + 1) with the global centre equal (only this hotel): 0.9 x 5/6 = 0.75
    assert findings(cur, s, "L3")[1]["confidence"] == Decimal("0.750")


# ─── Grouped signals ───


def test_one_signal_per_detector_ota_and_statement(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, n=3)
    no_shows(cur, s, n=1, doc_no="INV-2", start=40)
    out = detectors.run(cur, s, detectors=["L3"])
    assert out["signals"]["groups"] == 2
    sig = signal(cur, s, "pg:L3:BOOKING:doc:INV-1")
    assert sig["kind"] == "pg.leak.detected" and sig["section"] == "money_lost" and sig["impact_minor"] == 3 * 15750
    assert sig["title"] == "3 Booking.com no-show commissions, statement INV-1, AED 472.50"
    assert sig["recommended_action"]["type"] == "pg.dispute"  # a registered action type
    assert len(sig["recommended_action"]["params"]["finding_ids"]) == 3
    linked = {r["signal_id"] for r in findings(cur, s, "L3") if r["subject_ref"].startswith("doc:INV-1|")}
    assert linked == {sig["id"]}


def test_below_min_amount_or_floor_is_stored_not_signalled(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, n=1, commission=2000)            # AED 20 < the AED 25 minimum finding
    out = detectors.run(cur, s, detectors=["L3"])
    assert out["detectors"]["L3"] == {"found": 1, "created": 1, "signalable": 0}
    assert signal(cur, s, "pg:L3:BOOKING:doc:INV-1") is None
    assert len(findings(cur, s, "L3")) == 1


def test_a_dismissal_shrinks_then_resolves_the_group(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, n=2)
    detectors.run(cur, s, detectors=["L3"])
    a, b = findings(cur, s, "L3")
    dismiss(cur, s, a["id"], "NOT_WORTH_DISPUTING")
    detectors.refresh_signals_for(cur, s, [a["id"]])
    assert signal(cur, s, "pg:L3:BOOKING:doc:INV-1")["impact_minor"] == 15750
    dismiss(cur, s, b["id"], "ALREADY_HANDLED")
    detectors.refresh_signals_for(cur, s, [b["id"]])
    assert signal(cur, s, "pg:L3:BOOKING:doc:INV-1")["status"] == "resolved"


def test_structural_and_visibility_rows_never_signal(cur, hotel_uae):
    s = hotel_uae.slug
    pg.statement(cur, s, [{"ota_res_id": "987654", "gross_minor": 100000, "commissionable_minor": 100000,
                           "commission_rate": Decimal("0.18"), "commission_minor": 18000,
                           "charges": [{"type": "VAT", "amount_minor": 5000}]}], ota="AGODA", doc_no="AG-1")
    out = detectors.run(cur, s, detectors=["L1"])
    assert out["findings"] == 1 and out["signals"] == {"groups": 0, "items": 0, "resolved": 0, "proposed": 0}
    (row,) = findings(cur, s, "L1")
    assert row["class"] == "STRUCTURAL" and row["subject_ref"].startswith("nosignal|")


# ─── Urgency and auto-close (L8 / L14) ───


def test_l8_card_gets_its_own_signal_at_one_day_and_closes_when_charged(cur, hotel_uae):
    s = hotel_uae.slug
    pg.vcc(cur, s, "V1", deadline=date(2026, 10, 20))
    card = pg.vcc(cur, s, "V2", deadline=date(2026, 10, 3), balance=30000)
    out = detectors.run(cur, s, trigger="deadline")
    assert out["signals"]["groups"] == 1 and out["signals"]["items"] == 1
    v2 = next(r for r in findings(cur, s, "L8") if r["amount_minor"] == 30000)
    item = signal(cur, s, f"pg:L8:BOOKING:item:{v2['fingerprint'][:16]}")
    assert item["kind"] == "pg.vcc.expiring" and item["cadence"] == "daily" and item["severity"] == 4
    assert signal(cur, s, "pg:L8:BOOKING:month:2026-10")["impact_minor"] == 50000
    cur.execute("UPDATE pfg_virtual_cards SET status = 'FULLY_CHARGED', current_balance_minor = 0 WHERE id = %s",
                (card,))
    again = detectors.run(cur, s, trigger="deadline")
    assert again["closed"] == 1
    closed = next(r for r in findings(cur, s, "L8") if r["id"] == v2["id"])
    assert (closed["status"], closed["dismiss_reason"]) == ("DISMISSED", "ALREADY_HANDLED")
    assert signal(cur, s, f"pg:L8:BOOKING:item:{v2['fingerprint'][:16]}")["status"] == "resolved"
    # The charge is reversed: the machine closed it, so the machine reopens it.
    cur.execute("UPDATE pfg_virtual_cards SET status = 'AVAILABLE', current_balance_minor = 30000 WHERE id = %s",
                (card,))
    detectors.run(cur, s, trigger="deadline")
    assert next(r for r in findings(cur, s, "L8") if r["id"] == v2["id"])["status"] == "DETECTED"
    assert signal(cur, s, f"pg:L8:BOOKING:item:{v2['fingerprint'][:16]}")["status"] in ("open", "surfaced")


def test_l14_hands_over_to_l3_after_the_deadline(cur, hotel_uae, clock):
    s = hotel_uae.slug
    cur.execute("INSERT INTO pfg_owner_policies (hotel_slug, confidence_floor) VALUES (%s, 0.3)", (s,))
    pg.reservation(cur, s, "P15", ota_id="4000000150", status="NO_SHOW", check_in=date(2026, 9, 27))
    clock.freeze(datetime(2026, 10, 1, 19, 0, tzinfo=UTC))
    detectors.run(cur, s, trigger="deadline")
    (l14,) = findings(cur, s, "L14")
    assert signal(cur, s, f"pg:L14:BOOKING:item:{l14['fingerprint'][:16]}")["kind"] == "pg.deadline.approaching"
    clock.freeze(datetime(2026, 10, 1, 21, 0, tzinfo=UTC))
    out = detectors.run(cur, s, trigger="deadline")
    assert out["closed"] == 1 and findings(cur, s, "L14")[0]["status"] == "DISMISSED"
    (l3,) = findings(cur, s, "L3")
    assert l3["confidence"] == Decimal("0.306")        # 0.45 x 1 x 0.85 (no statement yet) x 0.8
    assert signal(cur, s, "pg:L3:BOOKING:month:2026-09")["status"] in ("open", "surfaced")
    # The statement then confirms it: same fingerprint, so the same finding.
    rid = l14["reservation_id"]
    pg.statement(cur, s, [{"ota_res_id": "4000000150", "commission_minor": 18252, "reservation_id": rid,
                           "commissionable_minor": 121680, "check_in": date(2026, 9, 27),
                           "check_out": date(2026, 9, 30), "match_status": "LINKED", "match_score": 1}])
    detectors.run(cur, s, detectors=["L3"])
    (same,) = findings(cur, s, "L3")
    assert same["id"] == l3["id"] and same["confidence"] == Decimal("0.720")
    # It moved to the statement's group: the month group it left resolves.
    assert signal(cur, s, "pg:L3:BOOKING:month:2026-09")["status"] == "resolved"
    assert signal(cur, s, "pg:L3:BOOKING:doc:INV-1")["impact_minor"] == 18252


# ─── Weekly feedback tuning ───


def test_too_small_raises_the_minimum_amount(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, n=3, commission=3000)
    detectors.run(cur, s, detectors=["L3"])
    rows = findings(cur, s, "L3")
    for r, amount in zip(rows, (3000, 4000, 5000)):
        cur.execute("UPDATE pfg_findings SET amount_minor = %s WHERE id = %s", (amount, r["id"]))
        dismiss(cur, s, r["id"], "TOO_SMALL")
    out = tuning.feedback_tune(cur, hotel_uae)
    assert out["min_amount_raised"] == {"L3": 4800}           # 1.2 x median 4000
    no_shows(cur, s, n=1, commission=4500, doc_no="INV-9", start=60)
    run = detectors.run(cur, s, detectors=["L3"])
    assert run["detectors"]["L3"]["signalable"] == 0           # 4500 < 4800: stored only
    assert tuning.feedback_tune(cur, hotel_uae)["min_amount_raised"] == {}   # never lowers, never repeats


def test_three_false_positives_suppress_for_14_days(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, n=3)
    detectors.run(cur, s, detectors=["L3"])
    for r in findings(cur, s, "L3"):
        dismiss(cur, s, r["id"], "FALSE_POSITIVE_DATA", at=NOW - timedelta(days=3))
    out = tuning.feedback_tune(cur, hotel_uae)
    assert out["suppressed"] == ["L3"]
    cur.execute("SELECT suppressed_until FROM pfg_detector_tuning WHERE hotel_slug = %s AND detector = 'L3'", (s,))
    assert cur.fetchone()["suppressed_until"] == NOW + timedelta(days=14)
    review = signal(cur, s, "pg:detector_review:L3:2026-10-02")
    assert review["kind"] == "pg.detector.review" and review["section"] == "compliance"
    no_shows(cur, s, n=1, doc_no="INV-9", start=60)
    run = detectors.run(cur, s, detectors=["L3"])
    assert run["detectors"]["L3"]["signalable"] == 0 and run["created"] == 1
    # the three dismissals also pull the feedback down: (4 + 0) / (5 + 3)
    assert findings(cur, s, "L3")[-1]["confidence"] == Decimal("0.450")


def test_two_false_positives_or_old_ones_do_not_suppress(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, n=3)
    detectors.run(cur, s, detectors=["L3"])
    a, b, c = findings(cur, s, "L3")
    dismiss(cur, s, a["id"], "FALSE_POSITIVE_DATA")
    dismiss(cur, s, b["id"], "FALSE_POSITIVE_DATA")
    dismiss(cur, s, c["id"], "FALSE_POSITIVE_DATA", at=NOW - timedelta(days=31))
    assert tuning.feedback_tune(cur, hotel_uae)["suppressed"] == []


def test_contract_exception_opens_a_contract_prompt(cur, hotel_uae):
    s = hotel_uae.slug
    no_shows(cur, s, n=1)
    detectors.run(cur, s, detectors=["L3"])
    (r,) = findings(cur, s, "L3")
    dismiss(cur, s, r["id"], "CONTRACT_EXCEPTION")
    assert tuning.feedback_tune(cur, hotel_uae)["contract_prompts"] == ["BOOKING"]
    sig = signal(cur, s, "pg:contract_review:BOOKING")
    assert sig["kind"] == "pg.config.fix_available" and sig["recommended_action"]["type"] == "core.task"


# ─── Jobs ───

STATEMENT = (
    "Book number,Guest name(s),Check-in,Check-out,Status,Price,Commission %,Commission amount,Currency\n"
    "4000012346,Omar Ali,02/09/2026,04/09/2026,ok,500.00,15%,75.00,AED\n"
)


def test_commit_enqueues_detection(cur, hotel_uae, run_jobs):
    from clefs import files
    from clefs.core.ingest import batches, commit
    from clefs.modules.profit_guard import imports
    from clefs.modules.profit_guard.detectors import jobs as pg_jobs

    imports.register()                # re-registering the plain handlers...
    pg_jobs.install_commit_hook()     # ...never drops the post-commit hook (idempotent)
    s = hotel_uae.slug
    pg.reservation(cur, s, "P2", name="Omar Ali", check_in=date(2026, 9, 2), nights=2, status="NO_SHOW",
                   ota_id="4000012346", rate=25000)
    fid = files.put_file(cur, s, STATEMENT.encode(), filename="stmt.csv", content_type="text/csv",
                         purpose="ingest.upload").id
    b = batches.create_batch(cur, s, file_id=fid, source_code="booking_res_statement")
    commit.commit_batch(cur, s, b["id"])
    cur.execute("SELECT payload FROM job_queue WHERE name = 'pg.detect_on_commit' AND hotel_slug = %s", (s,))
    assert cur.fetchone()["payload"]["batch_id"] == b["id"]
    run_jobs(only="pg.detect_on_commit")
    (f,) = findings(cur, s, "L3")
    assert f["amount_minor"] == 7500 and f["ota_code"] == "BOOKING"


def test_scheduled_deadline_watch_and_registrations(cur, hotel_uae, run_jobs):
    from clefs import jobs

    by = {j.name: j for j in jobs.registered_jobs()}
    assert {"pg.deadline_watch", "pg.nightly_detect", "pg.feedback_tune"} <= set(by)
    assert {by[n].module for n in ("pg.deadline_watch", "pg.nightly_detect")} == {"profit_guard"}
    assert "pg.detect_on_commit" in {t.name for t in jobs.registered_tasks()}
    pg.vcc(cur, hotel_uae.slug, "V1", deadline=date(2026, 10, 20))
    run_jobs(only="pg.deadline_watch", scheduled=True)
    assert [r["detector"] for r in findings(cur, hotel_uae.slug)] == ["L8"]


def test_one_explanation_per_booking(cur, hotel_uae):
    """A no-show charged on a base with fees and at a higher percent is one
    L3 claim, not L3 + L1 + L2 + L5."""
    s = hotel_uae.slug
    no_shows(cur, s, n=1, commission=17000)
    cur.execute("UPDATE pfg_ota_statement_lines SET commission_rate = NULL, charges = %s",
                ('[{"type": "MUNICIPALITYFEE", "amount_minor": 7000}]',))
    out = detectors.run(cur, s, trigger="OTA_STATEMENT")
    assert set(out["detectors"]) == {"L3"}


def test_two_lines_of_one_booking_are_one_finding(cur, hotel_uae):
    """A multi-room booking under one OTA id, printed as two invoice lines."""
    s = hotel_uae.slug
    rid = pg.reservation(cur, s, "P70", ota_id="4000000070", status="NO_SHOW")
    line = {"ota_res_id": "4000000070", "gross_minor": 105000, "commissionable_minor": 105000,
            "commission_rate": Decimal("0.15"), "commission_minor": 15750, "reservation_id": rid,
            "match_status": "LINKED", "match_score": 1}
    pg.statement(cur, s, [line, {**line, "commission_minor": 10000}])
    out = detectors.run(cur, s, detectors=["L3"])
    (f,) = findings(cur, s, "L3")
    assert out["findings"] == 1 and f["amount_minor"] == 25750
    assert f["calc"][-2]["label"] == "Lines combined" and len(f["evidence"]) == 4


# ─── One credit accounting: the detector's netted credit is the baseline ───


def test_credit_already_on_the_booking_is_the_baseline_not_a_recovery(cur, hotel_uae):
    from clefs.modules.profit_guard import ledger

    s = hotel_uae.slug
    no_shows(cur, s, n=1)                                              # 15750 charged on a no-show
    pg.statement(cur, s, [{"ota_res_id": "4000000020", "commission_minor": -5750, "line_status": "ADJUSTMENT"}],
                 doc_no="CN-1", doc_kind="CREDIT_NOTE")
    detectors.run(cur, s, detectors=["L3"], run_id="r1")
    (f,) = findings(cur, s, "L3")
    assert (f["amount_minor"], f["credit_baseline_minor"]) == (10000, 5750)
    assert ledger.auto_verify(cur, s)["credited_minor"] == 0         # netted once, never recovered too
    # A later credit shows in the ledger, and a re-run keeps the claim.
    pg.statement(cur, s, [{"ota_res_id": "4000000020", "commission_minor": -4000, "line_status": "ADJUSTMENT"}],
                 doc_no="CN-2", doc_kind="CREDIT_NOTE")
    detectors.run(cur, s, detectors=["L3"], run_id="r2")
    (f,) = findings(cur, s, "L3")
    assert (f["amount_minor"], f["credit_baseline_minor"]) == (10000, 5750)
    assert f["calc"][-1]["label"] == "Credited after detection"
    out = ledger.auto_verify(cur, s)
    assert out["credited_minor"] == 4000 and out["partial"] == [f["id"]]
    (f,) = findings(cur, s, "L3")
    assert (f["status"], f["recovered_minor"]) == ("DETECTED", 4000)
