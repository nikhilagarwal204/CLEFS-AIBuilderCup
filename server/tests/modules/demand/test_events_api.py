"""demand.api event doors (upcoming_events, find_event), manual entry, ICS
import (direct and through core ingest) and source markets."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from clefs import db as clefs_db
from clefs.core.errors import ClefsError
from clefs.modules.demand import api, curated, events, ics, markets

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:gitex@test
SUMMARY:GITEX Global
DTSTART;VALUE=DATE:20261207
DTEND;VALUE=DATE:20261212
LOCATION:Dubai Exhibition Centre, Expo City Dubai
END:VEVENT
BEGIN:VEVENT
UID:gala@test
SUMMARY:Pharma Leaders Summit
DTSTART:20261021T060000Z
DTEND:20261022T140000Z
LOCATION:Dubai World Trade Centre
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def hotel(cur, clock, hotel_uae):
    from clefs.core.hotels import get_hotel, update_hotel

    clock.freeze(NOW)
    update_hotel(cur, hotel_uae.slug, {"lat": 25.2697, "lng": 55.3095})
    curated.sync(cur, force=True)
    return get_hotel(cur, hotel_uae.slug, fresh=True)


# ─── api.py ───


def test_upcoming_events_scores_and_filters(cur, hotel):
    rows = api.upcoming_events(cur, hotel.slug, days=90)
    names = {r["name"] for r in rows}
    # A Deira hotel: DWTC shows 5 km away score; GITEX at Expo City (39 km)
    # only clears a low bar even with 200,000 visitors.
    assert "Big 5 Global" in names and "GITEX Global Summit" in names and "GITEX Global" not in names
    assert "GITEX Global" in {r["name"] for r in api.upcoming_events(cur, hotel.slug, days=90, min_score=0.1)}
    assert "Arabian Travel Market" not in names  # already over
    assert all(r["score"] >= 0.3 for r in rows)
    assert set(rows[0]) >= {"event_id", "name", "start", "end", "score", "source"}
    assert api.upcoming_events(cur, "no-such-hotel") == []
    # A registered scorer (the relevance model) replaces the estimate.
    gitex = next(r for r in rows if r["name"] == "Big 5 Global")
    events.register_scorer(lambda cur, h, evs: {e["id"]: 0.0 for e in evs})
    try:
        assert api.upcoming_events(cur, hotel.slug, days=90) == []
        assert api.upcoming_events(cur, hotel.slug, days=90, min_score=0.0)[0]["score"] == 0.0
    finally:
        events._SCORERS.clear()
    assert gitex["source"] == "CURATED"


def test_find_event_resolves_guest_phrases(cur, hotel):
    assert api.find_event(cur, hotel.slug, text="GITEX week")["name"] == "GITEX Global"
    assert api.find_event(cur, hotel.slug, text="during the F1")["name"] == "Formula 1 Abu Dhabi Grand Prix"
    assert api.find_event(cur, hotel.slug, text="big 5")["name"] == "Big 5 Global"
    assert api.find_event(cur, hotel.slug, text="DSF")["name"] == "Dubai Shopping Festival"
    assert api.find_event(cur, hotel.slug, text="national day")["start"] == "2026-12-02"
    assert api.find_event(cur, hotel.slug, text="my cousin's wedding") is None
    assert api.find_event(cur, hotel.slug, text="  ") is None


# ─── Manual entry ───


def test_manual_event_edit_reschedule_and_remove(cur, hotel):
    ev = events.create_manual(cur, hotel.slug, {"title": "Owners' family wedding block", "category": "COMMUNITY",
                                                "start": date(2026, 11, 5), "end": date(2026, 11, 7)})
    assert ev["created"] and ev["scope"] == "hotel" and ev["verification"] == "VERIFIED"
    fixed = events.update_manual(cur, hotel.slug, ev["id"], {"end": date(2026, 11, 8)}, reason="correction")
    assert fixed["status"] == "SCHEDULED" and fixed["changes"][-1]["reason"] == "correction"
    moved = events.update_manual(cur, hotel.slug, ev["id"], {"start": date(2026, 11, 12), "end": date(2026, 11, 14)},
                                 reason="rescheduled")
    assert moved["status"] == "RESCHEDULED" and moved["original_start"] == "2026-11-05"
    gitex = api.find_event(cur, hotel.slug, text="GITEX")
    with pytest.raises(ClefsError) as exc:
        events.update_manual(cur, hotel.slug, gitex["event_id"], {"title": "x"})
    assert exc.value.code == "event_not_editable"
    assert events.remove_own(cur, hotel.slug, ev["id"])["deleted"] is True
    with pytest.raises(ClefsError):
        events.get_event(cur, hotel.slug, ev["id"])


def test_manual_duplicate_of_a_curated_event_corroborates(cur, hotel):
    ev = events.create_manual(cur, hotel.slug, {"title": "Gitex Global 2026", "category": "EXPO",
                                                "start": date(2026, 12, 7), "venue_text": "Expo City"})
    assert ev["matched"] and ev["scope"] == "global" and ev["end_date"] == "2026-12-11"
    out = events.remove_own(cur, hotel.slug, ev["id"])
    assert out == {"deleted": False, "event_id": ev["id"], "sources_removed": 1}


def test_invalid_candidates_are_refused(cur, hotel):
    for bad in ({"title": "", "category": "EXPO", "start": date(2026, 11, 1)},
                {"title": "X show", "category": "PARTY", "start": date(2026, 11, 1)},
                {"title": "X show", "category": "EXPO", "start": date(2026, 11, 3), "end": date(2026, 11, 1)}):
        with pytest.raises(ClefsError) as exc:
            events.create_manual(cur, hotel.slug, bad)
        assert exc.value.code == "invalid_event"


# ─── ICS ───


def test_ics_import_is_idempotent_and_logs_moves(cur, hotel):
    out = ics.import_ics(cur, hotel.slug, ICS, tz=hotel.tz)
    assert out["imported"] == 2 and out["matched"] == 1 and out["created"] == 1
    summit = events.get_event(cur, hotel.slug, out["event_ids"][1])
    assert summit["category"] == "CONFERENCE" and summit["venue_codes"] == ["DWTC"]
    assert (summit["start_date"], summit["end_date"]) == ("2026-10-21", "2026-10-22")
    again = ics.import_ics(cur, hotel.slug, ICS, tz=hotel.tz)
    assert again["created"] == 0 and again["event_ids"] == out["event_ids"]
    moved = ICS.replace("20261021T060000Z", "20261104T060000Z").replace("20261022T140000Z", "20261105T140000Z")
    assert ics.import_ics(cur, hotel.slug, moved, tz=hotel.tz)["rescheduled"] == 1
    with pytest.raises(ClefsError):
        ics.import_ics(cur, hotel.slug, "BEGIN:VCALENDAR\nEND:VCALENDAR\n")


def test_ics_through_core_ingest_commit(cur, hotel):
    from clefs import files
    from clefs.core.ingest import batches, commit_batch, registered_handlers
    from clefs.modules.demand import events_jobs  # noqa: F401  (module.py imports it in the app)

    assert registered_handlers()["ICS"] == "clefs.modules.demand.ics.commit_ics"
    ref = files.put_file(cur, hotel.slug, ICS.encode(), filename="events.ics", content_type="text/calendar",
                         purpose="ingest")
    b = batches.create_batch(cur, hotel.slug, file_id=ref.id, kind="ICS")
    assert b["status"] == "REVIEW"
    done = commit_batch(cur, hotel.slug, b["id"])
    assert done["status"] == "COMMITTED"
    row = clefs_db.fetch_one("SELECT e.end_date FROM dr_event_sources s JOIN dr_events e ON e.id = s.event_id "
                             "WHERE s.source = 'ICS' AND s.external_id = %s", (f"{hotel.slug}:gitex@test",), cur=cur)
    assert row["end_date"] == date(2026, 12, 11)  # the exclusive all-day DTEND lost its day


# ─── Source markets and holidays ───


def test_source_markets_default_set_and_holidays(cur, hotel):
    d = markets.get_shares(cur, hotel.slug)
    assert d["is_default"] and {m["market_code"]: m["share_pct"] for m in d["markets"]}["WEU"] == "20"
    out = markets.set_shares(cur, hotel.slug, [{"market_code": "in", "share_pct": 30},
                                               {"market_code": "GCC", "share_pct": 25}], user_id=None)
    assert not out["is_default"] and [m["market_code"] for m in out["markets"]] == ["IN", "GCC"]
    hol = markets.market_holidays(cur, hotel.slug, start=date(2026, 11, 1), end=date(2026, 11, 30))
    assert {(h["name"], h["market_code"]) for h in hol} >= {("Diwali", "IN"), ("Saudi autumn school break", "GCC")}
    with pytest.raises(ClefsError):
        markets.set_shares(cur, hotel.slug, [{"market_code": "IN", "share_pct": 70},
                                             {"market_code": "GB", "share_pct": 40}], user_id=None)
    with pytest.raises(ClefsError):
        markets.set_shares(cur, hotel.slug, [{"market_code": "NOWHERE", "share_pct": 5}], user_id=None)
