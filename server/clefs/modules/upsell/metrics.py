"""
Upsell metrics (03 spec 2.8: order_revenue, upsell_accept_rate,
upsell_revenue), registered at import (module.py imports this file).

Revenue metrics exclude cancelled and refunded orders: the owner asks "what
did in-room dining and upselling bring in", and a refund brought in nothing.
Percent metrics are 0-100.
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

_LIVE = "o.status NOT IN ('cancelled', 'refunded')"
_ORDERS = "FROM ups_orders o LEFT JOIN ups_outlets ol ON ol.id = o.outlet_id"
_ORDER_DIMS = (
    Dimension("outlet", "Outlet", "COALESCE(ol.name_en, 'Several outlets')",
              "SELECT DISTINCT name_en FROM ups_outlets WHERE hotel_slug = %(hotel)s"),
    Dimension("source", "Channel", "o.source",
              "SELECT DISTINCT source FROM ups_orders WHERE hotel_slug = %(hotel)s"),
    Dimension("payment", "Payment", "o.payment"),
)
_EVENTS = "FROM ups_upsell_events e"
_EVENT_DIMS = (
    Dimension("kind", "Offer kind", "e.kind"),
)

register_metric(Metric(
    key="upsell.orders", label="Orders", description="In-room and outlet orders placed by guests",
    unit="count", source=_ORDERS, hotel_column="o.hotel_slug", time_column="o.created_at",
    time_kind="timestamptz", measure="COUNT(*)", where=_LIVE, dimensions=_ORDER_DIMS,
    permission="menu.view", higher_is_better=True, synonyms=("room service orders", "food orders"),
))

register_metric(Metric(
    key="upsell.order_revenue", label="Order revenue", description="Total of guest orders, fees included",
    unit="money", source=_ORDERS, hotel_column="o.hotel_slug", time_column="o.created_at",
    time_kind="timestamptz", measure="SUM(o.total_minor)", where=_LIVE, dimensions=_ORDER_DIMS,
    permission="menu.view", currency="hotel", higher_is_better=True,
    synonyms=("room service revenue", "in-room dining revenue", "f&b orders revenue"),
))

register_metric(Metric(
    key="upsell.average_order_value", label="Average order value", description="Mean order total",
    unit="money", source=_ORDERS, hotel_column="o.hotel_slug", time_column="o.created_at",
    time_kind="timestamptz", measure="AVG(o.total_minor)", where=_LIVE, dimensions=_ORDER_DIMS,
    permission="menu.view", currency="hotel", higher_is_better=True, synonyms=("aov", "average check"),
))

register_metric(Metric(
    key="upsell.upsell_revenue", label="Upsell revenue",
    description="Value of suggested items guests added to orders that were not cancelled or refunded",
    unit="money", source=_EVENTS + " JOIN ups_orders o ON o.id = e.order_id", hotel_column="e.hotel_slug",
    time_column="e.at", time_kind="timestamptz", measure="SUM(e.value_minor)",
    where="e.state = 'accepted' AND " + _LIVE, dimensions=_EVENT_DIMS,
    permission="menu.view", currency="hotel", higher_is_better=True, synonyms=("pairing revenue",),
))

register_metric(Metric(
    key="upsell.upsell_accept_rate", label="Upsell accept rate",
    description="Suggestions accepted as a share of suggestions shown",
    unit="percent", source=_EVENTS, hotel_column="e.hotel_slug", time_column="e.at", time_kind="timestamptz",
    measure="100.0 * COUNT(*) FILTER (WHERE e.state = 'accepted')"
            " / NULLIF(COUNT(*) FILTER (WHERE e.state = 'shown'), 0)",
    where="e.state IN ('shown', 'accepted')", dimensions=_EVENT_DIMS,
    permission="menu.view", higher_is_better=True, synonyms=("upsell conversion", "take rate"),
))
