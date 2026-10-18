"""Demo seed, growth part: the Brief over the whole seed (core, stays,
money, operations, growth). Signals across sections and cadences, the
anchor's daily brief with its top 5 in AED, and the weekly report. One seed
(about a minute at scale 0.1), so the assertions share one test."""

from __future__ import annotations

from datetime import date

from clefs.seed import demo_uae
from clefs.seed.demo_uae import growth

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-growth-brief"
PARTS = ["core", "core_catalog", "core_stays", "money", "ops", "growth"]


def test_brief_has_content_across_sections(cur):
    seeded = demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")
    out = seeded["parts"]["growth"]
    assert all("skipped" not in out[k] for k in ("demand", "visibility", "finance", "brief"))

    # Every growth plant surfaced, no decoy fired, with the other parts' rows present.
    plants = [p for p in seeded["plants"] if p.get("module") in ("demand", "visibility", "finance", "brief")]
    missing = [(p["detector"], p["key"]) for p in plants if p["klass"] != "DECOY" and not growth.surfaced(cur, SLUG, p)]
    fired = [(p["detector"], p["key"]) for p in plants if p["klass"] == "DECOY" and growth.surfaced(cur, SLUG, p)]
    assert missing == [] and fired == []

    # Signals across sections and cadences.
    cur.execute("SELECT count(DISTINCT section) AS s, count(DISTINCT module) AS m, "
                "array_agg(DISTINCT cadence) AS c FROM signals WHERE hotel_slug = %s AND status = 'open'", (SLUG,))
    r = cur.fetchone()
    assert r["s"] >= 3 and r["m"] >= 5 and {"daily", "weekly"} <= set(r["c"])

    # The anchor's daily brief: five ranked items in AED, ordered by score.
    cur.execute("""SELECT i.item_no, i.section, i.impact_minor, i.currency, i.score FROM brf_runs b
                   JOIN brf_items i ON i.run_id = b.id
                   WHERE b.hotel_slug = %s AND b.cadence = 'daily' AND b.brief_date = %s AND i.kind = 'ranked'
                   ORDER BY i.item_no""", (SLUG, ANCHOR))
    items = [dict(x) for x in cur.fetchall()]
    assert len(items) == 5
    assert all((i["currency"] or "AED").strip() == "AED" for i in items)
    assert sum(i["impact_minor"] or 0 for i in items) > 0
    scores = [float(i["score"]) for i in items if i["score"] is not None]
    assert scores == sorted(scores, reverse=True)
    daily = out["brief"]["daily"]
    assert daily["headline"] and "AED" in daily["headline"] and daily["verified"].startswith("Verified results")

    # The weekly runs and the verified-value report behind them.
    assert [w["status"] for w in out["brief"]["weekly"]] == ["generated", "generated"]
    cur.execute("SELECT count(*) AS n FROM brf_reports WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["n"] >= 1
    # Nothing was sent: the seed stores runs, it never delivers them.
    cur.execute("SELECT count(*) AS n FROM brf_runs WHERE hotel_slug = %s AND delivery <> '[]'::jsonb", (SLUG,))
    assert cur.fetchone()["n"] == 0
