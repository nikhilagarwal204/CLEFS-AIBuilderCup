"""AI answer parsers per engine, entity resolution and domain classes (04 spec 5.5, 5.12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clefs.modules.visibility import parsers
from clefs.modules.visibility.parsers import Known, classify_domain, normalise, resolve, same_name

FIX = Path(__file__).parent / "fixtures"
KNOWN = [
    Known("self", ["Test Hotel Dubai", "فندق تست دبي"], host="testhoteldubai.example", place_id="ChIJself"),
    Known("c1", ["Palm Vista Hotel"], host="palmvista.example", place_id="ChIJpalm"),
    Known("c2", ["Rove Downtown"]),
]


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_gemini_parser_citations_and_entities():
    p = parsers.parse("gemini_web_search", _load("gemini_web_search.json"))
    assert p.model_id == "gemini-2.5-flash" and p.searched is True and not p.empty
    inline = [c for c in p.citations if c.kind == "citation"]
    assert len(inline) == 3
    assert all(c.start_index is not None for c in inline)
    # booking.com city page was read but not cited inline: kept as a source
    assert any(c.kind == "source" and "booking.com/city" in c.url for c in p.citations)
    ents = resolve(p, KNOWN)
    assert [(e.key, e.position) for e in ents] == [("c1", 1), ("self", 2), ("c2", 3)]
    assert parsers.self_cited(p, "testhoteldubai.example")


def test_perplexity_parser_anchors_markers():
    p = parsers.parse("perplexity_sonar", _load("perplexity_sonar.json"))
    assert p.model_id == "sonar"
    anchored = [c for c in p.citations if c.kind == "citation"]
    assert [c.url.split("/")[2] for c in anchored] == [
        "www.expedia.com", "testhoteldubai.example", "www.tripadvisor.com", "www.reddit.com"]
    assert any(c.kind == "source" and "wikipedia" in c.url for c in p.citations)
    ents = resolve(p, KNOWN)
    assert [e.key for e in ents] == ["c2", "self", "other"]
    assert ents[2].name == "Grand Plaza Hotel"


def test_ai_mode_parser_rebuilds_blocks():
    p = parsers.parse("serpapi_ai_mode", _load("serpapi_ai_mode.json"))
    assert p.model_id == "google_ai_mode"
    assert "**Test Hotel Dubai**" in p.answer_text
    ents = resolve(p, KNOWN)
    assert [e.key for e in ents] == ["c1", "self", "other"]
    assert not parsers.self_cited(p, "testhoteldubai.example")


def test_resolution_by_place_id_when_the_name_is_unknown():
    p = parsers.Parsed(answer_text="1. **The Blue Building** - near the creek.",
                       citations=[parsers.Citation(url="https://maps.google.com/?cid=1&place_id=ChIJself",
                                                   start_index=3, end_index=10)])
    assert [(e.key, e.via) for e in resolve(p, KNOWN)] == [("self", "place_id")]


def test_ai_fallback_names_used_only_when_nothing_resolved():
    p = parsers.Parsed(answer_text="Many travellers like staying near the fountain.")
    assert resolve(p, KNOWN) == []
    ents = resolve(p, KNOWN, extracted_names=["test hotel dubai", "Some Inn"])
    assert [(e.key, e.via) for e in ents] == [("self", "ai"), ("other", "ai")]


@pytest.mark.parametrize(("a", "b"), [
    ("Test Hotel Dubai (DEMO)", "test dubai"),
    ("فُنْدُق الإمارات", "فندق الامارات"),
    ("Café Royale Hotel", "cafe royale"),
])
def test_normalisation(a, b):
    assert normalise(a) == normalise(b)
    assert same_name(a, b)


def test_arabic_taa_marbuta_and_alef():
    assert normalise("مدينة أبوظبي") == normalise("مدينه ابوظبي")


@pytest.mark.parametrize(("domain", "cls"), [
    ("booking.com", "ota"), ("m.agoda.com", "ota"), ("tripadvisor.co.uk", "review"),
    ("testhoteldubai.example", "official"), ("maps.google.com", "google"), ("reddit.com", "ugc"),
    ("en.wikipedia.org", "wiki"), ("timeoutdubai.com", "press_other"), ("", "press_other"),
])
def test_domain_classes(domain, cls):
    assert classify_domain(domain, official_hosts={"testhoteldubai.example"}) == cls
