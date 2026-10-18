"""
The event store (02 spec 4.1-4.2): every adapter hands `ingest()` one
candidate, and this file decides which dr_events row it belongs to, keeps
the per-source row, re-resolves the merged fields by source precedence
(dedup.py), writes dr_event_changes and emits dr.event.changed.

Scopes. A global event (hotel_slug NULL) comes from PredictHQ, Ticketmaster
or the curated seed. Manual entry, ICS files and AI discovery are
hotel-scoped. A hotel-scoped source that matches a global event is attached
as corroboration only: it never edits the global row, so one tenant's input
cannot move another tenant's calendar, and other hotels never see it.

Candidate dict (what adapters build; dates are `date`):
  title, category, start, end, status?, venue_codes?, venue_text?, lat?, lon?,
  emirate?, attendance?, phq_rank?, phq_local_rank?, spend_minor? (USD cents),
  moon_dependent?, uncertainty_days?, verification?, confidence?, urls?,
  description?, original_start?, original_end?
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

from clefs import db as clefs_db
from clefs import events as sse
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.signals import Evidence, SignalIn, register_signal_kind, upsert_signal
from clefs.modules.demand import dedup

log = logging.getLogger("clefs.demand.events")

CATEGORIES = ("CONFERENCE", "EXPO", "SPORTS", "CONCERT", "FESTIVAL", "PUBLIC_HOLIDAY", "SCHOOL_HOLIDAY",
              "RELIGIOUS", "SOURCE_MARKET_HOLIDAY", "COMMUNITY", "CITYWIDE")
STATUSES = ("SCHEDULED", "RESCHEDULED", "POSTPONED", "CANCELLED")
# Statuses a source can assert on its own; RESCHEDULED is also inferred
# when the date-owning source moves its dates.
_ASSERTED = ("CANCELLED", "POSTPONED", "RESCHEDULED")
# Candidates are matched against events within this many days of their
# dates (overlap is still required; the window only bounds the query).
_MATCH_WINDOW_DAYS = 3
_ACTIVE_ACTION_STATES = ("proposed", "approved", "assigned", "in_progress", "done", "snoozed", "submitted",
                         "awaiting_counterparty")
_APPROVED_STATES = ("approved", "assigned", "in_progress", "done")

EVENT_CHANGED = "dr.event.changed"

register_signal_kind(
    EVENT_CHANGED, label="A demand event changed", default_owner_role="revenue",
    section="service_risk", cadence="daily", icon="calendar",
)


# ─── Extension points (later Demand parts subscribe here) ───

# fn(cur, event_id) -> [{hotel_slug, ref_type, ref_id, approved: bool}]:
# recommendations, guidance and campaigns that name an event register a
# finder, so a reschedule reaches whatever depended on it.
_REF_FINDERS: list[Callable[[Any, str], list[dict]]] = []
# fn(cur, event: dict, changes: list[dict], refs: list[dict]) -> None
_CHANGE_LISTENERS: list[Callable[[Any, dict, list[dict], list[dict]], None]] = []


def register_ref_finder(fn: Callable[[Any, str], list[dict]]) -> None:
    if fn not in _REF_FINDERS:
        _REF_FINDERS.append(fn)


def on_event_changed(fn: Callable[[Any, dict, list[dict], list[dict]], None]) -> None:
    if fn not in _CHANGE_LISTENERS:
        _CHANGE_LISTENERS.append(fn)


# ─── Venues ───


def venues(cur: Any) -> list[dict]:
    rows = clefs_db.fetch_all("SELECT * FROM dr_venues ORDER BY code", cur=cur)
    for r in rows:
        for k in ("lat", "lon"):
            r[k] = float(r[k]) if r[k] is not None else None
    return rows


def _json_safe(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_json_safe(x) for x in v]
    return v


def _as_date(v: Any) -> date | None:
    if v is None or isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return date.fromisoformat(str(v)[:10])


# ─── Candidates ───


def normalize_candidate(cur: Any, cand: dict, *, venue_rows: list[dict] | None = None) -> dict:
    """Validate a candidate and give it a venue: by code, then the nearest
    venue to its coordinates, then the venue text. Coordinates are filled
    from the venue so distance matching works for text-only sources."""
    c = dict(cand)
    c["title"] = " ".join(str(c.get("title") or "").split())[:300]
    if not c["title"]:
        raise ClefsError(400, "An event needs a title", "invalid_event")
    c["start"], c["end"] = _as_date(c.get("start")), _as_date(c.get("end") or c.get("start"))
    if c["start"] is None or c["end"] is None:
        raise ClefsError(400, "An event needs a start date", "invalid_event")
    if c["end"] < c["start"]:
        raise ClefsError(400, "The event ends before it starts", "invalid_event")
    if c.get("category") not in CATEGORIES:
        raise ClefsError(400, f"Unknown event category {c.get('category')!r}", "invalid_event")
    if c.get("status") and c["status"] not in STATUSES:
        raise ClefsError(400, f"Unknown event status {c['status']!r}", "invalid_event")
    rows = venue_rows if venue_rows is not None else venues(cur)
    known = {v["code"]: v for v in rows}
    codes = [code for code in (c.get("venue_codes") or []) if code in known]
    if not codes:
        # Coordinates first (PredictHQ's are precise), then the venue text.
        code = dedup.nearest_venue(c.get("lat"), c.get("lon"), rows) or dedup.resolve_venue(c.get("venue_text"), rows)
        codes = [code] if code else []
    c["venue_codes"] = codes
    if codes and (c.get("lat") is None or c.get("lon") is None):
        v = known[codes[0]]
        c["lat"], c["lon"] = v.get("lat"), v.get("lon")
    if codes and not c.get("emirate"):
        c["emirate"] = known[codes[0]].get("emirate")
    for k in ("original_start", "original_end"):
        c[k] = _as_date(c.get(k))
    c["urls"] = [u for u in (c.get("urls") or []) if isinstance(u, str) and u.startswith(("http://", "https://"))]
    return c


def _source_fields(c: dict) -> dict:
    keys = ("title", "category", "start", "end", "status", "venue_codes", "lat", "lon", "emirate", "attendance",
            "phq_rank", "phq_local_rank", "spend_minor", "moon_dependent", "uncertainty_days", "verification",
            "confidence", "urls", "description", "original_start", "original_end")
    return _json_safe({k: c.get(k) for k in keys if c.get(k) not in (None, "", [])})


def _load_fields(f: dict) -> dict:
    out = dict(f or {})
    for k in ("start", "end", "original_start", "original_end"):
        if out.get(k):
            out[k] = _as_date(out[k])
    return out


# ─── Lookup ───


def _live_id(cur: Any, event_id: Any) -> str | None:
    """Follow merged_into to the surviving row (a folded event keeps its id
    for old references)."""
    seen = set()
    eid = str(event_id) if event_id else None
    while eid and eid not in seen:
        seen.add(eid)
        row = clefs_db.fetch_one("SELECT id, merged_into FROM dr_events WHERE id = %s", (eid,), cur=cur)
        if not row:
            return None
        if not row["merged_into"]:
            return str(row["id"])
        eid = str(row["merged_into"])
    return None


def _candidates_for(cur: Any, c: dict, hotel_slug: str | None) -> list[dict]:
    lo = c["start"] - timedelta(days=_MATCH_WINDOW_DAYS)
    hi = c["end"] + timedelta(days=_MATCH_WINDOW_DAYS)
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_events WHERE merged_into IS NULL AND start_date <= %s AND end_date >= %s "
        "AND (hotel_slug IS NULL OR %s::text IS NULL OR hotel_slug = %s)",
        (hi, lo, hotel_slug, hotel_slug), cur=cur,
    )
    for r in rows:
        for k in ("lat", "lon"):
            r[k] = float(r[k]) if r[k] is not None else None
    return rows


# ─── Ingest (every adapter ends here) ───


def ingest(cur: Any, cand: dict, *, source: str, external_id: str, hotel_slug: str | None = None,
           payload: dict | None = None, venue_rows: list[dict] | None = None,
           reason_hint: str | None = None) -> dict:
    """Upsert one source record and the event it belongs to. Returns
    {event_id, created, matched, changes}. Idempotent: the same record twice
    changes nothing but last_checked_at."""
    if source not in dedup.SOURCES:
        raise ValueError(f"unknown event source {source!r}")
    if (source in dedup.HOTEL_SCOPED_SOURCES) != bool(hotel_slug):
        raise ValueError(f"{source} events are {'hotel-scoped' if source in dedup.HOTEL_SCOPED_SOURCES else 'global'}")
    c = normalize_candidate(cur, cand, venue_rows=venue_rows)
    if source == "AI_WEB":
        # 02 spec 4.2: AI discovery is always UNVERIFIED, confidence <= 0.6.
        c["verification"] = "UNVERIFIED"
        c["confidence"] = min(float(c.get("confidence") or 0.5), dedup.AI_CONFIDENCE_CAP)
    conf = c["confidence"] if source == "AI_WEB" else dedup.source_confidence(source, c)
    external_id = str(external_id)[:300]
    existing = clefs_db.fetch_one(
        "SELECT id, event_id, fields FROM dr_event_sources WHERE source = %s AND external_id = %s",
        (source, external_id), cur=cur,
    )
    created = matched = False
    event_id: str | None = None
    if existing:
        event_id = _live_id(cur, existing["event_id"])
    if event_id is None:
        pool = _candidates_for(cur, c, hotel_slug)
        glob = [e for e in pool if e["hotel_slug"] is None]
        match = dedup.best_match(c, glob)
        if match is None and hotel_slug:
            match = dedup.best_match(c, [e for e in pool if e["hotel_slug"] == hotel_slug])
        if match is not None:
            event_id, matched = str(match["id"]), True
        else:
            event_id, created = _insert_event(cur, c, hotel_slug), True
        if hotel_slug is None:
            # A global record absorbs hotel-scoped duplicates (an AI hit or a
            # manual entry made before the listing reached a feed).
            for e in pool:
                if e["hotel_slug"] is not None and str(e["id"]) != event_id and dedup.match_score(c, e) > 0:
                    _fold(cur, str(e["id"]), event_id, source=source)
    fields = _source_fields(c)
    if existing:
        clefs_db.execute(
            "UPDATE dr_event_sources SET event_id = %s, fields = %s, payload = %s, confidence = %s, "
            "hotel_slug = %s, fetched_at = %s WHERE id = %s",
            (event_id, clefs_db.Json(fields), clefs_db.Json(_json_safe(payload or {})), conf, hotel_slug,
             clock.now(), existing["id"]), cur=cur,
        )
    else:
        clefs_db.execute(
            "INSERT INTO dr_event_sources (event_id, source, external_id, hotel_slug, fields, payload, confidence, "
            "fetched_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (event_id, source, external_id, hotel_slug, clefs_db.Json(fields),
             clefs_db.Json(_json_safe(payload or {})), conf, clock.now()), cur=cur,
        )
    changes = recompute(cur, event_id, trigger=source, created=created, reason_hint=reason_hint)
    return {"event_id": event_id, "created": created, "matched": matched, "changes": changes}


def _insert_event(cur: Any, c: dict, hotel_slug: str | None) -> str:
    row = clefs_db.fetch_one(
        "INSERT INTO dr_events (hotel_slug, canonical_key, title, category, start_date, end_date) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (hotel_slug, dedup.canonical_key(c["title"], c["venue_codes"], c["start"]), c["title"], c["category"],
         c["start"], c["end"]), cur=cur,
    )
    return str(row["id"])


def _fold(cur: Any, from_id: str, into_id: str, *, source: str) -> None:
    clefs_db.execute("UPDATE dr_event_sources SET event_id = %s WHERE event_id = %s", (into_id, from_id), cur=cur)
    clefs_db.execute("UPDATE dr_events SET merged_into = %s, updated_at = now() WHERE id = %s", (into_id, from_id),
                     cur=cur)
    _log_change(cur, into_id, "merge", {"event_id": from_id}, {"event_id": into_id}, source, "merge")


def _log_change(cur: Any, event_id: str, field: str, old: Any, new: Any, source: str, reason: str,
                *, at: datetime | None = None, source_url: str | None = None) -> dict:
    row = clefs_db.fetch_one(
        "INSERT INTO dr_event_changes (event_id, at, field, old, new, source, reason, source_url) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, event_id, at, field, old, new, source, reason",
        (event_id, at or clock.now(), field, clefs_db.Json(_json_safe(old)), clefs_db.Json(_json_safe(new)),
         source, reason, source_url), cur=cur,
    )
    return row


# ─── Re-resolution and change detection ───


def _scoped_sources(cur: Any, event: dict) -> list[dict]:
    rows = clefs_db.fetch_all(
        "SELECT source, external_id, hotel_slug, fields, confidence, fetched_at FROM dr_event_sources "
        "WHERE event_id = %s ORDER BY id", (event["id"],), cur=cur,
    )
    if event["hotel_slug"] is None:
        # Hotel-scoped rows corroborate a global event; they never edit it.
        rows = [r for r in rows if r["hotel_slug"] is None]
    for r in rows:
        r["fields"] = _load_fields(r["fields"])
        r["confidence"] = float(r["confidence"]) if r["confidence"] is not None else None
    return rows


def recompute(cur: Any, event_id: str, *, trigger: str | None = None, created: bool = False,
              reason_hint: str | None = None) -> list[dict]:
    """Re-resolve an event from its sources, log what moved, and emit
    dr.event.changed for a moved, postponed or cancelled event. Returns the
    change rows written."""
    ev = clefs_db.fetch_one("SELECT * FROM dr_events WHERE id = %s FOR UPDATE", (event_id,), cur=cur)
    srcs = _scoped_sources(cur, ev)
    if not srcs:
        return []
    r = dedup.resolve_fields(srcs)
    old_dates = (ev["start_date"], ev["end_date"])
    new_dates = (r["start_date"], r["end_date"])
    moved = not created and old_dates != new_dates
    src_status = r["status"]
    reason = None
    if moved:
        # The date owner moving its own dates is a reschedule; a better
        # source overriding a worse one is a correction (status unchanged).
        same_owner = r["date_source"] == ev["date_source"] == trigger
        reason = "rescheduled" if same_owner or src_status == "RESCHEDULED" else "correction"
        if reason_hint in ("rescheduled", "correction"):
            reason = reason_hint  # an owner editing a manual event says which
    if src_status in ("CANCELLED", "POSTPONED"):
        status = src_status
    elif reason == "rescheduled" or src_status == "RESCHEDULED":
        status = "RESCHEDULED"
    elif ev["status"] == "RESCHEDULED" and not created and ev["date_source"] == r["date_source"]:
        status = "RESCHEDULED"
    else:
        status = "SCHEDULED"
    orig_start, _ = dedup._first(dedup._ranked(srcs), "original_start")
    orig_end, _ = dedup._first(dedup._ranked(srcs), "original_end")
    if orig_start is None:
        orig_start, orig_end = ev["original_start"], ev["original_end"]
        if reason == "rescheduled" and orig_start is None:
            orig_start, orig_end = old_dates
    changes: list[dict] = []
    if moved:
        changes.append(_log_change(cur, event_id, "dates", {"start": old_dates[0], "end": old_dates[1]},
                                   {"start": new_dates[0], "end": new_dates[1]}, trigger or r["date_source"], reason))
    if not created and status != ev["status"]:
        changes.append(_log_change(cur, event_id, "status", ev["status"], status, trigger or r["date_source"], "status"))
    if not created and sorted(r["venue_codes"]) != sorted(ev["venue_codes"] or []) and ev["venue_codes"]:
        changes.append(_log_change(cur, event_id, "venue_codes", ev["venue_codes"], r["venue_codes"],
                                   trigger or r["date_source"], "update"))
    clefs_db.execute(
        """UPDATE dr_events SET canonical_key = %s, title = %s, category = %s, venue_codes = %s, lat = %s, lon = %s,
               emirate = %s, start_date = %s, end_date = %s, status = %s, original_start = %s, original_end = %s,
               attendance_est = %s, attendance_source = %s, phq_rank = %s, phq_local_rank = %s,
               predicted_accom_spend_minor = %s, predicted_accom_spend_currency = %s, moon_dependent = %s,
               date_uncertainty_days = %s, verification = %s, confidence = %s, date_source = %s,
               source_urls = %s, description = %s, last_checked_at = now(), updated_at = now()
           WHERE id = %s""",
        (dedup.canonical_key(r["title"], r["venue_codes"], r["start_date"]), r["title"], r["category"],
         r["venue_codes"], r["lat"], r["lon"], r["emirate"], r["start_date"], r["end_date"], status,
         orig_start, orig_end, r["attendance_est"], r["attendance_source"], r["phq_rank"], r["phq_local_rank"],
         r["predicted_accom_spend_minor"], r["predicted_accom_spend_currency"], r["moon_dependent"],
         r["date_uncertainty_days"], "VERIFIED" if ev["owner_confirmed_at"] else r["verification"],
         r["confidence"], r["date_source"],
         clefs_db.Json(r["source_urls"]), (r["description"] or None) and str(r["description"])[:4000], event_id),
        cur=cur,
    )
    if any(ch["field"] in ("dates", "status") for ch in changes):
        emit_changed(cur, event_id, changes)
    return changes


# ─── dr.event.changed ───


def _family_ids(cur: Any, event_id: str) -> list[str]:
    """The event and every row folded into it (old references keep old ids)."""
    ids, frontier = [str(event_id)], [str(event_id)]
    while frontier:
        rows = clefs_db.fetch_all("SELECT id FROM dr_events WHERE merged_into = ANY(%s::uuid[])", (frontier,), cur=cur)
        frontier = [str(r["id"]) for r in rows if str(r["id"]) not in ids]
        ids.extend(frontier)
    return ids


def references(cur: Any, event_id: str) -> list[dict]:
    """What depends on this event: Demand actions whose params name it
    (event_id or driver_event_ids), plus every registered finder."""
    ids = _family_ids(cur, event_id)
    refs = [
        {"hotel_slug": r["hotel_slug"], "ref_type": "action", "ref_id": r["id"],
         "approved": r["status"] in _APPROVED_STATES}
        for r in clefs_db.fetch_all(
            "SELECT id, hotel_slug, status FROM actions WHERE module = 'demand' AND status = ANY(%s) "
            "AND (params->>'event_id' = ANY(%s) OR (jsonb_typeof(params->'driver_event_ids') = 'array' "
            "AND params->'driver_event_ids' ?| %s::text[]))",
            (list(_ACTIVE_ACTION_STATES), ids, ids), cur=cur,
        )
    ]
    for finder in _REF_FINDERS:
        for fid in ids:
            try:
                refs.extend(finder(cur, fid) or [])
            except Exception:  # noqa: BLE001  (a broken finder must not block ingest)
                log.exception("demand event ref finder failed")
    return refs


def emit_changed(cur: Any, event_id: str, changes: list[dict]) -> list[dict]:
    """One dr.event.changed signal per hotel that depends on the event
    (02 spec 4.2). Money section when an approved item depends on it,
    service risk otherwise. The dedupe key carries the new dates and status,
    so each distinct change is its own signal and a repeat is a refresh."""
    ev = clefs_db.fetch_one("SELECT * FROM dr_events WHERE id = %s", (event_id,), cur=cur)
    refs = references(cur, event_id)
    by_hotel: dict[str, list[dict]] = {}
    for ref in refs:
        if ref.get("hotel_slug") and (ev["hotel_slug"] is None or ref["hotel_slug"] == ev["hotel_slug"]):
            by_hotel.setdefault(ref["hotel_slug"], []).append(ref)
    stamp = f"{ev['start_date']}:{ev['end_date']}:{ev['status']}"
    what = {"CANCELLED": "was cancelled", "POSTPONED": "was postponed"}.get(
        ev["status"], f"moved to {ev['start_date']:%d %b} - {ev['end_date']:%d %b %Y}")
    out = []
    for slug, hrefs in by_hotel.items():
        approved = any(r.get("approved") for r in hrefs)
        evidence = [Evidence(kind="row", label="Event", value=ev["title"], ref={"event_id": str(ev["id"])})]
        for ch in changes:
            evidence.append(Evidence(kind="calc", label=f"{ch['field']} ({ch['reason']})",
                                     value=json.dumps({"old": ch["old"], "new": ch["new"]}, default=str)))
        sig = upsert_signal(cur, slug, SignalIn(
            module="demand", kind=EVENT_CHANGED, dedupe_key=f"{EVENT_CHANGED}:{ev['id']}:{stamp}",
            title=f"{ev['title']} {what}",
            summary=f"{len(hrefs)} demand item(s) depend on this event. Review them before the old dates.",
            section="money_available" if approved else "service_risk",
            severity=3 if ev["status"] in ("CANCELLED", "POSTPONED") or approved else 2,
            confidence=float(ev["confidence"]), evidence=evidence, owner_role="revenue",
            subject_type="dr_event", subject_id=str(ev["id"]),
        ))
        sse.publish(slug, "demand.event_changed", {"event_id": str(ev["id"]), "status": ev["status"]}, cur=cur)
        out.append(sig)
    for listener in _CHANGE_LISTENERS:
        try:
            listener(cur, ev, changes, refs)
        except Exception:  # noqa: BLE001
            log.exception("demand event change listener failed")
    return out


# ─── Reads (always through a hotel: global rows plus the hotel's own) ───


def serialize(ev: dict, sources: list[dict] | None = None, changes: list[dict] | None = None) -> dict:
    out = {
        "id": str(ev["id"]), "title": ev["title"], "category": ev["category"],
        "venue_codes": list(ev["venue_codes"] or []), "emirate": ev["emirate"],
        "lat": float(ev["lat"]) if ev["lat"] is not None else None,
        "lon": float(ev["lon"]) if ev["lon"] is not None else None,
        "start_date": ev["start_date"].isoformat(), "end_date": ev["end_date"].isoformat(),
        "status": ev["status"],
        "original_start": ev["original_start"].isoformat() if ev["original_start"] else None,
        "original_end": ev["original_end"].isoformat() if ev["original_end"] else None,
        "attendance_est": ev["attendance_est"], "attendance_source": ev["attendance_source"],
        "phq_rank": ev["phq_rank"], "phq_local_rank": ev["phq_local_rank"],
        "predicted_accom_spend": (str(ev["predicted_accom_spend_minor"] / 100)
                                  if ev["predicted_accom_spend_minor"] is not None else None),
        "predicted_accom_spend_currency": ev["predicted_accom_spend_currency"],
        "moon_dependent": ev["moon_dependent"], "date_uncertainty_days": ev["date_uncertainty_days"],
        "verification": ev["verification"], "confidence": float(ev["confidence"]),
        "date_source": ev["date_source"], "source_urls": ev["source_urls"] or [],
        "description": ev["description"], "scope": "hotel" if ev["hotel_slug"] else "global",
        "owner_confirmed": ev["owner_confirmed_at"] is not None,
        "first_seen_at": ev["first_seen_at"].isoformat(), "last_checked_at": ev["last_checked_at"].isoformat(),
    }
    if sources is not None:
        names = {s["source"] for s in sources}
        out["sources"] = [{"source": s["source"], "external_id": s["external_id"], "fetched_at": s["fetched_at"].isoformat(),
                           "confidence": float(s["confidence"]) if s["confidence"] is not None else None,
                           "urls": (s["fields"] or {}).get("urls") or []} for s in sources]
        # 02 spec 4.2: an AI-only event may drive a recommendation only once
        # another source corroborates it or the owner confirms it.
        out["corroborated"] = bool(names - {"AI_WEB"})
        out["actionable"] = out["corroborated"] or out["owner_confirmed"]
    if changes is not None:
        out["changes"] = [{"at": c["at"].isoformat(), "field": c["field"], "old": c["old"], "new": c["new"],
                           "source": c["source"], "reason": c["reason"], "source_url": c.get("source_url")}
                          for c in changes]
    return out


def _visible_sources(cur: Any, hotel_slug: str, event_ids: list[str]) -> dict[str, list[dict]]:
    rows = clefs_db.fetch_all(
        "SELECT event_id, source, external_id, fields, confidence, fetched_at FROM dr_event_sources "
        "WHERE event_id = ANY(%s::uuid[]) AND (hotel_slug IS NULL OR hotel_slug = %s) ORDER BY id",
        (event_ids, hotel_slug), cur=cur,
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r["event_id"]), []).append(r)
    return out


def list_events(cur: Any, hotel_slug: str, *, start: date, end: date, categories: Iterable[str] | None = None,
                include_cancelled: bool = True, limit: int = 500) -> list[dict]:
    cats = [c for c in (categories or []) if c in CATEGORIES]
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_events WHERE merged_into IS NULL AND (hotel_slug IS NULL OR hotel_slug = %s) "
        "AND start_date <= %s AND end_date >= %s AND (%s OR category = ANY(%s)) AND (%s OR status <> 'CANCELLED') "
        "ORDER BY start_date, title LIMIT %s",
        (hotel_slug, end, start, not cats, cats, include_cancelled, limit), cur=cur,
    )
    srcs = _visible_sources(cur, hotel_slug, [str(r["id"]) for r in rows])
    return [serialize(r, srcs.get(str(r["id"]), [])) for r in rows]


def get_event(cur: Any, hotel_slug: str, event_id: str) -> dict:
    live = _live_id(cur, event_id) if _is_uuid(event_id) else None
    ev = clefs_db.fetch_one("SELECT * FROM dr_events WHERE id = %s AND (hotel_slug IS NULL OR hotel_slug = %s)",
                            (live, hotel_slug), cur=cur) if live else None
    if not ev:
        raise ClefsError(404, "Event not found", "event_not_found")
    ids = _family_ids(cur, live)
    changes = clefs_db.fetch_all(
        "SELECT * FROM dr_event_changes WHERE event_id = ANY(%s::uuid[]) ORDER BY at, id", (ids,), cur=cur)
    return serialize(ev, _visible_sources(cur, hotel_slug, [live]).get(live, []), changes)


def _is_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False


# ─── Manual entry, owner confirmation, removal ───

MANUAL_FIELDS = ("title", "category", "start", "end", "status", "venue_codes", "venue_text", "attendance",
                 "description", "urls", "moon_dependent", "uncertainty_days")


def create_manual(cur: Any, hotel_slug: str, data: dict) -> dict:
    cand = {k: data.get(k) for k in MANUAL_FIELDS if data.get(k) is not None}
    cand.setdefault("verification", "VERIFIED")
    res = ingest(cur, cand, source="MANUAL", external_id=f"{hotel_slug}:{uuid.uuid4()}", hotel_slug=hotel_slug,
                 payload={"entered": _json_safe(cand)})
    return {**get_event(cur, hotel_slug, res["event_id"]), "created": res["created"], "matched": res["matched"]}


def _own_source(cur: Any, hotel_slug: str, event_id: str, sources: tuple[str, ...]) -> dict | None:
    live = _live_id(cur, event_id) if _is_uuid(event_id) else None
    if not live:
        return None
    return clefs_db.fetch_one(
        "SELECT * FROM dr_event_sources WHERE event_id = %s AND hotel_slug = %s AND source = ANY(%s) "
        "ORDER BY id DESC LIMIT 1", (live, hotel_slug, list(sources)), cur=cur,
    )


def update_manual(cur: Any, hotel_slug: str, event_id: str, patch: dict, *, reason: str | None = None) -> dict:
    """Edit the hotel's own manual entry. Feed-owned events are not
    editable (the next sync would undo the edit); add a manual entry
    instead. `reason` says whether moved dates are a reschedule or a typo."""
    src = _own_source(cur, hotel_slug, event_id, ("MANUAL",))
    if not src:
        raise ClefsError(409, "Only events you entered by hand can be edited", "event_not_editable")
    base = _load_fields(src["fields"])
    if patch.get("venue_codes") is not None or patch.get("venue_text") is not None:
        for k in ("venue_codes", "lat", "lon", "emirate"):
            base.pop(k, None)  # a new venue re-derives its place
    cand = {**base, **{k: v for k, v in patch.items() if k in MANUAL_FIELDS and v is not None}}
    res = ingest(cur, cand, source="MANUAL", external_id=src["external_id"], hotel_slug=hotel_slug,
                 payload={"entered": _json_safe(cand)}, reason_hint=reason)
    return get_event(cur, hotel_slug, res["event_id"])


def confirm(cur: Any, hotel_slug: str, event_id: str, *, user_id: int | None) -> dict:
    """The owner vouches for a hotel-scoped event (an AI discovery): it
    becomes VERIFIED and actionable for this hotel. A global event already
    has a feed or curated source, so confirming it changes nothing."""
    ev = get_event(cur, hotel_slug, event_id)
    if ev["scope"] == "hotel" and not ev["owner_confirmed"]:
        clefs_db.execute(
            "UPDATE dr_events SET owner_confirmed_at = now(), owner_confirmed_by = %s, verification = 'VERIFIED', "
            "updated_at = now() WHERE id = %s AND hotel_slug = %s", (user_id, ev["id"], hotel_slug), cur=cur)
        _log_change(cur, ev["id"], "verification", ev["verification"], "VERIFIED", "MANUAL", "update")
        ev = get_event(cur, hotel_slug, ev["id"])
    return ev


def remove_own(cur: Any, hotel_slug: str, event_id: str) -> dict:
    """Drop this hotel's manual, ICS or AI rows for an event. A hotel-scoped
    event left with no source is deleted; a global one only loses the
    hotel's corroboration."""
    live = _live_id(cur, event_id) if _is_uuid(event_id) else None
    ev = clefs_db.fetch_one("SELECT * FROM dr_events WHERE id = %s AND (hotel_slug IS NULL OR hotel_slug = %s)",
                            (live, hotel_slug), cur=cur) if live else None
    if not ev:
        raise ClefsError(404, "Event not found", "event_not_found")
    n = clefs_db.execute(
        "DELETE FROM dr_event_sources WHERE event_id = %s AND hotel_slug = %s AND source = ANY(%s)",
        (live, hotel_slug, list(dedup.HOTEL_SCOPED_SOURCES)), cur=cur,
    )
    if n == 0:
        raise ClefsError(409, "This event comes from a shared feed; dismiss it instead", "event_not_editable")
    left = clefs_db.fetch_one("SELECT count(*) AS n FROM dr_event_sources WHERE event_id = %s", (live,), cur=cur)["n"]
    if ev["hotel_slug"] and left == 0:
        clefs_db.execute("DELETE FROM dr_event_changes WHERE event_id = %s", (live,), cur=cur)
        clefs_db.execute("DELETE FROM dr_events WHERE id = %s", (live,), cur=cur)
        return {"deleted": True, "event_id": live}
    if ev["hotel_slug"]:
        recompute(cur, live)
    return {"deleted": False, "event_id": live, "sources_removed": n}


# ─── Upcoming events and free-text lookup (api.py doors) ───

# fn(cur, hotel, events: list[dict]) -> {event_id: score 0..1}. The
# relevance model (02 spec 4.3) registers here; until it does, quick_score
# gives a distance-and-size estimate so Visibility and Inquiry Guard get a
# sensible list.
_SCORERS: list[Callable[[Any, Any, list[dict]], dict[str, float]]] = []
_GENERIC_WORDS = {"week", "weekend", "show", "expo", "event", "events", "festival", "conference", "days", "day",
                  "time", "period", "during", "season", "dubai", "abu", "dhabi", "uae", "exhibition", "the", "for",
                  "of", "in", "at", "and", "2026", "2027", "this", "next", "year"}
_SYNONYMS = {"f1": "formula 1", "nye": "new year", "gp": "grand prix", "national day": "eid al etihad"}


def register_scorer(fn: Callable[[Any, Any, list[dict]], dict[str, float]]) -> None:
    if fn not in _SCORERS:
        _SCORERS.append(fn)


def quick_score(ev: dict, hotel: Any) -> float:
    """Provisional relevance: distance decay by crowd size times scale. A
    holiday or a city-wide event has no place, so it gets a flat weight."""
    if ev["status"] == "CANCELLED":
        return 0.0
    flat = {"PUBLIC_HOLIDAY": 0.5, "RELIGIOUS": 0.5, "SCHOOL_HOLIDAY": 0.4, "CITYWIDE": 0.6,
            "SOURCE_MARKET_HOLIDAY": 0.3}
    att = ev.get("attendance_est")
    scale = min(1.0, math.log10(att) / 5) if att and att > 1 else (
        min(1.0, ev["phq_rank"] / 100) if ev.get("phq_rank") else 0.6)
    if ev["lat"] is None or ev["lon"] is None or getattr(hotel, "lat", None) is None:
        return round(flat.get(ev["category"], 0.4) * (scale if att or ev.get("phq_rank") else 1.0), 3)
    d = dedup.haversine_km(float(hotel.lat), float(hotel.lng), float(ev["lat"]), float(ev["lon"]))
    lam = 25.0 if (att or 0) > 100_000 else 15.0 if (att or 0) >= 10_000 else 8.0
    return round(math.exp(-d / lam) * scale, 3)


def upcoming(cur: Any, hotel: Any, *, days: int, min_score: float, today: date) -> list[dict]:
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_events WHERE merged_into IS NULL AND (hotel_slug IS NULL OR hotel_slug = %s) "
        "AND status <> 'CANCELLED' AND end_date >= %s AND start_date <= %s ORDER BY start_date, title",
        (hotel.slug, today, today + timedelta(days=days)), cur=cur,
    )
    scores: dict[str, float] = {}
    for scorer in _SCORERS:
        scores.update({str(k): float(v) for k, v in (scorer(cur, hotel, rows) or {}).items()})
    out = []
    for r in rows:
        score = scores.get(str(r["id"]), quick_score(r, hotel))
        if score >= min_score:
            out.append({"event_id": str(r["id"]), "name": r["title"], "start": r["start_date"].isoformat(),
                        "end": r["end_date"].isoformat(), "score": round(score, 3), "source": r["date_source"],
                        "category": r["category"], "status": r["status"], "verification": r["verification"]})
    return out


def _query_tokens(text: str) -> list[str]:
    t = " " + dedup._ascii(text or "").lower() + " "
    for k, v in _SYNONYMS.items():
        t = t.replace(f" {k} ", f" {v} ")
    return [w for w in re.findall(r"[a-z0-9]+", t) if w not in _GENERIC_WORDS]


def find(cur: Any, hotel_slug: str, *, text: str, today: date) -> dict | None:
    """The event a guest names ("GITEX week", "during F1"): every remaining
    query word appears in the title, or the query is the title's acronym, or
    the titles match by the dedup rule. Nearest upcoming wins a tie."""
    words = _query_tokens(text)
    if not words:
        return None
    rows = clefs_db.fetch_all(
        "SELECT id, title, start_date, end_date, attendance_est FROM dr_events WHERE merged_into IS NULL "
        "AND (hotel_slug IS NULL OR hotel_slug = %s) AND status <> 'CANCELLED' AND end_date >= %s "
        "AND start_date <= %s", (hotel_slug, today - timedelta(days=7), today + timedelta(days=400)), cur=cur,
    )
    best: tuple | None = None
    for r in rows:
        title_words = set(dedup.norm_title(r["title"]).split()) | set(_query_tokens(r["title"]))
        initials = dedup._initials(r["title"])
        if set(words) <= title_words or (len(words) == 1 and words[0] == initials and len(initials) >= 3):
            score = 1.0
        else:
            score = dedup.title_score(text, r["title"])
        if score < dedup.TITLE_MATCH:
            continue
        # Ties: the title with fewest unasked words ("GITEX" is GITEX Global,
        # not GITEX Global Summit), then the bigger crowd, then the nearest.
        key = (score, -len(title_words - set(words)), r["attendance_est"] or 0, -abs((r["start_date"] - today).days))
        if best is None or key > best[0]:
            best = (key, r)
    if best is None:
        return None
    r = best[1]
    return {"event_id": str(r["id"]), "name": r["title"], "start": r["start_date"].isoformat(),
            "end": r["end_date"].isoformat()}
