"""
Brief metrics (02 spec 6.4), registered at import (module.py imports this).

`actions.verified_value_aed` from the spec is the foundation's
`core.verified_value` (the `actions.` prefix belongs to no module); the
other three keep their spec keys. Hours are reported as a plain number
(unit "ratio"): the metric units have no hours.
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

_CADENCE = Dimension("cadence", "Cadence", "r.cadence")

register_metric(Metric(
    key="brief.time_to_decision_h", label="Hours to decide",
    description="Average hours from an action being proposed to a person deciding it, for decisions in the period",
    unit="ratio", source="FROM actions a", hotel_column="a.hotel_slug", time_column="a.decided_at",
    time_kind="timestamptz",
    measure="ROUND(AVG(EXTRACT(EPOCH FROM (a.decided_at - a.created_at)) / 3600.0)::NUMERIC, 1)",
    where="a.human_decided AND a.decided_via <> 'dial'", permission="brief.view", higher_is_better=False,
    synonyms=("time to decision", "decision speed"),
))

register_metric(Metric(
    key="brief.approval_rate", label="Approval rate",
    description="Share of human decisions in the period that approved (as proposed or adjusted) rather than ignored",
    unit="percent", source="FROM action_events e JOIN actions a ON a.id = e.action_id", hotel_column="a.hotel_slug",
    time_column="e.at", time_kind="timestamptz",
    measure="100.0 * COUNT(*) FILTER (WHERE e.event IN ('approve', 'adjust')) / NULLIF(COUNT(*), 0)",
    where="e.actor_type = 'user' AND e.event IN ('approve', 'adjust', 'ignore')", permission="brief.view",
    higher_is_better=None, synonyms=("approved share",),
))

register_metric(Metric(
    key="brief.overdue_count", label="Overdue escalations",
    description="Approved actions that reached the last escalation level (24 hours past their deadline)",
    unit="count", source="FROM brf_escalations x", hotel_column="x.hotel_slug", time_column="x.at",
    time_kind="timestamptz", measure="COUNT(*)", where="x.level = 3", permission="brief.view",
    higher_is_better=False, synonyms=("overdue actions", "missed deadlines"),
))

register_metric(Metric(
    key="brief.briefs_sent", label="Briefs sent",
    description="Owner briefs delivered to at least one recipient in the period",
    unit="count", source="FROM brf_runs r", hotel_column="r.hotel_slug", time_column="r.generated_at",
    time_kind="timestamptz", measure="COUNT(*)", where="r.status IN ('sent', 'partial')", dimensions=(_CADENCE,),
    permission="brief.view", higher_is_better=None, synonyms=("briefs delivered",),
))
