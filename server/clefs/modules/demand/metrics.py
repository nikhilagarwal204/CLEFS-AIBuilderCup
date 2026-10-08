"""
Demand Radar metrics (02 spec 4.4) for Ask and the console. Each reads core
tables (occupancy_daily, otb_snapshots) or Demand's own; permission
demand.view. dr.comp_median_rate_aed is registered by comp_rates.py.

| Key                   | Unit    | What                                                        |
|-----------------------|---------|-------------------------------------------------------------|
| dr.occupancy_pct      | percent | rooms occupied / rooms available, by stay date              |
| dr.adr_aed            | money   | room revenue / rooms occupied                               |
| dr.revpar_aed         | money   | room revenue / rooms available                              |
| dr.rooms_otb          | nights  | rooms on the books in the latest OTB snapshot               |
| dr.pickup_rooms       | nights  | rooms picked up in the 7 days to the latest snapshot        |
| dr.demand_index       | rate    | mean demand index (0-100)                                   |
| dr.rec_decision_rate  | percent | share of recommendations decided (superseded ones left out) |
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

_OCC = "FROM occupancy_daily o"
_OCC_DIMS = (
    Dimension("dow", "Weekday (ISO)", "EXTRACT(ISODOW FROM o.date)::int"),
    Dimension("month_of_year", "Month of year", "EXTRACT(MONTH FROM o.date)::int"),
)
# The latest-snapshot filter lives in `where`, so the source stays a plain FROM clause.
_OTB_SRC = "FROM otb_snapshots s"
_OTB_LATEST = "s.snapshot_date = (SELECT max(x.snapshot_date) FROM otb_snapshots x WHERE x.hotel_slug = s.hotel_slug)"
_OTB_DIMS = (
    Dimension("dow", "Weekday (ISO)", "EXTRACT(ISODOW FROM s.stay_date)::int"),
    Dimension("month_of_year", "Month of year", "EXTRACT(MONTH FROM s.stay_date)::int"),
)

register_metric(Metric(
    key="dr.occupancy_pct", label="Occupancy", unit="percent",
    description="Rooms occupied over rooms available (out-of-order rooms stay in), by stay date.",
    source=_OCC, hotel_column="o.hotel_slug", time_column="o.date", time_kind="date",
    measure="100.0 * SUM(o.rooms_occupied) / NULLIF(SUM(o.rooms_available), 0)", dimensions=_OCC_DIMS,
    permission="demand.view", higher_is_better=True, synonyms=("occupancy", "occ"),
))
register_metric(Metric(
    key="dr.adr_aed", label="ADR", unit="money",
    description="Average daily rate: room revenue over rooms occupied, by stay date.",
    source=_OCC, hotel_column="o.hotel_slug", time_column="o.date", time_kind="date",
    measure="SUM(o.room_revenue_minor)::numeric / NULLIF(SUM(o.rooms_occupied), 0)", dimensions=_OCC_DIMS,
    permission="demand.view", currency="hotel", higher_is_better=True, synonyms=("average daily rate", "adr"),
))
register_metric(Metric(
    key="dr.revpar_aed", label="RevPAR", unit="money",
    description="Room revenue over rooms available, by stay date.",
    source=_OCC, hotel_column="o.hotel_slug", time_column="o.date", time_kind="date",
    measure="SUM(o.room_revenue_minor)::numeric / NULLIF(SUM(o.rooms_available), 0)", dimensions=_OCC_DIMS,
    permission="demand.view", currency="hotel", higher_is_better=True, synonyms=("revenue per available room",),
))
register_metric(Metric(
    key="dr.rooms_otb", label="Rooms on the books", unit="nights",
    description="Rooms on the books per stay date in the latest on-the-books snapshot.",
    source=_OTB_SRC, hotel_column="s.hotel_slug", time_column="s.stay_date", time_kind="date",
    measure="COALESCE(SUM(s.rooms_otb), 0)", where=_OTB_LATEST, dimensions=_OTB_DIMS,
    permission="demand.view", higher_is_better=True, synonyms=("otb", "on the books"),
))
register_metric(Metric(
    key="dr.pickup_rooms", label="Pickup (7 days)", unit="nights",
    description="Rooms picked up per stay date in the 7 days to the latest snapshot.",
    source="FROM otb_snapshots s JOIN otb_snapshots p ON p.hotel_slug = s.hotel_slug AND p.stay_date = s.stay_date "
           "AND p.snapshot_date = s.snapshot_date - 7",
    hotel_column="s.hotel_slug", time_column="s.stay_date", time_kind="date",
    measure="COALESCE(SUM(s.rooms_otb - p.rooms_otb), 0)", where=_OTB_LATEST, dimensions=_OTB_DIMS,
    permission="demand.view", higher_is_better=True, synonyms=("pickup", "booking pace"),
))
register_metric(Metric(
    key="dr.demand_index", label="Demand index", unit="rate",
    description="Mean Demand Radar index (0-100) per stay date: season, events, pace and indicative comp prices.",
    source="FROM dr_demand_days d", hotel_column="d.hotel_slug", time_column="d.stay_date", time_kind="date",
    measure="AVG(d.demand_index)",
    dimensions=(Dimension("dow", "Weekday (ISO)", "EXTRACT(ISODOW FROM d.stay_date)::int"),
                Dimension("month_of_year", "Month of year", "EXTRACT(MONTH FROM d.stay_date)::int"),
                Dimension("event", "Top event",
                          "(SELECT e.title FROM dr_events e WHERE e.id = d.top_event_ids[1])")),
    permission="demand.view", higher_is_better=True, synonyms=("demand", "DI"),
))
register_metric(Metric(
    key="dr.rec_decision_rate", label="Recommendations decided", unit="percent",
    description="Share of rate recommendations the owner approved, adjusted or rejected, by creation time.",
    source="FROM dr_rate_recommendations r", hotel_column="r.hotel_slug", time_column="r.created_at",
    time_kind="timestamptz",
    measure="100.0 * COUNT(*) FILTER (WHERE r.decided_at IS NOT NULL) / NULLIF(COUNT(*), 0)",
    where="r.status <> 'SUPERSEDED'",
    dimensions=(Dimension("room_type", "Room type", "r.room_type_code"), Dimension("status", "Status", "r.status")),
    permission="demand.view", higher_is_better=True, synonyms=("decision rate", "actionability"),
))
