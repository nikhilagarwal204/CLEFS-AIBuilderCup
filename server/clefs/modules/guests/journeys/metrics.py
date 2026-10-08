"""
Journeys registrations (04 spec 3.10): signal kinds, metrics and PII
retention entries. The identity part owns guests.returning_share and
guests.direct_rebook_conversions (registrations.py); they are not
re-registered here.

guests.journey_cost is Metric(unit='money', currency='hotel') with no
currency in the key (legacy tenants use INR). Costs are stored in micro
units of the hotel's currency (gst_message_costs.cost_micros); the measure
turns them into minor units of a 2-decimal currency (AED, INR, USD, SAR).
The spec's "month" dimension is the query grain (MetricQuery grain=month):
the metrics core reserves day/week/month as dimension keys.
"""

from __future__ import annotations

from clefs.core import privacy
from clefs.core.metrics import Dimension, Metric, register_metric
from clefs.core.signals import register_signal_kind

# ─── Signal kinds ───

for key, label, section, cadence in (
    ("guests.checkout_open_issues", "Guest departing with open issues", "service_risk", "daily"),
    ("guests.templates_pending_approval", "Journey templates awaiting approval", "service_risk", "weekly"),
    ("guests.optout_spike", "Journey opt-out spike", "compliance", "weekly"),
    ("guests.meta_cap_blocks", "Meta held back marketing messages", "compliance", "weekly"),
    ("guests.wa_billing_not_ready", "WhatsApp payment method missing", "service_risk", "weekly"),
    ("guests.j8_conversion_verified", "Direct rebooking verified", "verified", "weekly"),
):
    register_signal_kind(key, label=label, default_owner_role="gm", section=section, cadence=cadence)

# ─── Metrics ───

SENT = "p.status IN ('sent', 'delivered', 'read', 'replied')"
_PLAN_DIMS = (
    Dimension("step", "Step", "p.step"),
    Dimension("channel", "Channel", "COALESCE(p.channel, 'none')"),
    Dimension("category", "Category", "COALESCE(p.category_at_send, 'UTILITY')"),
    Dimension("language", "Language", "COALESCE(t.language, 'en')"),
)
_PLANS = "FROM gst_journey_plans p LEFT JOIN gst_journey_templates t ON t.id = p.template_id"


def _plan_metric(key: str, label: str, description: str, unit: str, measure: str, where: str = SENT,
                 higher: bool | None = True, synonyms: tuple[str, ...] = ()) -> None:
    register_metric(Metric(
        key=key, label=label, description=description, unit=unit, source=_PLANS, hotel_column="p.hotel_slug",
        time_column="p.last_attempt_at", time_kind="timestamptz", measure=measure, where=where,
        dimensions=_PLAN_DIMS, permission="guests.journeys.view", higher_is_better=higher, synonyms=synonyms,
    ))


_plan_metric("guests.journey_sends", "Journey messages sent", "Journey step messages sent to guests", "count",
             "COUNT(*)", synonyms=("journey sends", "guest messages"))
_plan_metric("guests.journey_delivery_rate", "Journey delivery rate", "Sent journey messages that were delivered",
             "percent", "ROUND(100.0 * AVG(CASE WHEN p.status IN ('delivered', 'read', 'replied') THEN 1 ELSE 0 END), 1)")
_plan_metric("guests.journey_response_rate", "Journey response rate", "Sent journey messages the guest replied to",
             "percent", "ROUND(100.0 * AVG(CASE WHEN p.status = 'replied' THEN 1 ELSE 0 END), 1)")
_plan_metric("guests.optout_rate", "Journey opt-out rate", "Sent journey messages followed by an attributed opt-out",
             "percent",
             "ROUND(100.0 * AVG(CASE WHEN EXISTS (SELECT 1 FROM gst_journey_optouts o WHERE o.plan_id = p.id) "
             "THEN 1 ELSE 0 END), 1)", higher=False)
_plan_metric("guests.feedback_completion_rate", "Feedback completion rate",
             "Stay-feedback messages whose guest completed the one-minute survey", "percent",
             "ROUND(100.0 * AVG(CASE WHEN EXISTS (SELECT 1 FROM gst_feedback f WHERE f.hotel_slug = p.hotel_slug "
             "AND f.reservation_id = p.reservation_id) THEN 1 ELSE 0 END), 1)",
             where=f"{SENT} AND p.step = 'j7'")

register_metric(Metric(
    key="guests.j5_negative_rate", label="Day-1 negative replies",
    description="Day-1 check replies that reported a problem", unit="percent",
    source="FROM gst_journey_replies r JOIN gst_journey_plans p ON p.id = r.plan_id "
           "LEFT JOIN gst_journey_templates t ON t.id = p.template_id",
    hotel_column="r.hotel_slug", time_column="r.created_at", time_kind="timestamptz",
    measure="ROUND(100.0 * AVG(CASE WHEN (r.classification->>'negative')::boolean IS TRUE THEN 1 ELSE 0 END), 1)",
    where="p.step = 'j5'", dimensions=_PLAN_DIMS, permission="guests.journeys.view", higher_is_better=False,
))

register_metric(Metric(
    key="guests.review_link_clicks", label="Review link clicks",
    description="Clicks on the Google and Tripadvisor links shown to every guest (counts only, no identity)",
    unit="count", source="FROM gst_review_link_clicks c", hotel_column="c.hotel_slug", time_column="c.day",
    time_kind="date", measure="COALESCE(SUM(c.clicks), 0)", dimensions=(Dimension("platform", "Platform", "c.platform"),),
    permission="guests.journeys.view", higher_is_better=None,
))

register_metric(Metric(
    key="guests.direct_contact_capture_rate", label="Direct contact capture rate",
    description="OTA stays that gained the guest's own contact with consent (registration card, concierge, J5)",
    unit="percent", source="FROM gst_stays s", hotel_column="s.hotel_slug", time_column="s.check_in", time_kind="date",
    measure="ROUND(100.0 * AVG(CASE WHEN EXISTS (SELECT 1 FROM gst_contact_points cp WHERE cp.hotel_slug = s.hotel_slug "
            "AND cp.reservation_id = s.reservation_id AND cp.provenance = 'guest_direct') THEN 1 ELSE 0 END), 1)",
    where="UPPER(COALESCE(s.source, '')) IN ('BOOKING', 'AGODA', 'EXPEDIA') AND COALESCE(s.status, '') <> 'CANCELLED'",
    dimensions=(Dimension("channel", "Channel", "UPPER(s.source)"),), permission="guests.journeys.view",
    higher_is_better=True,
))

register_metric(Metric(
    key="guests.journey_cost", label="Journey messaging cost",
    description="What journey WhatsApp messages cost (Meta rate card, UNVERIFIED rates)", unit="money",
    source="FROM gst_message_costs c", hotel_column="c.hotel_slug", time_column="c.charged_at", time_kind="timestamptz",
    measure="ROUND(COALESCE(SUM(c.cost_micros), 0) / 10000.0)", currency="hotel",
    dimensions=(
        Dimension("step", "Step", "COALESCE(c.step, 'free_form')"),
        Dimension("channel", "Channel", "c.channel"),
        Dimension("category", "Category", "c.charged_category"),
    ),
    permission="guests.journeys.view", higher_is_better=False,
))

# ─── PII retention (3.2) ───

privacy.register_pii("guests", "gst_feedback", subject_columns={"went_well": "free_text", "improve": "free_text"},
                     ts_column="submitted_at", retention_days=365, strategy="redact", redact_columns=("went_well", "improve"))
privacy.register_pii("guests", "gst_journey_replies", subject_columns={"summary_en": "free_text"},
                     ts_column="created_at", retention_days=365, strategy="redact", redact_columns=("summary_en",))
privacy.register_pii("guests", "gst_stay_prefs", subject_columns={"requests": "free_text", "occasion": "free_text"},
                     ts_column="purge_after", retention_days=1, strategy="delete")
privacy.register_pii("guests", "gst_marketing_sends", subject_columns={"identity_hmac": "hmac"},
                     ts_column="sent_at", retention_days=400, strategy="delete")
privacy.register_pii("guests", "gst_journey_messages", subject_columns={"value_hmac": "hmac"},
                     ts_column="sent_at", retention_days=400, strategy="delete")
privacy.register_pii("guests", "gst_journey_optouts", subject_columns={"value_hmac": "hmac"},
                     ts_column="opted_out_at", retention_days=400, strategy="delete")
