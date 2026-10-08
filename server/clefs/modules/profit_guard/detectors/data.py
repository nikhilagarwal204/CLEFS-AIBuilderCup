"""Reads shared by the detectors. PMS data is truth and OTA data a claim
(02 spec 0.2): lines carry the reservation they were matched to, and every
read here is scoped to one hotel."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from clefs.modules.profit_guard import contracts, repo
from clefs.modules.profit_guard.detectors.base import Ctx, Scope

# Claims older than this are not re-read by a window run (a commit run
# reads its own batch whatever the dates).
LOOKBACK_DAYS = 400
CLAIM_DOCS = ("COMMISSION_INVOICE", "RES_STATEMENT", "PAYOUT_STATEMENT")


def window(ctx: Ctx, scope: Scope, *, back: int = LOOKBACK_DAYS) -> tuple[date, date]:
    return scope.since or (ctx.today - timedelta(days=back)), scope.until or (ctx.today + timedelta(days=400))


# ─── Statement lines ───


def statement_lines(ctx: Ctx, scope: Scope, *, ota: str | None = None, doc_kinds: tuple[str, ...] = CLAIM_DOCS,
                    extra: str = "") -> list[dict]:
    """Lines of claim documents (credit notes excluded: they net off, see
    credited()) with their document and matched reservation columns
    (r_* names). A commit run reads its batch; others the window."""
    since, until = window(ctx, scope)
    ota = ota or scope.ota_code
    ctx.cur.execute(
        f"""
        SELECT l.*, s.doc_no, s.doc_date, s.doc_kind, s.period_start, s.period_end,
               r.status AS r_status, r.check_in AS r_check_in, r.check_out AS r_check_out,
               r.actual_check_out AS r_actual_check_out, r.nights AS r_nights, r.rooms_count AS r_rooms,
               r.no_show_fee_minor AS r_no_show_fee, r.cancel_fee_minor AS r_cancel_fee,
               r.currency AS r_currency, r.channel_code AS r_channel, r.guest_country AS r_guest_country,
               r.collect_model AS r_collect_model
          FROM pfg_ota_statement_lines l
          JOIN pfg_ota_statements s ON s.id = l.statement_id
          LEFT JOIN reservations r ON r.id = l.reservation_id
         WHERE l.hotel_slug = %(h)s AND s.doc_kind = ANY(%(kinds)s)
           AND (%(ota)s::text IS NULL OR l.ota_code = %(ota)s)
           AND (CASE WHEN %(batch)s::bigint IS NOT NULL THEN l.batch_id = %(batch)s
                     ELSE COALESCE(s.doc_date, l.check_out, l.check_in) BETWEEN %(since)s AND %(until)s END)
           {extra}
         ORDER BY l.id
        """,
        {"h": ctx.slug, "kinds": list(doc_kinds), "ota": ota, "batch": scope.batch_id, "since": since,
         "until": until},
    )
    return [dict(r) for r in ctx.cur.fetchall()]


def credited(ctx: Ctx, ota: str, res_norm: str | None, currency: str, *, payouts: bool = True) -> int:
    """Commission already credited back for a booking, as a positive
    amount. It is the ledger's own measure (repo.booking_credit: the larger
    of statement and payout credits), so the credit a detector nets off a
    finding is exactly the baseline the ledger later counts recovery
    above. The draft carries it as `credited_minor`."""
    return repo.booking_credit(ctx.cur, ctx.slug, ota, res_norm, currency, payouts=payouts)


# ─── Terms (memoised per run: one contract lookup per OTA and date) ───


def terms_on(ctx: Ctx, ota: str, on: date | None) -> dict | None:
    cache = ctx.cache.setdefault("terms", {})
    key = (ota, on or ctx.today)
    if key not in cache:
        cache[key] = contracts.contract_on(ctx.cur, ctx.slug, ota, on or ctx.today)
    return cache[key]


def config_on(ctx: Ctx, ota: str, on: date | None) -> dict:
    cache = ctx.cache.setdefault("cfg", {})
    key = (ota, on or ctx.today)
    if key not in cache:
        cache[key] = repo.channel_config_on(ctx.cur, ctx.slug, ota, on or ctx.today)
    return cache[key]


def line_pct(ctx: Ctx, line: dict) -> Decimal | None:
    """The percent the OTA applied on a line: its own rate column (a ratio,
    0.15), else commission / commissionable, else the terms in force."""
    if line.get("commission_rate") is not None:
        return Decimal(str(line["commission_rate"])) * 100
    base, comm = line.get("commissionable_minor"), line.get("commission_minor")
    if base and comm is not None and base > 0:
        return Decimal(comm) * 100 / Decimal(base)
    terms = terms_on(ctx, line["ota_code"], line.get("check_in"))
    return Decimal(str(terms["base_commission_pct"])) if terms else None


def charge_sum(line: dict, types: tuple[str, ...]) -> int | None:
    """Σ charges of the given types on a line, None when the line carries
    no price breakdown at all."""
    charges = line.get("charges") or []
    if not charges:
        return None
    total = 0
    for c in charges:
        if str(c.get("type") or "").upper() in types:
            total += int(c.get("amount_minor") or 0)
    return total


def folio_fee(ctx: Ctx, reservation_id: int | None, codes: tuple[str, ...]) -> int:
    """Net amount of the given folio codes posted on a reservation."""
    if reservation_id is None:
        return 0
    ctx.cur.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS a FROM folio_charges "
        "WHERE hotel_slug = %s AND reservation_id = %s AND code = ANY(%s)",
        (ctx.slug, reservation_id, list(codes)),
    )
    return int(ctx.cur.fetchone()["a"] or 0)


def has_pms_coverage(ctx: Ctx, around: date | None) -> bool:
    """Whether PMS reservations exist around a date: an unmatched OTA line
    means something only when the PMS had data for that period."""
    if around is None:
        return False
    ctx.cur.execute(
        "SELECT 1 FROM reservations WHERE hotel_slug = %s AND check_in BETWEEN %s AND %s LIMIT 1",
        (ctx.slug, around - timedelta(days=3), around + timedelta(days=3)),
    )
    return ctx.cur.fetchone() is not None
