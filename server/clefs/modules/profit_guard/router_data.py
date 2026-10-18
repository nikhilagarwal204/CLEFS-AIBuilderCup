"""
Profit Guard data routes under /hotels/{slug}/profit-guard (02 spec 3.8,
Canon 13): imports (thin wrappers over core/ingest restricted to the
Profit Guard kinds), contracts (AI extraction, then confirmation),
programs, channel configuration, owner policies, and reads of statements,
payouts, bank credits, virtual cards, rate evidence and channel economics.

Reads need `profit.view`, writes `profit.manage` (owner, gm, finance).
Money leaves as `<x>_minor` ints plus `<x>` Decimal strings (Canon 2).
Guest names pass through core.pii by role. The tax routes stay on the older
/hotels/{slug}/profit prefix (router.py) because the settings Tax page
already calls them there.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import money, pii
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.core.ingest import (
    batch_view, commit_batch, confirm_mapping, create_batch, edit_row, get_batch, list_batches, row_counts,
)
from clefs.modules.profit_guard import contracts, imports, repo
from clefs.modules.profit_guard.models import (
    ChannelConfigIn,
    ContractIn,
    PoliciesIn,
    ProgramIn,
    normalize_ota,
)

router = APIRouter(prefix="/hotels/{slug}/profit-guard", tags=["profit_guard"])

VIEW = require_perm("profit.view")
MANAGE = require_perm("profit.manage")

# ─── Serializers ───

CONTRACT_MONEY: tuple[str, ...] = ()
PROGRAM_MONEY = ("fixed_minor",)
STATEMENT_MONEY = ("total_minor",)
PAYOUT_MONEY = ("amount_minor",)
BANK_MONEY = ("amount_minor",)
VCC_MONEY = ("initial_minor", "current_balance_minor")
EVIDENCE_MONEY = ("price_minor",)


def _mask_lines(obj: dict, ctx: HotelCtx) -> dict:
    return pii.mask_for_ctx(obj, ctx, field_classes={"lines.guest_name": pii.GUEST_NAME})


def _contract_out(row: dict) -> dict:
    res = repo.out(row, money_fields=CONTRACT_MONEY) or {}
    res["review_flags"] = ((row.get("extraction") or {}).get("review_flags") or []) if row.get("extraction") else []
    return res


def _statement_out(st: dict, ctx: HotelCtx) -> dict:
    res = repo.out({k: v for k, v in st.items() if k != "lines"}, money_fields=STATEMENT_MONEY) or {}
    if "lines" in st:
        res["lines"] = [repo.out(ln, money_fields=repo.LINE_MONEY) for ln in st["lines"]]
        res = _mask_lines(res, ctx)
    return res


def _payout_out(p: dict, ctx: HotelCtx) -> dict:
    res = repo.out({k: v for k, v in p.items() if k != "lines"}, money_fields=PAYOUT_MONEY) or {}
    if "lines" in p:
        res["lines"] = [repo.out(ln, money_fields=repo.PAYOUT_LINE_MONEY) for ln in p["lines"]]
        res = _mask_lines(res, ctx)
    return res


def _hotel(cur: Any, slug: str):
    return get_hotel(cur, slug)


# ─── Imports (core/ingest, Profit Guard kinds only) ───


class ImportIn(BaseModel):
    file_id: str = Field(min_length=1, max_length=64)
    kind: Literal["OTA_STATEMENT", "CREDIT_NOTE", "OTA_PAYOUT", "VCC_REPORT", "BANK", "CONTRACT_PDF"] | None = None
    source_code: str | None = Field(None, max_length=80)
    ota_code: str | None = Field(None, max_length=40)
    currency: str | None = Field(None, min_length=3, max_length=3)


class MappingIn(BaseModel):
    mapping: dict[str, str]
    transforms: dict[str, dict] | None = None
    save_template: bool = True


class RowEdit(BaseModel):
    row_id: int
    values: dict[str, Any] | None = None
    skip: bool | None = None


class ReviewIn(BaseModel):
    edits: list[RowEdit] = Field(min_length=1, max_length=200)


class CommitIn(BaseModel):
    skip_errors: bool = False


def _pg_batch(cur: Any, slug: str, batch_id: int) -> dict:
    b = get_batch(cur, slug, batch_id)
    if b["kind"] not in imports.KINDS:
        raise ClefsError(404, "Import not found", "batch_not_found")
    return b


def _batch_out(cur: Any, b: dict) -> dict:
    return batch_view(b, counts=row_counts(cur, b["id"]))


@router.get("/imports")
def list_imports(slug: str, kind: str | None = Query(None), status: str | None = Query(None),
                 limit: int = Query(50, ge=1, le=200), ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    """Batches of the Profit Guard kinds, newest first."""
    if kind is not None and kind not in imports.KINDS:
        raise ClefsError(400, "Not a Profit Guard import kind", "unknown_kind")
    with db.db() as cur:
        rows = list_batches(cur, slug, kind=kind, status=status, limit=200 if kind is None else limit)
        return [batch_view(b) for b in rows if b["kind"] in imports.KINDS][:limit]


@router.post("/imports", status_code=201)
def create_import(slug: str, body: ImportIn, response: Response, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """A stored file (POST /hotels/{slug}/files first) becomes a batch,
    processed synchronously; 200 when the same bytes were imported before."""
    if body.kind is None and body.source_code is None:
        raise ClefsError(400, "Say what the file is (kind or source_code)", "unknown_kind")
    with db.db() as cur:
        b = create_batch(cur, slug, file_id=body.file_id, kind=body.kind, source_code=body.source_code,
                         ota_code=normalize_ota(body.ota_code), currency=body.currency, via="WEB",
                         uploaded_by=ctx.principal.user_id)
        if b["kind"] not in imports.KINDS:
            raise ClefsError(400, f"{b['kind']} is not a Profit Guard import", "source_kind_mismatch")
        if not b.pop("created", True):
            response.status_code = 200
        return _batch_out(cur, b)


@router.get("/imports/{batch_id}")
def get_import(slug: str, batch_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        return _batch_out(cur, _pg_batch(cur, slug, batch_id))


@router.post("/imports/{batch_id}/mapping")
def confirm_import_mapping(slug: str, batch_id: int, body: MappingIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Confirm (or correct) the column mapping; it is saved as this hotel's
    template so next month's file maps itself."""
    with db.db() as cur:
        _pg_batch(cur, slug, batch_id)
        b = confirm_mapping(cur, slug, batch_id, mapping=body.mapping, transforms=body.transforms,
                            confirmed_by=ctx.principal.user_id, save_template=body.save_template)
        return _batch_out(cur, b)


@router.post("/imports/{batch_id}/review")
def review_import(slug: str, batch_id: int, body: ReviewIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """PDF (or table) row edits during review: each edit corrects canonical
    values or skips / un-skips a row; totals and the gate are recomputed."""
    with db.db() as cur:
        _pg_batch(cur, slug, batch_id)
        for e in body.edits:
            if e.values is None and e.skip is None:
                raise ClefsError(400, "Each edit needs values or skip", "invalid_row_edit")
            edit_row(cur, slug, batch_id, e.row_id, values=e.values, skip=e.skip, edited_by=ctx.principal.user_id)
        return _batch_out(cur, get_batch(cur, slug, batch_id))


@router.post("/imports/{batch_id}/commit")
def commit_import(slug: str, batch_id: int, body: CommitIn | None = None, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Commit through core/ingest: the gate is checked and the rows go to
    the Profit Guard handler; `commit_result` says what was written."""
    with db.db() as cur:
        _pg_batch(cur, slug, batch_id)
        b = commit_batch(cur, slug, batch_id, actor=ctx, skip_errors=bool(body and body.skip_errors))
        return _batch_out(cur, b)


# ─── Contracts ───


class ExtractIn(BaseModel):
    file_id: str = Field(min_length=1, max_length=64)
    ota_code: str | None = Field(None, max_length=40)


@router.get("/contracts")
def list_contracts(slug: str, ota_code: str | None = Query(None), status: str | None = Query(None),
                   ctx: HotelCtx = Depends(VIEW)) -> dict:
    """{contracts: [...], defaults: [...]}: the hotel's rows (drafts
    included, each with review_flags) and the default terms used while no
    confirmed contract covers a date."""
    with db.db() as cur:
        rows = contracts.list_contracts(cur, slug, ota_code=normalize_ota(ota_code), status=status)
    defaults = [contracts.default_terms(o) for o in contracts.DEFAULT_TERMS]
    return {"contracts": [_contract_out(r) for r in rows], "defaults": [repo.out(d) for d in defaults]}


@router.get("/contracts/{contract_id}")
def get_contract(slug: str, contract_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        return _contract_out(contracts.get_contract(cur, slug, contract_id))


@router.post("/contracts/extract", status_code=201)
def extract_contract(slug: str, body: ExtractIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """AI reads a stored contract (PDF text layer or a text file) into a
    DRAFT with clause citations and review flags; it counts only after
    PUT /contracts/{id} confirms it. 409 ai_unavailable when AI is off."""
    with db.db() as cur:
        pages = contracts.pages_from_file(cur, slug, body.file_id)
        row = contracts.extract_contract(cur, slug, pages=pages, ota_code=body.ota_code, file_id=body.file_id,
                                         actor=ctx)
        return _contract_out(row)


@router.post("/contracts", status_code=201)
def create_contract(slug: str, body: ContractIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Terms typed by a person (no document); confirmed unless confirm=false."""
    with db.db() as cur:
        return _contract_out(contracts.save_contract(cur, slug, body.model_dump(), actor=ctx))


@router.put("/contracts/{contract_id}")
def put_contract(slug: str, contract_id: int, body: ContractIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Edit and (confirm=true, the default) confirm a contract. Confirming
    closes the open-ended contract it renews; any other overlap with
    confirmed terms is 409 contract_overlap."""
    with db.db() as cur:
        return _contract_out(contracts.save_contract(cur, slug, body.model_dump(), contract_id=contract_id, actor=ctx))


@router.delete("/contracts/{contract_id}")
def retire_contract(slug: str, contract_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Retire (never delete): findings computed from it keep their source."""
    with db.db() as cur:
        return _contract_out(contracts.retire_contract(cur, slug, contract_id, actor=ctx))


# ─── Programs ───


@router.get("/programs")
def list_programs(slug: str, ota_code: str | None = Query(None), active_only: bool = Query(False),
                  ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db() as cur:
        rows = repo.list_programs(cur, slug, ota_code=normalize_ota(ota_code), active_only=active_only)
    return [repo.out(r, money_fields=PROGRAM_MONEY) for r in rows]


@router.post("/programs", status_code=201)
def create_program(slug: str, body: ProgramIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = repo.create_program(cur, slug, _hotel(cur, slug).currency, body.model_dump(), actor=ctx)
    return repo.out(row, money_fields=PROGRAM_MONEY)


@router.put("/programs/{program_id}")
def update_program(slug: str, program_id: int, body: ProgramIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = repo.update_program(cur, slug, _hotel(cur, slug).currency, program_id, body.model_dump(), actor=ctx)
    return repo.out(row, money_fields=PROGRAM_MONEY)


@router.delete("/programs/{program_id}", status_code=204)
def delete_program(slug: str, program_id: int, ctx: HotelCtx = Depends(MANAGE)) -> Response:
    with db.db() as cur:
        repo.delete_program(cur, slug, program_id, actor=ctx)
    return Response(status_code=204)


# ─── Channel configuration and owner policies ───


@router.get("/channel-config")
def list_channel_config(slug: str, ota_code: str | None = Query(None), on: date | None = Query(None),
                        ctx: HotelCtx = Depends(VIEW)) -> dict:
    """{rows: every dated row, in_force: {ota: config on `on`}} (default:
    the business date; unset values show the defaults)."""
    with db.db() as cur:
        day = on or htime.business_date(_hotel(cur, slug))
        rows = repo.list_channel_config(cur, slug, ota_code=normalize_ota(ota_code))
        otas = sorted({r["ota_code"] for r in rows} | set(contracts.DEFAULT_TERMS))
        in_force = {o: repo.out(repo.channel_config_on(cur, slug, o, day)) for o in otas}
    return {"on": day, "rows": [repo.out(r) for r in rows], "in_force": in_force}


@router.put("/channel-config")
def put_channel_config(slug: str, body: ChannelConfigIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Upsert the row for (ota_code, configured_from): a change of the
    extranet setup is a new dated row, so past commissions keep the setup
    that was in force."""
    with db.db() as cur:
        return repo.out(repo.put_channel_config(cur, slug, body.model_dump(), actor=ctx))


@router.delete("/channel-config/{ota_code}/{configured_from}", status_code=204)
def delete_channel_config(slug: str, ota_code: str, configured_from: date, ctx: HotelCtx = Depends(MANAGE)) -> Response:
    with db.db() as cur:
        repo.delete_channel_config(cur, slug, ota_code, configured_from, actor=ctx)
    return Response(status_code=204)


@router.get("/policies")
def get_policies(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        p = repo.get_policies(cur, slug)
        ccy = _hotel(cur, slug).currency
    return {**repo.out(p), "currency": ccy, "min_finding": money.money_str(p["min_finding_minor"], ccy)}


@router.put("/policies")
def put_policies(slug: str, body: PoliciesIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Partial update; min_finding is a Decimal string in the hotel currency."""
    with db.db() as cur:
        ccy = _hotel(cur, slug).currency
        changes = body.model_dump(exclude_unset=True)
        if changes.get("min_finding") is not None:
            changes["min_finding_minor"] = money.to_minor(changes.pop("min_finding"), ccy)
        p = repo.put_policies(cur, slug, changes, actor=ctx)
    return {**repo.out(p), "currency": ccy, "min_finding": money.money_str(p["min_finding_minor"], ccy)}


# ─── Statements, payouts, bank credits, virtual cards ───


@router.get("/statements")
def list_statements(slug: str, ota_code: str | None = Query(None), doc_kind: str | None = Query(None),
                    limit: int = Query(100, ge=1, le=500), ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db() as cur:
        rows = repo.list_statements(cur, slug, ota_code=normalize_ota(ota_code), doc_kind=doc_kind, limit=limit)
    return [_statement_out(r, ctx) for r in rows]


@router.get("/statements/{statement_id}")
def get_statement(slug: str, statement_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The document and its lines, each with its PMS match (reservation_id,
    match_status, match_score); guest names masked by role."""
    with db.db() as cur:
        return _statement_out(repo.get_statement(cur, slug, statement_id), ctx)


@router.get("/payouts")
def list_payouts(slug: str, ota_code: str | None = Query(None), limit: int = Query(100, ge=1, le=500),
                 ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db() as cur:
        return [_payout_out(r, ctx) for r in repo.list_payouts(cur, slug, ota_code=normalize_ota(ota_code),
                                                               limit=limit)]


@router.get("/payouts/{payout_id}")
def get_payout(slug: str, payout_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        return _payout_out(repo.get_payout(cur, slug, payout_id), ctx)


@router.get("/bank-credits")
def list_bank_credits(slug: str, start: date | None = Query(None, alias="from"),
                      end: date | None = Query(None, alias="to"), ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db() as cur:
        return [repo.out(r, money_fields=BANK_MONEY) for r in repo.list_bank_credits(cur, slug, start=start, end=end)]


@router.get("/vcc")
def list_vcc(slug: str, status: str | None = Query(None), open_only: bool = Query(False, alias="open"),
             ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    """Cards by charge deadline, each with days_left from the business date."""
    with db.db() as cur:
        today = htime.business_date(_hotel(cur, slug))
        rows = repo.list_vcc(cur, slug, status=status, open_only=open_only)
    return [{**repo.out(r, money_fields=VCC_MONEY), "days_left": (r["charge_deadline"] - today).days} for r in rows]


class ChargedIn(BaseModel):
    amount: str | None = Field(None, max_length=32, description="Decimal string; omitted = the whole balance")


@router.post("/vcc/{card_id}/charged")
def vcc_charged(slug: str, card_id: int, body: ChargedIn | None = None, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        amount = None
        if body is not None and body.amount is not None:
            cur.execute("SELECT currency FROM pfg_virtual_cards WHERE hotel_slug = %s AND id = %s", (slug, card_id))
            row = cur.fetchone()
            if row is None:
                raise ClefsError(404, "Virtual card not found", "vcc_not_found")
            try:
                amount = money.to_minor(body.amount, row["currency"])
            except (ArithmeticError, ValueError) as exc:
                raise ClefsError(400, "amount must be a decimal number", "invalid_amount") from exc
        return repo.out(repo.mark_vcc_charged(cur, slug, card_id, amount_minor=amount, actor=ctx),
                        money_fields=VCC_MONEY)


# ─── Rate evidence, channel economics, expected commission ───


class RateEvidenceIn(BaseModel):
    channel: str = Field(min_length=2, max_length=40)
    stay_date: date
    price: str = Field(min_length=1, max_length=32, description="Decimal string in `currency`")
    currency: str | None = Field(None, min_length=3, max_length=3)
    los: int = Field(1, ge=1, le=60)
    adults: int = Field(2, ge=1, le=10)
    room_label: str | None = Field(None, max_length=200)
    plan_label: str | None = Field(None, max_length=200)
    taxes_included: bool | None = None
    device: Literal["DESKTOP", "MOBILE"] = "DESKTOP"
    login_state: Literal["LOGGED_OUT", "LOGGED_IN", "MEMBER"] = "LOGGED_OUT"
    booker_country: str | None = Field(None, max_length=40)
    search_url: str | None = Field(None, max_length=2000)
    screenshot_file_id: str | None = Field(None, max_length=64)
    captured_at: str | None = None


@router.post("/rate-evidence", status_code=201)
def post_rate_evidence(slug: str, body: RateEvidenceIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """One parity observation typed by a person (source MANUAL); a
    screenshot is a file stored first through POST /hotels/{slug}/files."""
    with db.db() as cur:
        row = repo.insert_rate_evidence(cur, slug, _hotel(cur, slug).currency,
                                        {**body.model_dump(exclude_none=True), "source": "MANUAL"}, actor=ctx)
    return repo.out(row, money_fields=EVIDENCE_MONEY)


@router.get("/rate-evidence")
def list_rate_evidence(slug: str, start: date | None = Query(None, alias="from"),
                       end: date | None = Query(None, alias="to"), channel: str | None = Query(None),
                       ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db() as cur:
        rows = repo.list_rate_evidence(cur, slug, start=start, end=end, channel=normalize_ota(channel))
    return [repo.out(r, money_fields=EVIDENCE_MONEY) for r in rows]


@router.get("/channel-economics")
def get_channel_economics(slug: str, start: date | None = Query(None, alias="from"),
                          end: date | None = Query(None, alias="to"), ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Net ADR by channel over stayed nights (default: the 30 days ending
    at the business date; at most 400 days)."""
    from clefs.modules.profit_guard import api

    with db.db() as cur:
        today = htime.business_date(_hotel(cur, slug))
        end = end or today
        start = start or end - timedelta(days=29)
        if end < start or (end - start).days > 400:
            raise ClefsError(400, "from must be on or before to, at most 400 days apart", "invalid_range")
        return {"from": start, "to": end, "channels": api.channel_economics(cur, slug, start=start, end=end)}


@router.get("/expected-commission")
def get_expected_commission(slug: str, reservation_id: int = Query(...), ota_code: str | None = Query(None),
                            ctx: HotelCtx = Depends(VIEW)) -> dict:
    """What a stay should cost in commission under the terms in force at
    check-in (the reservation's own channel unless ota_code is given), with
    the calc trace; 404 when the channel has no terms."""
    from clefs.core import stays

    with db.db() as cur:
        res = stays.get_reservation(cur, reservation_id)
        if res is None or res["hotel_slug"] != slug:
            raise ClefsError(404, "Reservation not found", "reservation_not_found")
        ec = contracts.expected_commission(cur, slug, ota_code=ota_code or res["channel_code"],
                                           breakdown=stays.reservation_breakdown(cur, reservation_id),
                                           on=res["check_in"])
    if ec is None:
        raise ClefsError(404, "No contract or default terms for that channel", "no_terms")
    return {**repo.out({k: v for k, v in ec.items() if k != "calc"}, money_fields=("commission_minor", "base_minor")),
            "calc": ec["calc"]}
