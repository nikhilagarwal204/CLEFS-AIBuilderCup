"""
Auto-KB suite (03 spec 5.4): 2 paraphrased guest questions per approved KB
entry in English, plus 2 in Arabic when the entry has Arabic, each judged
for faithfulness to the entry's approved answer.

Entries come from concierge.api.kb_facts (approved rows only; legacy
paragraphs carry no entry_id and are skipped). A scenario is regenerated
only when its entry's text changes (source_hash), so a nightly sync costs
nothing for an unchanged KB. An entry that is no longer approved has its
scenarios deactivated.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel

from clefs import ai
from clefs.modules.concierge import api as concierge_api
from clefs.modules.trust.evals import scenarios, textcheck
from clefs.modules.trust.evals.sp import savepoint

TASK = "trust.eval_paraphrase"
MAX_ENTRIES = 80

SYSTEM = (
    "You write test questions for a hotel concierge. Given one approved hotel knowledge entry, write exactly two "
    "different questions a guest might type whose answer is in that entry. Sound like real guests: short, casual, "
    "no greeting. Do not copy the entry's wording. Arabic questions use simple Gulf-leaning Arabic. Never use an em dash."
)


class Paraphrases(BaseModel):
    questions_en: list[str]
    questions_ar: list[str]


def _has_arabic(text: str) -> bool:
    return textcheck.script_ratios(text).get("ar", 0.0) >= 0.3


def approved_entries(cur: Any, hotel_slug: str) -> list[dict]:
    """[{entry_id, text_en, text_ar | None}] for approved concierge entries."""
    def read(lang: str) -> list[dict]:
        # An unreadable KB (Concierge not migrated) is an empty one.
        return savepoint(cur, "trs_autokb_kb", lambda: concierge_api.kb_facts(cur, hotel_slug, language=lang)) or []

    en = [f for f in read("en") if f.get("entry_id")]
    ar = {f["entry_id"]: f.get("text") or "" for f in read("ar") if f.get("entry_id")}
    out = []
    for f in en[:MAX_ENTRIES]:
        text_ar = ar.get(f["entry_id"])
        out.append({"entry_id": int(f["entry_id"]), "text_en": f.get("text") or "",
                    "text_ar": text_ar if text_ar and text_ar != f.get("text") and _has_arabic(text_ar) else None})
    return out


def _source_hash(e: dict) -> str:
    return hashlib.sha256(f"{e['text_en']}\n{e['text_ar'] or ''}".encode()).hexdigest()


def _scenario(entry: dict, lang: str, n: int, question: str) -> dict:
    return scenarios.normalize({
        "key": f"kb.{entry['entry_id']}.{lang}.{n}",
        "category": "kb",
        "title": f"KB entry {entry['entry_id']} ({lang} paraphrase {n})",
        "language": lang,
        "turns": [question],
        "context": {"kb_reference": entry["text_en"], "kb_reference_ar": entry["text_ar"]},
        "expectations": {"grounded_in_kb": {"entry_id": entry["entry_id"]}, "language": lang,
                         "no_unlisted_prices": True},
    })


def sync(cur: Any, hotel_slug: str) -> dict:
    """Create or refresh this hotel's auto_kb scenarios. Never raises for AI
    trouble: an entry whose paraphrases fail keeps its old scenarios."""
    entries = approved_entries(cur, hotel_slug)
    cur.execute("SELECT kb_entry_id, source_hash FROM trs_eval_scenarios WHERE hotel_slug = %s AND suite = 'auto_kb' AND active",
                (hotel_slug,))
    have = {r["kb_entry_id"]: r["source_hash"] for r in cur.fetchall()}
    created = skipped = 0
    errors: list[str] = []
    for e in entries:
        h = _source_hash(e)
        if have.get(e["entry_id"]) == h:
            continue
        try:
            p = ai.structured(
                TASK, system=SYSTEM, schema=Paraphrases, hotel_slug=hotel_slug, max_output_tokens=400,
                input=f"Entry (English): {e['text_en']}\nEntry (Arabic): {e['text_ar'] or 'none, return an empty questions_ar list'}",
            )
        except (ai.AIDisabled, ai.AIError) as exc:
            skipped += 1
            errors.append(f"{e['entry_id']}: {exc.code}")
            continue
        # A new version of the entry replaces all of its old paraphrases.
        cur.execute("UPDATE trs_eval_scenarios SET active = FALSE, updated_at = NOW() "
                    "WHERE hotel_slug = %s AND suite = 'auto_kb' AND kb_entry_id = %s", (hotel_slug, e["entry_id"]))
        pairs = [("en", q) for q in p.questions_en[:2]]
        if e["text_ar"]:
            pairs += [("ar", q) for q in p.questions_ar[:2]]
        for i, (lang, q) in enumerate(pairs):
            if not q.strip() or textcheck.has_em_dash(q):
                q = q.replace(textcheck.EM_DASH, ", ")
            if not q.strip():
                continue
            n = 1 + sum(1 for lg, _ in pairs[:i] if lg == lang)
            scenarios.upsert(cur, hotel_slug, "auto_kb", _scenario(e, lang, n, q.strip()),
                             kb_entry_id=e["entry_id"], source_hash=h)
            created += 1
    live = [e["entry_id"] for e in entries]
    cur.execute(
        "UPDATE trs_eval_scenarios SET active = FALSE, updated_at = NOW() "
        "WHERE hotel_slug = %s AND suite = 'auto_kb' AND active AND NOT (kb_entry_id = ANY(%s))",
        (hotel_slug, live),
    )
    retired = cur.rowcount
    return {"entries": len(entries), "created": created, "skipped": skipped, "retired": retired, "errors": errors[:20]}
