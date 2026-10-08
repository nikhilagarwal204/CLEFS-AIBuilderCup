"""
Gemini (Google Search grounding) body -> Parsed (04 spec 5.5).

The body is the provider-neutral shape `ai.responses_raw` returns. Inline
citations are the `url_citation` annotations of the message's output_text
parts (start/end index into that text). Sources the search read but the
answer did not cite inline come from `grounding.sources` (and any
`web_search_call.action.sources`); both are kept, distinguished by `kind`.
`searched` records whether the model actually ran a search (it may answer
from memory, which the UI flags).
"""

from __future__ import annotations

from clefs.modules.visibility.parsers.common import Citation, Parsed


def parse(body: dict) -> Parsed:
    texts: list[str] = []
    cites: list[Citation] = []
    sources: list[Citation] = []
    searched = False
    offset = 0
    for item in body.get("output") or []:
        kind = item.get("type")
        if kind == "web_search_call":
            searched = True
            for s in ((item.get("action") or {}).get("sources") or []):
                if s.get("url"):
                    sources.append(Citation(url=s["url"], title=s.get("title"), kind="source"))
        elif kind == "message":
            for part in item.get("content") or []:
                if part.get("type") != "output_text":
                    continue
                text = part.get("text") or ""
                for a in part.get("annotations") or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        cites.append(Citation(
                            url=a["url"], title=a.get("title"),
                            start_index=_shift(a.get("start_index"), offset),
                            end_index=_shift(a.get("end_index"), offset)))
                texts.append(text)
                offset += len(text) + 2  # the "\n\n" join below
    grounding = body.get("grounding") or {}
    if grounding.get("web_search_queries"):
        searched = True
    for s in grounding.get("sources") or []:
        url = s.get("url") or (f"https://{s['domain']}/" if s.get("domain") else None)
        if url:
            sources.append(Citation(url=url, title=s.get("title"), kind="source"))
    answer = "\n\n".join(texts) if texts else str(body.get("output_text") or "")
    cited = {c.url for c in cites}
    for src in sources:
        if src.url not in cited:
            cited.add(src.url)
            cites.append(src)
    return Parsed(answer_text=answer, citations=cites, model_id=body.get("model"), searched=searched,
                  empty=not answer.strip())


def _shift(i: object, offset: int) -> int | None:
    return int(i) + offset if isinstance(i, int) else None
