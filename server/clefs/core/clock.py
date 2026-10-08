"""
The one source of "now".

All code asks clock.now() (aware UTC) instead of datetime.now()/time.time(), so
tests can freeze and advance time across SLA ladders, shift boundaries and
midnight in the hotel's timezone without sleeping.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

_lock = threading.Lock()
_frozen: datetime | None = None


def now() -> datetime:
    """Current instant, timezone-aware UTC."""
    f = _frozen
    return f if f is not None else datetime.now(timezone.utc)


def now_ms() -> int:
    """Epoch milliseconds (the legacy tables' time unit)."""
    return int(now().timestamp() * 1000)


def freeze(at: datetime) -> None:
    if at.tzinfo is None:
        raise ValueError("freeze() needs an aware datetime")
    global _frozen
    with _lock:
        _frozen = at.astimezone(timezone.utc)


def advance(delta: timedelta) -> datetime:
    global _frozen
    with _lock:
        if _frozen is None:
            raise RuntimeError("advance() requires a frozen clock")
        _frozen = _frozen + delta
        return _frozen


def unfreeze() -> None:
    global _frozen
    with _lock:
        _frozen = None


def is_frozen() -> bool:
    return _frozen is not None


@contextmanager
def frozen(at: datetime) -> Iterator[None]:
    freeze(at)
    try:
        yield
    finally:
        unfreeze()
