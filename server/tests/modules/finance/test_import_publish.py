"""Upload -> mapping -> checks -> publish (04 spec 6.3-6.7)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.finance import api, imports, mapping, publish, review
from tests.modules.finance.conftest import AUG_LINES, confirm_all, statement_csv

AUG = date(2026, 8, 1)


def _batch(cur, slug, month=AUG):
    cur.execute("""SELECT * FROM fin_import_batches WHERE hotel_slug = %s AND period_month = %s
                   AND status <> 'superseded' ORDER BY id DESC LIMIT 1""", (slug, month))
    return cur.fetchone()


def _stats_aug(cur, slug):
    review.set_overrides(cur, slug, AUG, {"available_rn": 2480, "rooms_rev_pms_minor": 42_410_000}, "AED")


def test_upload_creates_batch_with_rules_and_printed_net_profit(cur, hotel_uae, upload_statement, neighbours):
    res = upload_statement(hotel_uae.slug, statement_csv(), AUG)
    fb = res["commit_result"]["finance_batches"]
    assert len(fb) == 1 and fb[0]["period_month"] == "2026-08-01"
    b = _batch(cur, hotel_uae.slug)
    assert b["source"] == "upload_csv" and b["status"] == "mapping_required"
    assert b["source_net_profit_minor"] == 12_600_000
    lines = review.lines_for_batch(cur, b)
    by_code = {ln["code"]: ln for ln in lines}
    assert by_code["4000"]["amount_minor"] == 42_000_000 and by_code["6000"]["amount_minor"] == -7_000_000
    # deterministic rules proposed (not yet confirmed)
    assert by_code["6700"]["usali_line"] == "eww.other" and not by_code["6700"]["locked"]
    assert by_code["4400"]["usali_line"] == "excluded.tourism_dirham"
    assert by_code["6900"]["usali_line"] == "below_line.depreciation"
    assert by_code["4000"]["usali_line"] is None      # waits for the LLM task
    item = {i["key"]: i for i in b["checks"]["items"]}["reconciliation"]
    assert item["status"] == "fail" and item["blocking"]  # unmapped accounts block


def test_publish_golden_with_flow_through(cur, hotel_uae, upload_statement, neighbours):
    slug = hotel_uae.slug
    cur.execute("""INSERT INTO fin_monthly_summary (hotel_slug, period_month, currency, total_revenue_minor, gop_minor,
                       arn, published_at) VALUES (%s, '2025-08-01', 'AED', 70000000, 28000000, 2480, now())""", (slug,))
    upload_statement(slug, statement_csv(), AUG)
    b = _batch(cur, slug)
    confirm_all(cur, slug, b["id"], AUG_LINES)
    _stats_aug(cur, slug)
    b = review.refresh(cur, slug, b["id"])
    assert b["status"] == "review", b["checks"]
    items = {i["key"]: i for i in b["checks"]["items"]}
    assert items["reconciliation"]["status"] == "pass"
    assert items["pms_rooms"]["status"] == "pass" and items["pms_rooms"]["data"]["diff_pct"] == "-0.0097"
    assert items["pass_through"]["status"] == "pass"
    out = publish.publish(cur, slug, b["id"], actor=None, actor_id=None)
    s = out["summary"]
    assert s["gop_minor"] == 17_400_000 and s["gop_margin"] == Decimal("0.3283")
    assert s["goppar_minor"] == 7_016 and s["trevpar_minor"] == 21_371 and s["revpar_minor"] == 16_935
    assert s["labour_pct"] == Decimal("0.2698") and s["ebitda_minor"] == 16_800_000
    assert s["net_profit_minor"] == 12_600_000
    assert s["flow_through"] == Decimal("0.6235") and s["flow_through_basis"] == "yoy"
    assert out["batch"]["status"] == "published" and out["batch"]["map_versions"]
    m = api.monthly_summary(cur, slug, month=AUG)
    assert m["gop"] == "174000.00" and m["gop_margin_pct"] == "32.83"


def test_publish_refused_until_review(cur, hotel_uae, upload_statement, neighbours):
    upload_statement(hotel_uae.slug, statement_csv(), AUG)
    b = _batch(cur, hotel_uae.slug)
    with pytest.raises(ClefsError) as e:
        publish.publish(cur, hotel_uae.slug, b["id"], actor=None, actor_id=None)
    assert e.value.code == "not_publishable"


def test_reconciliation_mismatch_blocks(cur, hotel_uae, upload_statement, neighbours):
    rows = [r if r[1] != "Net Profit" else ("", "Net Profit", 120000, None)
            for r in __import__("tests.modules.finance.conftest", fromlist=["AUG_ROWS"]).AUG_ROWS]
    upload_statement(hotel_uae.slug, statement_csv(rows), AUG)
    b = _batch(cur, hotel_uae.slug)
    confirm_all(cur, hotel_uae.slug, b["id"], AUG_LINES)
    b = review.refresh(cur, hotel_uae.slug, b["id"])
    rec = {i["key"]: i for i in b["checks"]["items"]}["reconciliation"]
    assert b["status"] == "checks_failed" and rec["status"] == "fail" and rec["data"]["diff_minor"] == 600_000


def test_reconciliation_adds_back_uncancelled_pass_through(cur, hotel_uae, upload_statement, neighbours):
    """Regression: Tourism Dirham collected 14,880 and remitted 14,000 (timing)
    leaves 880 of pass-through in the printed Net Profit. Once both accounts are
    (correctly) mapped to excluded.*, mapped + excluded must reconcile to the
    source; comparing mapped alone would block the batch forever."""
    from tests.modules.finance.conftest import AUG_ROWS

    rows = [("6920", "Tourism Dirham Remitted", 14000, "excluded.tourism_dirham") if r[0] == "6920" else
            ("", "Net Profit", 126880, None) if r[1] == "Net Profit" else r for r in AUG_ROWS]
    slug = hotel_uae.slug
    upload_statement(slug, statement_csv(rows), AUG)
    b = _batch(cur, slug)
    assert b["source_net_profit_minor"] == 12_688_000
    confirm_all(cur, slug, b["id"], AUG_LINES)
    _stats_aug(cur, slug)
    b = review.refresh(cur, slug, b["id"])
    rec = {i["key"]: i for i in b["checks"]["items"]}["reconciliation"]
    assert rec["status"] == "pass" and not rec["blocking"], rec
    assert rec["data"]["mapped_minor"] == 12_600_000 and rec["data"]["reconciling_minor"] == 88_000
    assert rec["data"]["diff_minor"] == 0 and "excluded" in rec["detail"]
    assert b["status"] == "review" and b["checks"]["reconciling_minor"] == 88_000
    out = publish.publish(cur, slug, b["id"], actor=None, actor_id=None)
    assert out["summary"]["net_profit_minor"] == 12_600_000  # pass-through never enters the P&L


def test_reconciliation_still_catches_a_real_gap_beside_pass_through(cur, hotel_uae, upload_statement, neighbours):
    """The add-back must not hide a genuine mismatch: same uncancelled TD, but
    the printed Net Profit is 5,000 off."""
    from tests.modules.finance.conftest import AUG_ROWS

    rows = [("6920", "Tourism Dirham Remitted", 14000, "excluded.tourism_dirham") if r[0] == "6920" else
            ("", "Net Profit", 131880, None) if r[1] == "Net Profit" else r for r in AUG_ROWS]
    upload_statement(hotel_uae.slug, statement_csv(rows), AUG)
    b = _batch(cur, hotel_uae.slug)
    confirm_all(cur, hotel_uae.slug, b["id"], AUG_LINES)
    b = review.refresh(cur, hotel_uae.slug, b["id"])
    rec = {i["key"]: i for i in b["checks"]["items"]}["reconciliation"]
    assert rec["status"] == "fail" and rec["blocking"] and rec["data"]["diff_minor"] == -500_000
    assert b["status"] == "checks_failed"


def test_pass_through_mapped_inside_pl_fails(cur, hotel_uae, upload_statement, neighbours):
    upload_statement(hotel_uae.slug, statement_csv(), AUG)
    b = _batch(cur, hotel_uae.slug)
    lines = dict(AUG_LINES, **{"4400": "misc.income", "6920": "ag.other"})
    confirm_all(cur, hotel_uae.slug, b["id"], lines)
    b = review.refresh(cur, hotel_uae.slug, b["id"])
    pt = {i["key"]: i for i in b["checks"]["items"]}["pass_through"]
    assert pt["status"] == "fail" and len(pt["data"]["accounts"]) == 2 and b["status"] == "checks_failed"


def test_below_gop_guard_needs_override(cur, hotel_uae, upload_statement, neighbours):
    upload_statement(hotel_uae.slug, statement_csv(), AUG)
    b = _batch(cur, hotel_uae.slug)
    cur.execute("SELECT id FROM fin_coa_accounts WHERE hotel_slug = %s AND code = '6900'", (hotel_uae.slug,))
    dep = cur.fetchone()["id"]
    with pytest.raises(ClefsError) as e:
        mapping.confirm_one(cur, hotel_uae.slug, dep, actor_id=None, usali_line="pom.other")
    assert e.value.code == "override_reason_required"
    row = mapping.confirm_one(cur, hotel_uae.slug, dep, actor_id=None, usali_line="pom.other",
                              override_reason="equipment lease booked as depreciation by the accountant")
    assert row["locked"] and row["override_reason"]


def test_parse_amount_and_header_month():
    assert imports.parse_amount("(1,234.50)", "AED") == -123_450
    assert imports.parse_amount("AED 5,000", "AED") == 500_000
    assert imports.header_month("Aug 2026") == AUG and imports.header_month("2026-08") == AUG
    assert imports.header_month("Amount") is None
