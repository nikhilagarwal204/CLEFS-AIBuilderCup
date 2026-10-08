"""
Perplexity Sonar chat completion -> Parsed (04 spec 5.5).

Sources are `search_results[]{title, url, date, snippet}` (the old
`citations` field is gone and is not read). The answer marks them inline as
[1], [2]...; each marker becomes a citation anchored at its offset, so the
entity resolver can tell which list item a source supports. Sources never
marked inline are kept as kind='source'.
"""

from __future__ import annotations

import re

from clefs.modules.visibility.parsers.common import Citation, Parsed

_MARK = re.compile(r"\[(\d{1,2})\]")


def parse(body: dict) -> Parsed:
    choices = body.get("choices") or []
    answer = str(((choices[0] if choices else {}).get("message") or {}).get("content") or "")
    results = [r for r in (body.get("search_results") or []) if isinstance(r, dict) and r.get("url")]
    cites: list[Citation] = []
    used: set[int] = set()
    for m in _MARK.finditer(answer):
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(results):
            r = results[idx]
            used.add(idx)
            cites.append(Citation(url=r["url"], title=r.get("title"), start_index=m.start(), end_index=m.end()))
    cites += [Citation(url=r["url"], title=r.get("title"), kind="source")
              for i, r in enumerate(results) if i not in used]
    return Parsed(answer_text=answer, citations=cites, model_id=body.get("model"), searched=bool(results),
                  empty=not answer.strip())
