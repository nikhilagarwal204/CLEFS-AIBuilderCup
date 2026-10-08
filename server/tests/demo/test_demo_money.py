"""Demo seed, money part: statements, payouts, cards, bank and folio through
core/ingest and the Profit Guard commit handlers, then the detectors
(02 spec 9 "Planted cases": recall 1.0 on plants, no finding on a decoy,
precision >= 0.9, RECOVERABLE total >= 3 x an AED 1,500 subscription).

One seed per test (each ~40 s at scale 0.1), so the assertions are grouped."""

from __future__ import annotations


from collections import Counter
from datetime import date

import pytest

from clefs.seed import demo_uae
from clefs.seed.demo_uae import money

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-money"
PARTS = ["core", "core_catalog", "core_stays", "money"]
SUBSCRIPTION_MINOR = 150000  # AED 1,500 a month


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def _findings(cur) -> list[dict]:
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND source = 'detector'", (SLUG,))
    return [dict(r) for r in cur.fetchall()]


def _tol(expected: int) -> int:
    return max(200, abs(expected) * 2 // 100)


def test_plants_recall_decoys_precision_and_amounts(cur, seeded):
    plants = [p for p in seeded["plants"] if p["klass"] != "DECOY"]
    decoys = [p for p in seeded["plants"] if p["klass"] == "DECOY"]
    rows = _findings(cur)
    by_fp = {r["fingerprint"]: r for r in rows}
    fps = {p["key"]: money.fingerprints_of(cur, SLUG, p) for p in plants}

    # Every detector of the spec table is planted, with its decoys.
    planted = {p["detector"] for p in plants if fps[p["key"]]}
    assert {f"L{i}" for i in range(1, 16)} | {"L1b", "TX1"} | {f"T{i}" for i in range(1, 8)} <= planted
    assert {p["detector"] for p in decoys} >= {"L2", "L3", "L4", "L6", "L7", "L8", "L10", "L11"}

    # Recall 1.0: every planted finding exists.
    missing = [(p["detector"], p["key"]) for p in plants for fp in fps[p["key"]] if fp not in by_fp]
    assert missing == []

    # No finding on a decoy (by fingerprint, and by reservation for the detector).
    assert [fp for p in decoys for fp in p["decoy_fingerprints"] if fp in by_fp] == []
    for p in decoys:
        cur.execute("SELECT id FROM reservations WHERE hotel_slug = %s AND pms_res_id = ANY(%s)",
                    (SLUG, p["decoy_ids"].get("pms_res_ids") or []))
        rids = {r["id"] for r in cur.fetchall()}
        assert not [r for r in rows if r["detector"] == p["detector"] and r["reservation_id"] in rids], p["key"]

    # Precision >= 0.9 over claims (STRUCTURAL records are by design and
    # must be exactly the planted Agoda ones).
    planted_fps = {fp for p in plants for fp in fps[p["key"]]}
    claims = [r for r in rows if r["class"] != "STRUCTURAL"]
    hits = sum(r["fingerprint"] in planted_fps for r in claims)
    assert claims and hits / len(claims) >= 0.9, Counter(r["detector"] for r in claims
                                                          if r["fingerprint"] not in planted_fps)
    structural = {r["fingerprint"] for r in rows if r["class"] == "STRUCTURAL"}
    assert structural == {fp for p in plants if p["klass"] == "STRUCTURAL" for fp in fps[p["key"]]}

    # Expected amounts: per finding within max(AED 2, 2%); the structural
    # entry as a sum.
    off = []
    for p in plants:
        got = sum(int(by_fp[fp]["amount_minor"]) for fp in fps[p["key"]])
        if abs(got - p["expected_minor"]) > _tol(p["expected_minor"]):
            off.append((p["key"], p["expected_minor"], got))
        if fps[p["key"]] and p["klass"] != "STRUCTURAL":
            assert {by_fp[fp]["class"] for fp in fps[p["key"]]} == {p["klass"]}, p["key"]
    assert off == []

    # RECOVERABLE clears 3 x the demo subscription (the pilot threshold).
    recoverable = sum(int(r["amount_minor"]) for r in rows
                      if r["class"] == "RECOVERABLE" and r["currency"] == "AED" and not r["internal_only"])
    assert recoverable >= 3 * SUBSCRIPTION_MINOR, recoverable

    # The credit note settled exactly the two August Booking no-shows.
    recovered = [r for r in rows if r["status"] == "RECOVERED"]
    assert sorted(r["detector"] for r in recovered) == ["L3", "L3"]
    assert all(r["recovered_minor"] * 100 >= r["amount_minor"] * 95 for r in recovered)
