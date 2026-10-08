"""
Doors to other modules (their api.py only) and to core helpers, each safe.

Finance reads Profit Guard (distribution cost, cancellation cost, channel
economics, the service-charge treatment) and Service (recovery cost). Those
lanes ship in parallel and a hotel may run with either module off (or, in
tests, with its tables absent), so every call runs inside a SAVEPOINT and
falls back to the documented stub default (04 spec 7.2 "Stub returns"): a
failing neighbour turns a pulse figure into "n/a", never a 500.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar

from clefs.modules.profit_guard import api as profit_guard_api
from clefs.modules.service import api as service_api

log = logging.getLogger("clefs.finance.ports")
T = TypeVar("T")


# ─── Savepoint wrapper ───


def safe(cur: Any, name: str, fn: Callable[[], T], default: T) -> tuple[T, bool]:
    """(result, ok). Runs fn inside a SAVEPOINT; any exception rolls back to
    it (the caller's transaction stays usable) and returns (default, False)."""
    sp = f"fin_port_{name}"
    cur.execute(f"SAVEPOINT {sp}")
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001  (a neighbour's failure must not fail finance)
        cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        cur.execute(f"RELEASE SAVEPOINT {sp}")
        log.info("finance port %s unavailable: %s", name, exc)
        return default, False
    cur.execute(f"RELEASE SAVEPOINT {sp}")
    return out, True


# ─── Profit Guard ───


def treat_sc_as_revenue(cur: Any, hotel_slug: str) -> bool:
    """The single source of the service-charge treatment (6.4). The stub and
    the fallback are True (count SC as revenue)."""
    out, _ok = safe(cur, "sc", lambda: profit_guard_api.treat_sc_as_revenue(cur, hotel_slug), True)
    return bool(out)


def distribution_by_night(cur: Any, hotel_slug: str, start: date, end: date) -> tuple[list[dict], bool]:
    """(rows, available). `available` is False when Profit Guard failed or
    returned nothing, so the pulse shows Dist as n/a rather than 0."""
    rows, ok = safe(cur, "dist", lambda: profit_guard_api.distribution_cost_by_night(
        cur, hotel_slug, start=start, end=end), [])
    rows = list(rows or [])
    return rows, ok and bool(rows)


def cancellation_costs(cur: Any, hotel_slug: str, start: date, end: date) -> tuple[list[dict], bool]:
    rows, ok = safe(cur, "cxl", lambda: profit_guard_api.cancellation_cost_by_date(
        cur, hotel_slug, start=start, end=end), [])
    rows = list(rows or [])
    return rows, ok and bool(rows)


def channel_economics(cur: Any, hotel_slug: str, start: date, end: date) -> list[dict]:
    rows, _ok = safe(cur, "chan", lambda: profit_guard_api.channel_economics(
        cur, hotel_slug, start=start, end=end), [])
    return list(rows or [])


# ─── Service ───


def recovery_cost(cur: Any, hotel_slug: str, start: date, end: date) -> int | None:
    out, ok = safe(cur, "recovery", lambda: service_api.recovery_cost(cur, hotel_slug, start=start, end=end), 0)
    return int(out or 0) if ok else None
