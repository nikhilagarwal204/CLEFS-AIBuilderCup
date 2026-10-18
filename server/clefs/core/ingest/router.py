"""HTTP routes of core/ingest under /hotels/{slug}/ingest (mounted through
app.CORE_ROUTER_HOOKS).

Reads need core.settings.view (owner, gm, finance, revenue, dept_head).
Writes (upload, map, edit, commit) need core.ingest.manage (owner, gm,
finance, revenue): they are the people who hold the OTA and P&L files.

Guest names and contact values in staged rows are masked by role through
core.pii, the same matrix every guest-bearing serializer uses.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from clefs import db, notify
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import hotels, pii
from clefs.core.errors import ClefsError
from clefs.core.ingest import aliases, batches, commit, inbound, mapping as mapping_mod
from clefs.core.ingest.kinds import KINDS, registered_handlers, schema_for
from clefs.core.ingest.mapping import _contact_header, _person_header
from clefs.core.ingest.stage import raw_dict

router = APIRouter(tags=["ingest"])
VIEW = "core.settings.view"
MANAGE = "core.ingest.manage"
BASE = "/hotels/{slug}/ingest"

BATCH_FIELDS = (
    "id", "hotel_slug", "kind", "source_code", "ota_code", "file_id", "filename", "file_format", "encoding", "status",
    "mapping_template_id", "mapping", "transforms", "mapping_source", "headers", "header_row", "header_fingerprint",
    "sheet_name", "period_start", "period_end", "period_month", "doc_no", "currency", "row_count", "stats",
    "printed_total_minor", "parsed_total_minor", "total_field", "gate_status", "gate_override_by",
    "gate_override_reason", "gate_override_at", "via", "auto_commit", "uploaded_by", "supersedes_batch_id",
    "superseded_by", "committed_by", "committed_at", "commit_result", "errors", "created_at", "updated_at",
)


def _manage(ctx: HotelCtx) -> HotelCtx:
    if not ctx.can(MANAGE):
        raise ClefsError(403, "You cannot change imports at this hotel", "forbidden")
    return ctx


def _user_id(ctx: HotelCtx) -> int | None:
    return ctx.principal.user_id


def batch_view(b: dict, *, counts: dict | None = None) -> dict:
    out = {k: b.get(k) for k in BATCH_FIELDS}
    out["file_id"] = str(b["file_id"]) if b.get("file_id") else None
    out["has_suggestion"] = bool(b.get("mapping_suggestion"))
    out["gate_blocking"] = b.get("gate_status") in commit.GATE_BLOCKING and b.get("gate_override_at") is None
    if counts is not None:
        out["row_counts"] = counts
    return out


# ─── Masking ───


def _classes(batch: dict) -> dict[str, str]:
    """{raw header or canonical field: pii class} for this batch's rows."""
    schema = schema_for(batch["kind"])
    mp = batch.get("mapping") or {}
    out: dict[str, str] = {}
    if schema.get("guest_name"):
        out["guest_name"] = pii.GUEST_NAME
        if mp.get("guest_name"):
            out[mp["guest_name"]] = pii.GUEST_NAME
    for f in schema.contact_fields:
        out[f] = pii.GUEST_CONTACT
        if mp.get(f):
            out[mp[f]] = pii.GUEST_CONTACT
    person_kind = batch["kind"] in mapping_mod.PERSON_KINDS
    for h in batch.get("headers") or []:
        if h in out:
            continue
        if _contact_header(h):
            out[h] = pii.GUEST_CONTACT
        elif person_kind and _person_header(h) and h not in mp.values():
            out[h] = pii.GUEST_NAME
    return out


def _mask_values(values: dict, classes: dict[str, str], role: str | None) -> dict:
    out = dict(values)
    for k, cls in classes.items():
        if k in out and out[k] is not None:
            out[k] = pii.mask({"v": out[k]}, role, field_classes={"v": cls})["v"]
    return out


def row_view(row: dict, classes: dict[str, str], role: str | None) -> dict:
    out = dict(row)
    out["raw"] = _mask_values(row.get("raw") or {}, classes, role)
    out["canonical"] = _mask_values(row.get("canonical") or {}, classes, role)
    return out


# ─── Sources and batches ───


@router.get(f"{BASE}/sources")
def get_sources(ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    handlers = registered_handlers()
    return {
        "sources": [{"source_code": m.source_code, "label": m.label, "kind": m.kind, "ota_code": m.ota_code,
                     "unverified": m.unverified} for m in aliases.sources()],
        "kinds": [{"kind": k, "fields": [{"name": f.name, "type": f.type, "required": f.required}
                                         for f in schema_for(k).fields],
                   "total_field": schema_for(k).total_field, "committable": k in handlers} for k in KINDS],
    }


class CreateBody(BaseModel):
    file_id: str = Field(min_length=1, max_length=64)
    kind: Literal[KINDS] | None = None  # type: ignore[valid-type]
    source_code: str | None = Field(default=None, max_length=64)
    ota_code: Literal["BOOKING", "AGODA", "EXPEDIA"] | None = None
    period_month: date | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    sheet_name: str | None = Field(default=None, max_length=120)
    header_row: int | None = Field(default=None, ge=0, le=200)


@router.post(f"{BASE}/batches")
def create_batch(body: CreateBody, response: Response, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    """Start an import from a file already uploaded (POST /hotels/{slug}/files).
    201 with the processed batch; 200 with the existing batch when these
    bytes were imported before."""
    _manage(ctx)
    options = {k: v for k, v in (("sheet_name", body.sheet_name), ("header_row", body.header_row)) if v is not None}
    with db.db() as cur:
        b = batches.create_batch(
            cur, ctx.hotel.slug, file_id=body.file_id, kind=body.kind, source_code=body.source_code,
            ota_code=body.ota_code, via="WEB", uploaded_by=_user_id(ctx), period_month=body.period_month,
            currency=body.currency, options=options,
        )
        response.status_code = 201 if b.pop("created") else 200
        return batch_view(b, counts=batches.row_counts(cur, b["id"]))


@router.get(f"{BASE}/batches")
def list_batches(
    kind: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = None,
    ctx: HotelCtx = Depends(require_perm(VIEW)),
) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [batch_view(b) for b in batches.list_batches(cur, ctx.hotel.slug, kind=kind, status=status,
                                                            limit=limit, before_id=before_id)]


@router.get(f"{BASE}/batches/{{batch_id}}")
def get_batch(batch_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    with db.db(readonly=True) as cur:
        b = batches.get_batch(cur, ctx.hotel.slug, batch_id)
        return batch_view(b, counts=batches.row_counts(cur, batch_id))


@router.get(f"{BASE}/batches/{{batch_id}}/rows")
def get_rows(
    batch_id: int,
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    ctx: HotelCtx = Depends(require_perm(VIEW)),
) -> dict:
    with db.db(readonly=True) as cur:
        b = batches.get_batch(cur, ctx.hotel.slug, batch_id)
        rows = batches.list_rows(cur, batch_id, status=status, limit=limit, offset=offset)
        classes, role = _classes(b), pii.role_for_ctx(ctx)
        return {"batch_id": batch_id, "total": sum(batches.row_counts(cur, batch_id).values()),
                "rows": [row_view(r, classes, role) for r in rows]}


# ─── Mapping ───


@router.get(f"{BASE}/batches/{{batch_id}}/mapping")
def get_mapping(batch_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    """What the mapping screen needs: headers, the mapping in use, the AI
    proposal, the kind's fields and a few sample rows (masked by role)."""
    from clefs import files
    from clefs.core.ingest import sniff

    with db.db(readonly=True) as cur:
        b = batches.get_batch(cur, ctx.hotel.slug, batch_id)
        samples: list[dict] = []
        if b.get("file_format") in ("csv", "xlsx", "xls") and b["status"] not in ("COMMITTED", "SUPERSEDED"):
            _ref, data = files.get_file(cur, ctx.hotel.slug, str(b["file_id"]))
            opts = b.get("options") or {}
            t = sniff.sniff(data, filename=b.get("filename"), sheet_name=opts.get("sheet_name"),
                            header_row=opts.get("header_row"))
            samples = [raw_dict(t.headers, r) for r in t.rows[:5]]
        schema = schema_for(b["kind"])
        classes, role = _classes(b), pii.role_for_ctx(ctx)
        return {
            "batch_id": batch_id, "kind": b["kind"], "source_code": b.get("source_code"),
            "headers": b.get("headers") or [], "mapping": b.get("mapping") or {},
            "transforms": b.get("transforms") or {}, "mapping_source": b.get("mapping_source"),
            "suggestion": b.get("mapping_suggestion"),
            "fields": [{"name": f.name, "type": f.type, "required": f.required} for f in schema.fields],
            "missing": [f for f in schema.required if f not in (b.get("mapping") or {})],
            "samples": [_mask_values(s, classes, role) for s in samples],
            "unverified_aliases": bool((b.get("stats") or {}).get("unverified_aliases")),
        }


class MappingBody(BaseModel):
    mapping: dict[str, str]
    transforms: dict[str, dict[str, Any]] = Field(default_factory=dict)
    save_template: bool = True


@router.put(f"{BASE}/batches/{{batch_id}}/mapping")
def put_mapping(batch_id: int, body: MappingBody, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    _manage(ctx)
    with db.db() as cur:
        b = batches.confirm_mapping(cur, ctx.hotel.slug, batch_id, mapping=body.mapping, transforms=body.transforms,
                                    confirmed_by=_user_id(ctx), save_template=body.save_template)
        return batch_view(b, counts=batches.row_counts(cur, batch_id))


@router.post(f"{BASE}/batches/{{batch_id}}/mapping/suggest")
def suggest(batch_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    """Ask the AI again (it was off, or the person wants a second look)."""
    from clefs import files
    from clefs.core.ingest import sniff

    _manage(ctx)
    with db.db() as cur:
        b = batches.get_batch(cur, ctx.hotel.slug, batch_id, for_update=True)
        if b["status"] not in ("MAPPING_REQUIRED", "REVIEW") or b.get("file_format") not in ("csv", "xlsx", "xls"):
            raise ClefsError(409, "This import has no column mapping to suggest", "not_tabular")
        _ref, data = files.get_file(cur, ctx.hotel.slug, str(b["file_id"]))
        opts = b.get("options") or {}
        t = sniff.sniff(data, filename=b.get("filename"), sheet_name=opts.get("sheet_name"),
                        header_row=opts.get("header_row"))
        s = mapping_mod.suggest_mapping(hotel_slug=ctx.hotel.slug, kind=b["kind"], headers=t.headers,
                                        rows=[raw_dict(t.headers, r) for r in t.rows[:5]], current=b.get("mapping"))
        if s is None:
            raise ClefsError(409, "AI mapping is not available right now; map the columns by hand", "ai_unavailable")
        b = batches._update(cur, batch_id, mapping_suggestion=s)
        return batch_view(b)


class OptionsBody(BaseModel):
    sheet_name: str | None = Field(default=None, max_length=120)
    header_row: int | None = Field(default=None, ge=0, le=200)


@router.post(f"{BASE}/batches/{{batch_id}}/options")
def set_options(batch_id: int, body: OptionsBody, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    """Another sheet or header row; the batch re-runs from its file."""
    _manage(ctx)
    with db.db() as cur:
        b = batches.set_options(cur, ctx.hotel.slug, batch_id, sheet_name=body.sheet_name,
                                header_row=body.header_row)
        return batch_view(b, counts=batches.row_counts(cur, batch_id))


@router.post(f"{BASE}/batches/{{batch_id}}/reprocess")
def reprocess(batch_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    _manage(ctx)
    with db.db() as cur:
        b = batches.process_batch(cur, batch_id, hotel_slug=ctx.hotel.slug)
        b = batches._link_supersede(cur, b)
        return batch_view(b, counts=batches.row_counts(cur, batch_id))


# ─── Review ───


class RowBody(BaseModel):
    values: dict[str, Any] | None = None
    skip: bool | None = None


@router.patch(f"{BASE}/batches/{{batch_id}}/rows/{{row_id}}")
def patch_row(batch_id: int, row_id: int, body: RowBody, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    _manage(ctx)
    if body.values is None and body.skip is None:
        raise ClefsError(400, "nothing to change", "invalid_row_edit")
    with db.db() as cur:
        row = batches.edit_row(cur, ctx.hotel.slug, batch_id, row_id, values=body.values, skip=body.skip,
                               edited_by=_user_id(ctx))
        b = batches.get_batch(cur, ctx.hotel.slug, batch_id)
        return {"row": row_view(row, _classes(b), pii.role_for_ctx(ctx)),
                "batch": batch_view(b, counts=batches.row_counts(cur, batch_id))}


class OverrideBody(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


@router.post(f"{BASE}/batches/{{batch_id}}/gate-override")
def gate_override(batch_id: int, body: OverrideBody, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    _manage(ctx)
    with db.db() as cur:
        return batch_view(commit.override_gate(cur, ctx.hotel.slug, batch_id, actor=ctx, reason=body.reason))


class CommitBody(BaseModel):
    skip_errors: bool = False
    # Choices for the kind's commit handler (MENU: default_outlet,
    # create_outlets, strict); the handler reads them, unknown keys are
    # ignored by it. Kept small: they are stored on the batch.
    options: dict[str, Any] | None = None
    # Run the handler and roll back: the batch stays in REVIEW and the
    # response carries the would-be commit_result.
    dry_run: bool = False


@router.post(f"{BASE}/batches/{{batch_id}}/commit")
def commit_batch(batch_id: int, body: CommitBody | None = None, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    _manage(ctx)
    body = body or CommitBody()
    if body.options is not None and len(json.dumps(body.options, default=str)) > 4000:
        raise ClefsError(400, "Commit options are limited to 4000 characters", "options_too_large")
    with db.db() as cur:
        b = commit.commit_batch(cur, ctx.hotel.slug, batch_id, actor=ctx, skip_errors=body.skip_errors,
                                options=body.options, dry_run=body.dry_run)
        out = batch_view(b, counts=batches.row_counts(cur, batch_id))
        if body.dry_run:
            out["dry_run"] = True
            out["commit_result"] = b.get("commit_result")
        return out


@router.delete(f"{BASE}/batches/{{batch_id}}", status_code=204)
def discard(batch_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> Response:
    _manage(ctx)
    with db.db() as cur:
        commit.discard_batch(cur, ctx.hotel.slug, batch_id, actor=ctx)
    return Response(status_code=204)


# ─── Templates and inbound address ───


@router.get(f"{BASE}/templates")
def get_templates(include_inactive: bool = False, ctx: HotelCtx = Depends(require_perm(VIEW))) -> list[dict]:
    with db.db(readonly=True) as cur:
        return mapping_mod.list_templates(cur, ctx.hotel.slug, include_inactive=include_inactive)


@router.delete(f"{BASE}/templates/{{template_id}}")
def delete_template(template_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    _manage(ctx)
    with db.db() as cur:
        if not mapping_mod.deactivate_template(cur, ctx.hotel.slug, template_id):
            raise ClefsError(404, "Template not found", "template_not_found")
        return {"ok": True}


@router.get(f"{BASE}/inbound-address")
def inbound_address(ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    """Where statements can be forwarded; null until the hotel has an
    inbound alias. Add -<source> for a file the headers cannot identify."""
    with db.db(readonly=True) as cur:
        return {"address": notify.hotel_inbound_address(cur, ctx.hotel.slug, "ingest"),
                "hints": [m.source_code for m in aliases.sources()]}


# ─── Trusted email senders ───
# Mail to the ingest address auto-commits only from a proven sender the
# hotel vouches for (inbound.may_auto_commit): active members count on
# their own; this list adds the rest (the PMS's report mailer, an outside
# accountant, "@finance.booking.com"). It is a trust boundary, so only
# managers (core.settings.manage) change it, and every change is audited.

_SENDER = r"^(@[\w-]+(\.[\w-]+)+|[\w.+'-]+@[\w-]+(\.[\w-]+)+)$"


class EmailSendersBody(BaseModel):
    email_senders: list[str] = Field(default_factory=list, max_length=50)


@router.get(f"{BASE}/email-senders")
def get_email_senders(ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    with db.db(readonly=True) as cur:
        return {"email_senders": inbound.email_senders(cur, ctx.hotel.slug)}


@router.put(f"{BASE}/email-senders")
def put_email_senders(body: EmailSendersBody, ctx: HotelCtx = Depends(require_perm("core.settings.manage"))) -> dict:
    clean: list[str] = []
    for e in body.email_senders:
        e = e.strip().lower()
        if not re.fullmatch(_SENDER, e):
            raise ClefsError(400, f"{e!r} is not an email address or @domain", "invalid_sender")
        if e not in clean:
            clean.append(e)
    with db.db() as cur:
        row = hotels.module_settings_row(cur, ctx.hotel.slug, inbound.SETTINGS_MODULE)
        settings = {**((row or {}).get("settings") or {}), "email_senders": clean}
        hotels.set_module_settings(cur, ctx.hotel.slug, inbound.SETTINGS_MODULE, settings, actor=ctx)
    return {"email_senders": clean}
