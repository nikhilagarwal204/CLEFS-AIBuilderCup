"""
The NOTIFY events backend end to end, on real commits: publish inside a
transaction, Postgres delivers on commit, the LISTEN thread fans out to an SSE
subscriber. Marked `commits`, so it also exercises the harness's template reset.
"""

from __future__ import annotations

import asyncio

import pytest

from clefs import db, events
from clefs.settings import reload_settings


@pytest.mark.commits
def test_notify_backend_delivers_only_committed_events(monkeypatch):
    monkeypatch.setenv("EVENTS_BACKEND", "notify")
    reload_settings()
    loop = asyncio.new_event_loop()
    q: asyncio.Queue = asyncio.Queue()
    sub = events.Subscriber(hotel_slug="hotel-nandan", queue=q, loop=loop)
    events.subscribe(sub)
    try:
        events.start_listener()
        assert events.listener_ready.wait(10), "LISTEN connection never came up"
        with pytest.raises(RuntimeError):
            with db.db() as cur:
                events.publish("hotel-nandan", "service.rolled_back", {}, cur=cur)
                raise RuntimeError("abort")
        with db.db() as cur:
            cur.execute("INSERT INTO hotels (slug, name) VALUES ('commits-probe', 'Probe')")
            events.publish("hotel-nandan", "service.committed", {"n": 1}, cur=cur)
        msg = loop.run_until_complete(asyncio.wait_for(q.get(), 10))
        assert msg == 'event: service.committed\ndata: {"n": 1}\n\n'
        assert q.empty()
    finally:
        events.stop_listener()
        events.unsubscribe(sub)
        loop.close()
        monkeypatch.setenv("EVENTS_BACKEND", "local")
        reload_settings()


def test_after_a_commits_test_the_database_is_pristine(cur):
    # The commits test above really committed a probe hotel; the template
    # reset must have restored the seeded state (runs after it in file order).
    cur.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE slug = 'commits-probe') AS probe FROM hotels")
    assert cur.fetchone() == {"n": 40, "probe": 0}
