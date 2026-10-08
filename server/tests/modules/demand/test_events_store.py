"""Demand events store: curated seed, dedup merges across sources, change
history and dr.event.changed (02 spec 4.2)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from clefs import db as clefs_db
from clefs.modules.demand import curated, events

DXB = ZoneInfo("Asia/Dubai")
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=DXB)


@pytest.fixture
def seeded(cur, clock):
    clock.freeze(NOW)
    return curated.sync(cur, force=True)


def _event(cur, key: str, source: str = "CURATED") -> dict:
    return clefs_db.fetch_one(
        "SELECT e.* FROM dr_event_sources s JOIN dr_events e ON e.id = s.event_id WHERE s.source = %s "
        "AND s.external_id = %s", (source, key), cur=cur)


# ─── Curated seed ───


def test_curated_sync_loads_venues_events_and_holidays(cur, seeded):
    assert seeded["venues"] >= 8 and seeded["events"] >= 18 and seeded["holidays"] >= 20
    gitex = _event(cur, "gitex-global-2026")
    assert gitex["venue_codes"] == ["DEC"]
    assert (gitex["start_date"], gitex["end_date"]) == (date(2026, 12, 7), date(2026, 12, 11))
    assert gitex["attendance_est"] == 200000 and gitex["verification"] == "VERIFIED"
    dsf = _event(cur, "dsf-2026-27")
    assert dsf["verification"] == "UNVERIFIED" and float(dsf["confidence"]) <= 0.5
    eid = _event(cur, "ae-eid-al-fitr-2027")
    assert eid["category"] == "RELIGIOUS" and eid["moon_dependent"] and eid["date_uncertainty_days"] == 1
    dec = clefs_db.fetch_one("SELECT * FROM dr_venues WHERE code = 'DWTC'", cur=cur)
    assert "RED_LINE" in dec["corridor_tags"]
    # Foreign holidays stay out of the event calendar.
    assert _event(cur, "in-diwali-2026") is None
    assert clefs_db.fetch_one("SELECT 1 FROM dr_holidays WHERE seed_key = 'in-diwali-2026'", cur=cur)


def test_curated_sync_is_idempotent(cur, seeded):
    n = clefs_db.fetch_one("SELECT count(*) AS n FROM dr_events", cur=cur)["n"]
    assert curated.sync(cur)["skipped"] is True
    again = curated.sync(cur, force=True)
    assert again["created"] == 0 and again["changes"] == 0
    assert clefs_db.fetch_one("SELECT count(*) AS n FROM dr_events", cur=cur)["n"] == n


def test_atm_rescheduled_twice_keeps_original_dates(cur, seeded, hotel_uae):
    atm = _event(cur, "atm-2026")
    assert atm["status"] == "RESCHEDULED"
    assert (atm["original_start"], atm["original_end"]) == (date(2026, 5, 4), date(2026, 5, 7))
    assert (atm["start_date"], atm["end_date"]) == (date(2026, 9, 14), date(2026, 9, 17))
    detail = events.get_event(cur, hotel_uae.slug, str(atm["id"]))
    moves = [c for c in detail["changes"] if c["field"] == "dates"]
    assert len(moves) == 2
    assert moves[0]["old"] == {"start": "2026-05-04", "end": "2026-05-07"}
    assert moves[1]["new"] == {"start": "2026-09-14", "end": "2026-09-17"}
    curated.sync(cur, force=True)
    assert len([c for c in events.get_event(cur, hotel_uae.slug, str(atm["id"]))["changes"]
                if c["field"] == "dates"]) == 2


# ─── Reschedules, corrections, cancellations, dr.event.changed ───


def _phq(title="Big Tech Expo", start="2026-11-02", end="2026-11-04", **kw) -> dict:
    return {"title": title, "category": "EXPO", "start": date.fromisoformat(start), "end": date.fromisoformat(end),
            "venue_codes": ["DWTC"], **kw}


def _add_action(cur, slug: str, event_id: str, status: str) -> int:
    return clefs_db.fetch_one(
        "INSERT INTO actions (hotel_slug, module, type, title, params, status) VALUES (%s, 'demand', "
        "'dr.rate_change', 'Raise BAR', %s, %s) RETURNING id",
        (slug, clefs_db.Json({"driver_event_ids": [event_id]}), status), cur=cur)["id"]


def test_same_source_moving_dates_is_a_reschedule_and_signals(cur, clock, hotel_uae):
    clock.freeze(NOW)
    curated.sync(cur, force=True)
    first = events.ingest(cur, _phq(), source="PREDICTHQ", external_id="p1")
    _add_action(cur, hotel_uae.slug, first["event_id"], "approved")
    moved = events.ingest(cur, _phq(start="2026-11-16", end="2026-11-18"), source="PREDICTHQ", external_id="p1")
    assert [c["reason"] for c in moved["changes"]] == ["rescheduled", "status"]
    ev = events.get_event(cur, hotel_uae.slug, first["event_id"])
    assert ev["status"] == "RESCHEDULED"
    assert (ev["original_start"], ev["original_end"]) == ("2026-11-02", "2026-11-04")
    sig = clefs_db.fetch_one("SELECT * FROM signals WHERE hotel_slug = %s AND kind = 'dr.event.changed'",
                             (hotel_uae.slug,), cur=cur)
    assert sig["section"] == "money_available" and "moved to 16 Nov" in sig["title"]
    assert sig["subject_id"] == first["event_id"]
    # Cancelled later: a new signal (new dedupe key), service_risk once nothing approved depends on it.
    clefs_db.execute("UPDATE actions SET status = 'proposed' WHERE hotel_slug = %s", (hotel_uae.slug,), cur=cur)
    events.ingest(cur, _phq(start="2026-11-16", end="2026-11-18", status="CANCELLED"), source="PREDICTHQ",
                  external_id="p1")
    sigs = clefs_db.fetch_all("SELECT * FROM signals WHERE hotel_slug = %s AND kind = 'dr.event.changed' ORDER BY id",
                              (hotel_uae.slug,), cur=cur)
    assert len(sigs) == 2 and sigs[1]["section"] == "service_risk" and "cancelled" in sigs[1]["title"]
    ev = events.get_event(cur, hotel_uae.slug, first["event_id"])
    assert ev["status"] == "CANCELLED" and ev["original_start"] == "2026-11-02"


def test_better_source_overriding_dates_is_a_correction(cur, clock, hotel_uae):
    clock.freeze(NOW)
    tm = events.ingest(cur, _phq(title="Airport Show", start="2026-10-12", end="2026-10-13"),
                       source="TICKETMASTER", external_id="t1")
    curated.sync(cur, force=True)  # the curated Airport Show is 12-14 Oct
    ev = events.get_event(cur, hotel_uae.slug, tm["event_id"])
    assert ev["end_date"] == "2026-10-14" and ev["date_source"] == "CURATED" and ev["status"] == "SCHEDULED"
    assert [c["reason"] for c in ev["changes"] if c["field"] == "dates"] == ["correction"]
    # Nothing depended on it: no signal.
    assert not clefs_db.fetch_one("SELECT 1 FROM signals WHERE kind = 'dr.event.changed'", cur=cur)


def test_ai_and_manual_rows_never_edit_a_global_event(cur, clock, hotel_uae):
    clock.freeze(NOW)
    g = events.ingest(cur, _phq(), source="PREDICTHQ", external_id="p1")
    m = events.ingest(cur, _phq(title="Big Tech Expo 2026", start="2026-11-03", end="2026-11-05"),
                      source="MANUAL", external_id=f"{hotel_uae.slug}:m1", hotel_slug=hotel_uae.slug)
    assert m["event_id"] == g["event_id"] and m["matched"] and not m["changes"]
    ev = events.get_event(cur, hotel_uae.slug, g["event_id"])
    assert (ev["start_date"], ev["end_date"]) == ("2026-11-02", "2026-11-04")
    assert sorted(s["source"] for s in ev["sources"]) == ["MANUAL", "PREDICTHQ"]


def test_global_record_folds_an_earlier_hotel_entry(cur, clock, hotel_uae):
    clock.freeze(NOW)
    m = events.ingest(cur, _phq(), source="MANUAL", external_id=f"{hotel_uae.slug}:m1", hotel_slug=hotel_uae.slug)
    g = events.ingest(cur, _phq(title="BIG TECH EXPO"), source="PREDICTHQ", external_id="p1")
    assert g["event_id"] != m["event_id"]
    # The old id still resolves (references keep working) to the global row.
    ev = events.get_event(cur, hotel_uae.slug, m["event_id"])
    assert ev["id"] == g["event_id"] and sorted(s["source"] for s in ev["sources"]) == ["MANUAL", "PREDICTHQ"]
    assert any(c["field"] == "merge" for c in ev["changes"])
