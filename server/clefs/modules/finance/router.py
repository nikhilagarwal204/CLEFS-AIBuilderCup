"""
Finance console routes, all under /hotels/{slug}/finance/ (Canon 13; 04 spec 6.8).

Permissions (04 spec 7.1): the weekly pulse needs finance.weekly.view; every
P&L route needs finance.pl.view or the specific act permission
(finance.import, finance.mapping.confirm, finance.publish, finance.connect,
finance.act, finance.manage). Benchmarks are written by Clefs admins only.
OAuth runs through core: /hotels/{slug}/oauth/{xero|qbo|zoho}/start.

Money leaves as *_minor ints next to Decimal strings (never floats); ratios
as Decimal strings next to percent strings. The README lists every route.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, has_admin_powers, require_perm
from clefs.core import actions
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.finance import anomalies, benchmarks, calc, mapping, pulse, review, usali, views

router = APIRouter(prefix="/hotels/{slug}/finance", tags=["finance"])

WEEKLY = require_perm("finance.weekly.view")
PL = require_perm("finance.pl.view")
IMPORT = require_perm("finance.import")
CONFIRM = require_perm("finance.mapping.confirm")
PUBLISH = require_perm("finance.publish")
CONNECT = require_perm("finance.connect")
ACT = require_perm("finance.act")
MANAGE = require_perm("finance.manage")
ANY_VIEW = require_perm("finance.view")


# ─── Helpers ───


def clean(v: Any) -> Any:
    """JSON-safe without floats: Decimal -> str, dates -> ISO strings."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _month(s: str) -> date:
    try:
        return calc.parse_month(s)
    except ValueError as exc:
        raise ClefsError(400, "month must be YYYY-MM", "invalid_month") from exc


def _uid(ctx: HotelCtx) -> int | None:
    return ctx.principal.user_id


# ─── Weekly pulse ───


@router.get("/weekly")
def get_weekly(from_: date | None = Query(None, alias="from"), to: date | None = None,
               ctx: HotelCtx = Depends(WEEKLY)) -> dict:
    """ISO weeks whose Monday is in [from, to] (default: the 13 weeks ending
    with the last complete week). Stored rollups, else computed live (not
    stored; a writable transaction so neighbour doors behave as in the job)."""
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.slug)
        last = calc.iso_week_start(htime.local_date(hotel)) - timedelta(days=7)
        end = calc.iso_week_start(to) if to else last
        start = calc.iso_week_start(from_) if from_ else end - timedelta(weeks=12)
        if (end - start).days > 7 * 104 or end < start:
            raise ClefsError(400, "from/to must span 1-104 weeks", "invalid_range")
        stored = {p["week_start"]: p for p in pulse.list_weeks(cur, ctx.slug, start, end)}
        weeks = []
        wk = start
        while wk <= end:
            p = stored.get(wk) or pulse.compute_week(cur, ctx.slug, wk)
            weeks.append(pulse.to_api(p))
            wk += timedelta(weeks=1)
        return {"currency": hotel.currency, "from": start.isoformat(), "to": end.isoformat(), "weeks": weeks,
                "latest": weeks[-1] if weeks else None}


# ─── Monthly one-pager and trend ───


@router.get("/monthly/{month}")
def get_monthly(month: str, ctx: HotelCtx = Depends(PL)) -> dict:
    """`month` is YYYY-MM or `latest` (the newest published month)."""
    with db.db(readonly=True) as cur:
        if month == "latest":
            cur.execute("""SELECT MAX(period_month) AS m FROM fin_monthly_summary
                           WHERE hotel_slug = %s AND published_at IS NOT NULL""", (ctx.slug,))
            m = (cur.fetchone() or {}).get("m")
            if m is None:
                raise ClefsError(404, "no month is published yet", "not_published")
        else:
            m = _month(month)
        out = views.monthly(cur, ctx.slug, m, detail=True)
        if out is None:
            raise ClefsError(404, "this month is not published", "not_published")
        return clean(out)


@router.get("/trend")
def get_trend(months: int = Query(12, ge=1, le=36), ctx: HotelCtx = Depends(PL)) -> dict:
    with db.db(readonly=True) as cur:
        return clean({"months": views.trend(cur, ctx.slug, months=months)})


@router.get("/verified-profit")
def get_verified_profit(month: str, ctx: HotelCtx = Depends(ANY_VIEW)) -> dict:
    """Clefs-verified profit for one month, per available room (the first tile)."""
    from clefs.modules.finance import api

    m = _month(month)
    with db.db(readonly=True) as cur:
        return clean(api.verified_profit(cur, ctx.slug, start=m, end=calc.month_end(m)))


# ─── Imports ───


class ImportIn(BaseModel):
    file_id: str = Field(min_length=1, max_length=64)
    statement_kind: Literal["pl", "trial_balance"] = "pl"
    period_month: str | None = Field(None, max_length=10)


_CORE_KIND = {"pl": "PL_STATEMENT", "trial_balance": "TRIAL_BALANCE"}
_CORE_FIELDS = ("id", "kind", "status", "filename", "file_format", "period_month", "row_count", "errors",
                "has_suggestion", "gate_status", "commit_result", "created_at")


def _batch_view(b: dict) -> dict:
    ccy = (b.get("currency") or "").strip()
    out = {k: b.get(k) for k in ("id", "period_month", "statement_kind", "source", "core_import_batch_id",
                                 "file_id", "currency", "basis", "status", "superseded_by", "published_at",
                                 "published_by", "created_at", "created_by")}
    out["currency"] = ccy
    for k in ("source_net_profit_minor", "mapped_net_profit_minor"):
        out[k] = b.get(k)
        out[k.removesuffix("_minor")] = calc.money_or_none(b.get(k), ccy)
    checks = b.get("checks") or {}
    # The excluded.* sum that reconciles mapped to source net profit (review
    # check 1): shown between the two so the owner sees why they differ.
    out["reconciling_minor"] = checks.get("reconciling_minor")
    out["reconciling"] = calc.money_or_none(checks.get("reconciling_minor"), ccy)
    out["checks"] = checks.get("items") or []
    out["blocking"] = bool(checks.get("blocking"))
    out["booked_net"] = bool(checks.get("booked_net"))
    out["unmapped"] = len(checks.get("unmapped") or [])
    return clean(out)


@router.post("/imports", status_code=201)
def post_import(body: ImportIn, ctx: HotelCtx = Depends(IMPORT)) -> dict:
    """After the Files upload (purpose finance.statement): a core ingest batch
    of kind PL_STATEMENT / TRIAL_BALANCE, committed straight away when ingest
    mapped it cleanly (else the core batch waits for its column mapping)."""
    from clefs.core import ingest

    month = _month(body.period_month) if body.period_month else None
    if body.statement_kind == "trial_balance" and month is None:
        raise ClefsError(400, "a trial balance needs period_month", "period_month_required")
    kind = _CORE_KIND[body.statement_kind]
    with db.db() as cur:
        core = _create_core_batch(cur, ctx, body, kind, month)
        finance_batches: list[dict] = []
        if core["status"] == "REVIEW" and not core.get("gate_blocking"):
            cur.execute("SAVEPOINT fin_import_commit")
            try:
                core = ingest.commit_batch(cur, ctx.slug, core["id"], actor=ctx)
                cur.execute("RELEASE SAVEPOINT fin_import_commit")
            except ClefsError as exc:
                if exc.code == "period_month_required":
                    # A single-month P&L with no month column and no month
                    # given. WHY raise (the whole upload rolls back) rather
                    # than keep a pending batch: that batch had no way to take
                    # a month later, and the same bytes uploaded again with a
                    # month deduped straight back to it. The stored file
                    # survives (Files), so the client re-posts its file_id
                    # with the month and nothing is uploaded twice.
                    raise ClefsError(400, "This P&L has no month column: pick the month it covers and import it "
                                     "again", "period_month_required") from exc
                # e.g. rows_have_errors: the person fixes them on the ingest screen
                cur.execute("ROLLBACK TO SAVEPOINT fin_import_commit")
                core = dict(core, commit_error={"code": exc.code, "detail": exc.detail})
        elif core["status"] == "COMMITTED":
            pass  # the same bytes were imported before
        result = core.get("commit_result") or {}
        for fb in result.get("finance_batches") or []:
            finance_batches.append(_batch_view(_get_batch(cur, ctx.slug, fb["id"])))
        audit(cur, ctx, "finance.import", "import_batch", str(core["id"]), hotel_slug=ctx.slug,
              after={"statement_kind": body.statement_kind, "status": core["status"]})
        return clean({"core_batch": {**{k: core.get(k) for k in _CORE_FIELDS},
                                     "commit_error": core.get("commit_error")},
                      "finance_batches": finance_batches})


def _create_core_batch(cur: Any, ctx: HotelCtx, body: ImportIn, kind: str, month: date | None) -> dict:
    """ingest.create_batch, plus the one case its sha256 dedupe gets wrong
    for a statement: the same bytes are still sitting in an open (never
    committed) batch labelled with no month or another month, and the person
    now gives the month. WHY discard and re-create: create_batch returns the
    open batch untouched and ignores the new period_month, so the month-less
    batch could never commit (QA OWN-07). ingest.discard_batch is the public
    way to drop an uncommitted batch ("the same bytes can then be uploaded
    again"); a committed batch is never touched."""
    from clefs.core import ingest

    def create() -> dict:
        return ingest.create_batch(cur, ctx.slug, file_id=body.file_id, kind=kind,
                                   source_code="pl_statement" if body.statement_kind == "pl" else "trial_balance",
                                   via="WEB", uploaded_by=_uid(ctx), period_month=month)

    core = create()
    stale = (not core.get("created") and month is not None and core.get("kind") == kind
             and core.get("status") in ingest.kinds.OPEN_STATUSES and core.get("period_month") != month)
    if stale:
        ingest.discard_batch(cur, ctx.slug, core["id"], actor=ctx)
        core = create()
    return core


def _get_batch(cur: Any, slug: str, batch_id: int) -> dict:
    from clefs.modules.finance import imports

    return imports.get_batch(cur, slug, batch_id)


@router.get("/imports")
def list_imports(status: str | None = None, limit: int = Query(50, ge=1, le=200), ctx: HotelCtx = Depends(PL)) -> dict:
    with db.db(readonly=True) as cur:
        cur.execute(
            """SELECT * FROM fin_import_batches WHERE hotel_slug = %s AND (%s::text IS NULL OR status = %s)
               ORDER BY period_month DESC, id DESC LIMIT %s""",
            (ctx.slug, status, status, limit),
        )
        rows = [_batch_view(dict(r)) for r in cur.fetchall()]
        cur.execute(
            """SELECT id, kind, status, filename, period_month, created_at FROM import_batches
               WHERE hotel_slug = %s AND kind IN ('PL_STATEMENT', 'TRIAL_BALANCE')
                 AND status IN ('UPLOADED', 'MAPPING_REQUIRED', 'EXTRACTING', 'REVIEW')
               ORDER BY id DESC LIMIT 20""",
            (ctx.slug,),
        )
        return clean({"imports": rows, "pending_uploads": [dict(r) for r in cur.fetchall()]})


@router.get("/imports/{batch_id:int}")
def get_import(batch_id: int, ctx: HotelCtx = Depends(PL)) -> dict:
    """The batch with its checks, its lines read through the mapping, and the
    summary preview (as reported, and normalised when booked net)."""
    from clefs.modules.finance import publish, summary

    with db.db(readonly=True) as cur:
        b = _get_batch(cur, ctx.slug, batch_id)
        snap = b["map_versions"] if b["status"] in ("published", "superseded") and b.get("map_versions") else None
        lines = review.lines_for_batch(cur, b, snapshot=snap)
        h, reported, stats = publish.headline(cur, ctx.slug, b, lines, b.get("checks") or {})
        ccy = b["currency"].strip()
        out = _batch_view(b)
        out["lines"] = clean([{**{k: ln[k] for k in ("account_id", "code", "name_raw", "parent_group_raw", "type_raw",
                                                    "usali_line", "locked", "ai_confidence", "learned_from",
                                                    "override_reason", "amount_minor")},
                               "amount": calc.money_or_none(ln["amount_minor"], ccy)} for ln in lines])
        out["summary"] = clean(summary.money_view({k: v for k, v in h.items() if k != "by_line"}, ccy))
        out["summary_reported"] = clean(summary.money_view({k: v for k, v in reported.items() if k != "by_line"}, ccy))
        out["usali"] = clean(views.usali_table(reported["by_line"], ccy))
        out["stat_inputs"] = clean(stats)
        return out


@router.post("/imports/{batch_id:int}/refresh")
def refresh_import(batch_id: int, ctx: HotelCtx = Depends(IMPORT)) -> dict:
    with db.db() as cur:
        _get_batch(cur, ctx.slug, batch_id)
        return _batch_view(review.refresh(cur, ctx.slug, batch_id))


@router.post("/imports/{batch_id:int}/publish")
def publish_import(batch_id: int, ctx: HotelCtx = Depends(PUBLISH)) -> dict:
    from clefs.modules.finance import publish

    with db.db() as cur:
        out = publish.publish(cur, ctx.slug, batch_id, actor=ctx, actor_id=_uid(ctx))
        month = out["summary"]["period_month"]
        return clean({"batch": _batch_view(out["batch"]), "monthly": views.monthly(cur, ctx.slug, month, detail=True)})


# ─── Mapping ───


@router.get("/mapping")
def get_mapping(status: Literal["open", "confirmed", "all"] = "open", batch_id: int | None = None,
                ctx: HotelCtx = Depends(PL)) -> dict:
    """Accounts with their live mapping. `open` = unmapped or unconfirmed.
    Rows carry `language` (ar/en/mixed) so the console sets dir=rtl."""
    with db.db(readonly=True) as cur:
        cur.execute(
            """SELECT a.id AS account_id, a.code, a.name_raw, a.language, a.parent_group_raw, a.type_raw,
                      a.source_system, m.usali_line, m.usali_dept, m.category, m.version, m.locked, m.ai_confidence,
                      m.ai_rationale, m.learned_from, m.data_origin, m.alternatives, m.override_reason,
                      m.confirmed_at, m.is_pass_through_tax
               FROM fin_coa_accounts a
               LEFT JOIN fin_usali_map m ON m.account_id = a.id AND m.superseded_at IS NULL
               WHERE a.hotel_slug = %s
                 AND (%s::bigint IS NULL OR a.id IN (SELECT account_id FROM fin_pl_lines WHERE batch_id = %s))
                 AND (%s = 'all' OR (%s = 'open' AND (m.id IS NULL OR NOT m.locked))
                      OR (%s = 'confirmed' AND m.locked))
               ORDER BY COALESCE(m.usali_dept, 'zz'), a.code NULLS LAST, a.name_norm""",
            (ctx.slug, batch_id, batch_id, status, status, status),
        )
        rows = [dict(r, below_gop_word=mapping.below_gop_lexicon(r["name_raw"]),
                     bulk_ok=bool(r["ai_confidence"] is not None and float(r["ai_confidence"])
                                  >= mapping.BULK_MIN_CONFIDENCE and not r["locked"]))
                for r in cur.fetchall()]
        return clean({"accounts": rows, "catalog": usali.catalog(), "schedules": usali.SCHEDULES,
                      "bulk_min_confidence": mapping.BULK_MIN_CONFIDENCE})


class ConfirmItem(BaseModel):
    account_id: int
    usali_line: str | None = Field(None, max_length=60)
    override_reason: str | None = Field(None, max_length=500)


class ConfirmIn(BaseModel):
    all_above: float | None = Field(None, ge=0, le=1)
    account_ids: list[int] | None = Field(None, max_length=1000)
    items: list[ConfirmItem] | None = Field(None, max_length=500)


@router.post("/mapping/confirm")
def confirm_mapping(body: ConfirmIn, ctx: HotelCtx = Depends(CONFIRM)) -> dict:
    """Bulk "Confirm all >= 0.85" ({all_above: 0.85, account_ids?}) or explicit
    items; a proposal under 0.85 is confirmed as a single item. Every open
    batch is re-checked afterwards."""
    with db.db() as cur:
        res = mapping.confirm(cur, ctx.slug, actor_id=_uid(ctx), all_above=body.all_above,
                              account_ids=body.account_ids,
                              items=[i.model_dump() for i in body.items] if body.items else None)
        review.refresh_open(cur, ctx.slug)
        audit(cur, ctx, "finance.mapping.confirm", "fin_coa_accounts", ",".join(map(str, res["confirmed"][:50])),
              hotel_slug=ctx.slug, after={"count": len(res["confirmed"])})
        cur.execute("""SELECT id, period_month, status FROM fin_import_batches WHERE hotel_slug = %s
                       AND status IN ('mapping_required', 'review', 'checks_failed') ORDER BY period_month""",
                    (ctx.slug,))
        return clean({**res, "open_batches": [dict(r) for r in cur.fetchall()]})


@router.post("/mapping/propose")
def propose_mapping(batch_id: int | None = None, ctx: HotelCtx = Depends(IMPORT)) -> dict:
    """Queue the LLM step for every account still without a proposal."""
    from clefs import jobs

    with db.db() as cur:
        cur.execute(
            """SELECT a.id FROM fin_coa_accounts a
               LEFT JOIN fin_usali_map m ON m.account_id = a.id AND m.superseded_at IS NULL
               WHERE a.hotel_slug = %s AND m.id IS NULL
                 AND (%s::bigint IS NULL OR a.id IN (SELECT account_id FROM fin_pl_lines WHERE batch_id = %s))""",
            (ctx.slug, batch_id, batch_id),
        )
        ids = [r["id"] for r in cur.fetchall()]
        if ids:
            jobs.enqueue("finance.map_propose", {"batch_id": batch_id, "account_ids": ids}, hotel_slug=ctx.slug,
                         cur=cur)
        return {"queued": len(ids)}


# ─── Connections ───


@router.get("/connections")
def get_connections(ctx: HotelCtx = Depends(PL)) -> dict:
    """Xero, QuickBooks and Zoho (stub) links: the core OAuth state plus
    finance's last sync. Connect with GET /hotels/{slug}/oauth/{provider}/start."""
    from clefs.core import oauth

    with db.db(readonly=True) as cur:
        core = {c["provider"]: c for c in oauth.list_connections(cur, ctx.slug)
                if c.get("provider") in ("xero", "qbo", "zoho")}
        cur.execute("SELECT * FROM fin_connections WHERE hotel_slug = %s", (ctx.slug,))
        fin = {r["provider"]: dict(r) for r in cur.fetchall()}
        out = []
        for p in ("xero", "qbo", "zoho"):
            c = core.get(p, {"provider": p, "status": "not_connected", "configured": False})
            f = fin.get(p, {})
            out.append({"provider": p, "label": c.get("label") or p, "configured": c.get("configured"),
                        "status": c.get("status"), "account_ref": c.get("account_ref"),
                        "last_sync_at": f.get("last_sync_at"), "sync_status": f.get("status"),
                        "last_error": f.get("last_error") or c.get("last_error"),
                        "coming_next": p == "zoho", "start_path": f"/hotels/{ctx.slug}/oauth/{p}/start"})
        out.append({"provider": "tally", "label": "Tally", "upload_only": True})
        return clean({"connections": out})


class SyncIn(BaseModel):
    month: str | None = Field(None, max_length=10)
    statement_kind: Literal["pl", "trial_balance"] = "pl"


@router.post("/connections/{provider}/sync")
def sync_connection(provider: Literal["xero", "qbo", "zoho"], body: SyncIn | None = None,
                    ctx: HotelCtx = Depends(CONNECT)) -> dict:
    """On-demand sync of one month (default: the previous month)."""
    from clefs.modules.finance import connectors

    body = body or SyncIn()
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.slug)
        month = _month(body.month) if body.month else calc.prev_month(htime.local_date(hotel))
        out = connectors.sync_provider(cur, ctx.slug, provider, month, statement_kind=body.statement_kind,
                                       actor_id=_uid(ctx))
        audit(cur, ctx, "finance.connector_sync", "fin_connection", provider, hotel_slug=ctx.slug, after=out)
        return clean(out)


# ─── Anomalies ───


@router.get("/anomalies")
def get_anomalies(month: str | None = None, status: str | None = "open", ctx: HotelCtx = Depends(PL)) -> dict:
    with db.db(readonly=True) as cur:
        rows = anomalies.list_anomalies(cur, ctx.slug, month=_month(month) if month else None, status=status)
        return clean({"anomalies": [views.anomaly_view(a) for a in rows]})


@router.post("/anomalies/{anomaly_id:int}/action", status_code=201)
def anomaly_action(anomaly_id: int, ctx: HotelCtx = Depends(ACT)) -> dict:
    """"Create action": a finance.investigate_anomaly task in the ledger,
    linked to the anomaly's signal (idempotent per anomaly)."""
    with db.db() as cur:
        cur.execute("SELECT * FROM fin_anomalies WHERE hotel_slug = %s AND id = %s FOR UPDATE", (ctx.slug, anomaly_id))
        a = cur.fetchone()
        if a is None:
            raise ClefsError(404, "anomaly not found", "anomaly_not_found")
        month = a["period_month"].strftime("%B %Y")
        label = usali.LINES[a["usali_line"]].label if a["usali_line"] in usali.LINES else a["kind"].replace("_", " ")
        act = actions.create_action(
            cur, ctx.slug, type="finance.investigate_anomaly", title=f"Investigate {label} in {month}",
            params={"anomaly_id": a["id"], "kind": a["kind"], "month": a["period_month"].isoformat(),
                    "usali_line": a["usali_line"]},
            signal_id=a["signal_id"], expected_minor=abs(int(a["delta_minor"])) if a["delta_minor"] else None,
            currency=a["currency"].strip() if a["delta_minor"] else None, owner_role="gm",
            dedupe_key=f"finance.investigate_anomaly:{a['id']}", actor=ctx, via="console",
        )
        if act.get("id"):
            cur.execute("UPDATE fin_anomalies SET action_id = %s WHERE id = %s", (act["id"], a["id"]))
        return clean({"action": {k: act.get(k) for k in ("id", "status", "type", "title", "created", "level",
                                                         "reason")}, "anomaly_id": a["id"]})


# ─── Baseline ───


@router.get("/baseline")
def get_baseline(ctx: HotelCtx = Depends(PL)) -> dict:
    with db.db(readonly=True) as cur:
        b = benchmarks.get_baseline(cur, ctx.slug)
        return clean({"baseline": b})


class BaselineIn(BaseModel):
    end_month: str | None = Field(None, max_length=10)


@router.post("/baseline", status_code=201)
def post_baseline(body: BaselineIn | None = None, ctx: HotelCtx = Depends(PUBLISH)) -> dict:
    """Register the pilot baseline: up to 12 trailing published months with
    their mapping snapshots. Immutable once written."""
    end = _month(body.end_month) if body and body.end_month else None
    with db.db() as cur:
        row = benchmarks.register_baseline(cur, ctx.slug, actor_id=_uid(ctx), end_month=end)
        audit(cur, ctx, "finance.baseline.register", "fin_baseline", row["registered_at"].isoformat(),
              hotel_slug=ctx.slug, after={"period_start": row["period_start"].isoformat(),
                                          "period_end": row["period_end"].isoformat()})
        return clean({"baseline": row})


# ─── Market benchmarks (global; Clefs admins write) ───


@router.get("/benchmarks")
def get_benchmarks(region: str | None = None, ctx: HotelCtx = Depends(PL)) -> dict:
    with db.db(readonly=True) as cur:
        return clean({"benchmarks": benchmarks.list_benchmarks(cur, region=region), "caveat": benchmarks.CAVEAT})


class BenchmarkIn(BaseModel):
    region: str = Field(min_length=1, max_length=60)
    metric: Literal["goppar_yoy"] = "goppar_yoy"
    period: str = Field(min_length=7, max_length=10)
    period_kind: Literal["month", "ytd"]
    value: Decimal = Field(description="percent change, e.g. -47.8")
    source_url: str = Field(min_length=8, max_length=1000)
    publisher: str | None = Field(None, max_length=120)


@router.post("/benchmarks", status_code=201)
def post_benchmark(body: BenchmarkIn, ctx: HotelCtx = Depends(PL)) -> dict:
    if not has_admin_powers(ctx.principal):
        raise ClefsError(403, "only Clefs admins enter market benchmarks", "forbidden")
    with db.db() as cur:
        row = benchmarks.add_benchmark(cur, region=body.region, metric=body.metric, period=body.period,
                                       period_kind=body.period_kind, value=body.value, source_url=body.source_url,
                                       publisher=body.publisher, entered_by=_uid(ctx))
        audit(cur, ctx, "finance.benchmark.add", "fin_market_benchmark", str(row["id"]), after=clean(row))
        return clean(row)


# ─── Settings and statistical inputs ───

# The Profit Guard console page that edits its owner policies, relative to
# /app/{slug}/. None while no such page exists (its tabs today: overview,
# findings, disputes, imports, channels, vcc, tax, contracts, parity).
SC_SETTINGS_PATH: str | None = None


@router.get("/settings")
def get_settings_view(ctx: HotelCtx = Depends(PL)) -> dict:
    """Ratio bands (editable with finance.manage) and the service-charge
    treatment read from Profit Guard, its single source (6.4).

    WHY the card can write it: the Profit Guard console has no settings page
    (02 spec 3.10 lists none), so a link to one 404s and the treatment, which
    drives both NRevPAR and the USALI rooms line, could be changed nowhere.
    Finance still keeps no setting of its own: the client writes through
    Profit Guard's own PUT /profit-guard/policies (edit_api), gated by that
    route's profit.manage. `settings_path` stays None until Profit Guard
    ships a settings page; the client links only to a path given here."""
    from clefs.modules.finance import ports, settings as fin_settings

    with db.db(readonly=True) as cur:
        s = fin_settings.load(cur, ctx.slug)
        can_edit = ctx.can("profit.manage")
        return {"settings": s.model_dump(), "service_charge": {
            "treat_sc_as_revenue": ports.treat_sc_as_revenue(cur, ctx.slug), "source": "profit_guard",
            "read_only": not can_edit, "can_edit": can_edit, "edit_api": "profit-guard/policies",
            "settings_path": SC_SETTINGS_PATH}}


@router.put("/settings")
def put_settings(body: dict, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    from clefs.core.hotels import set_module_settings
    from clefs.modules.finance.settings import FinanceSettings

    try:
        s = FinanceSettings.model_validate(body.get("settings", body))
    except ValueError as exc:
        raise ClefsError(422, str(exc).splitlines()[0], "invalid_settings") from exc
    with db.db() as cur:
        set_module_settings(cur, ctx.slug, "finance", s.model_dump(), actor=ctx)
        return {"settings": s.model_dump()}


@router.get("/stat-inputs/{month}")
def get_stat_inputs(month: str, ctx: HotelCtx = Depends(PL)) -> dict:
    with db.db(readonly=True) as cur:
        return clean(review.stat_inputs(cur, ctx.slug, _month(month)))


class StatOverrides(BaseModel):
    available_rn: int | None = Field(None, ge=0)
    rooms_sold: int | None = Field(None, ge=0)
    rooms_rev_pms_minor: int | None = Field(None, ge=0)
    ota_share: Decimal | None = Field(None, ge=0, le=1)
    ota_commission_expected_minor: int | None = Field(None, ge=0)


@router.put("/stat-inputs/{month}")
def put_stat_inputs(month: str, body: StatOverrides, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Manual overrides that win over the computed inputs (audited)."""
    m = _month(month)
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.slug)
        review.set_overrides(cur, ctx.slug, m, body.model_dump(exclude_none=True), hotel.currency)
        audit(cur, ctx, "finance.stat_inputs.override", "fin_stat_inputs", m.isoformat(), hotel_slug=ctx.slug,
              after=clean(body.model_dump(exclude_none=True)))
        cur.execute("SELECT id FROM fin_import_batches WHERE hotel_slug = %s AND period_month = %s", (ctx.slug, m))
        for r in cur.fetchall():
            review.refresh(cur, ctx.slug, r["id"])
        return clean(review.stat_inputs(cur, ctx.slug, m))
