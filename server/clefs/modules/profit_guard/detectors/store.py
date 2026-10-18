"""Persist a run's drafts: supersede overlapping explanations, score the
confidence, upsert by fingerprint (a re-run updates amount, trace and
last_run_id, never the status a person set), decide which findings may be
signalled, and close what a full scan no longer sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from clefs.modules.profit_guard import confidence as conf
from clefs.modules.profit_guard import repo
from clefs.modules.profit_guard.detectors.base import Ctx, FindingDraft, step

# One booking, one explanation. L3 claims the whole commission back, so a
# partial claim on the same booking (fees in the base, the percent, the
# unstayed nights, the base gap) would count money twice; L5 sees the gap
# that L1 (fees in the base) or L4 (early departure) already name.
SUPERSEDED_BY = {"L1": ("L3",), "L2": ("L3",), "L4": ("L3",), "L5": ("L1", "L3", "L4")}
# Detectors whose finding means "still true right now": a full scan that
# no longer sees it closes it (card charged, deadline handed to L3, parity
# restored). ALREADY_HANDLED never counts against the detector.
AUTO_CLOSE = ("L7", "L8", "L14")


@dataclass
class Stored:
    draft: FindingDraft
    row: dict
    created: bool
    gate: str | None        # None: may be signalled; else why not

    @property
    def signalable(self) -> bool:
        return self.gate is None


def supersede(drafts: list[FindingDraft]) -> list[FindingDraft]:
    seen = {(d.detector, d.ota_code, d.res_norm) for d in drafts}
    out = []
    for d in drafts:
        winners = SUPERSEDED_BY.get(d.detector, ())
        if any((w, d.ota_code, d.res_norm) in seen for w in winners):
            continue
        out.append(d)
    return out


def merge(drafts: list[FindingDraft]) -> list[FindingDraft]:
    """Drafts that share a fingerprint are one finding (a multi-room
    booking printed as two lines of one invoice): amounts add up, evidence
    joins, the earliest deadline and the weakest match score win. The
    commission detectors (L1-L4) already combined their lines in
    base.net_once, netting the booking credit once; this is the fallback
    for any other detector."""
    by: dict[str, list[FindingDraft]] = {}
    for d in drafts:
        by.setdefault(d.fingerprint, []).append(d)
    out = []
    for same in by.values():
        first = same[0]
        if len(same) > 1:
            total = sum(int(d.amount_minor) for d in same)
            first.calc = list(first.calc) + [step("Lines combined", "Σ amounts of the booking's lines",
                                                  {"lines": len(same)}, total)]
            first.amount_minor = total
            # The credit is the booking's (data.credited): one baseline, never
            # k x C for a booking printed as k lines (review merge-double-nets).
            first.credited_minor = max(int(d.credited_minor) for d in same)
            first.evidence = [e for d in same for e in d.evidence]
            first.match_score = min(d.match_score for d in same)
            first.missing = sorted({m for d in same for m in d.missing})
            deadlines = [d.deadline_at for d in same if d.deadline_at is not None]
            first.deadline_at = min(deadlines) if deadlines else None
        out.append(first)
    return out


def _feedback(ctx: Ctx, detector: str) -> Decimal:
    cache = ctx.cache.setdefault("feedback", {})
    if detector not in cache:
        cache[detector] = conf.feedback(ctx.cur, ctx.slug, detector, tuning_row=ctx.cache["tuning"].get(detector))
    return cache[detector]


def persist(ctx: Ctx, drafts: list[FindingDraft]) -> list[Stored]:
    out: list[Stored] = []
    for d in merge(supersede(drafts)):
        if not d.signal or d.klass == "STRUCTURAL":
            d.group = "nosignal"  # never read back into a signal group
        fb = _feedback(ctx, d.detector)
        c = conf.score(d.base_confidence, match_score=d.match_score, missing=len(d.missing), feedback=fb)
        calc = list(d.calc) + [step("Confidence", "base x match_score x completeness x feedback",
                                    {"base": d.base_confidence, "match_score": d.match_score,
                                     "completeness": conf.completeness(len(d.missing)),
                                     "missing": ",".join(d.missing), "feedback": fb.quantize(conf.Q)}, result=c)]
        calc += _after_detection(ctx, d)
        f = {"detector": d.detector, "fingerprint": d.fingerprint, "ota_code": d.ota_code,
             "reservation_id": d.reservation_id, "statement_line_id": d.statement_line_id,
             "subject_ref": d.subject_ref, "class": d.klass, "amount_minor": int(d.amount_minor),
             "currency": d.currency, "confidence": c, "calc": calc, "evidence": d.evidence,
             "deadline_at": d.deadline_at, "internal_only": False, "source": "detector",
             "credit_baseline_minor": int(d.credited_minor)}
        row, created = repo.upsert_finding(ctx.cur, ctx.slug, f, run_id=ctx.run_id, actor=f"detector:{d.detector}")
        if _auto_closed(row):
            row = _reopen(ctx, row)
        if not d.signal:
            gate = "not_signalled"
        elif d.klass == "STRUCTURAL":
            gate = "structural"
        else:
            gate = conf.signal_gate(c, int(d.amount_minor),
                                    conf.thresholds(ctx.policies, ctx.cache["tuning"].get(d.detector)), now=ctx.now)
        out.append(Stored(draft=d, row=row, created=created, gate=gate))
    return out


def _after_detection(ctx: Ctx, d: FindingDraft) -> list[dict]:
    """The trace step that explains a re-run's amount when credit arrived
    after the finding was first stored: repo.upsert_finding keeps the claim
    at gross less the first baseline, so the later credit shows in the
    ledger as recovery instead of shrinking the claim (one accounting)."""
    if not d.credited_minor and d.detector not in repo.COMMISSION_DETECTORS:
        return []
    ctx.cur.execute("SELECT credit_baseline_minor, status, recovered_minor FROM pfg_findings "
                    "WHERE hotel_slug = %s AND fingerprint = %s", (ctx.slug, d.fingerprint))
    prior = ctx.cur.fetchone()
    if prior is None or prior["status"] in repo.FROZEN_STATUSES or int(prior["recovered_minor"] or 0) > 0:
        return []
    later = int(d.credited_minor) - int(prior["credit_baseline_minor"] or 0)
    if later == 0:
        return []
    return [step("Credited after detection", "claim = amount + credited since detection (the ledger counts it)",
                 {"baseline_minor": int(prior["credit_baseline_minor"] or 0), "credited_now_minor": d.credited_minor},
                 max(0, int(d.amount_minor) + later))]


AUTO_NOTE = "No longer detected"


def _auto_closed(row: dict) -> bool:
    return (row["status"] == "DISMISSED" and row.get("dismiss_reason") == "ALREADY_HANDLED"
            and row.get("dismiss_note") == AUTO_NOTE and row["detector"] in AUTO_CLOSE)


def _reopen(ctx: Ctx, row: dict) -> dict:
    """The condition came back (a charge reversed, the parity gap again):
    a finding the machine closed reopens; one a person closed never does."""
    ctx.cur.execute(
        "UPDATE pfg_findings SET status = 'DETECTED', dismiss_reason = NULL, dismiss_note = NULL, updated_at = NOW() "
        "WHERE id = %s RETURNING *", (row["id"],))
    out = dict(ctx.cur.fetchone())
    ctx.cur.execute(
        "INSERT INTO pfg_finding_events (finding_id, hotel_slug, from_status, to_status, actor, note, amount_minor) "
        "VALUES (%s, %s, 'DISMISSED', 'DETECTED', %s, 'Detected again', %s)",
        (row["id"], ctx.slug, f"detector:{row['detector']}", row["amount_minor"]),
    )
    return out


def previous_groups(ctx: Ctx, drafts: list[FindingDraft]) -> set[tuple]:
    """(detector, ota, group) each draft's stored finding sat in before this
    run: a finding that moves group (the pre-statement L3 a statement now
    confirms) must refresh the group it left."""
    fps = [d.fingerprint for d in drafts]
    if not fps:
        return set()
    ctx.cur.execute("SELECT detector, ota_code, subject_ref FROM pfg_findings WHERE hotel_slug = %s "
                    "AND fingerprint = ANY(%s)", (ctx.slug, fps))
    return {(r["detector"], r["ota_code"], str(r["subject_ref"] or "item|").split("|", 1)[0])
            for r in ctx.cur.fetchall()}


def auto_close(ctx: Ctx, detector: str, seen: set[str]) -> list[dict]:
    """Close DETECTED findings of a still-true detector that this full scan
    did not see. Returns the closed rows (their signals are refreshed)."""
    if detector not in AUTO_CLOSE:
        return []
    ctx.cur.execute(
        """
        UPDATE pfg_findings SET status = 'DISMISSED', dismiss_reason = 'ALREADY_HANDLED',
               dismiss_note = %s, updated_at = NOW()
         WHERE hotel_slug = %s AND detector = %s AND status = 'DETECTED' AND NOT (fingerprint = ANY(%s))
           AND source = 'detector'
        RETURNING *
        """,
        (AUTO_NOTE, ctx.slug, detector, list(seen) or [""]),
    )
    rows = [dict(r) for r in ctx.cur.fetchall()]
    for r in rows:
        ctx.cur.execute(
            "INSERT INTO pfg_finding_events (finding_id, hotel_slug, from_status, to_status, actor, note, "
            "amount_minor) VALUES (%s, %s, 'DETECTED', 'DISMISSED', %s, 'No longer detected', %s)",
            (r["id"], ctx.slug, f"detector:{detector}", r["amount_minor"]),
        )
    return rows


def load_tuning(ctx: Ctx) -> dict[str, Any]:
    ctx.cache["tuning"] = conf.tuning_rows(ctx.cur, ctx.slug)
    return ctx.cache["tuning"]
