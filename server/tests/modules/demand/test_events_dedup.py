"""Dedup rules (02 spec 4.2): canonical key, title and acronym matching,
venue and date overlap, and field precedence. Pure functions, no database."""

from __future__ import annotations

from datetime import date, datetime, timezone

from clefs.modules.demand import dedup

VENUES = [
    {"code": "DWTC", "name": "Dubai World Trade Centre", "aliases": ["Trade Centre", "DWTC"], "lat": 25.2285,
     "lon": 55.2866},
    {"code": "DEC", "name": "Dubai Exhibition Centre", "aliases": ["Expo City Dubai", "Expo City", "Dubai South"],
     "lat": 24.9635, "lon": 55.1487},
    {"code": "DWC", "name": "Dubai World Central", "aliases": ["DWC", "Al Maktoum International Airport"],
     "lat": 24.8960, "lon": 55.1610},
]


def test_canonical_key_drops_year_edition_and_the():
    a = dedup.canonical_key("The 46th edition GITEX Global 2026", ["DEC"], date(2026, 12, 7))
    b = dedup.canonical_key("GITEX Global", ["DEC"], date(2026, 12, 9))
    assert a == b == "gitex-global|DEC|2026-W50"
    assert dedup.canonical_key("Holiday", [], date(2027, 1, 1)).endswith("|-|2026-W53")


def test_title_matching_and_acronyms():
    assert dedup.title_score("GITEX Global 2026", "GITEX GLOBAL") == 1.0
    assert dedup.title_score("Arabian Travel Market", "ATM 2026") == dedup.TITLE_MATCH
    assert dedup.title_score("Dubai Shopping Festival", "DSF") == dedup.TITLE_MATCH
    assert dedup.title_score("Big 5 Global", "GITEX Global") < dedup.TITLE_MATCH
    # A subset title matches but scores below an exact one.
    assert dedup.TITLE_MATCH <= dedup.title_score("GITEX Global", "GITEX Global Summit") < 1.0


def test_venue_resolution_prefers_the_longest_alias_and_coordinates():
    assert dedup.resolve_venue("Hall 3, Expo City Dubai", VENUES) == "DEC"
    assert dedup.resolve_venue("Dubai World Central, Jebel Ali", VENUES) == "DWC"
    assert dedup.resolve_venue("somewhere in Deira", VENUES) is None
    assert dedup.nearest_venue(25.2279, 55.2872, VENUES) == "DWTC"
    assert dedup.nearest_venue(25.30, 55.40, VENUES) is None


def _ev(title, start, end, venues=(), lat=None, lon=None):
    return {"title": title, "start_date": start, "end_date": end, "venue_codes": list(venues), "lat": lat, "lon": lon}


def _cand(title, start, end, venues=(), lat=None, lon=None):
    return {"title": title, "start": start, "end": end, "venue_codes": list(venues), "lat": lat, "lon": lon}


def test_match_needs_overlap_venue_and_title():
    d = date
    gitex = _ev("GITEX Global", d(2026, 12, 7), d(2026, 12, 11), ["DEC"])
    summit = _ev("GITEX Global Summit", d(2026, 12, 4), d(2026, 12, 8), ["DWTC"])
    # Same title, other venue: no merge. Unknown venue: title and dates decide.
    assert dedup.match_score(_cand("GITEX Global", d(2026, 12, 7), d(2026, 12, 11), ["DWTC"]), gitex) == 0
    assert dedup.best_match(_cand("GITEX GLOBAL 2026", d(2026, 12, 7), d(2026, 12, 11)), [summit, gitex]) is gitex
    # No overlapping day: no merge.
    assert dedup.match_score(_cand("GITEX Global", d(2026, 12, 12), d(2026, 12, 13), ["DEC"]), gitex) == 0
    # Within 1 km without a venue code on one side.
    near = _cand("GITEX Global", d(2026, 12, 8), d(2026, 12, 8), lat=24.9640, lon=55.1480)
    assert dedup.match_score(near, gitex | {"lat": 24.9635, "lon": 55.1487}) > 0
    far = _cand("GITEX Global", d(2026, 12, 8), d(2026, 12, 8), lat=25.20, lon=55.27)
    assert dedup.match_score(far, gitex | {"lat": 24.9635, "lon": 55.1487}) == 0


def _src(source, start, end, fetched="2026-09-01", **fields):
    return {"source": source, "fetched_at": datetime.fromisoformat(fetched).replace(tzinfo=timezone.utc),
            "fields": {"title": fields.pop("title", "Show"), "category": "EXPO", "start": start, "end": end, **fields}}


def test_precedence_attendance_verification_and_confidence():
    d = date
    r = dedup.resolve_fields([
        _src("AI_WEB", d(2026, 12, 6), d(2026, 12, 12), attendance=900000, confidence=0.9, urls=["https://a.ae"]),
        _src("TICKETMASTER", d(2026, 12, 7), d(2026, 12, 10), urls=["https://tm.ae/x"]),
        _src("PREDICTHQ", d(2026, 12, 7), d(2026, 12, 11), attendance=180000, phq_rank=88),
        _src("CURATED", d(2026, 12, 7), d(2026, 12, 11), attendance=200000, verification="VERIFIED"),
    ])
    assert r["date_source"] == "CURATED" and r["phq_rank"] == 88
    assert (r["attendance_est"], r["attendance_source"]) == (200000, "CURATED")  # the AI crowd is not credible
    assert r["verification"] == "VERIFIED" and r["confidence"] == 0.9
    assert r["source_urls"] == ["https://tm.ae/x", "https://a.ae"]
    ai_only = dedup.resolve_fields([_src("AI_WEB", d(2026, 12, 6), d(2026, 12, 12), confidence=0.95)])
    assert ai_only["verification"] == "UNVERIFIED" and ai_only["confidence"] == 0.6
    # Within one source the newest fetch wins.
    newer = dedup.resolve_fields([_src("PREDICTHQ", d(2026, 1, 1), d(2026, 1, 2), fetched="2026-09-01"),
                                  _src("PREDICTHQ", d(2026, 1, 5), d(2026, 1, 6), fetched="2026-09-20")])
    assert newer["start_date"] == d(2026, 1, 5)
