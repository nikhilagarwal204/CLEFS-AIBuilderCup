"""
Review of an imported month: statistical inputs, the lines read through the
mapping, the pre-publish checks (04 spec 6.5) and the batch status.

Status: mapping_required while any account lacks a confirmed (locked)
mapping; then checks_failed when a blocking check fails, else review. Only a
review batch can be published (publish.py).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.finance import calc, mapping, ports, summary, usali

# ─── Statistical inputs (fin_stat_inputs) ───

STAT_KEYS = ("available_rn", "rooms_sold", "rooms_rev_pms_minor", "ota_share", "ota_commission_expected_minor")


def compute_stat_inputs(cur: Any, hotel_slug: str, month: date) -> dict:
    """ARN and rooms sold from occupancy_daily; PMS rooms revenue, OTA share
    and expected OTA commission from Profit Guard's channel economics."""
    hotel = get_hotel(cur, hotel_slug)
    start, end = month, calc.month_end(month)
    cur.execute(
        """SELECT COUNT(*) AS days, SUM(rooms_available) AS arn, SUM(rooms_occupied) AS sold
           FROM occupancy_daily WHERE hotel_slug = %s AND date BETWEEN %s AND %s""",
        (hotel_slug, start, end),
    )
    occ = cur.fetchone()
    chans = ports.channel_economics(cur, hotel_slug, start, end)
    # Room nights carry no currency, so the OTA share counts every row.
    rn_all = sum(int(c.get("room_nights") or 0) for c in chans)
    rn_ota = sum(int(c.get("room_nights") or 0) for c in chans if c.get("is_ota"))
    rooms_rev, commission, skipped = _channel_money(cur, chans, hotel.currency, end)
    return {
        "available_rn": int(occ["arn"]) if occ and occ["days"] else None,
        "rooms_sold": int(occ["sold"]) if occ and occ["days"] else None,
        "rooms_rev_pms_minor": rooms_rev,
        "ota_share": calc.ratio(rn_ota, rn_all) if rn_all else None,
        "ota_commission_expected_minor": commission,
        "currency": hotel.currency,
        "fx_skipped": skipped,
    }


def _channel_money(cur: Any, chans: list[dict], hotel_ccy: str, on: date) -> tuple[int | None, int | None, list]:
    """(PMS rooms revenue, expected OTA commission, skipped rows), in the
    hotel's currency. WHY per row: Profit Guard returns one row per
    (channel, currency) in the reservation's own currency, so summing raw
    added USD cents to AED fils. Each row converts at the month end; a row
    with no rate is left out and listed, never summed raw (a partial figure
    the owner is told about beats a silently wrong one). Both totals are None
    when Profit Guard had no row at all, or every row was skipped."""
    from clefs.core import money

    rev = com = 0
    counted = 0
    skipped: list[dict] = []
    for c in chans:
        src = str(c.get("currency") or hotel_ccy).strip().upper()
        r_minor = int(c.get("room_rev_fils") or 0)
        c_minor = int(c.get("commission_fils") or 0) if c.get("is_ota") else 0   # expected OTA commission only
        if src != hotel_ccy:
            try:
                r_minor = int(money.convert_minor(cur, r_minor, src, hotel_ccy, on)) if r_minor else 0
                c_minor = int(money.convert_minor(cur, c_minor, src, hotel_ccy, on)) if c_minor else 0
            except Exception:  # noqa: BLE001  (fx_unavailable: no rate reaches the hotel's currency)
                skipped.append({"channel_code": c.get("channel_code"), "currency": src,
                                "room_rev_minor": int(c.get("room_rev_fils") or 0),
                                "commission_minor": int(c.get("commission_fils") or 0) if c.get("is_ota") else 0})
                continue
        rev += r_minor
        com += c_minor
        counted += 1
    if not counted:
        return None, None, skipped
    return rev, com, skipped


def store_stat_inputs(cur: Any, hotel_slug: str, month: date) -> dict:
    v = compute_stat_inputs(cur, hotel_slug, month)
    cur.execute(
        """INSERT INTO fin_stat_inputs (hotel_slug, period_month, available_rn, rooms_sold, rooms_rev_pms_minor,
               ota_share, ota_commission_expected_minor, currency, fx_skipped, computed_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (hotel_slug, period_month) DO UPDATE SET available_rn = EXCLUDED.available_rn,
               rooms_sold = EXCLUDED.rooms_sold, rooms_rev_pms_minor = EXCLUDED.rooms_rev_pms_minor,
               ota_share = EXCLUDED.ota_share, ota_commission_expected_minor = EXCLUDED.ota_commission_expected_minor,
               currency = EXCLUDED.currency, fx_skipped = EXCLUDED.fx_skipped, computed_at = now()""",
        (hotel_slug, month, *[v[k] for k in STAT_KEYS], v["currency"], Json(v["fx_skipped"])),
    )
    return stat_inputs(cur, hotel_slug, month)


def set_overrides(cur: Any, hotel_slug: str, month: date, overrides: dict, currency: str) -> None:
    """Demo data or a manual fix: these values win over the computed ones."""
    clean = {k: overrides[k] for k in STAT_KEYS if k in overrides}
    cur.execute(
        """INSERT INTO fin_stat_inputs (hotel_slug, period_month, currency, overrides) VALUES (%s, %s, %s, %s)
           ON CONFLICT (hotel_slug, period_month) DO UPDATE
             SET overrides = COALESCE(fin_stat_inputs.overrides, '{}'::jsonb) || EXCLUDED.overrides""",
        (hotel_slug, month, currency, Json({k: (str(v) if isinstance(v, Decimal) else v) for k, v in clean.items()})),
    )


def stat_inputs(cur: Any, hotel_slug: str, month: date) -> dict:
    """The month's inputs: the stored row with its overrides applied, else
    a live computation (not stored). `source` says which."""
    cur.execute("SELECT * FROM fin_stat_inputs WHERE hotel_slug = %s AND period_month = %s", (hotel_slug, month))
    row = cur.fetchone()
    if row is None:
        return {**compute_stat_inputs(cur, hotel_slug, month), "source": "live", "overridden": []}
    base = {k: row[k] for k in STAT_KEYS}
    skipped = row.get("fx_skipped") or []
    if row["computed_at"] is None:
        live = compute_stat_inputs(cur, hotel_slug, month)
        base = {k: live[k] for k in STAT_KEYS}
        skipped = live["fx_skipped"]
    ov = row["overrides"] or {}
    for k, v in ov.items():
        if k in STAT_KEYS and v is not None:
            base[k] = Decimal(str(v)) if k == "ota_share" else int(v)
    if base.get("ota_share") is not None:
        base["ota_share"] = Decimal(str(base["ota_share"]))
    return {**base, "currency": row["currency"].strip(), "source": "stored", "overridden": sorted(ov),
            "fx_skipped": skipped}


# ─── Lines read through the mapping ───


def lines_for_batch(cur: Any, batch: dict, *, snapshot: dict | None = None) -> list[dict]:
    """One row per account: {account_id, code, name_raw, parent_group_raw,
    type_raw, amount_minor, usali_line, locked, ai_confidence, version,
    override_reason, learned_from}. `snapshot` ({account_id: version}, a
    published batch's map_versions) reads the mapping as it was published."""
    cur.execute(
        """SELECT l.account_id, a.code, a.name_raw, a.name_norm, a.parent_group_raw, a.type_raw, a.source_system,
                  SUM(l.amount_minor) AS amount_minor
           FROM fin_pl_lines l JOIN fin_coa_accounts a ON a.id = l.account_id
           WHERE l.batch_id = %s
           GROUP BY l.account_id, a.code, a.name_raw, a.name_norm, a.parent_group_raw, a.type_raw, a.source_system
           ORDER BY l.account_id""",
        (batch["id"],),
    )
    rows = [dict(r, amount_minor=int(r["amount_minor"])) for r in cur.fetchall()]
    ids = [r["account_id"] for r in rows]
    if snapshot:
        cur.execute("""SELECT * FROM fin_usali_map WHERE account_id = ANY(%s)""", (ids,))
        maps = {m["account_id"]: m for m in cur.fetchall() if snapshot.get(str(m["account_id"])) == m["version"]}
    else:
        cur.execute("SELECT * FROM fin_usali_map WHERE account_id = ANY(%s) AND superseded_at IS NULL", (ids,))
        maps = {m["account_id"]: m for m in cur.fetchall()}
    for r in rows:
        m = maps.get(r["account_id"])
        r.update({
            "usali_line": m["usali_line"] if m else None, "locked": bool(m and m["locked"]),
            "ai_confidence": m["ai_confidence"] if m else None, "version": m["version"] if m else None,
            "override_reason": m["override_reason"] if m else None,
            "learned_from": m["learned_from"] if m else None,
        })
    return rows


def confirmed_totals(lines: list[dict]) -> dict[str, int]:
    return summary.totals_by_line([ln for ln in lines if ln["locked"]])


def tolerance_minor(amount: int, currency: str) -> int:
    """max(one major unit, 0.01% of the amount): AED 1 = 100 fils."""
    from clefs.core import money

    return max(money.to_minor(Decimal(1), currency), abs(int(amount)) // 10_000)


def _item(key: str, label: str, status: str, blocking: bool, detail: str, **data: Any) -> dict:
    return {"key": key, "label": label, "status": status, "blocking": blocking and status == "fail",
            "detail": detail, "data": data}


# ─── Checks before publish (04 spec 6.5) ───


def run_checks(cur: Any, hotel_slug: str, batch: dict, lines: list[dict] | None = None) -> dict:
    """{items: [...], blocking, booked_net, mapped_net_profit_minor,
    source_net_profit_minor, reconciling_minor (excluded.* added back on a
    P&L, 0 on a TB), unmapped: [account ids]}."""
    from clefs.modules.finance import anomalies

    ccy = batch["currency"].strip()
    month = batch["period_month"]
    lines = lines if lines is not None else lines_for_batch(cur, batch)
    unmapped = [ln for ln in lines if not ln["locked"]]
    by_line = confirmed_totals(lines)
    stats = stat_inputs(cur, hotel_slug, month)
    s = summary.compute(by_line, arn=stats.get("available_rn"))
    items: list[dict] = []
    is_tb = batch["statement_kind"] == "trial_balance"

    # 1. Reconciliation: every source line lands either in the mapped net
    # profit or in excluded.* (pass-through taxes, balance sheet), so the check
    # is mapped + excluded == source. WHY the excluded sum is a reconciling
    # item on a P&L: the printed Net Profit (or the sum of all lines) carries
    # pass-through lines such as Tourism Dirham collected and remitted, which
    # rarely net to zero inside a month (remittance timing), while
    # summary.compute leaves excluded.* out of net profit. Comparing the two
    # raw would block every such month once check 4 has forced those accounts
    # to excluded.*. A TB's source side already drops excluded.* lines (its
    # full sum is ~0 by construction), so nothing is added back there.
    if is_tb:
        source_np = sum(ln["amount_minor"] for ln in lines
                        if not (ln["usali_line"] or "").startswith("excluded."))
        reconciling = 0
    else:
        source_np = (int(batch["source_net_profit_minor"]) if batch.get("source_net_profit_minor") is not None
                     else sum(ln["amount_minor"] for ln in lines))
        reconciling = s["excluded_minor"]
    mapped_np = s["net_profit_minor"]
    tol = tolerance_minor(source_np, ccy)
    diff = mapped_np + reconciling - source_np
    if unmapped:
        items.append(_item("reconciliation", "Reconciles to the source net profit", "fail", True,
                           f"{len(unmapped)} account(s) have no confirmed mapping",
                           unmapped=[ln["account_id"] for ln in unmapped], source_minor=source_np,
                           mapped_minor=mapped_np, reconciling_minor=reconciling, diff_minor=diff,
                           tolerance_minor=tol))
    else:
        ok = abs(diff) <= tol
        plus = (f" plus {calc.money_or_none(reconciling, ccy)} excluded (pass-through and balance sheet)"
                if reconciling else "")
        items.append(_item("reconciliation", "Reconciles to the source net profit", "pass" if ok else "fail", True,
                           f"mapped net profit{plus} equals the source" if ok else
                           f"mapped net profit{plus} differs from the source by {calc.money_or_none(diff, ccy)}",
                           source_minor=source_np, mapped_minor=mapped_np, reconciling_minor=reconciling,
                           diff_minor=diff, tolerance_minor=tol, excluded_minor=s["excluded_minor"]))

    # 2. PMS rooms revenue within 2% (warns, never blocks).
    pms = stats.get("rooms_rev_pms_minor")
    pl_rooms = s["rooms_revenue_minor"]
    rooms_diff = calc.ratio(pl_rooms - pms, pms) if pms else None
    fx_skipped = stats.get("fx_skipped") or []
    # Stays in a currency with no rate to the hotel's are left out of the PMS
    # figure (compute_stat_inputs), so the owner reads the gap knowing that.
    fx_note = (f"; {len(fx_skipped)} channel row(s) in {', '.join(sorted({x['currency'] for x in fx_skipped}))} "
               "left out (no exchange rate)") if fx_skipped else ""
    if rooms_diff is None:
        items.append(_item("pms_rooms", "Rooms revenue matches the PMS", "n/a", False,
                           f"no PMS rooms revenue{fx_note}", fx_skipped=fx_skipped))
    else:
        ok = abs(rooms_diff) <= Decimal("0.02")
        items.append(_item("pms_rooms", "Rooms revenue matches the PMS", "pass" if ok else "warn", False,
                           f"P&L rooms revenue is {calc.pct_str(rooms_diff)}% from the PMS{fx_note}",
                           pl_minor=pl_rooms, pms_minor=pms, diff_pct=str(rooms_diff), fx_skipped=fx_skipped))

    # 3. OTA booked net: rooms revenue recorded net of commission.
    booked = booked_net(s, by_line, stats)
    items.append(_item("booked_net", "OTA revenue booked gross", "warn" if booked["flag"] else
                       ("pass" if booked["evaluated"] else "n/a"), False, booked["detail"], **booked["data"]))

    # 4. Pass-through taxes excluded; revenue booked gross of taxes.
    taxes = [ln for ln in lines if mapping.rule_for(ln["name_raw"], None) in usali.PASS_THROUGH_KIND
             and ln["usali_line"] and not ln["usali_line"].startswith("excluded.")]
    gross_ratio = calc.ratio(pl_rooms, pms) if pms else None
    gross = gross_ratio is not None and Decimal("1.20") <= gross_ratio <= Decimal("1.27")
    items.append(_item("pass_through", "Pass-through taxes excluded", "fail" if taxes else ("warn" if gross else "pass"),
                       True, f"{len(taxes)} tax account(s) are mapped inside the P&L" if taxes else
                       ("rooms revenue looks booked gross of taxes" if gross else "taxes are excluded"),
                       accounts=[ln["account_id"] for ln in taxes], gross_ratio=calc.dec_str(gross_ratio)))

    # 5. Below GOP: lexicon-matched lines above GOP need an override reason.
    above = [ln for ln in lines if ln["usali_line"] and usali.above_gop(ln["usali_line"])
             and mapping.below_gop_lexicon(ln["name_raw"]) and not ln["override_reason"]]
    items.append(_item("below_gop", "Depreciation, interest, rent and drawings below GOP",
                       "fail" if above else "pass", True,
                       f"{len(above)} account(s) sit above GOP without a reason" if above else "below GOP",
                       accounts=[ln["account_id"] for ln in above]))

    # 6. Trial balance: debits equal credits.
    if is_tb:
        tb = (batch.get("connector_meta") or {}).get("tb") or {}
        dr, cr = int(tb.get("debit_minor") or 0), int(tb.get("credit_minor") or 0)
        ok = abs(dr - cr) <= tolerance_minor(max(dr, cr), ccy)
        items.append(_item("trial_balance", "Debits equal credits", "pass" if ok else "fail", True,
                           "the trial balance balances" if ok else "debits and credits differ",
                           debit_minor=dr, credit_minor=cr))

    # 7. Ratio bands and z-scores against the trailing months (warn only).
    ratio_found = anomalies.ratio_findings(cur, hotel_slug, month, s)
    items.append(_item("ratio_bands", "Labour and energy ratios", "warn" if ratio_found else "pass", False,
                       "; ".join(f["detail"] for f in ratio_found) or "within bands",
                       findings=[{k: v for k, v in f.items() if k != "evidence"} for f in ratio_found],
                       labour_pct=calc.dec_str(s["labour_pct"]), eww_pct=calc.dec_str(s["eww_pct"])))

    # 8. Revenue sign sanity.
    neg = [k for k in ("rooms.revenue", "fnb.revenue", "ood.revenue", "misc.income") if by_line.get(k, 0) < 0]
    bad_total = bool(by_line) and s["total_revenue_minor"] <= 0
    items.append(_item("revenue_sign", "Revenue is positive", "fail" if bad_total else ("warn" if neg else "pass"),
                       True, "total revenue is not positive: check the section signs" if bad_total else
                       (f"negative revenue on {', '.join(neg)}" if neg else "revenue is positive"), lines=neg))

    return {"items": items, "blocking": any(i["blocking"] for i in items), "booked_net": booked["flag"],
            "mapped_net_profit_minor": mapped_np, "source_net_profit_minor": source_np,
            "reconciling_minor": reconciling,
            "unmapped": [ln["account_id"] for ln in unmapped], "stat_inputs": _json_stats(stats)}


def _json_stats(stats: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in stats.items()}


def booked_net(s: dict, by_line: dict[str, int], stats: dict) -> dict:
    """Check 3. Fires when OTA share >= 30%, the mapped rooms commission is
    under 25% of the expected OTA commission, and PMS - P&L rooms is within
    30% of that expected commission: rooms revenue was booked net."""
    share, expected, pms = stats.get("ota_share"), stats.get("ota_commission_expected_minor"), \
        stats.get("rooms_rev_pms_minor")
    if share is None or not expected or pms is None:
        return {"flag": False, "evaluated": False, "detail": "no OTA statistics for the month", "data": {}}
    commissions = -by_line.get("rooms.other.commissions", 0)
    pl_rooms = s["rooms_revenue_minor"]
    gap = pms - pl_rooms - expected
    flag = Decimal(str(share)) >= Decimal("0.30") and commissions * 4 < expected and abs(gap) * 10 <= 3 * expected
    data = {"ota_share": str(share), "expected_commission_minor": int(expected), "commission_line_minor": commissions,
            "pms_rooms_minor": pms, "pl_rooms_minor": pl_rooms, "rooms_diff_pct": calc.dec_str(calc.ratio(
                pl_rooms - pms, pms)), "normalized_rooms_minor": pl_rooms + int(expected) if flag else None}
    detail = ("rooms revenue looks booked net of OTA commission: shown normalised (GOP unchanged)" if flag
              else "OTA revenue is booked gross")
    return {"flag": flag, "evaluated": True, "detail": detail, "data": data}


# ─── Status ───

OPEN = ("uploaded", "mapping_required", "review", "checks_failed")


def refresh(cur: Any, hotel_slug: str, batch_id: int) -> dict:
    """Recompute checks and status of an open batch (published, superseded
    and failed batches are left alone). Returns the batch row."""
    cur.execute("SELECT * FROM fin_import_batches WHERE hotel_slug = %s AND id = %s FOR UPDATE",
                (hotel_slug, batch_id))
    batch = cur.fetchone()
    if batch is None or batch["status"] not in OPEN:
        return dict(batch) if batch else {}
    lines = lines_for_batch(cur, batch)
    checks = run_checks(cur, hotel_slug, batch, lines)
    status = "mapping_required" if checks["unmapped"] else ("checks_failed" if checks["blocking"] else "review")
    cur.execute(
        """UPDATE fin_import_batches SET status = %s, checks = %s, mapped_net_profit_minor = %s
           WHERE id = %s RETURNING *""",
        (status, calc.jsonb(checks), checks["mapped_net_profit_minor"], batch_id),
    )
    row = dict(cur.fetchone())
    from clefs.modules.finance import signals as fin_signals

    fin_signals.mapping_needed(cur, hotel_slug, row, len(checks["unmapped"]))
    return row


def refresh_open(cur: Any, hotel_slug: str) -> int:
    """Refresh every open batch of the hotel (after a mapping confirmation)."""
    cur.execute("SELECT id FROM fin_import_batches WHERE hotel_slug = %s AND status = ANY(%s) ORDER BY id",
                (hotel_slug, list(OPEN)))
    ids = [r["id"] for r in cur.fetchall()]
    for i in ids:
        refresh(cur, hotel_slug, i)
    return len(ids)
