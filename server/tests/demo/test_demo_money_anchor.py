"""Demo seed, money part at a later anchor (the CLI defaults to today): the
statement months stay Jun-Aug 2026, every relative plant (cards, payouts,
deadlines, parity, the exempt night, the TD closes) moves with the anchor,
and the plants still hold. An anchor before the August documents is skipped."""

from __future__ import annotations

from datetime import date

from clefs.seed import demo_uae
from clefs.seed.demo_uae import money

SLUG = "demo-t-money-late"
PARTS = ["core", "core_catalog", "core_stays", "money"]


def test_later_anchor_keeps_recall_and_silent_decoys(cur):
    out = demo_uae.seed(cur, SLUG, anchor=date(2026, 10, 20), parts=PARTS, scale=0.05, password="demo-pass-123")
    cur.execute("SELECT fingerprint, class FROM pfg_findings WHERE hotel_slug = %s AND source = 'detector'", (SLUG,))
    rows = {r["fingerprint"]: r["class"] for r in cur.fetchall()}
    plants = [p for p in out["plants"] if p["klass"] != "DECOY"]
    want = {fp for p in plants for fp in money.fingerprints_of(cur, SLUG, p)}
    missing = [p["key"] for p in plants for fp in money.fingerprints_of(cur, SLUG, p) if fp not in rows]
    assert want and missing == []
    assert not {fp for p in out["plants"] for fp in p["decoy_fingerprints"]} & set(rows)
    claims = [fp for fp, klass in rows.items() if klass != "STRUCTURAL"]
    assert sum(fp in want for fp in claims) / len(claims) >= 0.9


def test_anchor_before_the_august_documents_is_skipped(cur):
    out = demo_uae.seed(cur, SLUG, anchor=date(2026, 9, 10), parts=["core", "money"], password="demo-pass-123")
    assert out["parts"]["money"] == {"skipped": "anchor before 2026-09-23"}
