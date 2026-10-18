"""
The distinct-values cache (spec 7 "enum values are checked against the
distinct-values cache"). A filter value the model writes ("Booking.com") is
matched to a value the hotel's data actually holds ("BOOKING") before it is
bound; a value that matches nothing goes back to the model as an error, so a
typo can never silently turn a question into an empty answer.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from clefs.core import metrics
from clefs.modules.ask.constants import VALUES_TTL_S

_cache: dict[tuple[str, str, str], tuple[float, list[str] | None]] = {}
_lock = threading.Lock()
_NORM_RE = re.compile(r"[^a-z0-9]+")


def clear() -> None:
    """Tests, and after an import changes what a hotel's data holds."""
    with _lock:
        _cache.clear()


def _has_values_sql(metric_key: str, dimension: str) -> bool:
    try:
        m = metrics.get_metric(metric_key)
    except Exception:
        return False
    return any(d.key == dimension and d.values_sql for d in m.dimensions)


def allowed(ctx: Any, metric_key: str, dimension: str) -> list[str] | None:
    """Known values of one dimension at the ctx's hotel, or None when the
    metric declares no values query (then any value is bound as written)."""
    if not _has_values_sql(metric_key, dimension):
        return None
    key = (ctx.hotel.slug, metric_key, dimension)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit is not None and now - hit[0] < VALUES_TTL_S:
        return hit[1]
    vals = metrics.dimension_values(ctx, metric_key, dimension)
    with _lock:
        _cache[key] = (now, vals)
    return vals


def _norm(v: str) -> str:
    return _NORM_RE.sub("", str(v).casefold())


def match(value: str, known: list[str]) -> str | None:
    """The known value `value` means, or None. Exact, then case-insensitive,
    then punctuation-blind, then a unique prefix either way round
    ("booking.com" -> "BOOKING", "exp" -> "EXPEDIA")."""
    if value in known:
        return value
    folded = {k.casefold(): k for k in known}
    if str(value).casefold() in folded:
        return folded[str(value).casefold()]
    n = _norm(value)
    if not n:
        return None
    normed = {_norm(k): k for k in known}
    if n in normed:
        return normed[n]
    cands = {k for nk, k in normed.items() if nk and (nk.startswith(n) or n.startswith(nk))}
    return cands.pop() if len(cands) == 1 else None


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compare(values: list[str], op: str, bound: str) -> list[str]:
    """Known values >= or <= `bound` (numeric when both sides parse, else
    text order): how "floors 5 and up" becomes an equality filter."""
    b = _num(bound)
    out = []
    for v in values:
        x = _num(v)
        if b is not None and x is not None:
            ok = x >= b if op == "gte" else x <= b
        else:
            ok = str(v) >= str(bound) if op == "gte" else str(v) <= str(bound)
        if ok:
            out.append(v)
    return out
