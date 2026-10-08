"""Brief test helpers: signal and action factories on the conftest hotel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

# 2026-10-01 07:00 Asia/Dubai = 03:00 UTC (a Thursday)
T0 = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def frozen(clock):
    clock.freeze(T0)
    return clock


@pytest.fixture
def sig(cur, hotel_uae):
    """sig(key, *, section, cadence="daily", aed=None, conf=0.9, hours=None, kind=None, **kw) -> signal row."""
    from clefs.core import clock
    from clefs.core import signals as S

    def _sig(key: str, *, section: str = "money_lost", cadence: str = "daily", aed: float | None = None,
             conf: float = 0.9, hours: float | None = None, kind: str | None = None, **kw):
        s = S.SignalIn(
            module="profit_guard", kind=kind or f"pg.test_{section}", dedupe_key=key, title=kw.pop("title", key),
            section=section, cadence=cadence, confidence=conf,
            impact_amount=None if aed is None else aed, currency="AED" if aed is not None else None,
            due_at=None if hours is None else clock.now() + timedelta(hours=hours), **kw,
        )
        return S.upsert_signal(cur, hotel_uae.slug, s)

    return _sig


@pytest.fixture
def act(cur, hotel_uae):
    """act(title, *, signal_id=None, aed=None, due_hours=None, **kw) -> action row (core.task, proposed)."""
    from clefs.core import actions as A
    from clefs.core import clock

    def _act(title: str, *, signal_id: int | None = None, aed: float | None = None, due_hours: float | None = None,
             **kw):
        return A.create_action(
            cur, hotel_uae.slug, type="core.task", title=title, signal_id=signal_id,
            expected_minor=None if aed is None else int(aed * 100), currency="AED" if aed is not None else None,
            due_at=None if due_hours is None else clock.now() + timedelta(hours=due_hours), **kw,
        )

    return _act


@pytest.fixture
def owner(make_user, hotel_uae):
    return make_user("owner", hotel_uae.slug, name="Omar Owner")


@pytest.fixture
def gm(make_user, hotel_uae):
    return make_user("gm", hotel_uae.slug, name="Gina GM")
