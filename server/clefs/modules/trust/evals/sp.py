"""Savepoint helper shared by the eval runner, runs and the gate."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def savepoint(cur: Any, name: str, fn: Any, default: Any = None) -> Any:
    """fn() inside a savepoint: a database error in another module's code
    (a session build, a menu read, a missing table) rolls back only its own
    work and leaves the caller's transaction usable. `default` is returned
    on failure, or called with the exception when it is callable."""
    cur.execute(f"SAVEPOINT {name}")
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        log.info("trust eval step %s failed: %s", name, exc)
        return default(exc) if callable(default) else default
    cur.execute(f"RELEASE SAVEPOINT {name}")
    return out
