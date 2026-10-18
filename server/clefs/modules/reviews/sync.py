"""
Daily source sync (reviews.sync) and the console's "sync now".

Each source syncs in its own savepoint and records its outcome on the row
(last_synced_at, status, last_error), so one broken connection shows up in
the console without stopping the others. SerpApi is skipped while its
disclosure toggle is off, and once GBP is connected (GBP is the preferred,
first-party path for the same Google reviews).
"""

from __future__ import annotations

import logging
from typing import Any

from clefs.core import clock, oauth, serpapi
from clefs.core.errors import ClefsError
from clefs.modules.reviews import store
from clefs.modules.reviews.settings import get_settings
from clefs.modules.reviews.sources import gbp, serpapi_google, tripadvisor

log = logging.getLogger(__name__)
SYNCABLE = ("serpapi_google", "gbp", "tripadvisor")


def _skip_reason(cur: Any, hotel_slug: str, source: dict, cfg: Any) -> str | None:
    # Only a pause stops a sync: an 'error' or 'disconnected' source is
    # retried daily (a reconnected Google account heals by itself, and a
    # missing token fails fast without any network call).
    if source["status"] == "paused":
        return "paused"
    if source["kind"] == "serpapi_google":
        if not cfg.serpapi_enabled:
            return "serpapi_disabled"
        gbp_src = store.find_source(cur, hotel_slug, "gbp")
        if gbp_src and gbp_src["status"] == "active":
            return "gbp_preferred"
    return None


def sync_source(cur: Any, hotel: Any, source: dict, *, cfg: Any = None) -> dict:
    cfg = cfg or get_settings(cur, hotel.slug)
    if source["kind"] not in SYNCABLE:
        return {"source_id": source["id"], "skipped": "not_syncable"}
    reason = _skip_reason(cur, hotel.slug, source, cfg)
    if reason:
        return {"source_id": source["id"], "skipped": reason}
    cur.execute("SAVEPOINT rev_sync")
    try:
        if source["kind"] == "serpapi_google":
            res = serpapi_google.sync(cur, hotel, source, max_pages=cfg.sync_max_pages)
        elif source["kind"] == "gbp":
            res = gbp.sync(cur, hotel, source)
        else:
            res = tripadvisor.sync(cur, hotel, source)
        cur.execute("RELEASE SAVEPOINT rev_sync")
    except serpapi.BudgetExhausted:
        # Not the source's fault: skip quietly and try again tomorrow.
        cur.execute("ROLLBACK TO SAVEPOINT rev_sync")
        return {"source_id": source["id"], "skipped": "budget_exhausted"}
    except (ClefsError, oauth.NotConnected) as exc:
        cur.execute("ROLLBACK TO SAVEPOINT rev_sync")
        code = getattr(exc, "code", None) or type(exc).__name__
        status = "disconnected" if code == "not_connected" else "error"
        store.update_source(cur, hotel.slug, source["id"], status=status, last_error=code)
        log.warning("reviews.sync %s source %s failed: %s", hotel.slug, source["id"], code)
        return {"source_id": source["id"], "error": code}
    error = res.get("error")
    store.update_source(cur, hotel.slug, source["id"], last_synced_at=clock.now(),
                        status="error" if error else "active", last_error=error)
    return {"source_id": source["id"], **res}


def sync_hotel(cur: Any, hotel: Any) -> dict:
    cfg = get_settings(cur, hotel.slug)
    results = [sync_source(cur, hotel, s, cfg=cfg) for s in store.list_sources(cur, hotel.slug)
               if s["kind"] in SYNCABLE]
    return {"sources": results, "created": sum(r.get("created", 0) for r in results)}
