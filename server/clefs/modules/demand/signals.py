"""
Demand Radar signal kinds (02 spec 4.4). dr.event.changed is registered by
events.py (the events part); the engine adds the rest here.

| Kind                     | Section          | Cadence | Raised by                          |
|--------------------------|------------------|---------|------------------------------------|
| dr.opportunity           | money_available  | weekly  | dr.weekly_top5 (top 5 by impact)   |
| dr.pace.behind           | money_available  | daily   | dr.recommend (pace well behind)    |
| dr.staffing.gap          | service_risk     | daily   | graph_actions.staffing_guidance    |
| dr.ghc.deadline          | money_available  | daily   | ghc.check_deadline                 |
| dr.rec.applied_verified  | verified         | weekly  | measure.check_applied (BAR +/- 2%) |

Every impact is an "estimated opportunity" (observed, never causal), and
every title stays factual: no "will increase", no "guaranteed".
"""

from __future__ import annotations

from typing import Any

from clefs.core.signals import Evidence, RecommendedAction, SignalIn, register_signal_kind, resolve_signal, upsert_signal

OPPORTUNITY = "dr.opportunity"
PACE_BEHIND = "dr.pace.behind"
STAFFING_GAP = "dr.staffing.gap"
GHC_DEADLINE = "dr.ghc.deadline"
APPLIED_VERIFIED = "dr.rec.applied_verified"

register_signal_kind(OPPORTUNITY, label="Demand opportunity", default_owner_role="revenue",
                     section="money_available", cadence="weekly", icon="trending-up")
register_signal_kind(PACE_BEHIND, label="Pickup behind pace", default_owner_role="revenue",
                     section="money_available", cadence="daily", icon="activity")
register_signal_kind(STAFFING_GAP, label="Housekeeping staffing gap", default_owner_role="gm",
                     section="service_risk", cadence="daily", icon="users")
register_signal_kind(GHC_DEADLINE, label="Google Hotel Center deadline", default_owner_role="owner",
                     section="money_available", cadence="daily", icon="calendar-clock")
register_signal_kind(APPLIED_VERIFIED, label="Rate change applied (verified)", default_owner_role="revenue",
                     section="verified", cadence="weekly", icon="check-circle")


def raise_signal(cur: Any, hotel_slug: str, *, kind: str, dedupe_key: str, title: str, summary: str | None = None,
                 impact_minor: int | None = None, currency: str | None = None, confidence: float = 0.5,
                 evidence: list[Evidence] | None = None, action: RecommendedAction | None = None,
                 severity: int = 2, subject_type: str | None = None, subject_id: str | None = None,
                 due_at: Any = None, expires_at: Any = None) -> dict:
    """One upsert with the module's defaults (dedupe keys are prefixed by
    the kind, so kinds never collide)."""
    return upsert_signal(cur, hotel_slug, SignalIn(
        module="demand", kind=kind, dedupe_key=f"{kind}:{dedupe_key}", title=title[:200], summary=summary,
        impact_minor=impact_minor if impact_minor else None, currency=currency if impact_minor else None,
        confidence=max(0.0, min(1.0, float(confidence))), evidence=evidence or [], recommended_action=action,
        severity=severity, subject_type=subject_type, subject_id=subject_id, due_at=due_at, expires_at=expires_at,
    ))


def resolve(cur: Any, hotel_slug: str, *, kind: str, dedupe_key: str, resolution: str) -> bool:
    return resolve_signal(cur, hotel_slug, f"{kind}:{dedupe_key}", resolution=resolution)
