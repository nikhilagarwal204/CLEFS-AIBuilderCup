"""
The public AI snapshot for Profit Guard's free audit (04 spec 5.9, Canon 23).

A snapshot is a kind='snapshot' batch with no hotel row: 5 prompts x the
available engines x 3 runs, executed by the same step task as the weekly
radar. Mentions are counted over prompts 1-3 only and shown as counts
("mentioned in 4 of 9 answers"): n is far below the minimum sample for a
percentage. Prompts 4-5 (brand, fact check) are quoted, not counted.
Snapshots are purged at 30 days (expires_at), or earlier by Profit Guard.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from clefs.core import clock
from clefs.modules.visibility import ai_radar, stats

COUNTED = ("snap.1", "snap.2", "snap.3")
QUOTED = ("snap.4", "snap.5")


def label_for(engines: list[str]) -> str:
    parts = []
    if any(e != "serpapi_ai_mode" for e in engines):
        parts.append(ai_radar.LABEL_SIMULATION)
    if "serpapi_ai_mode" in engines:
        parts.append(ai_radar.LABEL_THIRD_PARTY)
    return "; ".join(parts) or ai_radar.LABEL_SIMULATION


def _runs(cur: Any, batch_id: int) -> list[dict]:
    cur.execute("SELECT id, engine, prompt_key, prompt_text, status, self_mentioned, entities, answer_text, "
                "citations, cited_domains, run_at FROM vis_ai_runs WHERE batch_id = %s ORDER BY id", (batch_id,))
    return [dict(r) for r in cur.fetchall()]


def _snapshot_batch(cur: Any, batch_id: int) -> dict | None:
    cur.execute("SELECT * FROM vis_ai_batches WHERE id = %s AND kind = 'snapshot'", (batch_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def get(cur: Any, batch_id: int) -> dict | None:
    """The AiSnapshot shape of visibility.api.get_ai_snapshot."""
    batch = _snapshot_batch(cur, batch_id)
    if batch is None:
        return None
    engines = list((batch.get("plan") or {}).get("engines") or [])
    runs = _runs(cur, batch_id)
    good = [r for r in runs if r["status"] in ("ok", "empty")]
    per_engine = []
    for e in engines:
        counted = [r for r in good if r["engine"] == e and r["prompt_key"] in COUNTED]
        k, n = sum(1 for r in counted if r["self_mentioned"]), len(counted)
        ci = stats.wilson(k, n)
        per_engine.append({
            "engine": e, "label": ai_radar.label_for(e), "mentioned": k, "runs": n,
            "ci": [str(stats.q4(ci[0])), str(stats.q4(ci[1]))] if ci else None,
            "display": f"mentioned in {k} of {n} answers",
        })
    domains = Counter(d for r in good for d in (r.get("cited_domains") or []))
    about = []
    for key in QUOTED:
        for e in engines:
            r = next((r for r in good if r["prompt_key"] == key and r["engine"] == e and r["answer_text"]), None)
            if r:
                about.append({
                    "prompt": r["prompt_text"], "engine": e, "label": ai_radar.label_for(e),
                    "answer": (r["answer_text"] or "")[:1200], "run_at": r["run_at"].isoformat(),
                    "citations": [{"url": c.get("url"), "title": c.get("title")}
                                  for c in (r.get("citations") or [])[:5] if c.get("url")],
                })
    return {
        "status": batch["status"], "per_engine": per_engine,
        "top_cited_domains": [d for d, _ in domains.most_common(5)],
        "answers_about_you": about, "label": label_for(engines),
    }


def latest_for_subject(cur: Any, *, hotel_name: str, city: str, max_queries: int = 3) -> dict | None:
    """The foundation stub's shape (visibility.api.ai_snapshot): the latest
    ready snapshot for that hotel name and city, or None."""
    cur.execute(
        """
        SELECT id, plan FROM vis_ai_batches
        WHERE kind = 'snapshot' AND status = 'ready' AND lower(subject->>'hotel_name') = lower(%s)
          AND lower(subject->>'city') = lower(%s) AND (expires_at IS NULL OR expires_at > %s)
        ORDER BY created_at DESC LIMIT 1
        """,
        ((hotel_name or "").strip(), (city or "").strip(), clock.now()),
    )
    batch = cur.fetchone()
    if batch is None:
        return None
    engines = list((batch["plan"] or {}).get("engines") or [])
    runs = [r for r in _runs(cur, batch["id"]) if r["status"] in ("ok", "empty") and r["prompt_key"] in COUNTED]
    queries = list(dict.fromkeys(r["prompt_text"] for r in runs))[: max(0, int(max_queries))]
    runs = [r for r in runs if r["prompt_text"] in queries]
    self_k = sum(1 for r in runs if r["self_mentioned"])
    total = sum(len(r.get("entities") or []) for r in runs)
    return {
        "batch_id": batch["id"], "queries": queries, "mentioned": self_k,
        "share_of_voice": str(stats.q4(self_k / total)) if total else None, "sample_size": len(runs),
        "engines": engines, "label": label_for(engines),
    }


def purge(cur: Any, batch_id: int) -> int:
    """Delete one snapshot batch and its answers; the rows removed."""
    cur.execute("DELETE FROM vis_ai_runs WHERE batch_id = %s AND batch_kind = 'snapshot'", (batch_id,))
    n = cur.rowcount
    cur.execute("DELETE FROM vis_ai_batches WHERE id = %s AND kind = 'snapshot'", (batch_id,))
    return n + cur.rowcount


def purge_expired(cur: Any) -> dict:
    cur.execute("SELECT id FROM vis_ai_batches WHERE kind = 'snapshot' AND expires_at < %s", (clock.now(),))
    ids = [r["id"] for r in cur.fetchall()]
    return {"batches": len(ids), "rows": sum(purge(cur, i) for i in ids)}
