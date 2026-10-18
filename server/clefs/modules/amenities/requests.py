"""
Request linking (02 spec 5.2): which catalog items a service request
consumed, so expected use can add what guests asked for.

Resolution order: the concierge tool value (TOOL, via amenities.api.
link_request), then an alias match on the request's type, label and
description (ALIAS), then a nightly LLM batch for the housekeeping requests
still unmatched (LLM). A person can always set the lines (MANUAL). A weaker
method never overwrites a stronger one (constants.MATCH_RANK).

Requests are read from the service_requests_v1 contract view; the label
(subtype) and description come from staff_requests, the legacy baseline
table, read-only. FOUNDATION-REQUEST: add subtype and description to
service_requests_v1 so Amenities reads only the contract view.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from clefs import ai
from clefs.core import clock
from clefs.core.errors import bad_request, not_found
from clefs.modules.amenities import catalog
from clefs.modules.amenities.constants import MATCH_RANK

log = logging.getLogger("clefs.amenities.requests")

ALIAS_CONFIDENCE = 0.8
LLM_MIN_CONFIDENCE = 0.6
LLM_BATCH = 40
SCAN_DAYS = 3
# Service request types (legacy and v2 keys) that are housekeeping supplies.
HK_TYPES = ("amenities", "towels", "water", "pillows", "linen", "towel", "cleaning", "housekeeping")


class LineIn(BaseModel):
    item_id: int
    qty: int = Field(1, ge=1, le=50)


class LinesIn(BaseModel):
    lines: list[LineIn] = Field(max_length=20)


# ─── Reading requests ───


def get_request(cur: Any, hotel_slug: str, request_id: int) -> dict | None:
    cur.execute(
        """
        SELECT v.id, v.room, v.floor, v.request_type, v.department, v.status, v.created_at,
               r.subtype, r.description
        FROM service_requests_v1 v JOIN staff_requests r ON r.id = v.id
        WHERE v.hotel_slug = %s AND v.id = %s
        """,
        (hotel_slug, request_id),
    )
    return cur.fetchone()


def lines_for(cur: Any, hotel_slug: str, request_id: int) -> list[dict]:
    cur.execute(
        "SELECT l.*, i.key AS item_key, i.name_en AS item_name FROM amn_request_lines l "
        "JOIN amn_items i ON i.id = l.item_id WHERE l.hotel_slug = %s AND l.request_id = %s ORDER BY l.id",
        (hotel_slug, request_id),
    )
    return list(cur.fetchall())


def line_out(r: dict) -> dict:
    return {"request_id": r["request_id"], "item_id": r["item_id"], "item_key": r.get("item_key"),
            "item_name": r.get("item_name"), "qty": int(r["qty"]), "match_method": r["match_method"],
            "confidence": float(r["confidence"])}


# ─── Writing lines ───


def link(cur: Any, hotel_slug: str, request_id: int, item_id: int, qty: int, *, method: str,
         confidence: float = 1.0) -> dict | None:
    """Upsert one line. Returns the stored line, or None when a stronger
    method already owns that (request, item) pair."""
    rank = MATCH_RANK[method]
    cur.execute("SELECT * FROM amn_request_lines WHERE hotel_slug = %s AND request_id = %s AND item_id = %s FOR UPDATE",
                (hotel_slug, request_id, item_id))
    prev = cur.fetchone()
    if prev and MATCH_RANK[prev["match_method"]] > rank:
        return None
    cur.execute(
        """
        INSERT INTO amn_request_lines (hotel_slug, request_id, item_id, qty, match_method, confidence)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, request_id, item_id) DO UPDATE SET qty = EXCLUDED.qty,
            match_method = EXCLUDED.match_method, confidence = EXCLUDED.confidence, updated_at = NOW()
        RETURNING *
        """,
        (hotel_slug, request_id, item_id, qty, method, confidence),
    )
    row = cur.fetchone()
    if rank >= MATCH_RANK["TOOL"]:
        # The tool or a person named the item: text guesses on other items of
        # the same request are dropped.
        cur.execute("DELETE FROM amn_request_lines WHERE hotel_slug = %s AND request_id = %s AND item_id <> %s "
                    "AND match_method IN ('ALIAS', 'LLM')", (hotel_slug, request_id, item_id))
    return row


def set_manual_lines(cur: Any, hotel_slug: str, request_id: int, body: LinesIn) -> list[dict]:
    """A person states the items of a request: the list replaces every line."""
    if not get_request(cur, hotel_slug, request_id):
        raise not_found("Request not found", "request_not_found")
    ids = [ln.item_id for ln in body.lines]
    if len(set(ids)) != len(ids):
        raise bad_request("Each item may appear once", "duplicate_item")
    for ln in body.lines:
        catalog.require_item(cur, hotel_slug, ln.item_id)
    cur.execute("DELETE FROM amn_request_lines WHERE hotel_slug = %s AND request_id = %s", (hotel_slug, request_id))
    for ln in body.lines:
        link(cur, hotel_slug, request_id, ln.item_id, ln.qty, method="MANUAL")
    return lines_for(cur, hotel_slug, request_id)


# ─── Alias pass ───


def _request_text(r: dict) -> str:
    return " ".join(str(x) for x in (r.get("subtype"), r.get("description")) if x)


def _unscanned(cur: Any, hotel_slug: str, since: datetime, method: str, *, hk_only: bool,
               limit: int = 500) -> list[dict]:
    """Requests since `since` with no lines and no scan by `method` yet. The
    LLM pass also needs the alias pass to have missed them."""
    hk = ""
    if hk_only:
        # Only baseline columns: Amenities must run where Service's v2 columns
        # (request_type_key) are absent, e.g. a hotel without Service.
        hk = "AND (v.department ILIKE 'house%%' OR lower(v.request_type) = ANY(%(hk)s))"
    after_alias = ("AND EXISTS (SELECT 1 FROM amn_request_scans a WHERE a.hotel_slug = v.hotel_slug "
                   "AND a.request_id = v.id AND a.method = 'ALIAS')") if method == "LLM" else ""
    cur.execute(
        f"""
        SELECT v.id, v.request_type, v.department, v.created_at, r.subtype, r.description
        FROM service_requests_v1 v JOIN staff_requests r ON r.id = v.id
        WHERE v.hotel_slug = %(slug)s AND v.created_at >= %(since)s {hk} {after_alias}
          AND NOT EXISTS (SELECT 1 FROM amn_request_lines l WHERE l.hotel_slug = v.hotel_slug AND l.request_id = v.id)
          AND NOT EXISTS (SELECT 1 FROM amn_request_scans s WHERE s.hotel_slug = v.hotel_slug AND s.request_id = v.id
                          AND s.method = %(method)s)
        ORDER BY v.id LIMIT %(limit)s
        """,
        {"slug": hotel_slug, "since": since, "method": method, "limit": limit, "hk": list(HK_TYPES)},
    )
    return list(cur.fetchall())


def _scan(cur: Any, hotel_slug: str, request_id: int, method: str, outcome: str) -> None:
    cur.execute("INSERT INTO amn_request_scans (hotel_slug, request_id, method, outcome) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (hotel_slug, request_id, method) DO UPDATE SET outcome = EXCLUDED.outcome, "
                "scanned_at = NOW()", (hotel_slug, request_id, method, outcome))


def alias_pass(cur: Any, hotel_slug: str, *, since: datetime | None = None) -> dict:
    """Link unmatched requests by alias. Every request looked at is recorded,
    so the pass is incremental and the LLM only sees what it missed."""
    since = since or clock.now() - timedelta(days=SCAN_DAYS)
    items = catalog.list_items(cur, hotel_slug, active_only=True)
    linked = scanned = 0
    for r in _unscanned(cur, hotel_slug, since, "ALIAS", hk_only=False):
        scanned += 1
        hits = catalog.match_items(_request_text(r), items) if items else []
        for h in hits:
            link(cur, hotel_slug, r["id"], h["item"]["id"], h["qty"], method="ALIAS", confidence=ALIAS_CONFIDENCE)
        linked += bool(hits)
        _scan(cur, hotel_slug, r["id"], "ALIAS", "linked" if hits else "no_match")
    return {"scanned": scanned, "linked": linked}


# ─── Nightly LLM batch ───


class _Link(BaseModel):
    request_id: int
    item_key: str
    qty: int
    confidence: float


class _Links(BaseModel):
    links: list[_Link]


_SYSTEM = (
    "You match hotel housekeeping requests to items in the hotel's amenity catalog. For each request, return "
    "zero or more links {request_id, item_key, qty, confidence}. Use only item_key values from the catalog. "
    "Link only consumables the guest asked to receive (a toothbrush, water, slippers); never link a complaint, "
    "a cleaning job or linen. qty is the number of units asked for (1 when not stated). confidence is 0-1. "
    "When unsure, return no link for that request."
)
_LONG_DIGITS = re.compile(r"\d{6,}")


def llm_pass(cur: Any, hotel_slug: str, *, since: datetime | None = None) -> dict:
    """One structured call per LLM_BATCH housekeeping requests the alias pass
    missed. Links below LLM_MIN_CONFIDENCE or naming an unknown key are
    dropped. With AI off nothing is recorded, so the next night retries."""
    since = since or clock.now() - timedelta(days=SCAN_DAYS)
    todo = _unscanned(cur, hotel_slug, since, "LLM", hk_only=True, limit=LLM_BATCH * 5)
    if not todo:
        return {"scanned": 0, "linked": 0}
    items = catalog.list_items(cur, hotel_slug, active_only=True)
    by_key = {i["key"]: i for i in items}
    catalog_txt = "\n".join(f"{i['key']}: {i['name_en']} ({', '.join(i['aliases'][:6])})" for i in items)
    linked = scanned = 0
    for start in range(0, len(todo), LLM_BATCH):
        chunk = todo[start:start + LLM_BATCH]
        # Only the request label and staff description go out, truncated, with
        # long digit runs (phone or card numbers) removed.
        reqs = "\n".join(f"{r['id']}: {_LONG_DIGITS.sub('#', _request_text(r) or r['request_type'] or '')[:200]}"
                         for r in chunk)
        try:
            out = ai.structured("amenities.link_requests", system=_SYSTEM, schema=_Links, hotel_slug=hotel_slug,
                                input=f"Catalog:\n{catalog_txt}\n\nRequests:\n{reqs}")
        except ai.AIDisabled:
            return {"scanned": scanned, "linked": linked, "skipped": "ai_disabled"}
        except ai.AIError as e:
            log.warning("amenities llm link failed for %s: %s", hotel_slug, e)
            for r in chunk:
                _scan(cur, hotel_slug, r["id"], "LLM", "error")
            continue
        ids = {r["id"] for r in chunk}
        hit_ids: set[int] = set()
        for ln in out.links:
            item = by_key.get(ln.item_key)
            if ln.request_id not in ids or not item or ln.confidence < LLM_MIN_CONFIDENCE:
                continue
            qty = max(1, min(int(ln.qty or 1), int(item["max_qty"])))
            if link(cur, hotel_slug, ln.request_id, item["id"], qty, method="LLM",
                    confidence=round(min(ln.confidence, 0.99), 3)):
                hit_ids.add(ln.request_id)
        for r in chunk:
            scanned += 1
            _scan(cur, hotel_slug, r["id"], "LLM", "linked" if r["id"] in hit_ids else "no_match")
        linked += len(hit_ids)
    return {"scanned": scanned, "linked": linked}
