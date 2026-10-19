"""
Concierge test fixtures.

hotel_uae is created after migration 0204, so it has no cc_live row and
its concierge starts NOT live (live.py: only pre-rebuild hotels are
grandfathered, by that migration). Most tests exercise a live concierge, so this override
switches it on directly in cc_live, the state a hotel reaches
after passing the readiness gate. The gate itself is tested in
test_live_gate.py, which unsets the switch again.
"""

from __future__ import annotations

import pytest

from clefs.modules.concierge import mood as _mood

_REAL_SCORE = _mood.score


def switch_live(cur, slug: str, live: bool | None) -> None:
    """Write concierge.live straight into cc_live with no gate (fixtures
    and tests only). None deletes the row: never switched."""
    if live is None:
        cur.execute("DELETE FROM cc_live WHERE hotel_slug = %s", (slug,))
        return
    cur.execute(
        "INSERT INTO cc_live (hotel_slug, live) VALUES (%s, %s) ON CONFLICT (hotel_slug) DO UPDATE SET live = EXCLUDED.live",
        (slug, live),
    )


@pytest.fixture
def hotel_uae(hotel_uae, cur):
    switch_live(cur, hotel_uae.slug, True)
    return hotel_uae


@pytest.fixture(autouse=True)
def quiet_mood(monkeypatch):
    """Mood scoring (mood.py) is one more model call per guest turn, made
    beside the reply: under fake_ai it would take answers queued for the
    reply model, in no fixed order. It is off here; the tests about it
    turn it back on with the mood_scoring fixture."""
    monkeypatch.setattr(_mood, "score", lambda *a, **kw: None)


@pytest.fixture
def mood_scoring(monkeypatch, quiet_mood):
    """The real mood scorer (answer it per task: see test_mood.by_task)."""
    monkeypatch.setattr(_mood, "score", _REAL_SCORE)
    return _mood
