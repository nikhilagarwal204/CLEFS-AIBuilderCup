"""Demo seed, stays part: reservations, nights, folio, occupancy, baselines
and pace through core.stays (02 spec 9, "Windows")."""

from __future__ import annotations

from datetime import date, timedelta

from clefs.seed import demo_uae

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-stays"
PARTS = ["core", "core_stays"]


def _seed(cur, scale=0.1):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=scale, password="demo-pass-123")


def test_stays_part_writes_history_forward_and_occupancy(cur):
    out = _seed(cur)["parts"]["core_stays"]
    assert out["reservations"] > 100 and out["cancelled"] > 0 and out["folio_charges"] > 0
    start = ANCHOR - timedelta(days=119)
    cur.execute("SELECT count(*) AS n, min(date) AS lo, max(date) AS hi FROM occupancy_daily "
                "WHERE hotel_slug = %s AND date BETWEEN %s AND %s", (SLUG, start, ANCHOR))
    row = cur.fetchone()
    assert (row["n"], row["lo"], row["hi"]) == (120, start, ANCHOR)
    cur.execute("SELECT status, count(*) AS n FROM reservations WHERE hotel_slug = %s GROUP BY 1", (SLUG,))
    by_status = {r["status"]: r["n"] for r in cur.fetchall()}
    assert {"CHECKED_OUT", "IN_HOUSE", "CONFIRMED", "CANCELLED"} <= set(by_status)
    cur.execute("SELECT count(*) AS n FROM reservations WHERE hotel_slug = %s AND check_in > %s "
                "AND status = 'CONFIRMED' AND booked_at >= %s",
                (SLUG, ANCHOR, demo_uae.DemoCtx(None, SLUG, ANCHOR).now))
    assert cur.fetchone()["n"] == 0  # nothing on the books was booked after the anchor evening
    cur.execute("SELECT max(check_in) AS hi FROM reservations WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["hi"] <= ANCHOR + timedelta(days=125)
    # baselines: 2024-01-01 up to the window, DEMO source; the distortion is excluded
    cur.execute("SELECT count(*) AS n FROM occupancy_daily WHERE hotel_slug = %s AND source = 'DEMO'", (SLUG,))
    assert cur.fetchone()["n"] == (start - date(2024, 1, 1)).days
    cur.execute("SELECT count(*) AS n FROM baseline_exclusions WHERE (hotel_slug IS NULL OR hotel_slug = %s) "
                "AND from_date <= '2026-03-01' AND to_date >= '2026-07-31'", (SLUG,))
    assert cur.fetchone()["n"] >= 1
    cur.execute("SELECT count(DISTINCT snapshot_date) AS n FROM otb_snapshots WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["n"] == 17
