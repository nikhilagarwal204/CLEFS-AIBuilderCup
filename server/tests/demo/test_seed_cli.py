"""seed-demo and demo-check commands (python -m clefs.cli)."""

from __future__ import annotations

import json

import pytest

from clefs import cli
from clefs.seed.demo_uae import _check

SLUG = "demo-t-cli"


def test_seed_demo_prints_logins_pins_and_a_pairing_code(cur, capsys, tmp_path):
    manifest = tmp_path / "plants.json"
    rc = cli.main(["seed-demo", "--hotel", SLUG, "--anchor", "2026-09-27", "--scale", "0.05",
                   "--password", "demo-pass-123", "--manifest", str(manifest)])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"owner+{SLUG}@example.com" in out and "demo-pass-123" in out
    assert "pair this browser as a device" in out
    # The deck's demo: the keyed Room 204 link (the printed QR card) and /try.
    assert f"/stay/{SLUG}/204?k=" in out and "/try" in out
    data = json.loads(manifest.read_text())
    assert data["hotel"] == SLUG and data["anchor"] == "2026-09-27" and isinstance(data["plants"], list)
    # a second run is the same hotel, rebuilt
    assert cli.main(["seed-demo", "--hotel", SLUG, "--anchor", "2026-09-27", "--scale", "0.05",
                     "--password", "demo-pass-123", "--no-manifest"]) == 0
    assert "created=False" in capsys.readouterr().out


def test_seed_demo_refuses_a_real_hotel(cur, hotel_uae, capsys):
    assert cli.main(["seed-demo", "--hotel", hotel_uae.slug, "--anchor", "2026-09-27", "--parts", "core",
                     "--no-manifest"]) == 1
    assert "not a demo hotel" in capsys.readouterr().err


def test_demo_check_reports_each_spec_check(cur, capsys):
    assert cli.main(["seed-demo", "--hotel", SLUG, "--anchor", "2026-09-27", "--scale", "0.05",
                     "--password", "demo-pass-123", "--no-manifest"]) == 0
    capsys.readouterr()
    results = {name: ok for name, ok, _d in _check.run_checks(cur, SLUG)}
    for name in ("hotel is a demo hotel", "80 rooms on 8 floors, 10 family units", "18 demo staff with PINs",
                 "owner, gm, finance, revenue, front desk, duty manager users", "120 history days",
                 "forward on the books",
                 "pace snapshots", "2024-2025 baselines"):
        assert results[name], name
    # at 5% volume the occupancy check must fail loudly, not pass by accident
    assert results["history occupancy 55-66%"] is False
    with pytest.raises(SystemExit):
        cli.main(["demo-check", "--hotel", SLUG])
    assert "[FAIL] history occupancy" in capsys.readouterr().out


# ─── The Tokyo demo hotel (the default) ───


def test_seed_demo_builds_the_tokyo_hotel_by_default(cur, capsys):
    from clefs.seed import variants

    assert variants.DEFAULT_HOTEL == "aozora-shinjuku"
    assert cli.main(["seed-demo", "--anchor", "2026-10-21", "--parts", "core", "--password", "demo-pass-123",
                     "--no-manifest"]) == 0
    out = capsys.readouterr().out
    assert "seeded aozora-shinjuku anchor=2026-10-21 as_of=2026-10-21T21:00:00+09:00" in out
    assert "variant=demo_jp" in out
    assert "dutymanager+aozora-shinjuku@example.com" in out and "Yuki Sato" in out
    cur.execute("SELECT name, country, timezone FROM hotels WHERE slug = 'aozora-shinjuku'")
    assert dict(cur.fetchone()) == {"name": "Hotel Aozora Shinjuku", "country": "JP", "timezone": "Asia/Tokyo"}
    # The UAE demo hotel is still one flag away.
    assert cli.main(["seed-demo", "--hotel", "demo-deira-crest", "--anchor", "2026-09-27", "--parts", "core",
                     "--password", "demo-pass-123", "--no-manifest"]) == 0
    assert "variant=demo_uae" in capsys.readouterr().out
    cur.execute("SELECT name, currency FROM hotels WHERE slug = 'demo-deira-crest'")
    assert dict(cur.fetchone()) == {"name": "Al Rigga Crest Hotel", "currency": "AED"}


def test_variant_flag_and_a_reseed_keeps_the_hotels_variant(cur, capsys, tmp_path):
    slug = "demo-t-jpcli"
    manifest = tmp_path / "jp_plants.json"
    assert cli.main(["seed-demo", "--hotel", slug, "--variant", "jp", "--anchor", "2026-10-21", "--scale", "0.05",
                     "--password", "demo-pass-123", "--manifest", str(manifest)]) == 0
    assert "variant=demo_jp" in capsys.readouterr().out
    assert json.loads(manifest.read_text())["hotel"] == slug
    # demo-check reads it as the Tokyo hotel it is (its country says so).
    assert cli.main(["demo-check", "--hotel", slug, "--manifest", str(manifest), "--scale", "0.05"]) == 0
    out = capsys.readouterr().out
    assert "21:00 Asia/Tokyo" in out and f"PASS: 0 miss(es) for {slug}" in out
    for title in ("Property and stays", "AI Builder Cup deck cast", "Service Control", "Amenities", "Upsell"):
        assert f"== {title}" in out, title
    assert "== Profit Guard" not in out
    assert cli.main(["seed-demo", "--hotel", slug, "--anchor", "2026-10-21", "--parts", "core",
                     "--password", "demo-pass-123", "--no-manifest"]) == 0
    assert "variant=demo_jp" in capsys.readouterr().out
