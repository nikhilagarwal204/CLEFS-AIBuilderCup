"""
The AI radar's verifier hook for visibility.ota_listing_fix (04 spec 5.7),
looked up lazily by action_types._ota_listing_verify.

An OTA listing fix is verified when the fact-check prompt for that fact,
answered after the fix by the engines that erred, shows no error in n >= 8
answers. Fewer answers keep it pending (the action lapses to not_verified at
verify_until); an error among >= 8 answers is not_verified.
"""

from __future__ import annotations

from typing import Any

from clefs.core.actions import VerifyResult

MIN_ANSWERS = 8


def verify_ota_listing_fix(ctx: Any) -> VerifyResult:
    p = ctx.params or {}
    fact_key, fp = p.get("fact_key"), p.get("fingerprint")
    engines = list(p.get("engines") or [])
    since = ctx.action.get("executed_at") or ctx.action.get("decided_at") or ctx.action.get("created_at")
    if not fact_key or not engines:
        return VerifyResult(status="pending", evidence=[{"kind": "row", "label": "AI radar", "value": "no fact given"}])
    ctx.cur.execute(
        """
        SELECT id FROM vis_ai_runs WHERE hotel_slug = %s AND prompt_intent = 'fact_check'
          AND prompt_key LIKE %s AND engine = ANY(%s) AND status = 'ok' AND facts_checked_at IS NOT NULL
          AND run_at > %s
        """,
        (ctx.hotel_slug, f"%.fact.{fact_key}", engines, since),
    )
    ids = [r["id"] for r in ctx.cur.fetchall()]
    if len(ids) < MIN_ANSWERS:
        return VerifyResult(status="pending", evidence=[
            {"kind": "metric", "label": "Fact-check answers since the fix", "value": len(ids)}])
    ctx.cur.execute(
        """
        SELECT COUNT(*) AS n FROM vis_ai_facts WHERE hotel_slug = %s AND fact_key = %s AND status <> 'dismissed'
          AND error_type <> 'unverifiable' AND run_ids && %s::bigint[]
        """,
        (ctx.hotel_slug, fact_key, ids),
    )
    wrong = int(ctx.cur.fetchone()["n"])
    ev = [{"kind": "metric", "label": "Fact-check answers since the fix", "value": len(ids)},
          {"kind": "metric", "label": "Answers still wrong", "value": wrong}]
    if wrong:
        return VerifyResult(status="not_verified", evidence=ev)
    if fp:
        ctx.cur.execute("UPDATE vis_ai_facts SET status = 'fixed' WHERE hotel_slug = %s AND fingerprint = %s",
                        (ctx.hotel_slug, fp))
    return VerifyResult(status="verified", attribution="influenced", evidence=ev)
