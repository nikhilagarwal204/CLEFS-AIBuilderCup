"""What core/stays declares to the foundation registries on import: its PII
tables (Trust retention and DSAR find guests through them) and its metrics
(Ask Clefs, Finance and the brief read them; keys "stays.*").
"""

from __future__ import annotations

from clefs.core import metrics, privacy
from clefs.core.metrics import Dimension, Metric

# ─── PII ───
# Stays are financial records: guest names are kept 3 years, then redacted.
# Contact values have their own lifetimes (contacts.py and the purge job);
# the rows themselves go once purged and past retention.
FINANCE_RETENTION_DAYS = 1095

privacy.register_pii(
    "core",
    "reservations",
    subject_columns={"guest_name": "name"},
    ts_column="check_out",
    retention_days=FINANCE_RETENTION_DAYS,
    strategy="redact",
    redact_columns=("guest_name", "guest_name_norm"),
)
privacy.register_pii(
    "core",
    "reservation_contacts",
    subject_columns={"value_hmac": "hmac"},
    ts_column="created_at",
    retention_days=FINANCE_RETENTION_DAYS,
    strategy="delete",
    where="purged_at IS NOT NULL",
)
privacy.register_pii(
    "core",
    "reservation_matches",
    subject_columns={},
    ts_column="created_at",
    retention_days=FINANCE_RETENTION_DAYS,
    strategy="redact",
    redact_columns=("query",),
)

# ─── Metrics ───

_NIGHTS = "FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id"
_CHANNEL_VALUES = ("SELECT DISTINCT channel_code FROM reservations WHERE hotel_slug = %(hotel)s "
                   "AND channel_code IS NOT NULL ORDER BY 1")
_NIGHT_DIMS = (
    Dimension("channel", "Channel", "r.channel_code", _CHANNEL_VALUES),
    Dimension("segment", "Segment", "r.segment"),
    Dimension("room_type", "Room type", "COALESCE(n.room_type_code, r.room_type_code)"),
)
_RES_DIMS = (
    Dimension("channel", "Channel", "r.channel_code", _CHANNEL_VALUES),
    Dimension("status", "Status", "r.status"),
    Dimension("segment", "Segment", "r.segment"),
)

ROOM_NIGHTS = metrics.register_metric(Metric(
    key="stays.room_nights", label="Room nights", unit="nights",
    description="Occupied room nights (nights that started and were not left), by stay date.",
    source=_NIGHTS, hotel_column="n.hotel_slug", time_column="n.stay_date", time_kind="date",
    measure="COALESCE(SUM(n.rooms), 0)", where="n.stayed", dimensions=_NIGHT_DIMS,
    permission="core.metrics.view", higher_is_better=True, synonyms=("occupied rooms", "rooms sold"),
))
ROOM_REVENUE = metrics.register_metric(Metric(
    key="stays.room_revenue", label="Room revenue", unit="money",
    description="Room revenue net of service charge, fees, VAT and Tourism Dirham, by stay date.",
    source=_NIGHTS, hotel_column="n.hotel_slug", time_column="n.stay_date", time_kind="date",
    measure="COALESCE(SUM(n.rate_minor * n.rooms), 0)", where="n.stayed", dimensions=_NIGHT_DIMS,
    permission="core.metrics.view", currency="hotel", higher_is_better=True, synonyms=("rooms revenue",),
))
BOOKINGS = metrics.register_metric(Metric(
    key="stays.bookings", label="Bookings", unit="count",
    description="Reservations by the time they were booked.",
    source="FROM reservations r", hotel_column="r.hotel_slug", time_column="r.booked_at", time_kind="timestamptz",
    measure="COUNT(*)", dimensions=_RES_DIMS, permission="core.metrics.view", higher_is_better=True,
    synonyms=("reservations", "pickup"),
))
CANCELLATIONS = metrics.register_metric(Metric(
    key="stays.cancellations", label="Cancellations", unit="count",
    description="Reservations cancelled, by the time they were cancelled.",
    source="FROM reservations r", hotel_column="r.hotel_slug", time_column="r.cancelled_at",
    time_kind="timestamptz", measure="COUNT(*)", where="r.status = 'CANCELLED'", dimensions=_RES_DIMS,
    permission="core.metrics.view", higher_is_better=False, synonyms=("cancelled bookings",),
))
