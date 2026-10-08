"""
SerpApi google_ai_mode -> Parsed (04 spec 5.5).

The answer is rebuilt from `text_blocks` (paragraph, heading, list) so each
block's `reference_indexes` can be anchored at its offset in the text; with
no blocks, `reconstructed_markdown` is used as is and `references[]` become
unanchored sources. AI Mode has no model id: model_id is 'google_ai_mode'
and version drift is detected from the cited-domain distribution instead.
"""

from __future__ import annotations

from clefs.modules.visibility.parsers.common import Citation, Parsed

MODEL_ID = "google_ai_mode"


def parse(body: dict) -> Parsed:
    refs = {int(r.get("index", i)): r for i, r in enumerate(body.get("references") or [])
            if isinstance(r, dict) and r.get("link")}
    lines: list[str] = []
    cites: list[Citation] = []
    used: set[int] = set()
    pos = 0

    def emit(text: str, ref_idx: list | None) -> None:
        nonlocal pos
        for i in ref_idx or []:
            r = refs.get(int(i)) if isinstance(i, int) or str(i).isdigit() else None
            if r:
                used.add(int(i))
                cites.append(Citation(url=r["link"], title=r.get("title"), start_index=pos, end_index=pos + len(text)))
        lines.append(text)
        pos += len(text) + 1

    for b in body.get("text_blocks") or []:
        t = b.get("type")
        if t == "list":
            for li in b.get("list") or []:
                title = (li.get("title") or "").strip()
                snip = (li.get("snippet") or "").strip()
                text = f"- **{title}**: {snip}" if title else f"- {snip}"
                emit(text, li.get("reference_indexes"))
        elif t == "heading":
            emit("## " + (b.get("snippet") or "").strip(), b.get("reference_indexes"))
        elif b.get("snippet"):
            emit(str(b["snippet"]).strip(), b.get("reference_indexes"))
    answer = "\n".join(lines) if lines else str(body.get("reconstructed_markdown") or "")
    cites += [Citation(url=r["link"], title=r.get("title"), kind="source") for i, r in refs.items() if i not in used]
    return Parsed(answer_text=answer, citations=cites, model_id=MODEL_ID, searched=bool(refs) or None,
                  empty=not answer.strip())
