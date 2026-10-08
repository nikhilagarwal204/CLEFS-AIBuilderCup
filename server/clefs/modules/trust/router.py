"""
Trust console routes. Every path here lives under /hotels/{slug}/trust/
(Canon 13); public routes are in public.py under /public/privacy.

Sections: audit viewer and chain (5.1), PII matrix (5.2), consent texts,
DSR queue, retention and tenant export (5.3). The HTTP contract is listed in
README.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import consent, pii
from clefs.modules.trust import audit_view, chain, dsr, pii_classes, retention, tenant_export

router = APIRouter(prefix="/hotels/{slug}/trust", tags=["trust"])

AUDIT_PERM = "trust.audit.view"

# ─── Audit viewer (5.1) ───


def _filters(
    actor_type: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    since: str | None = Query(default=None, alias="from"),
    until: str | None = Query(default=None, alias="to"),
    q: str | None = Query(default=None, max_length=200),
) -> audit_view.AuditFilters:
    return audit_view.AuditFilters(
        actor_type=actor_type, actor_id=actor_id, action=action, entity_type=entity_type,
        entity_id=entity_id, since=since, until=until, q=q,
    )


def _mask_audit(row: Any, ctx: HotelCtx) -> Any:
    # The IP column is the only audit field with its own class; before/after
    # already passed through clefs.audit's credential redaction, and only
    # owner/GM (full on every core row) can read this viewer.
    return pii.mask_for_ctx(row, ctx, field_classes={"ip": pii_classes.AUDIT_IP})


@router.get("/audit")
def list_audit(
    f: audit_view.AuditFilters = Depends(_filters),
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    ctx: HotelCtx = Depends(require_perm(AUDIT_PERM)),
) -> dict:
    with db.db(readonly=True) as cur:
        page = audit_view.list_entries(cur, ctx.hotel, f, limit=limit, before_id=before_id)
    page["items"] = _mask_audit(page["items"], ctx)
    return page


@router.get("/audit/facets")
def audit_facets(ctx: HotelCtx = Depends(require_perm(AUDIT_PERM))) -> dict:
    with db.db(readonly=True) as cur:
        return audit_view.facets(cur, ctx.hotel)


@router.get("/audit/export.csv")
def export_audit_csv(
    f: audit_view.AuditFilters = Depends(_filters),
    ctx: HotelCtx = Depends(require_perm(AUDIT_PERM)),
) -> Response:
    # Built inside the transaction (a streamed body would outlive the
    # connection); CSV_MAX_ROWS bounds the size. The export itself is audited:
    # who took a copy of the log, with which filters.
    with db.db() as cur:
        body = "".join(audit_view.iter_csv(cur, ctx.hotel, f, mask_row=lambda r: _mask_audit(r, ctx)))
        audit(cur, ctx, "trust.audit.export", "audit_log", None,
              meta={"filters": {k: v for k, v in vars(f).items() if v}, "bytes": len(body)})
    filename = f"audit-{ctx.slug}.csv"
    return Response(
        content="﻿" + body,  # BOM: Excel opens UTF-8 (Arabic names) correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/audit/chain")
def chain_status(ctx: HotelCtx = Depends(require_perm(AUDIT_PERM))) -> dict:
    with db.db(readonly=True) as cur:
        return chain.get_state(cur, ctx.slug)


@router.post("/audit/chain/verify")
def verify_chain_now(full: bool = False, ctx: HotelCtx = Depends(require_perm(AUDIT_PERM))) -> dict:
    with db.db() as cur:
        out = chain.verify_chain(cur, ctx.slug, full=full)
        audit(cur, ctx, "trust.audit.verify", "audit_log", None, after={"status": out["status"], "full": full})
    return out


@router.get("/audit/{entry_id}")
def get_audit_entry(entry_id: int, ctx: HotelCtx = Depends(require_perm(AUDIT_PERM))) -> dict:
    with db.db(readonly=True) as cur:
        return _mask_audit(audit_view.get_entry(cur, ctx.hotel, entry_id), ctx)


# ─── Retention (5.3) ───


class RetentionBody(BaseModel):
    keep_days: int | None = Field(default=None, ge=1, le=3650)  # None resets to the default


@router.get("/retention")
def list_retention(ctx: HotelCtx = Depends(require_perm("trust.view"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        return retention.list_policies(cur, ctx.slug)


@router.put("/retention/{data_class}")
def set_retention(data_class: str, body: RetentionBody, ctx: HotelCtx = Depends(require_perm("trust.manage"))) -> dict:
    with db.db() as cur:
        change = retention.set_policy(cur, ctx.slug, data_class, body.keep_days, user_id=ctx.principal.user_id)
        audit(cur, ctx, "trust.retention.update", "retention_policy", data_class,
              before=change["before"], after=change["after"])
        return next(p for p in retention.list_policies(cur, ctx.slug) if p["data_class"] == data_class)


@router.post("/retention/run")
def run_retention_now(ctx: HotelCtx = Depends(require_perm("trust.manage"))) -> dict:
    # The same purge the 02:30 job runs, on demand (after lowering a
    # policy). A short budget keeps the request bounded; the job finishes.
    out = retention.run_purge(ctx.slug, budget_s=20.0)
    with db.db() as cur:
        audit(cur, ctx, "trust.retention.run", "retention_run", out["run_id"],
              after={"counts": out["counts"], "failures": out["failures"]})
    return out


@router.get("/retention/log")
def deletion_log(
    limit: int = Query(default=100, ge=1, le=500), ctx: HotelCtx = Depends(require_perm("trust.view"))
) -> list[dict]:
    with db.db(readonly=True) as cur:
        cur.execute(
            "SELECT id, run_id, data_class, table_name, action, row_count, criteria, at FROM trs_deletion_log "
            "WHERE hotel_slug = %s ORDER BY at DESC, id DESC LIMIT %s",
            (ctx.slug, limit),
        )
        return [dict(r) for r in cur.fetchall()]


# ─── Privacy requests (DSR queue, 5.3) ───

DSR_PERM = "trust.privacy.manage"


class DsrCreateBody(BaseModel):
    type: str
    identifiers: dsr.Identifiers
    details: str | None = Field(default=None, max_length=4000)
    channel: str = "console"
    language: str = Field(default="en", max_length=8)


class DsrVerifyBody(BaseModel):
    method: str
    note: str | None = Field(default=None, max_length=500)


class DsrExecuteBody(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
    confirm: bool = False


class DsrCloseBody(BaseModel):
    status: str  # rejected | cancelled
    note: str | None = Field(default=None, max_length=2000)


@router.get("/dsr")
def list_dsr(
    status: str = Query(default="open", max_length=20),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: HotelCtx = Depends(require_perm(DSR_PERM)),
) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [dsr.to_api(r, ctx) for r in dsr.list_requests(cur, ctx.slug, status=status, limit=limit)]


@router.post("/dsr", status_code=201)
def create_dsr(body: DsrCreateBody, ctx: HotelCtx = Depends(require_perm(DSR_PERM))) -> dict:
    # Staff filing a request received by phone, email or at the desk.
    if body.channel == "public_form":
        body.channel = "console"
    with db.db() as cur:
        row = dsr.create_request(cur, ctx.slug, type=body.type, identifiers=body.identifiers, details=body.details,
                                 channel=body.channel, language=body.language, actor=ctx)
        return dsr.to_api(row, ctx)


@router.get("/dsr/{dsr_id}")
def get_dsr(dsr_id: int, ctx: HotelCtx = Depends(require_perm(DSR_PERM))) -> dict:
    with db.db(readonly=True) as cur:
        return dsr.to_api(dsr.get_row(cur, ctx.slug, dsr_id), ctx)


@router.post("/dsr/{dsr_id}/verify")
def verify_dsr(dsr_id: int, body: DsrVerifyBody, ctx: HotelCtx = Depends(require_perm(DSR_PERM))) -> dict:
    with db.db() as cur:
        return dsr.to_api(dsr.verify_request(cur, ctx, dsr_id, method=body.method, note=body.note), ctx)


@router.post("/dsr/{dsr_id}/execute")
def execute_dsr(dsr_id: int, body: DsrExecuteBody, ctx: HotelCtx = Depends(require_perm(DSR_PERM))) -> dict:
    with db.db() as cur:
        return dsr.to_api(dsr.execute(cur, ctx, dsr_id, note=body.note, confirm=body.confirm), ctx)


@router.post("/dsr/{dsr_id}/close")
def close_dsr(dsr_id: int, body: DsrCloseBody, ctx: HotelCtx = Depends(require_perm(DSR_PERM))) -> dict:
    with db.db() as cur:
        return dsr.to_api(dsr.close_request(cur, ctx, dsr_id, status=body.status, note=body.note), ctx)


@router.get("/dsr/{dsr_id}/download")
def download_dsr(dsr_id: int, ctx: HotelCtx = Depends(require_perm(DSR_PERM))) -> dict:
    with db.db() as cur:
        return dsr.download_path(cur, ctx, dsr_id)


# ─── Tenant export (5.3, owner only) ───


@router.post("/export", status_code=202)
def request_tenant_export(ctx: HotelCtx = Depends(require_perm("trust.manage"))) -> dict:
    with db.db() as cur:
        return tenant_export.request_export(cur, ctx)


@router.get("/exports")
def list_tenant_exports(ctx: HotelCtx = Depends(require_perm("trust.manage"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        return tenant_export.list_exports(cur, ctx.slug)


# ─── PII matrix (5.2) and consent wording ───


@router.get("/pii-matrix")
def pii_matrix(ctx: HotelCtx = Depends(require_perm("trust.view"))) -> dict:
    """The live masking matrix (core rows plus registered classes), for the
    console's read-only table."""
    return {"roles": list(pii_classes.COLUMNS), "classes": {k: dict(v) for k, v in sorted(pii.MATRIX.items())}}


class ConsentTextBody(BaseModel):
    purpose: str = Field(max_length=40)
    version: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    language: str = Field(default="en", max_length=8)
    body: str = Field(min_length=10, max_length=4000)


@router.get("/consent-texts")
def list_consent_texts(ctx: HotelCtx = Depends(require_perm("trust.view"))) -> list[dict]:
    """The wording in force per purpose and language (the hotel's own
    version first, else the platform default), plus older versions."""
    with db.db(readonly=True) as cur:
        cur.execute(
            """
            SELECT id, hotel_slug, purpose, version, language, body, created_at, retired_at FROM consent_texts
            WHERE hotel_slug = %s OR hotel_slug IS NULL
            ORDER BY purpose, language, (hotel_slug IS NULL), created_at DESC, id DESC
            """,
            (ctx.slug,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r["purpose"], r["language"])
        r["current"] = r["retired_at"] is None and key not in seen
        r["scope"] = "hotel" if r["hotel_slug"] else "platform"
        if r["current"]:
            seen.add(key)
    return rows


@router.post("/consent-texts", status_code=201)
def add_consent_text(body: ConsentTextBody, ctx: HotelCtx = Depends(require_perm("trust.manage"))) -> dict:
    # A version is immutable once recorded (core.consent: 409
    # consent_text_immutable): consents point at it as evidence.
    with db.db() as cur:
        row = consent.record_consent_text(cur, body.purpose, body.version, body.body, language=body.language,
                                          hotel_slug=ctx.slug)
        audit(cur, ctx, "trust.consent_text.add", "consent_text", row["id"],
              after={"purpose": body.purpose, "version": body.version, "language": body.language})
        return row
