"""
API views of published months (api.monthly_summary, the console one-pager,
the trend). Every figure comes from fin_monthly_summary, i.e. confirmed,
published data. Money: *_minor ints next to Decimal strings; ratios as
Decimal strings ("0.3283") next to percent strings ("32.83").
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clefs.modules.finance import anomalies, calc, usali

MONEY = ("total_revenue_minor", "rooms_revenue_minor", "dept_profit_minor", "undistributed_minor", "gop_minor",
         "goppar_minor", "trevpar_minor", "revpar_minor", "payroll_minor", "ebitda_minor", "net_profit_minor",
         "verified_profit_minor", "verified_profit_par_minor")
RATIOS = (("gop_margin", "gop_margin_pct"), ("labour_pct", "labour_pct"), ("flow_through", "flow_through_pct"),
          ("eww_pct", "eww_pct"))


def summary_row_view(r: dict) -> dict:
    ccy = r["currency"].strip()
    out: dict[str, Any] = {"month": r["period_month"].isoformat(), "currency": ccy, "batch_id": r.get("batch_id"),
                           "normalized": bool(r.get("normalized")), "booked_net": bool(r.get("booked_net")),
                           "available_rn": r.get("arn"), "flow_through_basis": r.get("flow_through_basis"),
                           "published_at": r["published_at"].isoformat() if r.get("published_at") else None}
    for k in MONEY:
        out[k] = r.get(k)
        out[k.removesuffix("_minor")] = calc.money_or_none(r.get(k), ccy)
    for col, name in RATIOS:
        out[f"{col}_ratio" if col == name else col] = calc.dec_str(r.get(col))
        out[name] = calc.pct_str(r.get(col))
    return out


def usali_table(lines: dict | None, currency: str) -> list[dict]:
    """The USALI summary table: one row per line with a value, in schedule
    order, revenue as income and every other line as a cost."""
    out = []
    for key, spec in usali.LINES.items():
        v = (lines or {}).get(key)
        if v in (None, 0):
            continue
        amount = int(v) if spec.category == "revenue" else -int(v)
        out.append({"usali_line": key, "label": spec.label, "schedule": spec.schedule, "dept": spec.dept,
                    "category": spec.category, "amount_minor": amount, "amount": calc.money_or_none(amount, currency)})
    return out


def monthly(cur: Any, hotel_slug: str, month: date, *, detail: bool = False) -> dict | None:
    cur.execute("SELECT * FROM fin_monthly_summary WHERE hotel_slug = %s AND period_month = %s AND published_at IS NOT NULL",
                (hotel_slug, month))
    r = cur.fetchone()
    if r is None:
        return None
    out = summary_row_view(r)
    out["market_index"] = r.get("market_index") or {"available": False}
    top = anomalies.list_anomalies(cur, hotel_slug, month=month, status=None)
    out["anomalies"] = [anomaly_view(a) for a in top
                        if a["kind"] != "booked_net" and a["status"] != "resolved"][:anomalies.TOP_N]
    if detail:
        out["usali"] = usali_table(r.get("lines"), out["currency"])
        cur.execute("SELECT checks, source, statement_kind FROM fin_import_batches WHERE id = %s", (r["batch_id"],))
        b = cur.fetchone() or {}
        out["checks"] = (b.get("checks") or {}).get("items") or []
        out["source"] = b.get("source")
        out["statement_kind"] = b.get("statement_kind")
    return out


def anomaly_view(a: dict) -> dict:
    ccy = a["currency"].strip()
    return {"id": a["id"], "month": a["period_month"].isoformat(), "kind": a["kind"], "usali_line": a["usali_line"],
            "value": calc.dec_str(a["value"]), "expected": calc.dec_str(a["expected"]),
            "delta_minor": a["delta_minor"], "delta": calc.money_or_none(a["delta_minor"], ccy), "currency": ccy,
            "zscore": calc.dec_str(a["zscore"]), "confidence": calc.dec_str(a["confidence"]),
            "evidence": a["evidence"] or {}, "status": a["status"], "signal_id": a["signal_id"],
            "action_id": a["action_id"]}


def trend(cur: Any, hotel_slug: str, *, months: int, end: date | None = None) -> list[dict]:
    cur.execute(
        """SELECT * FROM fin_monthly_summary WHERE hotel_slug = %s AND published_at IS NOT NULL
             AND (%s::date IS NULL OR period_month <= %s) ORDER BY period_month DESC LIMIT %s""",
        (hotel_slug, end, end, months),
    )
    return [summary_row_view(r) for r in reversed(cur.fetchall())]
