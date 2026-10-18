"""
The curated UAE calendar (seeds/venue_events_2026q4_2027.yaml, 02 spec 4.2):
venues with corridor tags, the events the research listed, and holidays.
sync() is idempotent and cheap to re-run: an unchanged file (same sha256)
is skipped, and every row is keyed by its stable `key`.

UAE holidays (country AE) also become calendar events, because a national
or school holiday moves local demand; other countries' holidays stay in
dr_holidays only, where the source-market model reads them.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from clefs import db as clefs_db
from clefs.modules.demand import events

SEED_PATH = Path(__file__).with_name("seeds") / "venue_events_2026q4_2027.yaml"
_HOLIDAY_CATEGORY = {"PUBLIC": "PUBLIC_HOLIDAY", "SCHOOL": "SCHOOL_HOLIDAY", "RELIGIOUS": "RELIGIOUS"}
_DXB = ZoneInfo("Asia/Dubai")
STALE_AFTER_DAYS = 92


# ─── Loading ───


def load_seed(path: Path | str | None = None) -> tuple[dict, str]:
    raw = Path(path or SEED_PATH).read_bytes()
    return yaml.safe_load(raw) or {}, hashlib.sha256(raw).hexdigest()


def event_candidate(row: dict) -> dict:
    urls = [row["source_url"]] if row.get("source_url") else []
    cand = {
        "title": row["title"], "category": row["category"], "venue_codes": row.get("venues") or [],
        "emirate": row.get("emirate"), "start": row["start"], "end": row.get("end") or row["start"],
        "status": row.get("status"), "attendance": row.get("attendance"),
        "verification": row.get("verification", "VERIFIED"), "urls": urls,
        "description": "; ".join(x for x in (row.get("note"), row.get("attendance_note")) if x) or None,
        "moon_dependent": bool(row.get("moon_dependent")), "uncertainty_days": int(row.get("uncertainty_days") or 0),
    }
    if row.get("original"):
        cand["original_start"], cand["original_end"] = row["original"]["start"], row["original"]["end"]
    return cand


def holiday_candidate(row: dict) -> dict:
    return {
        "title": row["name"], "category": _HOLIDAY_CATEGORY[row["kind"]], "emirate": None,
        "start": row["start"], "end": row.get("end") or row["start"],
        "verification": row.get("verification", "VERIFIED"),
        "urls": [row["source_url"]] if row.get("source_url") else [], "description": row.get("note"),
        "moon_dependent": bool(row.get("moon_dependent")), "uncertainty_days": int(row.get("uncertainty_days") or 0),
    }


# ─── Sync ───


def sync(cur: Any, *, path: Path | str | None = None, force: bool = False) -> dict:
    seed, digest = load_seed(path)
    state = clefs_db.fetch_one("SELECT cursor FROM dr_sync_state WHERE source = 'CURATED' AND scope = 'seed'", cur=cur)
    if state and state["cursor"] == digest and not force:
        return {"skipped": True, "version": seed.get("version")}
    for v in seed.get("venues") or []:
        upsert_venue(cur, v)
    venue_rows = events.venues(cur)
    result = {"venues": len(seed.get("venues") or []), "events": 0, "holidays": 0, "created": 0, "changes": 0}
    for row in seed.get("events") or []:
        out = events.ingest(cur, event_candidate(row), source="CURATED", external_id=row["key"],
                            payload=row, venue_rows=venue_rows)
        _write_history(cur, out["event_id"], row.get("history") or [])
        result["events"] += 1
        result["created"] += int(out["created"])
        result["changes"] += len(out["changes"])
    for row in seed.get("holidays") or []:
        upsert_holiday(cur, row)
        result["holidays"] += 1
        if row["country"] == "AE":
            out = events.ingest(cur, holiday_candidate(row), source="CURATED", external_id=row["key"],
                                payload=row, venue_rows=venue_rows)
            result["created"] += int(out["created"])
    clefs_db.execute(
        "INSERT INTO dr_sync_state (source, scope, cursor, last_run_at, last_result) VALUES ('CURATED', 'seed', %s, now(), %s) "
        "ON CONFLICT (source, scope) DO UPDATE SET cursor = EXCLUDED.cursor, last_run_at = now(), "
        "last_result = EXCLUDED.last_result",
        (digest, clefs_db.Json(result)), cur=cur,
    )
    return {"skipped": False, "version": seed.get("version"), **result}


def upsert_venue(cur: Any, v: dict) -> None:
    clefs_db.execute(
        """INSERT INTO dr_venues (code, name, lat, lon, emirate, corridor_tags, aliases, capacity, feed_available,
               coords_verified, last_verified_at, source_url, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
               emirate = EXCLUDED.emirate, corridor_tags = EXCLUDED.corridor_tags, aliases = EXCLUDED.aliases,
               capacity = EXCLUDED.capacity, feed_available = EXCLUDED.feed_available,
               coords_verified = EXCLUDED.coords_verified, last_verified_at = EXCLUDED.last_verified_at,
               source_url = EXCLUDED.source_url, updated_at = now()""",
        (v["code"], v["name"], v.get("lat"), v.get("lon"), v.get("emirate"), list(v.get("corridor_tags") or []),
         list(v.get("aliases") or []), v.get("capacity"), bool(v.get("feed_available")),
         bool(v.get("coords_verified")), v.get("last_verified_at"), v.get("source_url")), cur=cur,
    )


def upsert_holiday(cur: Any, h: dict) -> None:
    clefs_db.execute(
        """INSERT INTO dr_holidays (country_code, name, start_date, end_date, kind, moon_dependent, uncertainty_days,
               verification, source_url, seed_key, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (seed_key) DO UPDATE SET country_code = EXCLUDED.country_code, name = EXCLUDED.name,
               start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date, kind = EXCLUDED.kind,
               moon_dependent = EXCLUDED.moon_dependent, uncertainty_days = EXCLUDED.uncertainty_days,
               verification = EXCLUDED.verification, source_url = EXCLUDED.source_url, updated_at = now()""",
        (h["country"].upper(), h["name"], h["start"], h.get("end") or h["start"], h["kind"],
         bool(h.get("moon_dependent")), int(h.get("uncertainty_days") or 0), h.get("verification", "VERIFIED"),
         h.get("source_url"), h["key"]), cur=cur,
    )


def _write_history(cur: Any, event_id: str, history: list[dict]) -> int:
    """The seed's known reschedules become dr_event_changes rows, once each
    (keyed by the new dates), dated when the move was announced."""
    written = 0
    for h in history:
        old = {"start": h["from"]["start"].isoformat(), "end": h["from"]["end"].isoformat()}
        new = {"start": h["to"]["start"].isoformat(), "end": h["to"]["end"].isoformat()}
        exists = clefs_db.fetch_one(
            "SELECT 1 FROM dr_event_changes WHERE event_id = %s AND field = 'dates' AND new = %s::jsonb",
            (event_id, clefs_db.Json(new)), cur=cur,
        )
        if exists:
            continue
        at = h.get("at")
        at_ts = datetime.combine(at, time(12, 0), tzinfo=_DXB) if isinstance(at, date) else None
        events._log_change(cur, event_id, "dates", old, new, "CURATED", "rescheduled", at=at_ts,
                           source_url=h.get("source_url"))
        written += 1
    return written


def stale_rows(seed: dict, today: date, *, max_age_days: int = STALE_AFTER_DAYS) -> list[dict]:
    """Rows of the seed whose last_verified_at is older than a quarter and
    that still lie ahead (a past event needs no re-check)."""
    out = []
    for section in ("venues", "events"):
        for row in seed.get(section) or []:
            checked = row.get("last_verified_at")
            ends = row.get("end") or row.get("start")
            if section == "events" and isinstance(ends, date) and ends < today:
                continue
            if not isinstance(checked, date) or checked < today - timedelta(days=max_age_days):
                out.append({"section": section, "key": row.get("key") or row.get("code"),
                            "last_verified_at": checked.isoformat() if isinstance(checked, date) else None})
    return out
