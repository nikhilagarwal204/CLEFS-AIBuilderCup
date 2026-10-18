"""Demo seed, operations part: a re-seed gives the same ops manifest bytes
(no database id, random ref or wall-clock value in a plant), wipes and
rebuilds cleanly, and leaves no stale work queued for a worker."""

from __future__ import annotations

import json
from datetime import date

from clefs.seed import demo_uae

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-ops-det"
PARTS = ["core", "core_catalog", "core_stays", "ops"]


def _ops_manifest(result: dict) -> str:
    m = demo_uae.manifest(result)
    m["plants"] = [p for p in m["plants"] if p.get("module") in ("service", "amenities", "reviews", "guests",
                                                                  "inquiries", "upsell")]
    return json.dumps(m, sort_keys=True, ensure_ascii=False, default=str)


def test_reseed_same_manifest_and_counts(cur):
    first = demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.05, password="demo-pass-123")
    cur.execute("SELECT count(*) AS n FROM staff_requests WHERE hotel_slug = %s", (SLUG,))
    n1 = cur.fetchone()["n"]
    again = demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.05, password="demo-pass-123")
    assert again["created"] is False
    assert _ops_manifest(first) == _ops_manifest(again)
    assert first["parts"]["ops"] == again["parts"]["ops"]
    cur.execute("SELECT count(*) AS n FROM staff_requests WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["n"] == n1
    # Nothing past-due is left for a worker: inquiry timers, extraction,
    # acks and their deliveries ran inside the seed at their own instants.
    cur.execute("SELECT name, count(*) AS n FROM job_queue WHERE hotel_slug = %s AND status = 'queued' "
                "AND name LIKE ANY(ARRAY['inquiries.%%', 'core.notify.%%', 'core.actions.%%']) GROUP BY 1", (SLUG,))
    assert {r["name"]: r["n"] for r in cur.fetchall()} == {}
