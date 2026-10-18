"""
Service metrics (03 spec 1.7), registered with clefs.core.metrics.

Definitions:
  first response  = acknowledged_at - timestamp  (the ack metrics)
  completion      = resolved_at - timestamp      (as the guest sees it)

Pre-v2 rows (intake_version NULL) are excluded from every ack metric: their
accepted_at was a name typed into the old dashboard, not an acknowledgement,
and the console labels them "pre-v2". Buckets are hotel-local days through
core.metrics (epoch-ms instants become `to_timestamp(ts / 1000) AT TIME ZONE
<hotel tz>`), which retires the hardcoded Asia/Kolkata and the UTC midnight
of the old staff-analytics endpoint.
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

# ─── Shared shape ───

SOURCE = "FROM staff_requests r"
HOTEL = "r.hotel_slug"
TIME = 'r."timestamp"'
V2 = "r.intake_version IS NOT NULL"
NOT_CANCELLED = "r.state IS DISTINCT FROM 'cancelled'"
ACKED = f'{V2} AND r.acknowledged_at IS NOT NULL AND r.acknowledged_at >= r."timestamp"'
PERMISSION = "service.view"

DIMENSIONS = (
    Dimension("department", "Department", "COALESCE(r.department_key, r.category)"),
    Dimension("floor", "Floor", "r.floor"),
    Dimension("shift", "Shift", "r.shift_key"),
    Dimension("source", "Source", "r.source"),
    Dimension("language", "Language", "COALESCE(r.language, 'en')"),
    Dimension("staff", "Staff", "r.assigned_to"),
    Dimension("room_type", "Room type", "r.room_type"),
    Dimension("stay_stage", "Stay stage", "r.stay_stage"),
    Dimension("request_type", "Request type", "COALESCE(r.request_type_key, r.type)"),
    Dimension("severity", "Severity", "r.severity"),
)


def _minutes(expr: str, q: float) -> str:
    return f"ROUND((percentile_cont({q}) WITHIN GROUP (ORDER BY ({expr}) / 60000.0))::numeric, 1)"


def _rate(numerator: str, denominator: str = "TRUE") -> str:
    """Percent 0-100 of rows matching `numerator` among rows matching
    `denominator` (NULL when there are none)."""
    return (f"ROUND(100.0 * COUNT(*) FILTER (WHERE {numerator}) "
            f"/ NULLIF(COUNT(*) FILTER (WHERE {denominator}), 0), 1)")


def _metric(key: str, label: str, description: str, unit: str, measure: str, *, where: str = "",
            higher_is_better: bool | None = None, synonyms: tuple[str, ...] = ()) -> Metric:
    return Metric(key=key, label=label, description=description, unit=unit, source=SOURCE, hotel_column=HOTEL,
                  time_column=TIME, time_kind="epoch_ms", measure=measure, where=where, dimensions=DIMENSIONS,
                  permission=PERMISSION, higher_is_better=higher_is_better, synonyms=synonyms)


# ─── Registrations ───

METRICS: tuple[Metric, ...] = (
    _metric("service.requests", "Guest requests", "Requests created (merged repeats count once)", "count",
            "COUNT(*)", synonyms=("requests", "tickets")),
    _metric("service.ack_p50_min", "First response, median", "Minutes from request to a person acknowledging it (v2 requests)",
            "minutes", _minutes('r.acknowledged_at - r."timestamp"', 0.5), where=ACKED, higher_is_better=False,
            synonyms=("response time", "ack time")),
    _metric("service.ack_p90_min", "First response, 90th percentile", "Minutes by which 9 in 10 requests were acknowledged",
            "minutes", _minutes('r.acknowledged_at - r."timestamp"', 0.9), where=ACKED, higher_is_better=False),
    _metric("service.resolve_p50_min", "Completion, median", "Minutes from request to done, as the guest sees it",
            "minutes", _minutes('r.resolved_at - r."timestamp"', 0.5),
            where="r.state = 'completed' AND r.resolved_at IS NOT NULL AND r.resolved_at >= r.\"timestamp\"",
            higher_is_better=False, synonyms=("resolution time", "completion time")),
    _metric("service.sla_ack_breach_rate", "Acknowledged late", "Share of v2 requests acknowledged after their SLA",
            "percent", _rate("r.sla_ack_breached"), where=f"{V2} AND {NOT_CANCELLED}", higher_is_better=False),
    _metric("service.sla_resolve_breach_rate", "Completed late", "Share of v2 requests completed after their SLA",
            "percent", _rate("r.sla_resolve_breached"), where=f"{V2} AND {NOT_CANCELLED}", higher_is_better=False),
    _metric("service.reopen_rate", "Reopened", "Share of requests reopened at least once",
            "percent", _rate("r.reopen_count > 0"), where=f"{V2} AND {NOT_CANCELLED}", higher_is_better=False),
    _metric("service.guest_confirmed_rate", "Guest confirmed fixed", "Share of guest answers that said resolved",
            "percent", _rate("r.guest_confirmation = 'yes'", "r.guest_confirmation IN ('yes', 'no')"),
            where="r.guest_confirmation IN ('yes', 'no')", higher_is_better=True),
    _metric("service.requests_by_source", "Requests by channel", "Requests per intake channel (group by source)",
            "count", "COUNT(*)", synonyms=("channels", "by source")),
    _metric("service.phone_desk_share", "Phoned to the desk", "Share of requests typed in by the desk from a phone call",
            "percent", _rate("r.source = 'phone_desk'"), higher_is_better=False),
)

for _m in METRICS:
    register_metric(_m)

# Recovery gestures given (Canon 2: minor units in the hotel's currency; the
# key keeps the spec's name). Pending, rejected and cancelled gestures cost
# nothing yet.
register_metric(Metric(
    key="service.recovery_cost_aed",
    label="Service recovery cost",
    description="Value of gestures given to guests after a service failure",
    unit="money",
    source="FROM svc_recovery_actions a",
    hotel_column="a.hotel_slug",
    time_column="a.created_at",
    time_kind="timestamptz",
    measure="COALESCE(SUM(a.value_minor), 0)",
    where="a.status = 'given' AND a.currency = (SELECT h.currency FROM hotels h WHERE h.slug = a.hotel_slug)",
    dimensions=(Dimension("kind", "Gesture", "a.kind"),),
    permission=PERMISSION,
    currency="hotel",
    higher_is_better=False,
    synonyms=("recovery cost", "compensation"),
))
