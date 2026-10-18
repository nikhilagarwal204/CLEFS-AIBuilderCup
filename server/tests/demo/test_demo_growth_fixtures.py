"""Demo seed, growth part: the vendor-shaped fixtures parse with each
module's own parser, the AI stand-in extracts only what the text says, and
the plant list is a constant of the anchor (no database needed)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from clefs.seed.demo_uae import growth

ANCHOR = date(2026, 9, 27)


def test_predicthq_fixtures_parse():
    from clefs.modules.demand.adapters import phq_events, phq_features

    evs = [phq_events.parse_event(e) for e in growth.fixture("phq_events.json")["results"]]
    assert [(e["title"], e["start"].isoformat(), e["end"].isoformat()) for e in evs] == [
        ("GITEX Global 2026", "2026-12-07", "2026-12-11"), ("CABSAT 2026", "2026-10-05", "2026-10-07"),
        ("Eid Al Etihad (National Day)", "2026-12-02", "2026-12-03")]
    # Only real public events: a synthetic one would reach every hotel's feed.
    assert not any("(DEMO)" in e["title"] for e in evs)
    rows = phq_features.parse_rows(growth.fixture("phq_features.json"))
    assert len({d for d, _f, _v in rows}) == 15
    assert max(v for d, f, v in rows if f == "phq_attendance_expos") == 180000


def test_google_hotels_fixture_gives_the_plant_and_the_decoy():
    from clefs.modules.visibility import google_hotels as gh

    fx = growth.fixture("google_hotels.json")
    plant = gh.analyse_prices(fx["details"]["2026-10-02"], "AED")
    assert (plant["official_price_minor"], plant["min_other_price_minor"], plant["parity_gap_minor"]) == \
        (45000, 41200, 3800)
    decoy = gh.analyse_prices(fx["details"]["2026-10-26"], "AED")
    assert gh.q3(decoy["parity_gap_pct"]) == Decimal("0.781") and not gh.is_breach(decoy["parity_gap_pct"], 1)
    assert gh.analyse_prices(fx["details"]["2026-10-27"], "AED")["official_present"] is False
    organic, sponsored = gh.organic_properties(fx["area_page"])
    assert gh.rank_in(organic, fx["property_token"]) == 5 and sponsored == 2


def test_gbp_body_parses_and_omits_nothing_it_should_keep():
    from clefs.modules.visibility import gbp

    fx = growth.fixture("gbp_performance.json")
    values = gbp.parse_daily(growth.gbp_body(fx, date(2026, 8, 1), date(2026, 8, 31)))
    assert len(values) == 31 * len(gbp.DAILY_METRICS)
    assert {m for _d, m in values} == set(gbp.DAILY_METRICS)
    assert len(gbp.parse_keywords({"searchKeywordsCounts": fx["keywords"]})) == 5


def test_ai_answers_parse_and_resolve_names():
    from clefs.modules.visibility import parsers

    fx = growth.fixture("ai_answers.json")
    d1 = fx["discovery_en"][0]
    for body, engine in ((growth.gemini_body(d1, "gemini-demo"), "gemini_web_search"),
                         (growth.ai_mode_body(d1), "serpapi_ai_mode")):
        parsed = parsers.parse(engine, body)
        assert not parsed.empty and parsed.citations
        known = [parsers.Known("self", ["Al Rigga Crest Hotel", "Al Rigga Crest"]),
                 parsers.Known("demo-creekview-inn", ["Creekview Inn"])]
        assert {e.key for e in parsers.resolve(parsed, known)} >= {"self", "demo-creekview-inn"}


def test_fact_stand_in_extracts_only_stated_claims():
    fx = growth.fixture("ai_answers.json")
    got = {a["key"]: growth.extract_facts(a["text"])["facts"] for a in fx["brand_en"]}
    assert [(f["fact_key"], f["value"]) for f in got["b1"]] == [("checkin_time", "14:00"), ("parking", "free")]
    assert [(f["fact_key"], f["value"]) for f in got["b2"]] == [("checkin_time", "12:00")]
    assert [(f["fact_key"], f["value"]) for f in got["b3"]] == [("parking", "no")]
    assert growth.extract_facts("A pleasant hotel near the souk.") == {"facts": []}


def test_plants_are_constants_of_the_anchor():
    a = growth.expected_plants(ANCHOR)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(growth.expected_plants(ANCHOR), sort_keys=True,
                                                                     default=str)
    assert {p["module"] for p in a} == {"demand", "visibility", "finance", "brief"}
    assert sum(p["klass"] == "DECOY" for p in a) == 2
    # No database id: the manifest bytes depend only on the seed and the anchor.
    assert not any(k == "id" or k.endswith("_id") for p in a for k in p["subjects"])
