"""
Inquiry Guard metrics, intake side (04 spec 4.11), registered at import.

The period is the query's [start, end] on the inquiry's arrival
(created_at) in hotel time. Test leads (Google `is_test`) and merged
duplicates never count. Money metrics carry no currency in their keys
(`currency='hotel'`): legacy tenants use INR. Money at risk is always an
estimate: label it "est." wherever it is shown.
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

_SRC = "FROM inq_inquiries i"
_DIMS = (
    Dimension("source", "Channel", "i.source"),
    Dimension("segment", "Segment", "i.segment"),
    Dimension("language", "Language", "COALESCE(i.language, 'unknown')"),
    Dimension("stage", "Stage", "i.stage"),
)
_WHERE = "NOT i.is_test AND i.duplicate_of IS NULL"
_COMMON = dict(source=_SRC, hotel_column="i.hotel_slug", time_column="i.created_at", time_kind="timestamptz",
               dimensions=_DIMS, permission="inquiries.view")

register_metric(Metric(
    key="inq.inquiries_count", label="Inquiries", description="Direct inquiries received in the period, all channels",
    unit="count", measure="COUNT(*)", where=_WHERE, higher_is_better=True,
    synonyms=("leads", "direct inquiries", "number of inquiries"), **_COMMON,
))

register_metric(Metric(
    key="inq.first_response_p50_min", label="First response (median)",
    description="Median business minutes from arrival to the first staff or approved AI reply (the ack excluded)",
    unit="minutes", measure="percentile_cont(0.5) WITHIN GROUP (ORDER BY i.first_response_business_min)",
    where=_WHERE + " AND i.first_response_business_min IS NOT NULL AND i.source <> 'phone_desk'",
    higher_is_better=False, synonyms=("response time", "speed to lead"), **_COMMON,
))

register_metric(Metric(
    key="inq.first_response_p90_min", label="First response (p90)",
    description="90th percentile of business minutes to the first reply",
    unit="minutes", measure="percentile_cont(0.9) WITHIN GROUP (ORDER BY i.first_response_business_min)",
    where=_WHERE + " AND i.first_response_business_min IS NOT NULL AND i.source <> 'phone_desk'",
    higher_is_better=False, synonyms=("slow responses",), **_COMMON,
))

register_metric(Metric(
    key="inq.sla_breach_rate", label="SLA breach rate",
    description="Share of inquiries in the period that missed their first-response SLA",
    unit="percent", measure="100.0 * COUNT(*) FILTER (WHERE i.sla_breached) / NULLIF(COUNT(*), 0)",
    where=_WHERE + " AND i.source <> 'phone_desk'", higher_is_better=False,
    synonyms=("missed SLA", "unanswered share"), **_COMMON,
))

register_metric(Metric(
    key="inq.money_at_risk", label="Money at risk (est.)",
    description="Estimated value of open inquiries that arrived in the period: room-nights x expected ADR x "
                "stage probability",
    unit="money", currency="hotel", measure="SUM(i.money_at_risk_minor)",
    where=_WHERE + " AND i.stage IN ('new', 'replied', 'quoted', 'payment_pending', 'future')",
    higher_is_better=None, synonyms=("at risk", "pipeline at risk"), **_COMMON,
))


# ─── Quote-to-cash metrics (4.11) ───
# Outcomes bucket on their decision time (inq_outcomes.decided_at); the
# pipeline and money at risk on the inquiry's arrival like the intake set.

_OUT_SRC = "FROM inq_outcomes o JOIN inq_inquiries i ON i.id = o.inquiry_id"
_OUT_DIMS = (
    Dimension("source", "Channel", "i.source"),
    Dimension("segment", "Segment", "i.segment"),
)
_OUT = dict(source=_OUT_SRC, hotel_column="o.hotel_slug", time_column="o.decided_at", time_kind="timestamptz",
            dimensions=_OUT_DIMS, permission="inquiries.view")
_VERIFIED = "o.outcome = 'verified_direct' AND NOT i.is_test"

register_metric(Metric(
    key="inq.verified_revenue", label="Verified direct revenue",
    description="Room revenue of reservations verified as direct bookings from an inquiry (payment plus PMS match, "
                "or a completed stay)",
    unit="money", currency="hotel", measure="SUM(o.verified_revenue_minor)", where=_VERIFIED,
    higher_is_better=True, synonyms=("direct revenue from inquiries", "verified bookings revenue"), **_OUT,
))

register_metric(Metric(
    key="inq.commission_avoided", label="Commission avoided (net)",
    description="The reference OTA's commission these direct bookings avoided, net of payment-link fees; only "
                "outcomes Clefs caused",
    unit="money", currency="hotel", measure="SUM(o.outcome_net_minor)",
    where=_VERIFIED + " AND o.counts_toward_verified_profit", higher_is_better=True,
    synonyms=("OTA commission saved", "commission saved"), **_OUT,
))

register_metric(Metric(
    key="inq.conversion_rate", label="Inquiry conversion",
    description="Verified direct bookings as a share of closed inquiries (verified, lost, turned down) that arrived "
                "in the period",
    unit="percent", measure="100.0 * COUNT(*) FILTER (WHERE i.stage = 'confirmed_verified') / NULLIF(COUNT(*), 0)",
    where=_WHERE + " AND i.stage IN ('confirmed_verified', 'lost', 'turned_down')", higher_is_better=True,
    synonyms=("win rate", "inquiry to booking"), **_COMMON,
))

register_metric(Metric(
    key="inq.lost_reason_mix", label="Lost inquiries",
    description="Inquiries closed as lost or turned down, by reason (group by lost_reason)",
    unit="count", measure="COUNT(*)", where=_WHERE + " AND i.stage IN ('lost', 'turned_down')",
    higher_is_better=False, synonyms=("why we lose inquiries", "lost reasons"),
    source=_SRC, hotel_column="i.hotel_slug", time_column="i.stage_changed_at", time_kind="timestamptz",
    dimensions=(*_DIMS, Dimension("lost_reason", "Lost reason", "COALESCE(i.lost_reason, 'NONE')")),
    permission="inquiries.view",
))

register_metric(Metric(
    key="inq.leak_share", label="Leak share",
    description="Inquiries that booked through an OTA as a share of verified, leaked and lost ones, by when they "
                "closed (internal only)",
    unit="percent",
    measure="100.0 * COUNT(*) FILTER (WHERE i.lost_reason = 'BOOKED_VIA_OTA') / NULLIF(COUNT(*), 0)",
    where=_WHERE + " AND i.stage IN ('confirmed_verified', 'lost')", higher_is_better=False,
    synonyms=("OTA leakage of inquiries",), source=_SRC, hotel_column="i.hotel_slug",
    time_column="i.stage_changed_at", time_kind="timestamptz", dimensions=_DIMS, permission="inquiries.approve",
))

register_metric(Metric(
    key="inq.weighted_pipeline", label="Weighted pipeline (est.)",
    description="Money at risk of open inquiries arriving on or after the period start, by check-in date: "
                "room-nights x expected ADR x stage probability",
    unit="money", currency="hotel", measure="SUM(i.money_at_risk_minor)",
    where=_WHERE + " AND i.stage IN ('new', 'replied', 'quoted', 'payment_pending') AND i.check_in IS NOT NULL",
    higher_is_better=True, synonyms=("pipeline", "potential bookings"),
    source=_SRC, hotel_column="i.hotel_slug", time_column="i.check_in", time_kind="date",
    dimensions=_DIMS, permission="inquiries.view",
))
