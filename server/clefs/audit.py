"""
audit(): one append-only, hash-chained row per consequential change.

    audit(cur, ctx, "core.settings.update", "hotel", slug, before=..., after=...)

`ctx` is a HotelCtx (hotel taken from it) or a bare Principal (pass hotel_slug=
for hotel-scoped events). The row joins the caller's transaction, so an audit
entry exists exactly when the change it describes committed.

The chain (prev_hash, row_hash) is computed by a BEFORE INSERT trigger in
0005_core_audit_ratelimit.sql, per hotel, so rows written from psql or another
process are chained too. UPDATE/DELETE/TRUNCATE are blocked by trigger.
Never put secrets (tokens, PINs, password hashes) into before/after/meta.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from clefs.core.context import request_info
from clefs.db import Json

REDACT_KEYS = frozenset({"password", "password_hash", "pin", "pin_hash", "token", "token_hash", "code_hash", "secret"})


def _clean(value: Any) -> Any:
    """JSON-safe copy with credential-looking keys masked."""
    if isinstance(value, dict):
        return {k: ("***" if k in REDACT_KEYS else _clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<bytes>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.loads(json.dumps(value, default=str))


def actor_fields(ctx_or_principal: Any) -> tuple[str, str | None, str | None]:
    """(actor_type, actor_id, actor_label) for a HotelCtx, Principal or None."""
    principal = getattr(ctx_or_principal, "principal", ctx_or_principal)
    if principal is None:
        return "system", None, None
    kind = getattr(principal, "kind", "system")
    if kind == "user":
        return "user", str(principal.user_id), getattr(principal, "label", None)
    if kind == "device":
        return "device", str(principal.device_id), getattr(principal, "label", None)
    if kind == "service":
        return "service", getattr(principal, "label", None), getattr(principal, "label", None)
    return kind, None, None


def audit(
    cur: Any,
    ctx: Any,
    action: str,
    entity_type: str,
    entity_id: str | int | None,
    *,
    before: dict | None = None,
    after: dict | None = None,
    meta: dict | None = None,
    hotel_slug: str | None = None,
) -> None:
    if hotel_slug is None:
        hotel = getattr(ctx, "hotel", None)
        hotel_slug = getattr(hotel, "slug", None)
    actor_type, actor_id, actor_label = actor_fields(ctx)
    info = request_info()
    cur.execute(
        """
        INSERT INTO audit_log (hotel_slug, actor_type, actor_id, actor_label, action, entity_type,
                               entity_id, before, after, ip, user_agent, request_id, meta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            hotel_slug,
            actor_type,
            actor_id,
            actor_label,
            action,
            entity_type,
            None if entity_id is None else str(entity_id),
            Json(_clean(before)) if before is not None else None,
            Json(_clean(after)) if after is not None else None,
            info.ip,
            (info.user_agent or "")[:400] or None,
            info.request_id,
            Json(_clean(meta or {})),
        ),
    )
