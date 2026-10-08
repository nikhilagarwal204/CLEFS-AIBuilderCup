"""
Ask Clefs public API (frozen once published; other modules import only this file).

Ask Clefs answers questions from the governed metrics registry
(clefs.core.metrics). Other modules (a voice or chat surface for owners)
reach it through these two doors; both act AS a user, so the user's role
filters the metrics exactly as in the console, and every question counts
toward that user's 30-per-hour limit.
"""

from __future__ import annotations

from typing import Any

from clefs.auth.deps import Principal, resolve_ctx


def answer_question(cur: Any, hotel_slug: str, *, user_id: int, question: str, channel: str = "WEB") -> dict:
    """{log_id, status: answered|table_only|unsupported|refused|error, answer_text, table, chart_spec, trace,
    query, results[], unsupported, reason, suggestions[], grounded, regenerated, tool_calls, latency_ms, tokens}.
    Raises ClefsError 403 when the user may not use Ask at the hotel, 429 rate_limited over the limit."""
    from clefs.core.errors import ClefsError
    from clefs.modules.ask import engine

    ctx = resolve_ctx(Principal(kind="user", user_id=user_id, session_kind="api"), hotel_slug, cur=cur)
    if not ctx.can("ask.view"):
        raise ClefsError(403, "This user cannot use Ask Clefs here", "forbidden")
    return engine.ask(cur, ctx, question, channel=channel if channel in ("WEB", "WHATSAPP") else "WEB")


def askable_metrics(cur: Any, hotel_slug: str, *, user_id: int) -> list[dict]:
    """The catalog the user may ask about: [{key, label, description, unit, currency, dimensions[{key,label}],
    higher_is_better, synonyms, derived}]; [] without Ask access."""
    from clefs.modules.ask import catalog

    ctx = resolve_ctx(Principal(kind="user", user_id=user_id, session_kind="api"), hotel_slug, cur=cur)
    return catalog.entries(ctx) if ctx.can("ask.view") else []
