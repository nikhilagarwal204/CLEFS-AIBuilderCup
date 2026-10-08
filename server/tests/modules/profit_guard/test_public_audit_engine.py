"""The public audit's sandbox checks without HTTP (02 spec 3.11): L1 under
both charge scenarios, Agoda's structural commission on taxes, L2's 0.1 pp
boundary, the public classes (TO_CONFIRM and CHECK_CONTRACT never reach
found), status-only L3 with its dispute window (the L14 view), L12 refunds,
second-currency lines, and the Leakage Index publication rule."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from clefs.modules.profit_guard import index as leak_index
from clefs.modules.profit_guard import public_audit as pa

NOW = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)


def row(n: int, **kw) -> dict:
    base = {"_row_no": n, "ota_res_id": f"40000{n:05d}", "guest_name": "Test Guest", "check_in": "2026-10-01",
            "check_out": "2026-10-03", "line_status": "OK", "gross_minor": 100000, "commission_pct": "0.15",
            "commission_minor": 15000, "currency": "AED"}
    return {**base, **kw}


def run(rows, ota="BOOKING", currency="AED"):
    return pa.run_sandbox(rows, ota=ota, currency=currency, now=NOW)


def dets(rep) -> list[tuple[str, int]]:
    return [(f["detector"], f["amount_minor"]) for f in rep["full"]["findings"]]


# ─── L1 and L1b (Booking) and the Agoda structural row ───


def test_l1_reports_both_scenarios_for_booking():
    rep = run([row(1)])
    # AED 1,000 for 2 nights: MF 61.06 + TD 20.00 inside the gross, 15% of 81.06.
    assert dets(rep) == [("L1", 1216), ("L1b", 1216)]
    l1, l1b = rep["full"]["findings"]
    assert (l1["class"], l1["scenario"], l1b["class"], l1b["scenario"]) == (
        "TO_CONFIRM", "fees_configured", "TO_CONFIRM", "fees_not_configured")
    assert [s["result_minor"] for s in l1["calc"]] == [8106, 91894, 8106, 1216]
    zero = {"minor": 0, "amount": "0.00"}
    assert rep["teaser"]["found"] == {"low": zero, "high": zero}


def test_l1_uses_the_printed_commissionable_base():
    # The OTA already took MF and TD out of the base: nothing to find.
    assert dets(run([row(1, commissionable_minor=91894, commission_minor=13784)])) == []


def test_agoda_commission_on_taxes_is_structural_and_outside_the_range():
    rep = run([row(1, commission_pct="0.18", commission_minor=18000)], ota="AGODA")
    # 18% of MF 62.31 + VAT 47.62: Agoda's rate carries no Tourism Dirham
    # (collected at the property, default terms), so none is stripped or
    # counted (review public-audit-agoda-td; it was 22.99 with TD inside).
    assert dets(rep) == [("L1", 1979)]
    assert rep["full"]["findings"][0]["class"] == "STRUCTURAL"
    assert rep["full"]["findings"][0]["calc"][0]["inputs"]["td"] == 0
    assert rep["full"]["structural"]["minor"] == 1979
    assert rep["teaser"]["found"]["high"]["minor"] == 0
    # Informational (it depends on whether MF was collected): not a finding,
    # not a line to confirm either.
    assert (rep["teaser"]["findings"], rep["teaser"]["by_detector"], rep["teaser"]["to_confirm"]) == (0, {}, 0)


# ─── Public classes: what may reach an AED figure ───


def test_l1_is_to_confirm_and_never_in_found():
    # A charged no-show (counted) beside an ordinary line whose gross holds
    # MF + TD only by the sandbox's guess (L1 and L1b).
    rep = run([row(1, line_status="NO_SHOW", commission_minor=7500), row(2)])
    l1 = [f for f in rep["full"]["findings"] if f["detector"] in ("L1", "L1b")]
    assert {f["class"] for f in l1} == {"TO_CONFIRM"} and {f["amount_minor"] for f in l1} == {1216}
    t = rep["teaser"]
    assert t["found"]["low"] == t["found"]["high"] == {"minor": 7500, "amount": "75.00"}
    assert (t["findings"], t["by_detector"], t["to_confirm"]) == (1, {"L3": 1}, 1)
    assert rep["index"]["leak_minor"] == 7500


def test_l2_is_check_contract_and_never_in_found():
    # 18% where the check assumed 15%: a Preferred property can pay that by
    # contract. The line also has L1 and L1b, and is still one line to confirm.
    rep = run([row(1, commission_pct="0.18", commission_minor=18000)])
    l2 = next(f for f in rep["full"]["findings"] if f["detector"] == "L2")
    assert (l2["class"], l2["amount_minor"]) == ("CHECK_CONTRACT", 3000)
    assert l2["title"] == pa.L2_TITLE == ("Above the percentage this check assumed: check your contract and "
                                          "programme terms")
    t = rep["teaser"]
    assert t["found"]["high"] == {"minor": 0, "amount": "0.00"} and (t["findings"], t["by_detector"]) == (0, {})
    assert t["to_confirm"] == 1
    assert (rep["index"]["leak_minor"], rep["index"]["detector_counts"]) == (0, {})


# What the code before the guard (5379f69) stored, word for word: its class,
# its title and, for L2, its calc labels. The legacy fixture below carries
# these, not today's words, so current_report has to rewrite them.
PRE_GUARD = {"L1": ("RECOVERABLE", "Commission on municipality fee and Tourism Dirham"),
             "L1b": ("PREVENTABLE", "Commission you can stop paying on fees"),
             "L2": ("RECOVERABLE", "Commission above the standard 15%")}
PRE_GUARD_L2_STEP = {"Assumed percent": ("Expected percent", "default contract percent"),
                     "Above the assumed percent": ("Overcharge", "(effective - expected) x base")}


def _pre_guard(f: dict) -> dict:
    if f["detector"] not in PRE_GUARD or f["scenario"] == "agoda_tax_inclusive":
        return f
    klass, title = PRE_GUARD[f["detector"]]
    out = {**f, "class": klass, "title": title}
    if f["detector"] == "L2":
        out["calc"] = [{**s, "label": PRE_GUARD_L2_STEP[s["label"]][0], "formula": PRE_GUARD_L2_STEP[s["label"]][1]}
                       if s["label"] in PRE_GUARD_L2_STEP else s for s in f["calc"]]
    return out


def test_a_report_stored_before_the_guard_is_read_under_it():
    """A 30-day token link from before the guard counted L1 in found.high and
    L2 in found, and its L2 row called the amount an "Overcharge" against a
    "default contract percent". current_report re-reads it from its own
    findings, words included, so the old link shows exactly what a new audit
    of the file would."""
    new = run([row(1), row(2, line_status="NO_SHOW", commission_minor=7500),
               row(3, commission_pct="0.18", commission_minor=18000)])
    legacy = {
        "teaser": {**{k: v for k, v in new["teaser"].items() if k != "to_confirm"},
                   "found": {"low": {"minor": 10500, "amount": "105.00"},
                             "high": {"minor": 13175, "amount": "131.75"}},
                   "findings": 4, "by_detector": {"L1": 2, "L2": 1, "L3": 1}},
        "full": {**new["full"], "scenarios": {"fees_configured": {}, "fees_not_configured": {}},
                 "findings": [_pre_guard(f) for f in new["full"]["findings"]]},
        "index": {**new["index"], "leak_minor": 10500,
                  "detector_counts": {"L2": {"n": 1, "minor": 3000}, "L3": {"n": 1, "minor": 7500}}},
    }
    old = str(legacy)
    assert "Commission above the standard 15%" in old and "'Overcharge', 'formula': '(effective" in old
    assert pa.current_report(legacy) == new
    assert "standard" not in str(pa.current_report(legacy)["full"])
    assert new["teaser"]["found"]["high"]["minor"] == 7500 and new["teaser"]["to_confirm"] == 2
    assert pa.current_report(new) is new


# ─── L2 boundary ───


def test_l2_exactly_a_tenth_of_a_point_does_not_flag():
    gross = 1000000   # AED 10,000: 0.1 pp is AED 10, above the tolerance
    at = row(1, gross_minor=gross, commissionable_minor=gross, commission_pct=None, commission_minor=151000)
    above = row(2, gross_minor=gross, commissionable_minor=gross, commission_pct=None, commission_minor=151100)
    assert [d for d in dets(run([at])) if d[0] == "L2"] == []
    assert [d for d in dets(run([above])) if d[0] == "L2"] == [("L2", 1100)]


# ─── L3 status-only and the L14 deadline view ───


def test_l3_from_the_statement_status_with_its_dispute_window():
    open_ = row(1, line_status="NO_SHOW", check_out="2026-10-04", commission_minor=7500)
    closed = row(2, line_status="CANCELLED", check_in="2026-09-20", check_out="2026-09-22", commission_minor=6000)
    rep = run([open_, closed])
    l3 = {f["reservation"]: f for f in rep["full"]["findings"] if f["detector"] == "L3"}
    assert l3["4000000001"]["deadline_at"] == "2026-10-06T00:00:00+04:00" and l3["4000000001"]["window_open"]
    assert l3["4000000002"]["window_open"] is False
    assert l3["4000000001"]["confidence"] == "0.80" and l3["4000000001"]["missing"] == ["fee_collected"]
    assert [d["reservation"] for d in rep["full"]["deadlines"]] == ["4000000001"]
    assert rep["teaser"]["open_deadlines"] == 1 and rep["teaser"]["found"]["low"]["minor"] == 13500


def test_expedia_window_is_the_fourth_of_next_month():
    rep = run([row(1, line_status="NO_SHOW", commission_pct="0.18", commission_minor=9000)], ota="EXPEDIA")
    f = next(f for f in rep["full"]["findings"] if f["detector"] == "L3")
    assert f["deadline_at"] == "2026-11-04T12:00:00+04:00"


# ─── L12 refunds ───


def test_l12_refund_without_the_commission_reduced():
    paid = row(1)
    refund = row(2, ota_res_id="4000000001", gross_minor=-40000, commission_pct=None, commission_minor=0)
    rep = run([paid, refund])
    assert ("L12", 6000) in dets(rep)
    # The booking's own percent (15% of its paid line): nothing assumed.
    l12 = next(f for f in rep["full"]["findings"] if f["detector"] == "L12")
    assert l12["missing"] == [] and "Assumed percent" not in [s["label"] for s in l12["calc"]]
    part = row(3, ota_res_id="4000000001", gross_minor=0, commission_pct=None, commission_minor=-6000)
    assert "L12" not in [d[0] for d in dets(run([paid, refund, part]))]


def test_l12_without_the_bookings_commission_line_is_check_contract():
    """A refund whose stay was billed on an earlier statement: the default
    percent stands in for the amount, so the amount is an assumption, as
    L2's is. It stays in the full report with its calc and is never in the
    headline, the findings count or the index."""
    rep = run([row(1, gross_minor=-40000, commission_pct=None, commission_minor=0)])
    l12 = next(f for f in rep["full"]["findings"] if f["detector"] == "L12")
    assert (l12["class"], l12["amount_minor"], l12["missing"]) == ("CHECK_CONTRACT", 6000, ["contract"])
    assert l12["title"].endswith("at an assumed percentage: check your contract")
    assumed = next(s for s in l12["calc"] if s["label"] == "Assumed percent")
    assert (assumed["formula"], assumed["result"]) == ("a default percentage per OTA, not a market rate", "15")
    assert [s["label"] for s in l12["calc"]] == ["Refunded", "Assumed percent", "Expected reduction", "Shortfall"]
    assert rep["teaser"]["found"]["low"]["minor"] == 0 and rep["teaser"]["findings"] == 0
    assert rep["teaser"]["to_confirm"] == 1


# ─── Currency, unknown OTA, masking ───


def test_second_currency_lines_are_counted_not_audited_and_unknown_ota_skips_rate_checks():
    rep = run([row(1), row(2), row(3, currency="USD", line_status="NO_SHOW")])
    assert rep["full"]["skipped"]["other_currency_lines"] == 1 and rep["teaser"]["lines"] == 2
    unknown = run([row(1, commission_minor=20000), row(2, line_status="NO_SHOW")], ota=None)
    assert [d[0] for d in dets(unknown)] == ["L3"]
    assert unknown["full"]["skipped"]["ota_unknown"] is True
    assert any("not recognised" in n for n in unknown["full"]["notes"])


def test_mask_name_keeps_initials_only():
    assert pa.mask_name("AL MANSOURI/AHMED") == "A. M. A."
    assert pa.mask_name("Smith, John") == "S. J."
    assert pa.mask_name(None) is None and pa.mask_name("123") == "Guest"


# ─── Leakage Index ───


def _sample(cur, ota: str, leak: int, commission: int = 100000) -> None:
    cur.execute(
        "INSERT INTO pfg_index_samples (ota_code, period_month, lines, commission_minor, leak_minor, leak_rate, "
        "currency, detector_counts) VALUES (%s, %s, 10, %s, %s, %s, 'AED', %s)",
        (ota, date(2026, 9, 1), commission, leak, Decimal(leak) / Decimal(commission),
         '{"L3": {"n": 1, "minor": %d}}' % leak if leak else "{}"),
    )


def test_index_is_hidden_below_thirty_samples(cur):
    for i in range(leak_index.MIN_N - 1):
        _sample(cur, "BOOKING", 1000 * i)
    got = leak_index.aggregate(cur)
    assert got["published"] is False and got["n"] == 29 and got["needed"] == 1
    assert "median_leak_rate" not in got and got["methodology"]["publication"].startswith("Published only")


def test_index_publishes_with_methodology_from_thirty(cur):
    for i in range(leak_index.MIN_N):
        _sample(cur, "BOOKING", 1000 * i)          # rates 0.00 .. 0.29
    for i in range(3):
        _sample(cur, "AGODA", 5000)
    got = leak_index.aggregate(cur)
    assert got["published"] is True and got["n"] == 33 and got["claim_id"] == "uae_ota_leakage_index"
    assert got["median_leak_rate"] == "0.1300"      # 17th of 33 sorted rates
    assert [o["ota_code"] for o in got["by_ota"]] == ["BOOKING"] and got["suppressed_otas"] == ["AGODA"]
    booking = got["by_ota"][0]
    assert booking["n"] == 30 and booking["median_leak_rate"] == "0.1450"
    assert got["share_with_leak"] == "0.9697"       # 32 of 33 found something
    assert got["detector_share"] == {"L3": "0.9697"}
    assert set(got["methodology"]) >= {"source", "measure", "assumptions", "limitations"}


def test_index_reads_samples_recorded_before_the_guard_without_l2(cur):
    """A sample recorded before 2026-10-06 counted L2 in its leak, rate and
    detector_counts, and outlives the 30-day purge. The aggregate re-sums it
    under today's detectors, so the published figures never mix measures."""
    for _ in range(leak_index.MIN_N):
        # Pre-guard shape: AED 30.00 of L2 plus AED 75.00 of L3 in 405.00.
        cur.execute(
            "INSERT INTO pfg_index_samples (ota_code, period_month, lines, commission_minor, leak_minor, leak_rate, "
            "currency, detector_counts) VALUES ('BOOKING', %s, 3, 40500, 10500, 0.259259, 'AED', %s)",
            (date(2026, 9, 1), '{"L2": {"n": 1, "minor": 3000}, "L3": {"n": 1, "minor": 7500}}'))
    cur.execute(
        "INSERT INTO pfg_index_samples (ota_code, period_month, lines, commission_minor, leak_minor, leak_rate, "
        "currency, detector_counts) VALUES ('BOOKING', %s, 1, 18000, 3000, 0.166667, 'AED', %s)",
        (date(2026, 9, 1), '{"L2": {"n": 1, "minor": 3000}}'))   # only an L2: no leak today
    got = leak_index.aggregate(cur)
    assert got["published"] is True and got["n"] == 31
    assert got["median_leak_rate"] == "0.1852"          # 75.00 / 405.00, not 105.00 / 405.00
    assert got["share_with_leak"] == "0.9677"           # 30 of 31: the L2-only sample found nothing
    assert got["detector_share"] == {"L3": "0.9677"}    # no L2 key
