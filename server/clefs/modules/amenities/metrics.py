"""
Amenity metrics (02 spec 5.3), registered at import (jobs.py imports this
file, and module.py imports jobs.py).

Unit metrics read the rollup (amn_variance_daily) so issued, expected and
variance always agree; am.issued_units reads the issuance log instead so it
can break down by shift and staff. Money is minor units in the hotel
currency. Rates per 100 ORN read amn_daily_totals, which holds ORN once per
day (summing ORN over item rows would count it once per item).
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

_VAR = "FROM amn_variance_daily v JOIN amn_items i ON i.id = v.item_id"
_VAR_DIMS = (
    Dimension("item", "Item", "i.name_en",
              "SELECT DISTINCT name_en FROM amn_items WHERE hotel_slug = %(hotel)s"),
    Dimension("category", "Category", "i.category"),
    Dimension("floor", "Floor", "NULLIF(v.floor, '')"),
)
_ISS = "FROM amn_issuance s JOIN amn_items i ON i.id = s.item_id"
_ISS_WHERE = "s.voided_at IS NULL AND s.reason NOT IN ('VIP', 'DAMAGE')"
_ISS_DIMS = (
    Dimension("item", "Item", "i.name_en",
              "SELECT DISTINCT name_en FROM amn_items WHERE hotel_slug = %(hotel)s"),
    Dimension("category", "Category", "i.category"),
    Dimension("floor", "Floor", "s.to_floor"),
    Dimension("shift", "Shift", "s.shift"),
    Dimension("staff", "Issued by", "s.staff_name",
              "SELECT DISTINCT staff_name FROM amn_issuance WHERE hotel_slug = %(hotel)s AND staff_name IS NOT NULL"),
)
_TOT = "FROM amn_daily_totals t"


def _var(key: str, label: str, description: str, measure: str, unit: str = "count", **kw) -> None:
    register_metric(Metric(
        key=key, label=label, description=description, unit=unit, source=_VAR, hotel_column="v.hotel_slug",
        time_column="v.business_date", time_kind="date", measure=measure, dimensions=_VAR_DIMS,
        permission="amenities.view", **kw,
    ))


register_metric(Metric(
    key="am.issued_units", label="Amenities issued", description="Units issued from stores and closets, net of "
    "returns, without VIP and damage lines", unit="count", source=_ISS, hotel_column="s.hotel_slug",
    time_column="s.business_date", time_kind="date", measure="SUM(s.qty)", where=_ISS_WHERE, dimensions=_ISS_DIMS,
    permission="amenities.view", synonyms=("amenity issuance", "amenities used"),
))
_var("am.expected_units", "Amenities expected", "Units expected from occupancy standards plus logged requests",
     "SUM(v.expected_total)")
_var("am.variance_units", "Amenity variance (units)", "Issued minus expected", "SUM(v.variance_units)",
     higher_is_better=False, synonyms=("amenity leakage units",))
_var("am.variance_aed", "Amenity variance", "Issued minus expected, at unit cost incl. excise",
     "SUM(v.variance_minor)", unit="money", currency="hotel", higher_is_better=False,
     synonyms=("amenity leakage", "amenity loss"))

register_metric(Metric(
    key="am.cpor_aed", label="Amenity cost per occupied room", description="Net issued amenity cost per occupied "
    "room-night", unit="money", source=_TOT, hotel_column="t.hotel_slug", time_column="t.business_date",
    time_kind="date", measure="SUM(t.issued_minor)::numeric / NULLIF(SUM(t.orn), 0)", currency="hotel",
    permission="amenities.view", higher_is_better=False, synonyms=("amenity cpor", "cpor"),
))
register_metric(Metric(
    key="am.requests_per_100_orn", label="Amenity requests per 100 room-nights",
    description="Requested units linked to guest requests per 100 occupied room-nights", unit="rate", source=_TOT,
    hotel_column="t.hotel_slug", time_column="t.business_date", time_kind="date",
    measure="SUM(t.requests)::numeric * 100 / NULLIF(SUM(t.orn), 0)", permission="amenities.view",
))
register_metric(Metric(
    key="am.issued_per_100_orn", label="Amenities issued per 100 room-nights",
    description="Net issued units per 100 occupied room-nights", unit="rate", source=_TOT,
    hotel_column="t.hotel_slug", time_column="t.business_date", time_kind="date",
    measure="SUM(t.issued_units) * 100 / NULLIF(SUM(t.orn), 0)", permission="amenities.view",
    higher_is_better=False,
))
