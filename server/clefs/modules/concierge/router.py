"""
Guest Concierge console routes. Every path here lives under /hotels/{slug}/concierge/
(Canon 13); public, device and webhook routes go on separate routers under
/public/, /device/ and /webhooks/ and are added to MODULE.routers.

The KB editor, imports, unknowns, the go-live switch and the prompt preview
live in router_console.py, the masked transcripts in transcripts.py; both
are included here, so they inherit the prefix.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.modules.concierge.router_console import router as _console_router
from clefs.modules.concierge.transcripts import router as _transcripts_router

router = APIRouter(prefix="/hotels/{slug}/concierge", tags=["concierge"])
router.include_router(_console_router)
router.include_router(_transcripts_router)

# ─── Single-question test (knowledge console, Wave 1 integration) ───
# One dry-run answer on the eval surface: nothing is filed, no order is
# placed and no turn is logged, so an owner can check an answer before
# approving it. The model call happens outside any DB transaction.

KB_TAG = re.compile(r"\[KB:(\d+)\]")


class KbTestIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    language: Literal["en", "ar"] = "en"
    entry_id: int | None = None


@router.post("/kb/test")
def kb_test(slug: str, body: KbTestIn, ctx: HotelCtx = Depends(require_perm("concierge.manage"))) -> dict:
    """{text, prompt_version, kb_entry_ids, cited, tool_calls, entry_included}.
    `entry_included` says whether entry_id (an approved entry) was in the
    prompt; a pending entry is not until it is approved. AI switched off
    answers 409 ai_disabled."""
    from clefs.modules.concierge import api

    with db.db(readonly=True) as cur:
        spec = api.build_surface_session(slug, surface="eval", language=body.language, dry_run=True, cur=cur)
    turn = api.respond_text(spec, [{"role": "user", "content": body.question.strip()}])
    ids = [int(i) for i in (getattr(spec, "kb_entry_ids", None) or [])]
    cited = sorted({int(m) for m in KB_TAG.findall(turn.text or "")})
    return {
        "text": re.sub(r"\s*\[KB:\d+\]", "", turn.text or "").strip(),
        "prompt_version": getattr(spec, "prompt_version", None),
        "kb_entry_ids": ids,
        "cited": cited,
        "tool_calls": [{"name": t.get("name"), "status": t.get("status")} for t in turn.tool_calls if isinstance(t, dict)],
        "entry_included": None if body.entry_id is None else body.entry_id in ids,
    }
