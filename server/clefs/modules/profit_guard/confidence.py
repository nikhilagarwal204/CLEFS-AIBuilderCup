"""Finding confidence and feedback (02 spec 3.4).

confidence = base x match_score x completeness x feedback
- completeness = 1 - 0.15 x (missing optional inputs), never below 0;
- feedback = (a + accepted) / (a + b + accepted + dismissed_fp): a Beta
  prior (a=4, b=1: 0.8 before any feedback) per hotel and detector, shrunk
  toward the global rate across the other hotels (leave-one-out). Their
  posterior mean sets the hotel's prior centre while the prior keeps its
  weight a + b, so with no
  feedback anywhere the formula is exactly (4 + 0) / (4 + 1 + 0 + 0).
"accepted" counts the findings a person validated (ACCEPTED, DISPUTED,
RECOVERED: the pilot scorecard's "valid"); "dismissed_fp" counts
dismissals with reason FALSE_POSITIVE_DATA. Only the last 365 days count,
so an old mistake stops weighing on a fixed detector.

Findings below the confidence floor or the minimum amount, or from a
detector suppressed for the hotel, are stored but not signalled.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock

PRIOR_A = Decimal(4)
PRIOR_B = Decimal(1)
MISSING_STEP = Decimal("0.15")
WINDOW_DAYS = 365
VALID_STATUSES = ("ACCEPTED", "DISPUTED", "RECOVERED")
FP_REASON = "FALSE_POSITIVE_DATA"
DISMISS_REASONS = ("FALSE_POSITIVE_DATA", "CONTRACT_EXCEPTION", "ALREADY_HANDLED", "TOO_SMALL", "NOT_WORTH_DISPUTING",
                   "OTHER")
Q = Decimal("0.001")


# ─── Pieces ───


def completeness(missing: int) -> Decimal:
    return max(Decimal(0), 1 - MISSING_STEP * max(0, int(missing)))


def counts(cur: Any, detector: str, hotel_slug: str | None = None) -> tuple[int, int]:
    """(accepted, dismissed_fp) for one detector, one hotel or all hotels."""
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status = ANY(%(valid)s)) AS accepted,
               COUNT(*) FILTER (WHERE status = 'DISMISSED' AND dismiss_reason = %(fp)s) AS fp
          FROM pfg_findings
         WHERE detector = %(d)s AND (%(h)s::text IS NULL OR hotel_slug = %(h)s) AND updated_at >= %(since)s
           AND NOT internal_only
        """,
        {"valid": list(VALID_STATUSES), "fp": FP_REASON, "d": detector, "h": hotel_slug,
         "since": clock.now() - timedelta(days=WINDOW_DAYS)},
    )
    r = cur.fetchone()
    return int(r["accepted"] or 0), int(r["fp"] or 0)


def feedback_from(accepted: int, fp: int, *, g_accepted: int = 0, g_fp: int = 0, a: Decimal = PRIOR_A,
                  b: Decimal = PRIOR_B) -> Decimal:
    """The shrunk Beta posterior mean (pure, for tests and the API)."""
    n0 = a + b
    centre = (a + g_accepted) / (n0 + g_accepted + g_fp)
    a_h = n0 * centre
    return (a_h + accepted) / (n0 + accepted + fp)


def feedback(cur: Any, hotel_slug: str, detector: str, *, tuning_row: dict | None = None) -> Decimal:
    a = Decimal(str((tuning_row or {}).get("prior_a") or PRIOR_A))
    b = Decimal(str((tuning_row or {}).get("prior_b") or PRIOR_B))
    acc, fp = counts(cur, detector, hotel_slug)
    g_acc, g_fp = counts(cur, detector, None)
    # The centre comes from the other hotels: the hotel's own decisions are
    # already its evidence, counting them in the centre too would double them.
    return feedback_from(acc, fp, g_accepted=g_acc - acc, g_fp=g_fp - fp, a=a, b=b)


def score(base: Any, *, match_score: Any = 1, missing: int = 0, feedback: Any = 1) -> Decimal:
    """The stored confidence, clamped to [0, 1] and rounded to 3 places."""
    raw = Decimal(str(base)) * Decimal(str(match_score)) * completeness(missing) * Decimal(str(feedback))
    return min(Decimal(1), max(Decimal(0), raw)).quantize(Q, rounding=ROUND_HALF_UP)


# ─── Tuning rows and thresholds ───


def tuning_rows(cur: Any, hotel_slug: str) -> dict[str, dict]:
    cur.execute("SELECT * FROM pfg_detector_tuning WHERE hotel_slug = %s", (hotel_slug,))
    return {r["detector"]: dict(r) for r in cur.fetchall()}


def thresholds(policies: dict, tuning_row: dict | None) -> dict:
    """{min_amount_minor, confidence_floor, suppressed_until}: the tuned
    values win when set, else the owner policies."""
    t = tuning_row or {}
    min_amount = int(policies.get("min_finding_minor") or 0)
    if t.get("min_amount_minor") is not None:
        min_amount = max(min_amount, int(t["min_amount_minor"]))
    floor = Decimal(str(t["confidence_floor"])) if t.get("confidence_floor") is not None \
        else Decimal(str(policies.get("confidence_floor") or "0.5"))
    return {"min_amount_minor": min_amount, "confidence_floor": floor, "suppressed_until": t.get("suppressed_until")}


def signal_gate(confidence: Decimal, amount_minor: int, th: dict, *, now: datetime | None = None) -> str | None:
    """None when the finding may be signalled, else the reason it is not."""
    now = now or clock.now()
    if th.get("suppressed_until") is not None and th["suppressed_until"] > now:
        return "suppressed"
    if confidence < th["confidence_floor"]:
        return "below_confidence_floor"
    if amount_minor < th["min_amount_minor"]:
        return "below_min_amount"
    return None
