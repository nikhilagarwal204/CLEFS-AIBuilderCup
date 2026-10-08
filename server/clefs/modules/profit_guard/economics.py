"""Net ADR by channel (02 spec 3.6): the nightly `pg.channel_economics_rollup`
into pfg_channel_economics, per channel and hotel-local month, allocated by
stayed night.

```
RN = stayed room nights            Gross = Σ guest-paid
PassThrough = MF + VAT + TD (+TF)  RoomRev = R (+ SC when treat_sc_as_revenue, default true)
C = invoiced commission (statements, credit notes netted) else expected from the contract
CommissionOnTax = C - pct x base_ex_tax, capped at pct x the taxes inside the commissioned base
PayCost = (card fee + payments fee) x gross, + booking-engine fee on direct channels
FX = L11 losses        CxlCost = commission on stays that never happened + AGX per-booking costs
ProgramCost (info only) = Σ (public - sell) over programme-discounted OTA lines
Net = RoomRev - C - PayCost - FX - CxlCost;  NetADR = Net / RN;  Retention = Net / Gross
```

Commission on tax is structural for Agoda (commission on the tax-inclusive
RSR is the contract: shown here and in contract negotiation, never
disputed) and the L1 case for Booking.com (the municipality fee or Tourism
Dirham not configured as charges; Canon K2 puts room + SC + VAT in the
base). The live per-stay figures come from repo.channel_economics (the
data part's api.channel_economics); this file adds the terms that need the
month (FX, programmes, AGX) and stores the result.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock, money
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel

FIELDS = ("room_nights", "gross_minor", "room_rev_minor", "sc_minor", "pass_through_tax_minor", "commission_minor",
          "commission_on_tax_minor", "payment_cost_minor", "fx_loss_minor", "cancellation_cost_minor",
          "program_cost_minor", "net_minor")
# Which pass-through taxes sit inside each base rule's commissioned amount
# (so commission on them is commission on tax). Booking's ROOM_INCL_VAT has
# VAT in the base by contract (K2); what is left is MF/TD/TF when they are
# not configured as charges.
BASE_EX_TAX = {"ROOM_INCL_VAT": ("room", "sc", "vat"), "RSR_TAX_INCL": ("room", "sc"),
               "ROOM_EXCL_TAX": ("room", "sc"), "NET_RATE": ("room", "sc")}
TAXES = ("mf", "vat", "td", "tf")
COT_KIND = {"AGODA": "structural", "BOOKING": "fee_config"}


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def rnd(x: Decimal) -> int:
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ─── Inputs ───


def night_parts(cur: Any, hotel_slug: str, start: date, end: date) -> dict[str, dict[str, int]]:
    """Σ nightly components per channel over stayed nights in [start, end]
    (cancelled and no-show stays never stayed)."""
    cur.execute(
        """
        SELECT UPPER(COALESCE(r.channel_code, 'OTHER')) AS channel, SUM(n.rate_minor * n.rooms) AS room,
               SUM(n.sc_minor) AS sc, SUM(n.mf_minor) AS mf, SUM(n.vat_minor) AS vat, SUM(n.td_minor) AS td,
               SUM(n.tf_minor) AS tf
          FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
         WHERE n.hotel_slug = %s AND n.stayed AND n.stay_date BETWEEN %s AND %s
           AND r.status NOT IN ('CANCELLED', 'NO_SHOW')
         GROUP BY 1
        """,
        (hotel_slug, start, end),
    )
    return {r["channel"]: {k: int(r[k] or 0) for k in ("room", "sc", "mf", "vat", "td", "tf")}
            for r in cur.fetchall()}


def commission_on_tax(cur: Any, hotel_slug: str, channel: str, commission: int, parts: dict[str, int],
                      start: date, end: date) -> tuple[int, dict]:
    """(minor units, trace): C - pct x base_ex_tax, never below 0 and never
    above pct x the taxes that can sit inside the commissioned amount (so a
    rate overcharge, L2's business, is not called tax).

    Booking.com (K2): MF and TD sit in the price only while they are NOT
    configured as charges (the L1b case), so only those count here; when
    they are configured and Booking still commissions them, that is L1,
    and the L1 findings for the month's stays are added as they stand."""
    from clefs.modules.profit_guard import contracts, repo

    terms = contracts.contract_on(cur, hotel_slug, channel, start)
    if terms is None or commission <= 0 or not parts:
        return 0, {}
    pct, _uplifts = contracts.commission_pct(cur, hotel_slug, terms, start)
    rule = str(terms.get("commission_base_rule") or "ROOM_EXCL_TAX")
    inside = BASE_EX_TAX.get(rule, ("room", "sc"))
    base_ex_tax = sum(parts.get(k, 0) for k in inside)
    taxed_keys = [k for k in TAXES if k not in inside]
    l1 = 0
    if channel == "BOOKING":
        cfg = repo.channel_config_on(cur, hotel_slug, channel, start)
        flags = {"mf": "mf_configured_as_charge", "td": "td_configured_as_charge", "tf": "td_configured_as_charge"}
        taxed_keys = [k for k in taxed_keys if k in flags and not cfg.get(flags[k])]
        cur.execute(
            "SELECT COALESCE(SUM(f.amount_minor), 0) AS s FROM pfg_findings f JOIN pfg_ota_statement_lines l "
            "ON l.id = f.statement_line_id WHERE f.hotel_slug = %s AND f.detector = 'L1' AND f.ota_code = 'BOOKING' "
            "AND f.status <> 'DISMISSED' AND l.check_in BETWEEN %s AND %s", (hotel_slug, start, end))
        l1 = int(cur.fetchone()["s"])
    elif rule == "RSR_TAX_INCL":
        # A fee collected at the property (Agoda: the Tourism Dirham) is not
        # in the sell rate at all, so no commission can land on it.
        excluded = {"MUNICIPALITYFEE": ("mf",), "TOURISMFEE": ("td", "tf")}
        out_of_rate = {c for t in terms.get("excluded_charge_types") or [] for c in excluded.get(str(t).upper(), ())}
        taxed_keys = [k for k in taxed_keys if k not in out_of_rate]
    taxed = sum(parts.get(k, 0) for k in taxed_keys)
    over = Decimal(commission) - pct / 100 * base_ex_tax
    cap = pct / 100 * taxed
    value = max(0, min(rnd(over), rnd(cap))) + l1
    return value, {"pct": str(pct), "base_rule": rule, "base_ex_tax_minor": base_ex_tax, "taxes_in_base_minor": taxed,
                   "over_minor": rnd(over), "cap_minor": rnd(cap), "l1_findings_minor": l1}


def fx_losses(cur: Any, hotel_slug: str, month: date, currency: str) -> dict[str, int]:
    """L11 losses per OTA for payouts credited in the month."""
    cur.execute(
        "SELECT ota_code, SUM(amount_minor) AS s FROM pfg_findings WHERE hotel_slug = %s AND detector = 'L11' "
        "AND status <> 'DISMISSED' AND currency = %s AND split_part(COALESCE(subject_ref, ''), '|', 1) = %s "
        "GROUP BY ota_code", (hotel_slug, currency, f"month:{month.strftime('%Y-%m')}"),
    )
    return {(r["ota_code"] or "").upper(): int(r["s"] or 0) for r in cur.fetchall()}


def program_costs(cur: Any, hotel: Any, start: date, end: date) -> dict[str, dict]:
    """Per OTA: {program_cost_minor, agx_minor, depth_weighted, gross} from
    the month's statement lines. A line's discount depth stacks its
    DISCOUNT programmes as 1 - Π(1 - d); public = sell / (1 - depth). AGX
    and other PER_BOOKING_COST programmes cost their fixed amount (and
    percent) per line that carries them."""
    from clefs.modules.profit_guard import repo

    cur.execute(
        """
        SELECT l.ota_code, l.check_in, l.gross_minor, l.programs, l.booker_country, l.rate_plan, l.currency
          FROM pfg_ota_statement_lines l JOIN pfg_ota_statements s ON s.id = l.statement_id
         WHERE l.hotel_slug = %s AND l.check_in BETWEEN %s AND %s AND l.line_status IN ('OK', 'MODIFIED')
           AND s.doc_kind IN ('COMMISSION_INVOICE', 'RES_STATEMENT') AND COALESCE(l.gross_minor, 0) > 0
        """,
        (hotel.slug, start, end),
    )
    out: dict[str, dict] = defaultdict(lambda: {"program_cost_minor": 0, "agx_minor": 0, "depth_weighted": Decimal(0),
                                                "gross": 0})
    cache: dict[tuple, list[dict]] = {}
    for ln in [dict(r) for r in cur.fetchall()]:
        ota = (ln["ota_code"] or "").upper()
        acc, gross = out[ota], int(ln["gross_minor"])
        if ln["currency"] != hotel.currency:
            try:
                gross = money.convert_minor(cur, gross, ln["currency"], hotel.currency, ln["check_in"])
            except ClefsError:
                continue
        acc["gross"] += gross
        names = {str(p).upper() for p in ln["programs"] or []}
        if not names:
            continue
        key = (ota, ln["check_in"], ln["booker_country"], ln["rate_plan"])
        if key not in cache:
            cache[key] = repo.active_programs(cur, hotel.slug, ota, ln["check_in"], booker_country=ln["booker_country"],
                                              rate_plan=ln["rate_plan"])
        progs = [p for p in cache[key] if str(p["program"]).upper() in names]
        keep = Decimal(1)
        for p in progs:
            if p["kind"] == "DISCOUNT" and p["pct"] is not None:
                keep *= 1 - Decimal(str(p["pct"])) / 100
            elif p["kind"] == "PER_BOOKING_COST":
                acc["agx_minor"] += int(p["fixed_minor"] or 0) + (rnd(Decimal(gross) * Decimal(str(p["pct"])) / 100)
                                                                 if p["pct"] is not None else 0)
        depth = 1 - keep
        if 0 < depth < 1:
            acc["program_cost_minor"] += rnd(Decimal(gross) / keep - gross)
            acc["depth_weighted"] += depth * gross
    return out


# ─── Rollup ───


def _to_hotel(cur: Any, hotel: Any, row: dict, on: date) -> dict | None:
    """A per-currency row from repo in the hotel's currency (None: no rate)."""
    if row["currency"] == hotel.currency:
        return row
    try:
        conv = {k: money.convert_minor(cur, int(row[k]), row["currency"], hotel.currency, on)
                for k in FIELDS if k in row and k != "room_nights"}
    except ClefsError:
        return None
    return {**row, **conv, "currency": hotel.currency}


def compute_month(cur: Any, hotel_slug: str, month: date) -> list[dict]:
    """The 3.6 rows for one month, in the hotel's currency, not stored."""
    from clefs.modules.profit_guard import repo

    hotel = get_hotel(cur, hotel_slug)
    start, end = month_start(month), month_end(month)
    sc_rev = bool(repo.get_policies(cur, hotel_slug).get("treat_sc_as_revenue", True))
    acc: dict[str, dict] = {}
    for raw in repo.channel_economics(cur, hotel_slug, start=start, end=end, sc_as_revenue=sc_rev):
        row = _to_hotel(cur, hotel, raw, end)
        if row is None:
            continue
        ch = row["channel_code"]
        a = acc.setdefault(ch, {"channel": ch, "is_ota": row["is_ota"], "bases": set(), **{f: 0 for f in FIELDS}})
        a["bases"].add(row.get("commission_basis") or "none")
        for f in ("room_nights", "gross_minor", "room_rev_minor", "sc_minor", "pass_through_tax_minor",
                  "commission_minor", "payment_cost_minor", "cancellation_cost_minor"):
            a[f] += int(row.get(f) or 0)
    parts = night_parts(cur, hotel_slug, start, end)
    fx = fx_losses(cur, hotel_slug, start, hotel.currency)
    progs = program_costs(cur, hotel, start, end)
    for ch in set(fx) | {k for k, v in progs.items() if v["agx_minor"] or v["program_cost_minor"]}:
        acc.setdefault(ch, {"channel": ch, "is_ota": True, "bases": set(), **{f: 0 for f in FIELDS}})
    out = []
    for ch, a in sorted(acc.items()):
        cot, trace = (commission_on_tax(cur, hotel_slug, ch, a["commission_minor"], parts.get(ch, {}), start, end)
                      if a["is_ota"] else (0, {}))
        p = progs.get(ch) or {"program_cost_minor": 0, "agx_minor": 0, "depth_weighted": Decimal(0), "gross": 0}
        a["commission_on_tax_minor"] = cot
        a["fx_loss_minor"] = fx.get(ch, 0)
        a["cancellation_cost_minor"] += p["agx_minor"]
        a["program_cost_minor"] = p["program_cost_minor"]
        a["net_minor"] = (a["room_rev_minor"] - a["commission_minor"] - a["payment_cost_minor"] - a["fx_loss_minor"]
                          - a["cancellation_cost_minor"])
        bases = a.pop("bases") - {"none"}
        a["commission_basis"] = "none" if not bases else (bases.pop() if len(bases) == 1 else "mixed")
        a["avg_discount_depth"] = ((p["depth_weighted"] / p["gross"]).quantize(Decimal("0.0001"))
                                   if p["gross"] else None)
        a["commission_on_tax_trace"] = trace
        a["currency"] = hotel.currency
        out.append(a)
    return out


def rollup_month(cur: Any, hotel_slug: str, month: date) -> list[dict]:
    """Recompute and store one month (rows for channels that no longer
    have anything that month are removed, so a corrected import heals)."""
    rows = compute_month(cur, hotel_slug, month)
    m = month_start(month)
    for r in rows:
        cols = {f: r[f] for f in FIELDS} | {"avg_discount_depth": r["avg_discount_depth"],
                                            "commission_basis": r["commission_basis"], "currency": r["currency"]}
        names = list(cols)
        cur.execute(
            f"""
            INSERT INTO pfg_channel_economics (hotel_slug, month, channel, {', '.join(names)}, computed_at)
            VALUES (%s, %s, %s, {', '.join(['%s'] * len(names))}, %s)
            ON CONFLICT (hotel_slug, month, channel) DO UPDATE SET
                {', '.join(f'{n} = EXCLUDED.{n}' for n in names)}, computed_at = EXCLUDED.computed_at
            """,
            (hotel_slug, m, r["channel"], *cols.values(), clock.now()),
        )
    cur.execute("DELETE FROM pfg_channel_economics WHERE hotel_slug = %s AND month = %s AND NOT (channel = ANY(%s))",
                (hotel_slug, m, [r["channel"] for r in rows] or [""]))
    return rows


def nightly(cur: Any, hotel: Any) -> dict:
    """pg.channel_economics_rollup (03:00 local): this month so far and the
    last one (late statements and credit notes still move it)."""
    this = month_start(htime.local_date(hotel, clock.now()))
    months = [month_start(this - timedelta(days=1)), this]
    return {m.isoformat(): len(rollup_month(cur, hotel.slug, m)) for m in months}


# ─── Reads ───


def ratio(num: int, den: int, q: str = "0.0001") -> Decimal | None:
    return (Decimal(num) / Decimal(den)).quantize(Decimal(q), rounding=ROUND_HALF_UP) if den else None


def waterfall(r: dict, *, sc_as_revenue: bool) -> list[dict]:
    """Gross -> pass-through taxes -> (SC when not revenue) -> commission ->
    payment costs -> FX -> cancellations -> Net, for the WaterfallChart."""
    steps = [{"label": "Gross", "kind": "total", "value_minor": r["gross_minor"]},
             {"label": "Taxes and fees passed through", "kind": "delta", "value_minor": -r["pass_through_tax_minor"]}]
    if not sc_as_revenue:
        steps.append({"label": "Service charge", "kind": "delta", "value_minor": -r["sc_minor"]})
    steps += [
        {"label": "Commission", "kind": "delta", "value_minor": -r["commission_minor"]},
        {"label": "Payment costs", "kind": "delta", "value_minor": -r["payment_cost_minor"]},
        {"label": "FX loss", "kind": "delta", "value_minor": -r["fx_loss_minor"]},
        {"label": "Cancellation costs", "kind": "delta", "value_minor": -r["cancellation_cost_minor"]},
        {"label": "Net", "kind": "total", "value_minor": r["net_minor"]},
    ]
    return steps


def read_months(cur: Any, hotel_slug: str, start: date, end: date) -> dict:
    """Stored rollup rows between two months, each with NetADR, GrossADR,
    retention, effective commission and its waterfall, plus totals."""
    from clefs.modules.profit_guard import repo

    hotel = get_hotel(cur, hotel_slug)
    sc_rev = bool(repo.get_policies(cur, hotel_slug).get("treat_sc_as_revenue", True))
    cur.execute("SELECT * FROM pfg_channel_economics WHERE hotel_slug = %s AND month BETWEEN %s AND %s "
                "ORDER BY month, channel", (hotel_slug, month_start(start), month_start(end)))
    rows = []
    for r in [dict(x) for x in cur.fetchall()]:
        out = repo.out(r, money_fields=FIELDS)
        rn = int(r["room_nights"])
        out.update(
            month=r["month"].strftime("%Y-%m"),
            net_adr_minor=rnd(Decimal(r["net_minor"]) / rn) if rn else None,
            gross_adr_minor=rnd(Decimal(r["gross_minor"]) / rn) if rn else None,
            retention=str(ratio(r["net_minor"], r["gross_minor"])) if r["gross_minor"] else None,
            effective_commission_pct=str(ratio(100 * r["commission_minor"], r["room_rev_minor"], "0.01"))
            if r["room_rev_minor"] else None,
            commission_on_tax_kind=COT_KIND.get(r["channel"], "other") if r["commission_on_tax_minor"] else None,
            waterfall=waterfall(r, sc_as_revenue=sc_rev),
        )
        for k in ("net_adr_minor", "gross_adr_minor"):
            out[k.removesuffix("_minor")] = money.money_str(out[k], r["currency"]) if out[k] is not None else None
        rows.append(out)
    tot = {f: sum(int(r[f]) for r in rows) for f in FIELDS}
    return {"currency": hotel.currency, "from": month_start(start).strftime("%Y-%m"),
            "to": month_start(end).strftime("%Y-%m"), "rows": rows, "totals": repo.out(
                {**tot, "currency": hotel.currency}, money_fields=FIELDS)}


# ─── Routes ───
# Mounted by the manifest (FOUNDATION-REQUEST: module.py lists
# economics.router). The live range view stays GET /channel-economics
# (router_data.py); these read and refresh the stored monthly rollup.

from fastapi import APIRouter, Depends, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from clefs import db  # noqa: E402
from clefs.auth.deps import HotelCtx, require_perm  # noqa: E402

router = APIRouter(prefix="/hotels/{slug}/profit-guard", tags=["profit_guard"])
VIEW = require_perm("profit.view")
MANAGE = require_perm("profit.manage")


def _month(v: str | None, default: date) -> date:
    if not v:
        return default
    try:
        return month_start(date.fromisoformat(v if len(v) > 7 else f"{v}-01"))
    except ValueError as exc:
        raise ClefsError(400, "month must look like 2026-09", "invalid_month") from exc


class RollupIn(BaseModel):
    months: list[str] | None = Field(default=None, max_length=24)


@router.get("/channel-economics/monthly")
def get_monthly(slug: str, from_: str | None = Query(None, alias="from"), to: str | None = None,
                ctx: HotelCtx = Depends(VIEW)) -> dict:
    """?from=2026-04&to=2026-09 (default: the last 6 months)."""
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, slug)
        this = month_start(htime.local_date(hotel, clock.now()))
        end = _month(to, this)
        start = _month(from_, month_start(end - timedelta(days=150)))
        if start > end or (end.year - start.year) * 12 + end.month - start.month > 36:
            raise ClefsError(400, "from must be before to, at most 36 months apart", "invalid_range")
        return read_months(cur, slug, start, end)


@router.post("/channel-economics/rollup")
def post_rollup(slug: str, body: RollupIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Recompute months now (default: last and this month)."""
    with db.db() as cur:
        hotel = get_hotel(cur, slug)
        if body.months:
            today = htime.local_date(hotel, clock.now())
            return {m: len(rollup_month(cur, slug, _month(m, month_start(today)))) for m in body.months}
        return nightly(cur, hotel)
