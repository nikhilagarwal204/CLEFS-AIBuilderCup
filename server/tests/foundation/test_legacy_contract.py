"""
Golden legacy contract: every pre-refactor endpoint keeps its path, method,
status code, JSON shape and business outcome.

golden/legacy_contract.json was captured from server/main.py@aaf5cf4 (the
monolith) by golden/capture_legacy.py, before any code moved. The scenario runs
anonymously against a seeded Indian hotel, so it also proves that
legacy_open_access keeps today's dashboards and guest pages working without login.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.foundation.legacy_scenario import LEGACY_SLUG, run_scenario

GOLDEN = json.loads((Path(__file__).parent / "golden" / "legacy_contract.json").read_text())["steps"]


# The scenario runs once, inside whichever test first asks for it (one test
# transaction, rolled back after), and every golden step is then its own test.
_OBSERVED: dict = {}


@pytest.fixture
def scenario(client):
    if not _OBSERVED:
        _OBSERVED.update(run_scenario(client, LEGACY_SLUG))
    return _OBSERVED


def test_scenario_covers_every_golden_step(scenario):
    assert set(scenario) == set(GOLDEN)


def _template(path: str) -> str:
    """Path with the slug and generated ids (which vary run to run) abstracted."""
    return re.sub(r"/\d+(?=/|$)", "/{id}", path.replace(LEGACY_SLUG, "{slug}"))


@pytest.mark.parametrize("step", sorted(GOLDEN))
def test_step_matches_golden(scenario, step):
    want = GOLDEN[step]
    got = scenario[step]
    assert (got["method"], _template(got["path"])) == (want["method"], _template(want["path"]))
    assert got["status"] == want["status"], f"{step}: status {got['status']} != {want['status']}"
    assert got["shape"] == want["shape"], f"{step}: shape changed\n got={got['shape']}\nwant={want['shape']}"
    if "body" in want:
        assert got.get("body") == want["body"], f"{step}: body changed"
    if "values" in want:
        assert got["values"] == want["values"], f"{step}: values changed"
    if "count" in want:
        assert got["count"] == want["count"]


def test_legacy_hotel_projection_hides_new_columns(client):
    """GET /hotels/{slug} is public: it must never expose settings added by 0003+."""
    body = client.get(f"/hotels/{LEGACY_SLUG}").json()
    for secret in ("tax_profile", "whatsapp_phone_number_id", "legacy_open_access", "inbound_email_alias", "front_desk_phone"):
        assert secret not in body


def test_staff_list_never_exposes_pin_hash(client, cur):
    client.post(f"/hotels/{LEGACY_SLUG}/staff/seed")
    cur.execute("UPDATE staff_members SET pin_hash = 'scrypt$x' WHERE hotel_slug = %s", (LEGACY_SLUG,))
    for row in client.get(f"/hotels/{LEGACY_SLUG}/staff").json():
        assert "pin_hash" not in row and "pin_failed_count" not in row
    analytics = client.get(f"/hotels/{LEGACY_SLUG}/staff-analytics").json()
    assert all("pin_hash" not in s for s in analytics["staff"])


def test_legacy_stream_opens_without_ticket(client, monkeypatch):
    # TestClient buffers a response until the app finishes, so the stream is
    # given a short life here; in production it lives STREAM_MAX_S.
    from clefs import events

    monkeypatch.setattr(events, "STREAM_MAX_S", 0.2)
    resp = client.get(f"/hotels/{LEGACY_SLUG}/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.text.startswith(": keepalive")
