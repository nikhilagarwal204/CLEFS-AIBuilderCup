"""
DB-backed sliding-window rate limits, shared by every API process.

    if not ratelimit.hit(cur, f"pending:{slug}:{room}", limit=5, window_s=600):
        raise too_many(...)

Replaces the in-memory dict that did not survive Vercel's many instances.
Semantics match the old limiter: a rejected attempt is NOT recorded, so a
client that keeps retrying is let back in as soon as the oldest hit ages out.
Concurrent hits on one key are serialized with a transaction advisory lock so
two instances cannot both take the last slot.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs.core import clock

# Stale rows for OTHER keys are swept opportunistically, at most this often
# per process, so the table stays small without a cron job.
SWEEP_EVERY_S = 300
SWEEP_OLDER_THAN = timedelta(days=1)
_last_sweep = 0.0


def hit(cur: Any, key: str, *, limit: int, window_s: int) -> bool:
    """Record one attempt for `key`. True if allowed, False if over the limit."""
    global _last_sweep
    now = clock.now()
    cutoff = now - timedelta(seconds=window_s)
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("clefs_rl:" + key,))
    cur.execute("DELETE FROM rate_limits WHERE key = %s AND at <= %s", (key, cutoff))
    cur.execute("SELECT COUNT(*) AS n FROM rate_limits WHERE key = %s AND at > %s", (key, cutoff))
    if cur.fetchone()["n"] >= limit:
        return False
    cur.execute("INSERT INTO rate_limits (key, at) VALUES (%s, %s)", (key, now))
    mono = now.timestamp()
    if mono - _last_sweep > SWEEP_EVERY_S:
        _last_sweep = mono
        cur.execute("DELETE FROM rate_limits WHERE at < %s", (now - SWEEP_OLDER_THAN,))
    return True


def remaining(cur: Any, key: str, *, limit: int, window_s: int) -> int:
    cutoff = clock.now() - timedelta(seconds=window_s)
    cur.execute("SELECT COUNT(*) AS n FROM rate_limits WHERE key = %s AND at > %s", (key, cutoff))
    return max(0, limit - int(cur.fetchone()["n"]))


def reset(cur: Any, key: str) -> None:
    cur.execute("DELETE FROM rate_limits WHERE key = %s", (key,))
