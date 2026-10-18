"""
Guardrails that run BEFORE any model sees the question (spec 7): guest-level
personal data is refused outright, and a few question shapes no metric can
answer (other hotels' future prices) are declined without spending a call.
The model can still decline anything else through answer_unsupported; these
rules exist so the most sensitive refusals never depend on a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from clefs.modules.ask.constants import MAX_QUESTION_CHARS

# "guest numbers" means a count, so bare number/id are not personal fields here.
_PII_FIELDS = r"(name|names|phone|phones|mobile|e-?mail|emails|contact|contacts|address|passport|details|whatsapp)"
_PII_PATTERNS = [
    re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.I),  # an email address
    re.compile(r"\+?\d[\d\s().-]{7,}\d"),  # a phone number
    re.compile(rf"\bguests?(?:'s|s')?\s+(?:\w+\s+){{0,2}}{_PII_FIELDS}\b", re.I),
    re.compile(rf"\b{_PII_FIELDS}\s+(?:number\s+|details\s+)?(?:of|for)\s+(?:the\s+)?(?:guest|mr|mrs|ms|miss|dr)\b", re.I),
    re.compile(r"\b[A-Z][a-z]+(?:'s|s')\s+(?:phone|mobile|number|e-?mail|contact|address|passport|room|booking)\b"),
    re.compile(r"\bwho\s+(?:is|was|are|were)\s+(?:staying|in\s+room|checked|the\s+guest)", re.I),
    re.compile(r"\bwho\s+stayed\b", re.I),
    re.compile(r"\bguest\s+in\s+room\s+\d+", re.I),
    re.compile(r"\b(?:list|show|give)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?guest\s*(?:names|list|contacts|emails|phones)\b", re.I),
    re.compile(r"\b(?:credit\s+card|card\s+number|cvv|passport\s+number)\b", re.I),
]
_FORECAST_OTHERS = [
    re.compile(r"\bcompetitors?(?:'s|s')?\b.*\b(?:will|going\s+to|next\s+year|forecast|predict)", re.I),
    re.compile(r"\b(?:will|going\s+to|predict|forecast)\b.*\bcompetitors?\b", re.I),
]
_SUGGEST_PII = [
    "How many returning guests stayed last month?",
    "Direct contact capture rate this month",
]
_SUGGEST_FORECAST = [
    "Median competitor rate for the next 30 days",
    "Room revenue on the books for the next 90 days",
]


@dataclass
class Verdict:
    ok: bool
    code: str | None = None  # guest_pii | forecast_others | too_long | empty
    message: str = ""
    suggestions: list[str] = field(default_factory=list)


def check_question(question: str) -> Verdict:
    q = (question or "").strip()
    if not q:
        return Verdict(False, "empty", "Ask a question about your hotel's numbers.")
    if len(q) > MAX_QUESTION_CHARS:
        return Verdict(False, "too_long", f"Please keep questions under {MAX_QUESTION_CHARS} characters.")
    if any(p.search(q) for p in _PII_PATTERNS):
        return Verdict(
            False, "guest_pii",
            "Ask Clefs answers with hotel-level numbers only and never shows a guest's personal details. "
            "Open the guest's profile in the console if you are allowed to see it.",
            list(_SUGGEST_PII),
        )
    if any(p.search(q) for p in _FORECAST_OTHERS):
        return Verdict(
            False, "forecast_others",
            "Clefs does not predict what other hotels will charge. It can show the rates it has observed.",
            list(_SUGGEST_FORECAST),
        )
    return Verdict(True)
