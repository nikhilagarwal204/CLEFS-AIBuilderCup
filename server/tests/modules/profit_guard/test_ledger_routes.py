"""Findings, ledger, deadline and scorecard routes; the metrics this part
registers; the auto-verify commit hook and the scheduled jobs."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from clefs.modules.profit_guard import disputes, economics, jobs as pg_jobs, ledger, tax_checks
from tests.foundation.route_walk import iter_routes
from tests.modules.profit_guard.test_ledger import credit_note, finding, get

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
ROUTERS = (ledger.router, disputes.router, tax_checks.router, economics.router)


@pytest.fixture(scope="module")
def app():
    from clefs.app import create_app

    a = create_app()
    have = {getattr(r, "path", "") for r in iter_routes(a.routes)}
    for r in ROUTERS:
        # Mounted here until module.py lists these routers (lane FOUNDATION-REQUEST).
        if not any(getattr(x, "path", "") in have for x in iter_routes(r.routes)):
            a.include_router(r)
    return a


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


# ─── Routes ───


def test_findings_list_detail_transition_and_dismiss(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    a = finding(cur, s, "4000000031", amount=10000, deadline=NOW + timedelta(hours=10))
    b = finding(cur, s, "4000000032", amount=5000, detector="L2")
    finding(cur, s, "4000000033", amount=700, detector="INQ")
    cur.execute("UPDATE pfg_findings SET internal_only = TRUE WHERE detector = 'INQ'")
    fin = client_as("finance")
    rows = fin.get(f"/hotels/{s}/profit-guard/findings").json()
    assert {r["id"] for r in rows} == {a, b}                      # internal-only never lists
    assert fin.get(f"/hotels/{s}/profit-guard/findings?detector=l2").json()[0]["id"] == b
    one = fin.get(f"/hotels/{s}/profit-guard/findings/{a}").json()
    assert one["amount"] == "100.00" and one["booking_ref"] == "4000000031" and one["events"][0]["to_status"] == "DETECTED"
    r = fin.post(f"/hotels/{s}/profit-guard/findings/{a}/transition", json={"to": "accepted", "note": "agree"})
    assert r.status_code == 200 and r.json()["status"] == "ACCEPTED"
    r = fin.post(f"/hotels/{s}/profit-guard/findings/{a}/transition", json={"to": "REJECTED"})
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"
    r = fin.post(f"/hotels/{s}/profit-guard/findings/{b}/dismiss", json={"reason": "CONTRACT_EXCEPTION"})
    assert r.status_code == 200 and r.json()["dismiss_reason"] == "CONTRACT_EXCEPTION"
    r = fin.post(f"/hotels/{s}/profit-guard/findings/{a}/transition", json={"to": "RECOVERED", "amount": "90.00"})
    assert r.json()["status"] == "RECOVERED" and r.json()["recovered_minor"] == 9000
    assert client_as("revenue").get(f"/hotels/{s}/profit-guard/findings").status_code == 403


def test_ledger_deadlines_scorecard_and_verify(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    finding(cur, s, "4000000041", amount=10000, deadline=NOW + timedelta(hours=30))
    b = finding(cur, s, "4000000042", amount=20000, status="DISPUTED")
    credit_note(cur, s, "4000000042", 20000)
    owner = client_as("owner")
    r = owner.post(f"/hotels/{s}/profit-guard/findings/verify")
    assert r.status_code == 200 and r.json()["recovered"] == [b]
    led = owner.get(f"/hotels/{s}/profit-guard/ledger").json()
    assert led["recovered"] == "200.00" and led["found"] == "300.00" and led["due_72h_count"] == 1 and led["due_72h"] == "100.00"
    assert led["events"][0]["channel"] == "credit" and led["events"][0]["amount"] == "200.00"
    dl = owner.get(f"/hotels/{s}/profit-guard/deadlines?days=2").json()
    assert len(dl) == 1 and dl[0]["hours_left"] == 30.0
    card = owner.get(f"/hotels/{s}/profit-guard/pilot-scorecard").json()
    assert card["valid_findings"] == 1 and card["success"] is False


def test_td_ledger_routes_and_export(cur, client_as, hotel_uae):
    from tests.modules.profit_guard.test_tax_checks import SEP, TD, stay

    s = hotel_uae.slug
    stay(cur, s, "P1", rooms=2)
    fin = client_as("finance")
    r = fin.get(f"/hotels/{s}/profit-guard/tax/td-ledger?month=2026-09")
    assert r.status_code == 200, r.text
    m = r.json()["months"][0]
    assert (m["month"], m["td_expected"], m["td_collected"], m["status"]) == ("2026-09", "90.00", "45.00", "OPEN")
    r = fin.put(f"/hotels/{s}/profit-guard/tax/td-ledger/2026-09",
                json={"amount": "45.00", "remitted_on": "2026-10-12", "reference": "DET-1"})
    assert r.status_code == 200 and r.json()["status"] == "REMITTED" and r.json()["gap"] == "0.00"
    assert fin.put(f"/hotels/{s}/profit-guard/tax/td-ledger/Sept", json={"amount": "1", "remitted_on": "2026-10-12"}
                   ).json()["code"] == "invalid_month"
    csv = fin.get(f"/hotels/{s}/profit-guard/tax/td-export.csv?month=2026-09")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"] and csv.text.splitlines()[-1].startswith("TOTAL")
    run = client_as("owner").post(f"/hotels/{s}/profit-guard/tax/checks/run", json={"months": ["2026-09"]}).json()
    assert run["by_detector"] == {"T1": 1} and run["months"] == [SEP.isoformat()]
    assert TD == 1500


def test_channel_economics_monthly_routes(cur, client_as, hotel_uae):
    from tests.modules.profit_guard.test_economics import booking_stay

    s = hotel_uae.slug
    booking_stay(cur, s)
    owner = client_as("owner")
    r = owner.post(f"/hotels/{s}/profit-guard/channel-economics/rollup", json={"months": ["2026-09"]})
    assert r.status_code == 200 and r.json() == {"2026-09": 1}
    out = owner.get(f"/hotels/{s}/profit-guard/channel-economics/monthly?from=2026-09&to=2026-09").json()
    b = out["rows"][0]
    assert b["channel"] == "BOOKING" and b["commission"] == "201.40" and b["waterfall"][-1]["label"] == "Net"
    assert owner.get(f"/hotels/{s}/profit-guard/channel-economics/monthly?from=2026-10&to=2026-01").json()["code"] \
        == "invalid_range"


# ─── Metrics ───


def test_metrics_compile_and_sum_the_ledger(cur, hotel_uae):
    from clefs.core import metrics

    s = hotel_uae.slug
    b = finding(cur, s, "4000000042", amount=20000, status="DISPUTED")
    credit_note(cur, s, "4000000042", 20000)
    ledger.auto_verify(cur, s)
    keys = [m.key for m in metrics.registered_metrics() if m.key.startswith(("pg.", "tax."))]
    assert {"pg.leakage_detected_aed", "pg.leakage_recovered_aed", "pg.recovery_rate", "pg.open_findings",
            "pg.commission_paid_aed", "pg.effective_commission_pct", "pg.commission_on_tax_aed", "pg.net_adr_aed",
            "pg.gross_adr_aed", "pg.net_retention_pct", "pg.vcc_uncollected_aed", "tax.td_collected_aed",
            "tax.td_remitted_aed"} <= set(keys)
    for k in keys:
        m = metrics._metrics[k] if hasattr(metrics, "_metrics") else None
        q = metrics.MetricQuery(metric=k, start=date(2026, 9, 1), end=date(2026, 10, 31), grain="month",
                                group_by=[d.key for d in (m.dimensions[:1] if m else ())])
        sql, params = metrics.compile_metric(hotel_uae, q)
        cur.execute("EXPLAIN " + sql, params)
    sql, params = metrics.compile_metric(hotel_uae, metrics.MetricQuery(
        metric="pg.leakage_recovered_aed", start=date(2026, 10, 1), end=date(2026, 10, 31)))
    cur.execute(sql, params)
    assert int(cur.fetchone()["value"]) == 20000
    assert get(cur, b)["status"] == "RECOVERED"


# ─── Jobs ───


def test_commit_hook_enqueues_auto_verify(cur, hotel_uae):
    from clefs.core.ingest import post_commit_hook_names

    pg_jobs.install_commit_hook()
    assert "clefs.modules.profit_guard.jobs.enqueue_verify" in post_commit_hook_names("CREDIT_NOTE")
    assert pg_jobs.install_commit_hook() == []                                    # idempotent
    jid = pg_jobs.enqueue_verify(cur, {"id": 123, "hotel_slug": hotel_uae.slug, "kind": "CREDIT_NOTE"},
                                 {"ota_code": "BOOKING"})
    cur.execute("SELECT name, payload FROM job_queue WHERE id = %s", (jid,))
    row = cur.fetchone()
    assert row["name"] == "pg.auto_verify" and row["payload"]["ota_code"] == "BOOKING"
    assert pg_jobs.enqueue_verify(cur, {"id": 124, "hotel_slug": None, "kind": "CREDIT_NOTE"}) is None
    assert pg_jobs.enqueue_verify(cur, {"id": 125, "hotel_slug": hotel_uae.slug, "kind": "BANK"}) is None


def test_scheduled_jobs_are_registered_with_their_prefixes():
    from clefs import jobs

    names = {j.name for j in jobs.registered_jobs()}
    assert {"pg.auto_verify", "pg.channel_economics_rollup", "pg.dispute_followup", "tax.td_checks",
            "tax.td_deadline_watch", "tax.td_ledger_rollup", "tax.td_remit_reminder",
            "tax.unverified_rule_banner"} <= names
    assert "pg.auto_verify" in {t.name for t in jobs.registered_tasks()}


def test_remit_reminder_job_acts_only_on_the_10th_and_14th(cur, hotel_uae, clock):
    from types import SimpleNamespace

    from tests.modules.profit_guard.test_tax_checks import stay

    s = hotel_uae.slug
    stay(cur, s, "P1")
    ctx = SimpleNamespace(cur=cur, hotel=hotel_uae, hotel_slug=s)
    clock.freeze(datetime(2026, 10, 11, 6, 0, tzinfo=UTC))
    assert "skipped" in pg_jobs.td_remit_reminder(ctx)
    clock.freeze(datetime(2026, 10, 14, 6, 0, tzinfo=UTC))
    assert pg_jobs.td_remit_reminder(ctx) == {"reminded": ["2026-09"]}


def test_unverified_rule_banner(cur, hotel_uae):
    from types import SimpleNamespace

    ctx = SimpleNamespace(cur=cur, hotel=hotel_uae, hotel_slug=hotel_uae.slug)
    out = pg_jobs.unverified_rule_banner(ctx)
    cur.execute("SELECT status FROM signals WHERE hotel_slug = %s AND dedupe_key = 'tax:rule:unverified'",
                (hotel_uae.slug,))
    row = cur.fetchone()
    assert (row is not None) == out["signal"]
