"""
Brief ranking (02 spec 6.1): score = I x C x U, top 5 with caps.

    I  importance. Money items: min(1, ln(1 + AED) / ln(1 + R_ref)), where
       R_ref = 2 x the trailing-30-day average daily room revenue (fallback
       AED 20,000). Zero-money service items use a severity weight (critical
       .9, high .7, medium .4); a <=2-star review within 48 h is .5.
    C  the signal's confidence (0-1).
    U  urgency by time to deadline: 1.0 (<=24 h), .85 (<=72 h), .6 (<=7 d),
       .4 (<=30 d), .25 (no deadline or later). A passed deadline is 1.0.

Pure functions only (no database): brief.py feeds candidates in, and the
tests pin the arithmetic and the caps without fixtures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# ─── Constants ───

TOP_N = 5
SECTION_CAP = 3  # at most 3 items from one section
DETECTOR_CAP = 2  # at most 2 items from one source detector (detector_key)
R_REF_FALLBACK_MAJOR = 20_000  # AED, when no room revenue history exists
SEVERITY_WEIGHT = {"critical": 0.9, "high": 0.7, "medium": 0.4, "low": 0.25}
REVIEW_NEGATIVE_WEIGHT = 0.5
# signals.severity is 1-4; 4 is the most severe (Service raises L1 as 4).
SEVERITY_BY_LEVEL = {4: "critical", 3: "high", 2: "medium", 1: "low"}
URGENCY_STEPS: tuple[tuple[timedelta, float], ...] = (
    (timedelta(hours=24), 1.0),
    (timedelta(hours=72), 0.85),
    (timedelta(days=7), 0.6),
    (timedelta(days=30), 0.4),
)
URGENCY_NONE = 0.25
# Decision slots (QA OWN-02): zero-money L1 service items score .9 on
# severity alone, so a morning with three overdue incidents and a checklist
# filled the whole top 5 with read-only signals while hundreds of priced
# approvals waited elsewhere ("Waiting on you 0 of 5"). The daily tier keeps
# up to this many decidable items in the top N, each replacing the
# lowest-scored read-only pick; order within the N stays I x C x U.
MIN_DECIDABLE = 2


# ─── Factors ───


def importance(impact_major: float | None, r_ref_major: float, *, severity: int | None = None,
               section: str | None = None, recent_negative_review: bool = False) -> float:
    """I. Money dominates when there is money; otherwise the severity weight
    keeps a zero-AED L1 service item able to outrank a small money item."""
    if impact_major is not None and impact_major > 0:
        ref = r_ref_major if r_ref_major and r_ref_major > 0 else R_REF_FALLBACK_MAJOR
        return min(1.0, math.log1p(impact_major) / math.log1p(ref))
    if recent_negative_review:
        return REVIEW_NEGATIVE_WEIGHT
    if severity is not None:
        return SEVERITY_WEIGHT[SEVERITY_BY_LEVEL.get(int(severity), "medium")]
    return SEVERITY_WEIGHT["medium"] if section == "service_risk" else SEVERITY_WEIGHT["low"]


def urgency(deadline: datetime | None, now: datetime) -> float:
    """U. A deadline already past is the most urgent thing there is."""
    if deadline is None:
        return URGENCY_NONE
    left = deadline - now
    for limit, u in URGENCY_STEPS:
        if left <= limit:
            return u
    return URGENCY_NONE


# ─── Source detector ───
# The cap is "at most 2 per source detector" (02 spec 6.1), not per signal
# kind: Profit Guard files seven leak detectors (L1, L2, L3, L4, L5, L12,
# L13) under the one kind pg.leak.detected, so a per-kind cap would let only
# two OTA leaks through however many detectors fired. Its dedupe keys name
# the detector ("pg:<detector>:<ota>:<group>", group_key / item_key), which
# is a stable, documented key format; any other signal can name one with a
# `detector` field. Everything else falls back to its kind.


def detector_key(sig: dict[str, Any]) -> str:
    kind = str(sig.get("kind") or "")
    explicit = sig.get("detector")
    if isinstance(explicit, str) and explicit.strip():
        return f"{kind}:{explicit.strip()}"
    parts = str(sig.get("dedupe_key") or "").split(":")
    if len(parts) >= 3 and parts[0] == "pg" and parts[1]:
        return f"{kind}:{parts[1]}"
    return kind


# ─── Candidates ───


@dataclass
class Candidate:
    """One rankable thing: a signal (and its live action, when there is one)."""

    key: str  # stable identity for tie-breaks and tests (e.g. "signal:17")
    section: str
    detector: str  # the source detector (detector_key); the detector cap counts these
    title: str
    confidence: float
    impact_minor: int | None = None
    currency: str | None = None
    minor_per_major: int = 100
    deadline: datetime | None = None
    severity: int | None = None
    recent_negative_review: bool = False
    # A live action the owner can decide from the card (approve, adjust,
    # assign...). rank() keeps a few of these in the top N (MIN_DECIDABLE).
    decidable: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.0
    urgency: float = 0.0
    score: float = 0.0

    @property
    def impact_major(self) -> float | None:
        if self.impact_minor is None:
            return None
        return self.impact_minor / float(self.minor_per_major or 1)


def score_all(cands: list[Candidate], *, now: datetime, r_ref_major: float) -> list[Candidate]:
    """Fill importance, urgency and score in place; returns the same list."""
    for c in cands:
        c.importance = importance(
            c.impact_major, r_ref_major, severity=c.severity, section=c.section,
            recent_negative_review=c.recent_negative_review,
        )
        c.urgency = urgency(c.deadline, now)
        conf = max(0.0, min(1.0, float(c.confidence or 0.0)))
        c.score = round(c.importance * conf * c.urgency, 6)
    return cands


def _order_key(c: Candidate) -> tuple:
    """Highest score first; ties break on the earliest deadline (none last),
    then the larger impact, then the key so the order is deterministic."""
    dl = c.deadline.timestamp() if c.deadline else math.inf
    return (-c.score, dl, -(c.impact_minor or 0), c.key)


def _fits(picked: list[Candidate], c: Candidate, section_cap: int, detector_cap: int) -> bool:
    return (sum(p.section == c.section for p in picked) < section_cap
            and sum(p.detector == c.detector for p in picked) < detector_cap)


def rank(cands: list[Candidate], *, now: datetime, r_ref_major: float, limit: int = TOP_N,
         section_cap: int = SECTION_CAP, detector_cap: int = DETECTOR_CAP,
         min_decidable: int = 0) -> list[Candidate]:
    """The top `limit` under the section and detector caps. Compliance never
    ranks (Canon 6): callers route it to the weekly digest, and it is dropped
    here too in case one slips through.

    `min_decidable`: when fewer picks than that carry a live action, the best
    remaining decidable candidates replace the lowest-scored read-only picks
    (caps still hold), then the list is re-sorted by score."""
    score_all(cands, now=now, r_ref_major=r_ref_major)
    ordered = [c for c in sorted(cands, key=_order_key) if c.section != "compliance"]
    picked: list[Candidate] = []
    for c in ordered:
        if not _fits(picked, c, section_cap, detector_cap):
            continue
        picked.append(c)
        if len(picked) >= limit:
            break
    have = sum(c.decidable for c in picked)
    for c in ordered:
        if have >= min_decidable:
            break
        if not c.decidable or any(p is c for p in picked):  # identity: dataclass == compares fields
            continue
        if len(picked) < limit and _fits(picked, c, section_cap, detector_cap):
            picked.append(c)
            have += 1
            continue
        # Swap out the lowest-scored read-only pick that makes room under the caps.
        for out in reversed([p for p in picked if not p.decidable]):
            rest = [p for p in picked if p is not out]
            if _fits(rest, c, section_cap, detector_cap):
                picked = rest + [c]
                have += 1
                break
    return sorted(picked, key=_order_key)
