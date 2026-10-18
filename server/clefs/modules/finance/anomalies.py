"""
P&L anomalies (04 spec 6.5 check 7, 6.8): ratio z-scores against the trailing
published months, the assumed bands, and the flags of the other checks.

Primary trigger: |z| >= 2.5 with a move of at least 3 pp against the trailing
6 months. Secondary: labour % outside [20%, 45%] or EWW % outside [3%, 12%]
(configurable, UNVERIFIED defaults). At publish the findings are stored in
fin_anomalies; the top 3 by |delta| x confidence become fin.anomaly signals,
each offering finance.investigate_anomaly.
"""

from __future__ import annotations

import statistics
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.db import Json
from clefs.modules.finance import calc, settings as fin_settings, usali

TOP_N = 3


def _trailing(cur: Any, hotel_slug: str, month: date, n: int) -> list[dict]:
    cur.execute(
        """SELECT period_month, labour_pct, eww_pct, total_revenue_minor, lines FROM fin_monthly_summary
           WHERE hotel_slug = %s AND period_month < %s AND period_month >= %s AND total_revenue_minor > 0
           ORDER BY period_month DESC LIMIT %s""",
        (hotel_slug, month, calc.add_months(month, -n), n),
    )
    out = []
    for r in cur.fetchall():
        r = dict(r)
        lines = {k: int(v) for k, v in (r.get("lines") or {}).items() if k in usali.LINES}
        if lines:
            # Ratios as the books reported them: a booked-net month's headline
            # is normalised, but the history compares like with like.
            from clefs.modules.finance import summary

            s = summary.compute(lines, arn=None)
            if s["total_revenue_minor"] > 0:
                r.update(labour_pct=s["labour_pct"], eww_pct=s["eww_pct"], total_revenue_minor=s["total_revenue_minor"])
        out.append(r)
    return out


def _z(value: Decimal, history: list[Decimal]) -> tuple[Decimal | None, Decimal | None]:
    """(z, mean). z is None with fewer than 3 months or no spread."""
    if len(history) < 3:
        return None, (sum(history) / len(history) if history else None)
    mean = Decimal(str(statistics.fmean(float(h) for h in history)))
    sd = Decimal(str(statistics.stdev(float(h) for h in history)))
    if sd == 0:
        return None, mean
    return ((value - mean) / sd).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), mean


def _finding(kind: str, month: date, *, line: str | None, value: Decimal, expected: Decimal | None,
             z: Decimal | None, tor: int, currency: str, confidence: float, title: str, detail: str) -> dict:
    delta = None if expected is None else int(((value - expected) * tor).quantize(Decimal(1), ROUND_HALF_UP))
    return {"kind": kind, "usali_line": line, "value": value, "expected": expected, "zscore": z,
            "delta_minor": delta, "currency": currency, "confidence": round(confidence, 2), "title": title,
            "detail": detail, "fingerprint": f"{kind}:{month.isoformat()}:{line or '-'}",
            "evidence": {"value_pct": calc.pct_str(value), "expected_pct": calc.pct_str(expected),
                         "zscore": None if z is None else str(z), "month": month.isoformat()}}


def _ratio_check(kind: str, label: str, month: date, value: Decimal | None, history: list[Decimal],
                 band: tuple[float, float], cfg: Any, tor: int, currency: str, line: str | None) -> dict | None:
    if value is None:
        return None
    z, mean = _z(value, history)
    move_pp = None if mean is None else (value - mean) * 100
    if z is not None and abs(z) >= Decimal(str(cfg.zscore_min)) and abs(move_pp) >= Decimal(str(cfg.delta_min_pp)):
        conf = min(0.95, 0.5 + float(abs(z)) / 10)
        return _finding(kind, month, line=line, value=value, expected=mean, z=z, tor=tor, currency=currency,
                        confidence=conf, title=f"{label} {calc.pct_str(value)}% in {month:%B %Y}",
                        detail=(f"{label} {calc.pct_str(value)}% against a trailing mean of {calc.pct_str(mean)}% "
                                f"(z {z})"))
    lo, hi = Decimal(str(band[0])) / 100, Decimal(str(band[1])) / 100
    if not lo <= value <= hi:
        return _finding(kind, month, line=line, value=value, expected=mean if mean is not None else (lo + hi) / 2,
                        z=z, tor=tor, currency=currency, confidence=0.5,
                        title=f"{label} {calc.pct_str(value)}% is outside {band[0]:g}-{band[1]:g}%",
                        detail=f"{label} {calc.pct_str(value)}% is outside the {band[0]:g}-{band[1]:g}% band")
    return None


def ratio_findings(cur: Any, hotel_slug: str, month: date, s: dict) -> list[dict]:
    """Labour %, EWW % and per-line spikes against the trailing months."""
    cfg = fin_settings.load(cur, hotel_slug)
    tor = s["total_revenue_minor"]
    if tor <= 0:
        return []
    from clefs.core.hotels import get_hotel

    ccy = get_hotel(cur, hotel_slug).currency
    hist = _trailing(cur, hotel_slug, month, cfg.trailing_months)
    out = []
    for kind, label, key, band in (("labour_ratio", "Labour", "labour_pct",
                                    (cfg.labour_band_min_pct, cfg.labour_band_max_pct)),
                                   ("eww_ratio", "Energy, water and waste", "eww_pct",
                                    (cfg.eww_band_min_pct, cfg.eww_band_max_pct))):
        f = _ratio_check(kind, label, month, s.get(key), [Decimal(str(h[key])) for h in hist if h.get(key) is not None],
                         band, cfg, tor, ccy, None)
        if f:
            out.append(f)
    # Line spikes: expense lines above GOP (payroll is covered by labour).
    for key, spec in usali.LINES.items():
        if not usali.above_gop(key) or spec.category == "revenue" or spec.is_payroll or spec.dept == "eww":
            continue
        by = s.get("by_line") or {}
        share = calc.ratio(-by.get(key, 0), tor)
        past = [calc.ratio(-int((h.get("lines") or {}).get(key, 0)), int(h["total_revenue_minor"])) for h in hist]
        z, mean = _z(share, [p for p in past if p is not None]) if share is not None else (None, None)
        if z is None or mean is None:
            continue
        if abs(z) >= Decimal(str(cfg.zscore_min)) and abs(share - mean) * 100 >= Decimal(str(cfg.delta_min_pp)):
            out.append(_finding("line_spike", month, line=key, value=share, expected=mean, z=z, tor=tor, currency=ccy,
                                confidence=min(0.9, 0.4 + float(abs(z)) / 10),
                                title=f"{spec.label} {calc.pct_str(share)}% of revenue in {month:%B %Y}",
                                detail=f"{spec.label} is {calc.pct_str(share)}% of revenue against "
                                       f"{calc.pct_str(mean)}% (z {z})"))
    return out


# ─── Scan at publish (finance.anomaly_scan) ───


def _check_findings(month: date, checks: dict, currency: str) -> list[dict]:
    """Anomalies from the other checks' warnings (rooms mismatch, taxes
    booked gross, booked net)."""
    out = []
    items = {i["key"]: i for i in checks.get("items") or []}
    pms = items.get("pms_rooms")
    if pms and pms["status"] == "warn":
        d = pms["data"]
        out.append({"kind": "rooms_rev_mismatch", "usali_line": "rooms.revenue",
                    "value": Decimal(d["diff_pct"]), "expected": Decimal(0), "zscore": None,
                    "delta_minor": int(d["pl_minor"]) - int(d["pms_minor"]), "currency": currency, "confidence": 0.5,
                    "title": f"Rooms revenue is {calc.pct_str(Decimal(d['diff_pct']))}% from the PMS",
                    "detail": pms["detail"], "fingerprint": f"rooms_rev_mismatch:{month.isoformat()}:rooms.revenue",
                    "evidence": d})
    pt = items.get("pass_through")
    if pt and pt["status"] == "warn" and pt["data"].get("gross_ratio"):
        out.append({"kind": "pass_through_in_revenue", "usali_line": "rooms.revenue",
                    "value": Decimal(pt["data"]["gross_ratio"]), "expected": Decimal(1), "zscore": None,
                    "delta_minor": None, "currency": currency, "confidence": 0.7,
                    "title": "Rooms revenue looks booked gross of taxes", "detail": pt["detail"],
                    "fingerprint": f"pass_through_in_revenue:{month.isoformat()}:rooms.revenue",
                    "evidence": pt["data"]})
    bn = items.get("booked_net")
    if checks.get("booked_net") and bn:
        d = bn["data"]
        out.append({"kind": "booked_net", "usali_line": "rooms.other.commissions",
                    "value": Decimal(d.get("rooms_diff_pct") or 0), "expected": Decimal(0), "zscore": None,
                    "delta_minor": int(d.get("expected_commission_minor") or 0), "currency": currency,
                    "confidence": 0.8, "title": "OTA revenue booked net of commission", "detail": bn["detail"],
                    "fingerprint": f"booked_net:{month.isoformat()}:rooms.other.commissions", "evidence": d})
    return out


def _store(cur: Any, hotel_slug: str, month: date, f: dict) -> dict:
    cur.execute(
        """INSERT INTO fin_anomalies (hotel_slug, period_month, fingerprint, kind, usali_line, value, expected,
               delta_minor, currency, zscore, confidence, evidence, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
           ON CONFLICT (hotel_slug, fingerprint) DO UPDATE SET value = EXCLUDED.value, expected = EXCLUDED.expected,
               delta_minor = EXCLUDED.delta_minor, zscore = EXCLUDED.zscore, confidence = EXCLUDED.confidence,
               evidence = EXCLUDED.evidence,
               status = CASE WHEN fin_anomalies.status = 'resolved' THEN 'open' ELSE fin_anomalies.status END
           RETURNING *""",
        (hotel_slug, month, f["fingerprint"], f["kind"], f["usali_line"], f["value"], f["expected"],
         f["delta_minor"], f["currency"], f["zscore"], f["confidence"],
         Json({k: (str(v) if isinstance(v, Decimal) else v) for k, v in (f.get("evidence") or {}).items()})),
    )
    return dict(cur.fetchone())


def scan(cur: Any, hotel_slug: str, month: date, s: dict, checks: dict, currency: str) -> list[dict]:
    """Store the month's findings, retire the ones gone since the last
    publish, and raise signals: the top 3 (by |delta| x confidence, booked
    net aside) as fin.anomaly, booked net as fin.booked_net."""
    from clefs.core import signals as core_signals
    from clefs.modules.finance import signals as fin_signals

    findings = ratio_findings(cur, hotel_slug, month, s) + _check_findings(month, checks, currency)
    stored = [dict(_store(cur, hotel_slug, month, f), title=f["title"], detail=f["detail"]) for f in findings]
    keep = {a["fingerprint"] for a in stored}
    cur.execute("SELECT id, fingerprint FROM fin_anomalies WHERE hotel_slug = %s AND period_month = %s AND status = 'open'",
                (hotel_slug, month))
    for r in cur.fetchall():
        if r["fingerprint"] not in keep:
            cur.execute("UPDATE fin_anomalies SET status = 'resolved' WHERE id = %s", (r["id"],))
            core_signals.resolve_signal(cur, hotel_slug, f"fin.anomaly:{r['fingerprint']}", resolution="gone")
    ranked = sorted((a for a in stored if a["kind"] != "booked_net"),
                    key=lambda a: abs(int(a["delta_minor"] or 0)) * float(a["confidence"] or 0), reverse=True)
    for i, a in enumerate(ranked):
        if i < TOP_N:
            sig = fin_signals.anomaly(cur, hotel_slug, a)
            cur.execute("UPDATE fin_anomalies SET signal_id = %s WHERE id = %s", (sig["id"], a["id"]))
            a["signal_id"] = sig["id"]
        else:
            core_signals.resolve_signal(cur, hotel_slug, f"fin.anomaly:{a['fingerprint']}", resolution="not_top")
    for a in stored:
        if a["kind"] == "booked_net":
            sig = fin_signals.booked_net(cur, hotel_slug, month, a["evidence"] or {}, currency)
            cur.execute("UPDATE fin_anomalies SET signal_id = %s WHERE id = %s", (sig["id"], a["id"]))
    return ranked[:TOP_N]


def list_anomalies(cur: Any, hotel_slug: str, *, month: date | None = None, status: str | None = "open") -> list[dict]:
    cur.execute(
        """SELECT * FROM fin_anomalies WHERE hotel_slug = %s AND (%s::date IS NULL OR period_month = %s)
             AND (%s::text IS NULL OR status = %s)
           ORDER BY period_month DESC, ABS(COALESCE(delta_minor, 0)) * COALESCE(confidence, 0) DESC""",
        (hotel_slug, month, month, status, status),
    )
    return [dict(r) for r in cur.fetchall()]
