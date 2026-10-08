"""
Accounting connectors: Xero and QuickBooks Reports, Zoho Books (stub).

Tally has no connector: it is upload-only (a Tally Excel export goes through
the statement upload, recognised by its primary groups, 04 spec 6.3).

sync_provider reads one month's P&L through the provider's Reports API and
writes it as a finance batch (source = provider). The batch hash is the
canonical report JSON, so a re-sync of unchanged books is a no-op.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from clefs.core import clock, oauth
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.finance import imports
from clefs.modules.finance.connectors import qbo, xero, zoho

log = logging.getLogger("clefs.finance.connectors")
PROVIDERS = {"xero": xero, "qbo": qbo, "zoho": zoho}


def _connection_row(cur: Any, hotel_slug: str, provider: str, *, status: str, tenant_ref: str | None = None,
                    error: str | None = None, synced: bool = False) -> None:
    cur.execute(
        """INSERT INTO fin_connections (hotel_slug, provider, tenant_ref, status, last_sync_at, last_error)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (hotel_slug, provider) DO UPDATE SET status = EXCLUDED.status,
               tenant_ref = COALESCE(EXCLUDED.tenant_ref, fin_connections.tenant_ref),
               last_sync_at = COALESCE(EXCLUDED.last_sync_at, fin_connections.last_sync_at),
               last_error = EXCLUDED.last_error""",
        (hotel_slug, provider, tenant_ref, status, clock.now() if synced else None, error),
    )


def sync_provider(cur: Any, hotel_slug: str, provider: str, month: date, *, statement_kind: str = "pl",
                  actor_id: int | None = None) -> dict:
    """Fetch and import one month. NotConnected (409) propagates to the
    caller; transient provider errors are recorded on fin_connections."""
    mod = PROVIDERS.get(provider)
    if mod is None:
        raise ClefsError(404, f"unknown accounting provider {provider!r}", "unknown_provider")
    hotel = get_hotel(cur, hotel_slug)
    month = month.replace(day=1)
    fetch = mod.fetch_pl if statement_kind == "pl" else mod.fetch_tb
    try:
        rep = fetch(cur, hotel_slug, month, hotel.currency)
    except oauth.NotConnected as exc:
        _connection_row(cur, hotel_slug, provider, status="not_connected", error=exc.detail)
        raise
    except oauth.OAuthError as exc:
        _connection_row(cur, hotel_slug, provider, status="error", error=exc.detail)
        raise
    if not rep.lines:
        _connection_row(cur, hotel_slug, provider, status="active", tenant_ref=rep.meta.get("tenant_ref"),
                        error="the report had no account lines", synced=True)
        return {"provider": provider, "month": month.isoformat(), "lines": 0, "batch": None}
    canonical = {"lines": rep.lines, "np": rep.net_profit_minor, "dr": rep.debit_minor, "cr": rep.credit_minor,
                 "kind": statement_kind, "provider": provider}
    meta = {**rep.meta, "provider": provider}
    if statement_kind == "trial_balance":
        meta["tb"] = {"debit_minor": rep.debit_minor, "credit_minor": rep.credit_minor}
    batch = imports.write_month(cur, hotel_slug, period_month=month, statement_kind=statement_kind, source=provider,
                                sha256=imports.canonical_sha(canonical), currency=hotel.currency, lines=rep.lines,
                                source_net_profit_minor=rep.net_profit_minor, basis=rep.meta.get("basis", "accrual"),
                                meta=meta, actor_id=actor_id)
    _connection_row(cur, hotel_slug, provider, status="active", tenant_ref=rep.meta.get("tenant_ref"), synced=True)
    return {"provider": provider, "month": month.isoformat(), "lines": len(rep.lines), "batch": batch["id"],
            "status": batch["status"], "reused": bool(batch.get("reused"))}


def sync_hotel(cur: Any, hotel_slug: str, month: date) -> dict:
    """The monthly job: every active Xero / QuickBooks connection."""
    out = {}
    for conn in oauth.list_connections(cur, hotel_slug):
        provider = conn.get("provider")
        if provider not in ("xero", "qbo") or conn.get("status") != "active":
            continue
        cur.execute("SAVEPOINT fin_sync")
        try:
            out[provider] = sync_provider(cur, hotel_slug, provider, month)
            cur.execute("RELEASE SAVEPOINT fin_sync")
        except ClefsError as exc:
            cur.execute("ROLLBACK TO SAVEPOINT fin_sync")
            cur.execute("RELEASE SAVEPOINT fin_sync")
            _connection_row(cur, hotel_slug, provider, status="error", error=exc.detail)
            out[provider] = {"error": exc.code}
    return out
