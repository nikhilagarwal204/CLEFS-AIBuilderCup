"""Demo seed, growth part: a re-seed rebuilds the same growth data (the
answers rotate deterministically, fixtures are replayed, no wall clock)."""

from __future__ import annotations

from datetime import date

from clefs.seed import demo_uae
from clefs.seed.demo_uae import growth

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-growth-det"
PARTS = ["core", "core_catalog", "core_stays", "growth"]


def _snapshot(cur) -> tuple:
    cur.execute("SELECT stay_date, room_type_code, current_bar_minor, recommended_minor FROM dr_rate_recommendations "
                "WHERE hotel_slug = %s AND status = 'PROPOSED' ORDER BY 1, 2", (SLUG,))
    recs = [tuple(r.values()) for r in cur.fetchall()]
    cur.execute("SELECT prompt_key, engine, repeat_idx, self_mentioned, answer_text FROM vis_ai_runs "
                "WHERE hotel_slug = %s ORDER BY run_at, prompt_key, engine, repeat_idx", (SLUG,))
    runs = [tuple(r.values()) for r in cur.fetchall()]
    cur.execute("SELECT fact_key, claimed_value, occurrences FROM vis_ai_facts WHERE hotel_slug = %s ORDER BY 1",
                (SLUG,))
    return recs, runs, [tuple(r.values()) for r in cur.fetchall()]


def test_reseed_is_identical(cur, monkeypatch):
    monkeypatch.setattr(growth, "SECTIONS", ("demand", "visibility"))
    first = demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.05, password="demo-pass-123")
    snap = _snapshot(cur)
    again = demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.05, password="demo-pass-123")
    assert again["created"] is False
    counts = {k: v for k, v in first["parts"]["growth"].items() if not isinstance(v, dict)}
    assert counts == {k: v for k, v in again["parts"]["growth"].items() if not isinstance(v, dict)}
    assert _snapshot(cur) == snap
    assert demo_uae.manifest(first) == demo_uae.manifest(again)
