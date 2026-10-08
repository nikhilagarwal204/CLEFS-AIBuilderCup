"""
Concierge console routes: the knowledge base editor, imports, the unknowns
queue and the GM prompt preview (03 spec 2.5). Included into router.py, so
every path here sits under /hotels/{slug}/concierge.

Members only (require_perm, never the legacy open access): reading needs
concierge.view, anything that changes what guests hear needs
concierge.manage (owner, gm by default).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core.errors import not_found
from clefs.modules.concierge import kb

router = APIRouter()
VIEW = require_perm("concierge.view")
MANAGE = require_perm("concierge.manage")


class EntryIn(BaseModel):
    section_id: int | None = None
    section_key: str | None = None
    kind: str = "fact"
    question_en: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    answer_en: str = Field(max_length=kb.TEXT_MAX)
    question_ar: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    answer_ar: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    tags: list[str] = []
    fact_key: str | None = None
    fact_value: str | None = None
    location_slug: str | None = None
    source_url: str | None = None
    source_label: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    approve: bool = False


class EntryPatch(BaseModel):
    section_id: int | None = None
    kind: str | None = None
    question_en: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    answer_en: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    question_ar: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    answer_ar: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    tags: list[str] | None = None
    fact_key: str | None = None
    fact_value: str | None = None
    location_slug: str | None = None
    source_url: str | None = None
    source_label: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    approve: bool = False


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ImportIn(BaseModel):
    source: Literal["legacy_text", "text"] = "legacy_text"
    text: str | None = Field(default=None, max_length=kb.IMPORT_TEXT_MAX)


class ModeIn(BaseModel):
    kb_mode: Literal["legacy_text", "structured"]


class AnswerIn(BaseModel):
    question_en: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    answer_en: str = Field(max_length=kb.TEXT_MAX)
    question_ar: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    answer_ar: str | None = Field(default=None, max_length=kb.TEXT_MAX)
    section_key: str | None = None
    approve: bool = False


def _present(model: BaseModel) -> dict[str, Any]:
    # PATCH semantics: only fields the client sent (null clears a field).
    return {k: v for k, v in model.model_dump(exclude_unset=True).items() if k != "approve"}


# ─── Overview and sections ───


@router.get("/kb")
def kb_overview(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """What the editor opens with: mode, sections with counts, which
    required sections still lack an approved entry, recent imports and the
    open unknowns count."""
    with db.db() as cur:
        sections = kb.list_sections(cur, slug)
        return {
            "kb_mode": kb.kb_mode(cur, slug),
            "sections": sections,
            "missing_required": [s["key"] for s in sections if s["required"] and not s["approved"]],
            "has_legacy_text": bool(kb.legacy_paragraphs(cur, slug)),
            "imports": kb.list_imports(cur, slug, limit=5),
            "open_unknowns": len(kb.list_unknowns(cur, slug, status="open", limit=500)),
        }


@router.get("/kb/sections")
def kb_sections(slug: str, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db() as cur:
        return kb.list_sections(cur, slug)


# ─── Entries ───


@router.get("/kb/entries")
def kb_entries(
    slug: str,
    status: Literal["pending", "approved", "rejected", "archived"] | None = None,
    section_id: int | None = None,
    q: str | None = None,
    ctx: HotelCtx = Depends(VIEW),
) -> list[dict]:
    with db.db(readonly=True) as cur:
        return kb.list_entries(cur, slug, status=status, section_id=section_id, q=(q or "")[:200] or None)


@router.post("/kb/entries", status_code=201)
def kb_create(slug: str, body: EntryIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    data = body.model_dump(exclude={"approve"})
    with db.db() as cur:
        row = kb.create_entry(cur, slug, data, source="console", actor=ctx)
        return kb.approve_entry(cur, slug, row["id"], actor=ctx) if body.approve else row


@router.patch("/kb/entries/{entry_id}")
def kb_update(slug: str, entry_id: int, body: EntryPatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return kb.update_entry(cur, slug, entry_id, _present(body), actor=ctx, approve=body.approve)


@router.post("/kb/entries/{entry_id}/approve")
def kb_approve(slug: str, entry_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return kb.approve_entry(cur, slug, entry_id, actor=ctx)


@router.post("/kb/entries/{entry_id}/reject")
def kb_reject(slug: str, entry_id: int, body: RejectIn | None = None, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return kb.set_entry_status(cur, slug, entry_id, "rejected", actor=ctx, reason=(body.reason if body else None))


@router.post("/kb/entries/{entry_id}/archive")
def kb_archive(slug: str, entry_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return kb.set_entry_status(cur, slug, entry_id, "archived", actor=ctx)


@router.get("/kb/entries/{entry_id}/revisions")
def kb_revisions(slug: str, entry_id: int, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        if kb.get_entry(cur, slug, entry_id) is None:
            raise not_found("KB entry not found", "kb_entry_not_found")
        return kb.list_revisions(cur, slug, entry_id)


# ─── Imports and kb_mode ───


@router.get("/kb/imports")
def kb_imports(slug: str, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return kb.list_imports(cur, slug)


@router.post("/kb/imports", status_code=202)
def kb_import(slug: str, body: ImportIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Queue a split of the legacy text (or pasted text) into pending
    entries (task concierge.kb_import; AI when on, paragraphs otherwise)."""
    with db.db() as cur:
        return kb.start_import(cur, slug, source=body.source, text=body.text, actor=ctx)


@router.post("/kb/imports/{import_id}/approve")
def kb_import_approve(slug: str, import_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return kb.approve_import(cur, slug, import_id, actor=ctx)


@router.put("/kb/mode")
def kb_set_mode(slug: str, body: ModeIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return {"kb_mode": kb.set_kb_mode(cur, slug, body.kb_mode, actor=ctx)}


# ─── Unknowns ───


@router.get("/kb/unknowns")
def kb_unknowns(
    slug: str, status: Literal["open", "answered", "dismissed", "merged"] = "open", ctx: HotelCtx = Depends(VIEW)
) -> list[dict]:
    with db.db(readonly=True) as cur:
        return kb.list_unknowns(cur, slug, status=status)


@router.post("/kb/unknowns/{unknown_id}/answer", status_code=201)
def kb_answer_unknown(slug: str, unknown_id: int, body: AnswerIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    data = {k: v for k, v in body.model_dump(exclude={"approve"}).items() if v is not None}
    with db.db() as cur:
        return kb.answer_unknown(cur, slug, unknown_id, data, actor=ctx, approve=body.approve)


@router.post("/kb/unknowns/{unknown_id}/dismiss")
def kb_dismiss_unknown(slug: str, unknown_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return kb.dismiss_unknown(cur, slug, unknown_id, actor=ctx)


# ─── Go-live switch (readiness gate, live.py) ───


class LiveIn(BaseModel):
    live: bool


@router.get("/live")
def live_get(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """{live, source, readiness}: the switch and the gate it needs."""
    from clefs.modules.concierge import live

    with db.db(readonly=True) as cur:
        return {**live.live_state(cur, slug), "readiness": live.readiness(cur, slug)}


@router.put("/live")
def live_put(slug: str, body: LiveIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Switch the concierge live for guests. ON: 409 readiness_not_met
    unless trust.api.readiness() is ready or owner-overridden. OFF: always."""
    from clefs.modules.concierge import live

    with db.db() as cur:
        return live.set_live(cur, slug, body.live, actor=ctx)


# ─── Prompt preview (GM only) ───


@router.get("/prompt-preview")
def prompt_preview(
    slug: str,
    surface: Literal["web_room", "web_location", "phone", "text_fallback", "eval", "whatsapp"] = "web_room",
    language: str | None = None,
    room: str | None = None,
    location: str | None = None,
    ctx: HotelCtx = Depends(MANAGE),
) -> dict:
    """The exact instructions and tools a guest session would get (with a
    sample room and no personal context), and its prompt_version."""
    from clefs.modules.concierge import api

    if surface != "web_location" and not room:
        room = "101"
    with db.db(readonly=True) as cur:
        spec = api.build_surface_session(
            slug, surface=surface, room=room if surface != "web_location" else None, location=location, language=language, cur=cur
        )
    return {
        "surface": spec.surface,
        "prompt_version": spec.prompt_version,
        "model": spec.model,
        "voice": spec.voice,
        "kb_mode": spec.kb_mode,
        "greeting_text": spec.greeting_text,
        "instructions": spec.instructions,
        "tools": spec.tools,
        "kb_entry_ids": spec.kb_entry_ids,
    }
