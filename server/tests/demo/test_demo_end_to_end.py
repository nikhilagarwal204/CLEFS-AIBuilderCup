"""The whole demo, end to end: `seed-demo` then `demo-check` through the CLI
(00-PLAN "Demo dataset", 02 spec 9).

Test mode: the seed runs every part at scale 0.1 (about 260 history
reservations instead of 2,600) with the fixed anchor 2026-09-27, so seed plus
check stay under two minutes. Plants use their own dedicated stays, so every
planted case and decoy is still present at that scale; only the history
volume checks (occupancy, ADR, channel mix) cannot reach the spec's ranges,
and `demo-check --scale 0.1` reports those as skipped. A full-scale run is
the manual one in check.py's docstring.

One seed for the file (module-scoped would outlive the per-test rollback), so
the assertions share one test."""

from __future__ import annotations

import json
import time
from datetime import date

from clefs import cli
from clefs.seed.demo_uae import check

ANCHOR = "2026-09-27"
SLUG = "demo-t-e2e"
SCALE = "0.1"
BUDGET_S = 120


def test_seed_then_demo_check_passes(cur, capsys, tmp_path):
    t0 = time.monotonic()
    manifest = tmp_path / "expected_plants.json"
    assert cli.main(["seed-demo", "--hotel", SLUG, "--anchor", ANCHOR, "--scale", SCALE,
                     "--password", "demo-pass-123", "--manifest", str(manifest)]) == 0
    seeded_s = time.monotonic() - t0
    capsys.readouterr()
    data = json.loads(manifest.read_text())
    assert data["hotel"] == SLUG and data["anchor"] == ANCHOR

    # demo-check through the CLI: exit 0 (a miss raises SystemExit(1)).
    assert cli.main(["demo-check", "--hotel", SLUG, "--manifest", str(manifest), "--scale", SCALE]) == 0
    out = capsys.readouterr().out
    total_s = time.monotonic() - t0
    assert f"PASS: 0 miss(es) for {SLUG}" in out, out[-4000:]
    for title in ("Profit Guard", "Service Control", "Amenities", "Reviews", "Guests and journeys",
                  "Inquiry Guard", "Upsell", "Demand Radar", "Visibility", "Finance", "Brief"):
        assert f"== {title}" in out, title
    assert "Signals by section" in out and "Brief top 5 (2026-09-27)" in out and "Verified value" in out
    assert "[MISS]" not in out and "[FIRED]" not in out
    assert total_s < BUDGET_S, f"seed {seeded_s:.0f} s + check {total_s - seeded_s:.0f} s over {BUDGET_S} s"

    # The check can fail: a plant nothing produced is a miss, and a real
    # plant relabelled DECOY is a decoy that fired. Exit status 1 either way.
    plants = data["plants"]
    ghost = dict(next(p for p in plants if p.get("module") is None and p["klass"] != "DECOY"),
                 key="ghost", fingerprints=["no-such-fingerprint"], subjects=[])
    loud = dict(next(p for p in plants if p.get("module") == "service" and p["klass"] != "DECOY"), klass="DECOY")
    bad = tmp_path / "doctored.json"
    bad.write_text(json.dumps({**data, "plants": plants + [ghost, loud]}))
    try:
        cli.main(["demo-check", "--hotel", SLUG, "--manifest", str(bad), "--scale", SCALE, "--no-jobs"])
        raise AssertionError("demo-check passed a doctored manifest")
    except SystemExit as exc:
        assert exc.code == 1
    out = capsys.readouterr().out
    assert "[MISS]" in out and ":ghost" in out and "[FIRED]" in out and f"FAIL: 2 miss(es) for {SLUG}" in out


def test_demo_check_refuses_a_hotel_that_is_not_a_demo(cur, hotel_uae, capsys):
    # Jobs never run at a synthetic anchor on a real hotel, even rolled back.
    rep = check.check_hotel(hotel_uae.slug, anchor=date(2026, 9, 27))
    assert not rep.ok and rep.jobs == []
    assert "not a demo hotel" in check.render(rep)
