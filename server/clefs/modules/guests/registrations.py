"""
Guests registrations that must exist whenever the module is loaded: the
identity metrics (03 spec 2.7 keys, shared with 04 spec 3.10) and the PII
registry entries Trust's retention and DSR executors read.

The two metric keys are owned HERE (register_metric refuses a second
origin): the journeys part adds its own keys, never these.
"""

from __future__ import annotations

from clefs.core import privacy
from clefs.core.metrics import Dimension, Metric, register_metric
from clefs.modules.guests.evidence import EVIDENCE_PURPOSE

# ─── Metrics ───

# An arrival is "returning" when one of its own PMS contact identities was
# on an earlier stay that happened. Measured from core identities and never
# claimed on the website (Canon 16: claims.guest_memory is a separate flag).
_RETURNING = (
    "CASE WHEN EXISTS (SELECT 1 FROM reservation_contacts c "
    "JOIN reservation_contacts c2 ON c2.hotel_slug = c.hotel_slug AND c2.value_hmac = c.value_hmac "
    "AND c2.reservation_id <> c.reservation_id "
    "JOIN reservations r2 ON r2.id = c2.reservation_id "
    "WHERE c.reservation_id = r.id AND c.value_hmac IS NOT NULL AND r2.check_out <= r.check_in "
    "AND r2.status IN ('IN_HOUSE', 'CHECKED_OUT')) THEN 1 ELSE 0 END"
)

register_metric(
    Metric(
        key="guests.returning_share",
        label="Returning guest share",
        description="Arrivals whose own contact was on an earlier completed stay, as a percentage of arrivals",
        unit="percent",
        source="FROM reservations r",
        hotel_column="r.hotel_slug",
        time_column="r.check_in",
        time_kind="date",
        measure=f"ROUND(100.0 * AVG({_RETURNING}), 1)",
        where="r.status IN ('IN_HOUSE', 'CHECKED_OUT')",
        dimensions=(Dimension("channel", "Channel", "COALESCE(r.channel_code, 'UNKNOWN')"),),
        permission="guests.view",
        higher_is_better=True,
        synonyms=("repeat guests", "returning guests", "repeat share"),
    )
)

register_metric(
    Metric(
        key="guests.direct_rebook_conversions",
        label="Direct rebooking conversions",
        description="Direct codes redeemed on a booking: J8 rebooking offers and codes issued to a profile",
        unit="count",
        source="FROM gst_direct_codes d",
        hotel_column="d.hotel_slug",
        time_column="d.redeemed_at",
        time_kind="timestamptz",
        measure="COUNT(*)",
        where="d.redeemed_at IS NOT NULL",
        dimensions=(
            Dimension("basis", "Basis", "CASE WHEN d.plan_id IS NOT NULL THEN 'j8_code' ELSE 'direct_code' END"),
        ),
        permission="guests.view",
        higher_is_better=True,
        synonyms=("rebookings", "direct rebookings"),
    )
)

# ─── PII registry ───
# Profiles: "until consent is withdrawn, or 24 months inactive" (03 5.3).
# A withdrawal deletes at once (identity.forget_profile); this is the
# inactivity backstop. updated_at moves on every linked stay.
privacy.register_pii(
    "guests", "gst_profiles",
    subject_columns={"phone_hmac": "hmac", "email_hmac": "hmac", "first_name_enc": "name", "preferences": "health"},
    ts_column="updated_at", retention_days=730, strategy="delete",
)
# The guest's own captured contacts: 730 days without use (04 spec 3.10).
# OTA-provenance and guest_stay points are purged by the journeys' contact
# purge at usable_until, which this entry does not cover.
privacy.register_pii(
    "guests", "gst_contact_points",
    subject_columns={"value_hmac": "hmac", "value_enc": "contact"},
    ts_column="last_used_at", retention_days=730, strategy="delete", where="provenance = 'guest_direct'",
)
# Roster stays carry the GuestModal identities (contact_hmacs).
privacy.register_pii(
    "guests", "gst_stays",
    subject_columns={"contact_hmacs": "hmac", "room": "room"},
    ts_column="updated_at", retention_days=730, strategy="delete", where="origin = 'roster'",
)
# Registration-card evidence: a high-risk class (04 spec 3.4). Deleted with
# the contact (contacts.forget_contact); this is the retention backstop.
privacy.register_pii(
    "guests", "files",
    subject_columns={"data": "consent_evidence"},
    ts_column="created_at", retention_days=730, strategy="delete", where=f"purpose = '{EVIDENCE_PURPOSE}'",
)
