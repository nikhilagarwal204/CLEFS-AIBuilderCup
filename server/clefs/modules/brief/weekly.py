"""
Weekly verified-value report (02 spec 6.4): Monday 08:00 hotel-local, for
the previous ISO week (Mon-Sun, hotel timezone).

Contents, deliberately without vanity metrics:
    verified value by action type (caused only; observed outcomes listed apart)
    identified vs recovered (money the detectors found vs money verified)
    decisions made, with the median time to decision
    overdue items now
    top open items

Stored once per (hotel, ISO week) in brf_reports with its HTML and a PDF
(fpdf2, core fonts) kept in clefs.files. The weekly brief links it.
"""

from __future__ import annotations

import html as _html
import statistics
from datetime import date, datetime, timedelta
from typing import Any

from clefs.core import actions as core_actions
from clefs.core import clock, money
from clefs.core import signals as core_signals
from clefs.core import time as htime
from clefs.core.hotels import Hotel, get_hotel

REPORT_RETENTION_DAYS = 400  # a year of weekly reports plus a margin for year-on-year
DECISION_EVENTS = ("approve", "adjust", "ignore", "dispute", "assign")


# ─── Periods ───


def week_bounds(iso_week: str) -> tuple[date, date]:
    """"2026-W40" -> (Monday, Sunday). 400 invalid_week for anything else."""
    from clefs.core.errors import ClefsError

    try:
        y, w = iso_week.upper().split("-W")
        monday = date.fromisocalendar(int(y), int(w), 1)
    except (ValueError, AttributeError):
        raise ClefsError(400, "Use an ISO week like 2026-W40", "invalid_week") from None
    return monday, monday + timedelta(days=6)


def iso_week_of(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def previous_week(day: date) -> str:
    return iso_week_of(day - timedelta(days=7))


# ─── Sections ───


def _by_type(cur: Any, hotel: Hotel, t0: datetime, t1: datetime) -> list[dict]:
    cur.execute(
        """
        SELECT type, attribution, counts_toward_verified_profit AS counts,
               COUNT(*) AS n, COALESCE(SUM(outcome_minor), 0) AS minor
        FROM actions
        WHERE hotel_slug = %s AND status = 'verified' AND currency = %s AND verified_at >= %s AND verified_at < %s
        GROUP BY type, attribution, counts_toward_verified_profit ORDER BY type
        """,
        (hotel.slug, hotel.currency, t0, t1),
    )
    out: dict[str, dict] = {}
    for r in cur.fetchall():
        t = core_actions.get_action_type(r["type"])
        row = out.setdefault(r["type"], {"type": r["type"], "label": t.label if t else r["type"], "actions": 0,
                                         "verified_minor": 0, "observed_minor": 0})
        row["actions"] += int(r["n"])
        row["verified_minor" if r["counts"] else "observed_minor"] += int(r["minor"])
    rows = sorted(out.values(), key=lambda x: (-x["verified_minor"], x["type"]))
    for row in rows:
        row["verified_value"] = money.money_str(row["verified_minor"], hotel.currency)
        row["observed_value"] = money.money_str(row["observed_minor"], hotel.currency)
    return rows


def _identified(cur: Any, hotel: Hotel, t0: datetime, t1: datetime) -> dict:
    """Money the detectors first saw this week (money_lost + money_available)."""
    cur.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(impact_minor), 0) AS minor FROM signals
        WHERE hotel_slug = %s AND currency = %s AND section IN ('money_lost', 'money_available')
          AND first_seen_at >= %s AND first_seen_at < %s
        """,
        (hotel.slug, hotel.currency, t0, t1),
    )
    r = cur.fetchone()
    return {"signals": int(r["n"]), "minor": int(r["minor"])}


def _decisions(cur: Any, hotel: Hotel, t0: datetime, t1: datetime) -> dict:
    """Human decisions in the week and the median hours from an action's
    creation to its decision (dial approvals are not decisions)."""
    cur.execute(
        """
        SELECT e.event, e.at, a.created_at FROM action_events e JOIN actions a ON a.id = e.action_id
        WHERE a.hotel_slug = %s AND e.actor_type = 'user' AND e.event = ANY(%s) AND e.at >= %s AND e.at < %s
        """,
        (hotel.slug, list(DECISION_EVENTS), t0, t1),
    )
    rows = cur.fetchall()
    hours = [max(0.0, (r["at"] - r["created_at"]).total_seconds() / 3600) for r in rows]
    by_event: dict[str, int] = {}
    for r in rows:
        by_event[r["event"]] = by_event.get(r["event"], 0) + 1
    approved = by_event.get("approve", 0) + by_event.get("adjust", 0)
    judged = approved + by_event.get("ignore", 0)
    return {
        "count": len(rows),
        "by_event": by_event,
        "median_hours_to_decision": round(statistics.median(hours), 1) if hours else None,
        "approval_rate": round(approved / judged, 4) if judged else None,
    }


def _overdue(cur: Any, hotel: Hotel, now: datetime) -> list[dict]:
    rows = core_actions.list_actions(cur, hotel.slug, overdue=True, limit=20)
    return [{"action_id": a["id"], "title": a["title"], "status": a["status"],
             "due_at": a["due_at"].isoformat() if a.get("due_at") else None,
             "escalation_level": a.get("escalation_level", 0)} for a in rows]


def _top_open(cur: Any, hotel: Hotel) -> list[dict]:
    sigs = core_signals.list_signals(cur, hotel.slug, sections=["money_lost", "money_available", "service_risk", "reviews"],
                                     limit=100)
    sigs.sort(key=lambda s: (-(s.get("impact_minor") or 0), s["id"]))
    return [{"signal_id": s["id"], "title": s["title"], "section": s["section"], "impact_value": s.get("impact_amount"),
             "currency": s.get("currency")} for s in sigs[:5]]


# ─── Build and store ───


def build(cur: Any, hotel: Hotel, iso_week: str, *, now: datetime | None = None) -> dict:
    """The report payload for one ISO week (hotel-local days). Pure read."""
    now = now or clock.now()
    start, end = week_bounds(iso_week)
    t0, t1 = htime.local_range(hotel, start, end)
    by_type = _by_type(cur, hotel, t0, t1)
    verified = sum(r["verified_minor"] for r in by_type)
    ident = _identified(cur, hotel, t0, t1)
    ccy = hotel.currency
    return {
        "hotel_slug": hotel.slug,
        "hotel_name": hotel.name,
        "period_key": iso_week,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "currency": ccy,
        "verified_minor": verified,
        "verified_value": money.money_str(verified, ccy),
        "by_type": by_type,
        "identified_minor": ident["minor"],
        "identified_value": money.money_str(ident["minor"], ccy),
        "identified_signals": ident["signals"],
        "recovered_minor": verified,
        "recovered_value": money.money_str(verified, ccy),
        "decisions": _decisions(cur, hotel, t0, t1),
        "overdue": _overdue(cur, hotel, now),
        "top_open": _top_open(cur, hotel),
        "generated_at": now.isoformat(),
    }


def ensure_report(cur: Any, hotel: Hotel | str, *, day: date | None = None, iso_week: str | None = None,
                  now: datetime | None = None, rebuild: bool = False) -> dict:
    """The stored report for `iso_week` (default: the week before `day`),
    building HTML and PDF on first use. `rebuild` refreshes it in place."""
    from clefs import files
    from clefs.db import Json

    if isinstance(hotel, str):
        hotel = get_hotel(cur, hotel)
    now = now or clock.now()
    week = iso_week or previous_week(day or htime.local_date(hotel, now))
    if not rebuild:
        existing = get_report(cur, hotel.slug, week)
        if existing:
            return existing
    payload = build(cur, hotel, week, now=now)
    page = render_html(payload, locale=hotel.locale)
    ref = files.put_file(
        cur, hotel.slug, render_pdf(payload, locale=hotel.locale), filename=f"clefs-weekly-{week}.pdf",
        content_type="application/pdf", purpose="brief.weekly_report", source="generated",
        retention_days=REPORT_RETENTION_DAYS,
    )
    cur.execute(
        """
        INSERT INTO brf_reports (hotel_slug, kind, period_key, period_start, period_end, currency, payload, html,
                                 pdf_file_id, generated_at)
        VALUES (%s, 'weekly', %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, kind, period_key) DO UPDATE SET
            payload = EXCLUDED.payload, html = EXCLUDED.html, pdf_file_id = EXCLUDED.pdf_file_id,
            generated_at = EXCLUDED.generated_at
        """,
        (hotel.slug, week, payload["period_start"], payload["period_end"], hotel.currency, Json(payload), page,
         ref.id, now),
    )
    return get_report(cur, hotel.slug, week)


def get_report(cur: Any, hotel_slug: str, iso_week: str) -> dict | None:
    cur.execute(
        """
        SELECT id, period_key, period_start, period_end, currency, payload, html, pdf_file_id, generated_at
        FROM brf_reports WHERE hotel_slug = %s AND kind = 'weekly' AND period_key = %s
        """,
        (hotel_slug, iso_week),
    )
    row = cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["hotel_slug"] = hotel_slug
    out["period_start"] = out["period_start"].isoformat()
    out["period_end"] = out["period_end"].isoformat()
    out["pdf_file_id"] = str(out["pdf_file_id"]) if out.get("pdf_file_id") else None
    return out


def summary(report: dict) -> dict:
    """The weekly brief's one-line pointer to the report."""
    p = report.get("payload") or {}
    ccy = p.get("currency") or report.get("currency")
    verified = money.fmt_money(p.get("verified_minor") or 0, ccy, minor=True) if ccy else "0"
    ident = money.fmt_money(p.get("identified_minor") or 0, ccy, minor=True) if ccy else "0"
    return {
        "period_key": report["period_key"],
        "report_id": report["id"],
        "verified_minor": p.get("verified_minor") or 0,
        "identified_minor": p.get("identified_minor") or 0,
        "pdf_file_id": report.get("pdf_file_id"),
        "path": f"/app/{report['hotel_slug']}/brief/reports/weekly/{report['period_key']}",
        "text": f"Week {report['period_key']}: {verified} verified of {ident} identified.",
    }


# ─── Render ───


def _m(minor: int | None, ccy: str, locale: str | None) -> str:
    return money.fmt_money(minor or 0, ccy, locale, minor=True)


def _lines(p: dict, locale: str | None) -> list[tuple[str, list[str]]]:
    """(heading, lines) shared by the HTML and the PDF, so both say the same."""
    ccy = p["currency"]
    d = p["decisions"]
    rate = f"{round(d['approval_rate'] * 100)}% approved" if d.get("approval_rate") is not None else "no approvals"
    median = f"median {d['median_hours_to_decision']} h to decide" if d.get("median_hours_to_decision") is not None else "no decisions"
    out: list[tuple[str, list[str]]] = [
        ("Verified value", [f"{_m(p['verified_minor'], ccy, locale)} verified this week"] + [
            f"{r['label']}: {_m(r['verified_minor'], ccy, locale)} ({r['actions']} action{'' if r['actions'] == 1 else 's'})"
            + (f", {_m(r['observed_minor'], ccy, locale)} observed, not counted" if r["observed_minor"] else "")
            for r in p["by_type"]
        ]),
        ("Identified vs recovered", [
            f"Identified: {_m(p['identified_minor'], ccy, locale)} across {p['identified_signals']} findings",
            f"Recovered (verified): {_m(p['recovered_minor'], ccy, locale)}",
        ]),
        ("Decisions", [f"{d['count']} decisions, {rate}, {median}"]),
        ("Overdue now", [f"{o['title']} ({o['status'].replace('_', ' ')})" for o in p["overdue"]] or ["Nothing overdue"]),
        ("Top open items", [
            f"{t['title']}" + (f": {t['currency']} {t['impact_value']}" if t.get("impact_value") else "")
            for t in p["top_open"]
        ] or ["Nothing open"]),
    ]
    return out


def render_html(p: dict, *, locale: str | None = "en-AE") -> str:
    e = _html.escape
    parts = [
        '<!doctype html><html><body style="margin:0;background:#f9fafb">'
        '<div style="max-width:640px;margin:0 auto;padding:24px 16px;font-family:-apple-system,Segoe UI,Roboto,sans-serif">'
        f'<div style="font-size:13px;color:#6b7280">{e(p["hotel_name"])} · Weekly verified-value report</div>'
        f'<h1 style="font-size:20px;color:#111827;margin:8px 0">{e(p["period_key"])} '
        f'({e(p["period_start"])} to {e(p["period_end"])})</h1>'
    ]
    for heading, lines in _lines(p, locale):
        parts.append(f'<h2 style="font-size:15px;color:#111827;margin:20px 0 6px">{e(heading)}</h2><ul style="margin:0;padding-left:18px">')
        parts += [f'<li style="font-size:14px;color:#374151">{e(x)}</li>' for x in lines]
        parts.append("</ul>")
    parts.append('<p style="font-size:12px;color:#9ca3af">Verified value counts only outcomes Clefs caused and '
                 "could check against a statement, payment or record. Observed outcomes are listed, never added.</p>")
    parts.append("</div></body></html>")
    return "".join(parts)


def _latin1(s: str) -> str:
    """fpdf2's core fonts are Latin-1: anything else becomes '?', never a crash."""
    return s.encode("latin-1", "replace").decode("latin-1")


def render_pdf(p: dict, *, locale: str | None = "en-AE") -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _latin1(f"{p['hotel_name']}: weekly verified-value report"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, _latin1(f"{p['period_key']} ({p['period_start']} to {p['period_end']})"), new_x="LMARGIN", new_y="NEXT")
    for heading, lines in _lines(p, locale):
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _latin1(heading), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for x in lines:
            pdf.multi_cell(0, 6, _latin1(f"- {x}"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


# ─── Job ───
# The weekly brief (brief.weekly, Monday 08:00) builds the report through
# brief.generate; this job only backfills a report for a hotel whose weekly
# brief is switched off, so the console and Trust's pilot report still have it.

from clefs import jobs  # noqa: E402


@jobs.scheduled("brief.weekly_report", jobs.weekly_at(1, "08:30"))
def _weekly_report_job(ctx: jobs.JobCtx) -> dict:
    rep = ensure_report(ctx.cur, ctx.hotel, day=htime.local_date(ctx.hotel, ctx.now), now=ctx.now)
    return {"period_key": rep["period_key"], "report_id": rep["id"]}
