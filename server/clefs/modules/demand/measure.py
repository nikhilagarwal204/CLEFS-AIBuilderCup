"""
Measurement, APPLIED verification and the weekly top 5 (02 spec 4.3).

- APPLIED: an approved or adjusted recommendation is applied when a later
  BAR (a manual entry, a CSV import, or rate evidence) sits within +/- 2% of
  the decided rate. The dr.rate_change verifier and the daily check both use
  applied_match(); a match marks the action done and verified with
  attribution "observed" (never counted as verified profit) and raises
  dr.rec.applied_verified.
- Outcomes (dr.measure_outcomes, the day after the stay): pickup after the
  decision against the pickup comparable nights (same weekday, trailing 8
  weeks, excluded days skipped) showed at the same lead time; the night's
  ADR, occupancy and RevPAR; net revenue from Profit Guard's channel
  economics. Every outcome is labelled "observed, not causal".
- Actionability comes first in the report: share decided within 48 h,
  share applied, median time to decision.
- Top 5: open recommendations ranked by impact x confidence, at most 2 per
  driving event.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.actions import VerifyResult
from clefs.core.money import fmt_money, money_str
from clefs.core.signals import Evidence, RecommendedAction, resolve_missing
from clefs.core.time import local_date
from clefs.db import Json
from clefs.modules.demand import bar as bar_mod
from clefs.modules.demand import recommend as rec_mod
from clefs.modules.demand import relevance as rel
from clefs.modules.demand import signals as sig

APPLIED_TOLERANCE = Decimal("0.02")
OUTCOME_LABEL = "observed, not causal"
BASELINE_WEEKS = 8
TOP_N = 5
TOP_PER_EVENT = 2
TOP_HORIZON_DAYS = 60
DECIDED_FAST_H = 48


# ─── APPLIED verification ───


def applied_match(cur: Any, rec: dict) -> dict | None:
    """The BAR row that shows the decided rate (within +/- 2%), captured
    after the decision; None otherwise."""
    target = rec.get("decided_minor") or rec.get("recommended_minor")
    b = bar_mod.get_bar(cur, rec["hotel_slug"], rec["stay_date"], rec["room_type_code"])
    if not b or not target or b["currency"] != rec["currency"]:
        return None
    if rec.get("decided_at") and datetime.fromisoformat(b["captured_at"]) < rec["decided_at"]:
        return None
    if abs(Decimal(b["bar_minor"]) - Decimal(target)) <= Decimal(target) * APPLIED_TOLERANCE:
        return b
    return None


def _mark_applied(cur: Any, rec: dict, b: dict) -> None:
    now = clock.now()
    clefs_db.execute("UPDATE dr_rate_recommendations SET status = 'APPLIED', applied_at = %s, updated_at = %s "
                     "WHERE id = %s AND status <> 'APPLIED'", (now, now, rec["id"]), cur=cur)
    ccy = rec["currency"]
    sig.raise_signal(
        cur, rec["hotel_slug"], kind=sig.APPLIED_VERIFIED, dedupe_key=str(rec["id"]),
        title=(f"{rec['room_type_code']} BAR for {rec['stay_date']:%a %d %b} is now "
               f"{fmt_money(b['bar_minor'], ccy, minor=True)}"),
        summary="A later BAR matches the approved rate within 2%. Outcomes are observed, not causal.",
        confidence=0.9, subject_type="dr_rate_recommendation", subject_id=str(rec["id"]),
        evidence=[Evidence(kind="row", label="BAR on file",
                           value=f"{fmt_money(b['bar_minor'], ccy, minor=True)} ({b['source']})",
                           ref={"stay_date": rec["stay_date"].isoformat(), "room_type_code": rec["room_type_code"]}),
                  Evidence(kind="metric", label="Approved rate",
                           value=fmt_money(rec.get('decided_minor') or rec['recommended_minor'], ccy, minor=True))])


def verify_rate_action(ctx: Any) -> VerifyResult:
    """dr.rate_change verifier: verified once BAR evidence matches."""
    rid = ctx.params.get("recommendation_id")
    rec_mod.sync_from_actions(ctx.cur, ctx.hotel_slug)  # an adjust is the rate the evidence must match
    rec = clefs_db.fetch_one("SELECT * FROM dr_rate_recommendations WHERE id = %s AND hotel_slug = %s",
                             (rid, ctx.hotel_slug), cur=ctx.cur) if rid else None
    if rec is None:
        return VerifyResult(status="not_verified", attribution="observed")
    if rec["decided_minor"] is None and ctx.params.get("recommended_minor"):
        rec = {**rec, "decided_minor": int(ctx.params["recommended_minor"])}
    b = applied_match(ctx.cur, rec)
    if b is None:
        return VerifyResult(status="pending", attribution="observed")
    _mark_applied(ctx.cur, rec, b)
    return VerifyResult(status="verified", attribution="observed", counts_toward_verified_profit=False,
                        confidence=0.9, evidence=[Evidence(kind="row", label="BAR evidence",
                                                           value=f"{rec['currency']} {b['bar']}")])


def check_applied(cur: Any, hotel: Any, *, today: date) -> dict:
    """Daily: decided recommendations whose BAR now matches become APPLIED;
    a still-open human task is marked done by the system and verified."""
    from clefs.core import actions as core_actions

    rec_mod.sync_from_actions(cur, hotel.slug)
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND status IN ('APPROVED', 'ADJUSTED') "
        "AND stay_date >= %s", (hotel.slug, today - timedelta(days=1)), cur=cur)
    applied = 0
    for r in rows:
        b = applied_match(cur, r)
        if b is None:
            continue
        applied += 1
        if not r["action_id"]:
            _mark_applied(cur, r, b)
            continue
        act = clefs_db.fetch_one("SELECT status FROM actions WHERE id = %s", (r["action_id"],), cur=cur)
        if act and act["status"] in ("approved", "assigned", "in_progress"):
            core_actions.mark_done(cur, r["action_id"], actor=core_actions.SYSTEM,
                                   note="BAR evidence shows the approved rate", hotel_slug=hotel.slug)
        if act and act["status"] in ("approved", "assigned", "in_progress", "done"):
            core_actions.run_verifier(cur, r["action_id"])
        else:
            _mark_applied(cur, r, b)
    return {"applied": applied}


# ─── Outcomes (the day after the stay; observed, not causal) ───


def _otb_at(cur: Any, hotel_slug: str, stay_date: date, on_or_before: date) -> tuple[int | None, date | None]:
    r = clefs_db.fetch_one(
        "SELECT rooms_otb, snapshot_date FROM otb_snapshots WHERE hotel_slug = %s AND stay_date = %s "
        "AND snapshot_date <= %s ORDER BY snapshot_date DESC LIMIT 1", (hotel_slug, stay_date, on_or_before), cur=cur)
    return (r["rooms_otb"], r["snapshot_date"]) if r else (None, None)


def _occ(cur: Any, hotel_slug: str, d: date) -> dict | None:
    return clefs_db.fetch_one("SELECT * FROM occupancy_daily WHERE hotel_slug = %s AND date = %s",
                              (hotel_slug, d), cur=cur)


def baseline_pickup(cur: Any, hotel_slug: str, stay_date: date, lead: int, *, ranges: list) -> float | None:
    """Mean pickup (final rooms sold minus rooms on the books `lead` days
    out) over the same weekday in the BASELINE_WEEKS before the night,
    skipping excluded days and nights that carried a recommendation."""
    vals = []
    for w in range(1, BASELINE_WEEKS + 1):
        d = stay_date - timedelta(weeks=w)
        if rel.is_excluded(d, ranges):
            continue
        if clefs_db.fetch_one("SELECT 1 FROM dr_rate_recommendations WHERE hotel_slug = %s AND stay_date = %s "
                              "AND status IN ('APPROVED', 'ADJUSTED', 'APPLIED') LIMIT 1", (hotel_slug, d), cur=cur):
            continue
        occ = _occ(cur, hotel_slug, d)
        otb, _ = _otb_at(cur, hotel_slug, d, d - timedelta(days=lead))
        if occ and otb is not None:
            vals.append(occ["rooms_occupied"] - otb)
    return round(statistics.mean(vals), 2) if vals else None


def _net_revenue(cur: Any, hotel: Any, d: date) -> dict | None:
    from clefs.modules.demand.actions import safe_call
    from clefs.modules.profit_guard import api as pg_api

    rows = safe_call(cur, pg_api.channel_economics, cur, hotel.slug, start=d, end=d, default=[]) or []
    if not rows:
        return None
    try:
        net = sum(Decimal(str(r.get("net") or 0)) for r in rows)
    except (ArithmeticError, ValueError):
        return None
    return {"net": format(net, "f"), "currency": rows[0].get("currency") or hotel.currency}


def outcome_for(cur: Any, hotel: Any, rec: dict, *, ranges: list) -> dict:
    ccy = rec["currency"]
    decided_on = local_date(hotel, rec["decided_at"]) if rec["decided_at"] else local_date(hotel, rec["created_at"])
    lead = max(0, (rec["stay_date"] - decided_on).days)
    otb, snap = _otb_at(cur, hotel.slug, rec["stay_date"], decided_on)
    occ = _occ(cur, hotel.slug, rec["stay_date"])
    out: dict[str, Any] = {"label": OUTCOME_LABEL, "lead_days": lead, "otb_at_decision": otb,
                           "otb_snapshot_date": snap.isoformat() if snap else None, "currency": ccy}
    if occ:
        sold, avail, revenue = occ["rooms_occupied"], occ["rooms_available"], occ["room_revenue_minor"]
        out.update(rooms_sold=sold, occupancy=round(sold / avail, 4) if avail else None,
                   adr=money_str(revenue // sold, ccy) if sold else None,
                   revpar=money_str(revenue // avail, ccy) if avail else None,
                   pickup_after_decision=(sold - otb) if otb is not None else None)
    base = baseline_pickup(cur, hotel.slug, rec["stay_date"], lead, ranges=ranges)
    out["baseline_pickup"] = base
    if out.get("pickup_after_decision") is not None and base is not None:
        out["pickup_vs_baseline"] = round(out["pickup_after_decision"] - base, 2)
    out["net_revenue"] = _net_revenue(cur, hotel, rec["stay_date"])
    if rec["decided_at"]:
        out["hours_to_decision"] = round((rec["decided_at"] - rec["created_at"]).total_seconds() / 3600, 1)
    return out


def measure_outcomes(cur: Any, hotel: Any, *, today: date) -> dict:
    """dr.measure_outcomes: nights that have passed get their observed
    outcome once. A night that beat its baseline pickup after an approved
    change counts as useful feedback for the driving events' classes."""
    ranges = rel.excluded_ranges(cur, hotel.slug)
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND stay_date < %s AND measured_at IS NULL "
        "AND status IN ('APPROVED', 'ADJUSTED', 'APPLIED', 'REJECTED', 'EXPIRED')", (hotel.slug, today), cur=cur)
    n = 0
    for r in rows:
        out = outcome_for(cur, hotel, r, ranges=ranges)
        clefs_db.execute("UPDATE dr_rate_recommendations SET outcome = %s, measured_at = %s WHERE id = %s",
                         (Json(out), clock.now(), r["id"]), cur=cur)
        if r["status"] in ("APPROVED", "ADJUSTED", "APPLIED") and (out.get("pickup_vs_baseline") or 0) > 0:
            rel.mark_useful(cur, hotel.slug, rel.uuid_list(r["driver_event_ids"]))
        n += 1
    return {"measured": n}


# ─── Actionability and the outcomes report ───


def outcomes_report(cur: Any, hotel_slug: str, *, start: date, end: date) -> dict:
    """Actionability first (decided within 48 h, applied, time to decision),
    then each measured night. Superseded rows are not decisions anyone
    could take, so they are left out of the rates."""
    rec_mod.sync_from_actions(cur, hotel_slug)
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND stay_date BETWEEN %s AND %s "
        "AND status <> 'SUPERSEDED' ORDER BY stay_date, id", (hotel_slug, start, end), cur=cur)
    decided = [r for r in rows if r["decided_at"]]
    fast = [r for r in decided if (r["decided_at"] - r["created_at"]).total_seconds() <= DECIDED_FAST_H * 3600]
    approved = [r for r in rows if r["status"] in ("APPROVED", "ADJUSTED", "APPLIED")]
    applied = [r for r in rows if r["status"] == "APPLIED"]
    hours = [(r["decided_at"] - r["created_at"]).total_seconds() / 3600 for r in decided]

    def pct(a: int, b: int) -> float | None:
        return round(100 * a / b, 1) if b else None

    return {
        "label": "Observed, not causal",
        "actionability": {
            "recommendations": len(rows), "decided": len(decided), "decided_within_48h_pct": pct(len(fast), len(rows)),
            "approved": len(approved), "applied": len(applied), "applied_pct": pct(len(applied), len(approved)),
            "median_hours_to_decision": round(statistics.median(hours), 1) if hours else None,
            "rejected": sum(1 for r in rows if r["status"] == "REJECTED"),
            "expired": sum(1 for r in rows if r["status"] == "EXPIRED"),
        },
        "outcomes": [{**rec_mod.serialize(r), "outcome": r["outcome"]} for r in rows if r["outcome"]],
    }


# ─── Top 5 (weekly) ───


def top5(cur: Any, hotel_slug: str, *, today: date, horizon_days: int = TOP_HORIZON_DAYS) -> list[dict]:
    """Open recommendations ranked by impact x confidence, at most 2 per
    driving event (a night without an event is its own group)."""
    rec_mod.sync_from_actions(cur, hotel_slug)
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND status = 'PROPOSED' "
        "AND stay_date BETWEEN %s AND %s AND impact_minor > 0", (hotel_slug, today, today + timedelta(days=horizon_days)),
        cur=cur)
    ranked = sorted(rows, key=lambda r: (-(r["impact_minor"] * float(r["confidence"])), r["stay_date"], r["id"]))
    per_event: dict[str, int] = {}
    out = []
    for r in ranked:
        drivers = rel.uuid_list(r["driver_event_ids"])
        key = drivers[0] if drivers else f"night:{r['stay_date']}"
        if per_event.get(key, 0) >= TOP_PER_EVENT:
            continue
        per_event[key] = per_event.get(key, 0) + 1
        out.append({**rec_mod.serialize(r), "rank_score": round(r["impact_minor"] * float(r["confidence"])),
                    "group": key})
        if len(out) >= TOP_N:
            break
    return out


def raise_top5(cur: Any, hotel: Any, *, today: date) -> dict:
    """dr.weekly_top5: one dr.opportunity signal per top item (its action
    attached), and the previous week's items that fell out are resolved."""
    items = top5(cur, hotel.slug, today=today)
    seen = []
    for i, it in enumerate(items, 1):
        key = f"rec:{it['id']}"
        seen.append(f"{sig.OPPORTUNITY}:{key}")
        sig.raise_signal(
            cur, hotel.slug, kind=sig.OPPORTUNITY, dedupe_key=key,
            title=f"#{i} {it['room_type_code']} {it['stay_date']}: {it['currency']} {it['current_bar']} to "
                  f"{it['recommended']}",
            summary=f"{it['why']} Estimated opportunity, observed not causal.",
            impact_minor=it["impact_minor"], currency=it["currency"], confidence=it["confidence"],
            evidence=[Evidence(kind="calc", label="Rationale", value=it["rationale"])],
            action=RecommendedAction(type="dr.rate_change", label="Review the rate",
                                     params={"recommendation_id": it["id"]}, owner_role="revenue"),
            subject_type="dr_rate_recommendation", subject_id=str(it["id"]))
    resolved = resolve_missing(cur, hotel.slug, "demand", sig.OPPORTUNITY, seen)
    return {"top": len(items), "resolved": resolved}
