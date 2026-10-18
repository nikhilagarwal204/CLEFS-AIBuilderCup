"""
AI answer parsers, one per engine (04 spec 5.5 "Parsing"). `parse(engine,
body)` gives a Parsed; `common.resolve` then finds the hotels it names.
"""

from __future__ import annotations

from clefs.modules.visibility.parsers import ai_mode, gemini, perplexity
from clefs.modules.visibility.parsers.common import (
    PARSER_VERSION,
    Citation,
    Entity,
    Known,
    Parsed,
    classify_domain,
    domain_of,
    normalise,
    resolve,
    same_name,
    self_cited,
    split_items,
)

_BY_ENGINE = {
    "gemini_web_search": gemini.parse,
    "perplexity_sonar": perplexity.parse,
    "serpapi_ai_mode": ai_mode.parse,
}


def parse(engine: str, body: dict) -> Parsed:
    return _BY_ENGINE[engine](body or {})


__all__ = [
    "PARSER_VERSION", "Citation", "Entity", "Known", "Parsed", "classify_domain", "domain_of", "normalise",
    "parse", "resolve", "same_name", "self_cited", "split_items",
]
