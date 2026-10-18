-- Google attribution (04 spec 5.8): GBP website clicks next to the direct
-- bookings tagged utm_source=google, and the OTA commission they avoided.
-- The public lead magnet's vis_public_checks (spec 0906 file) ships with the
-- public part of this lane in its own migration (0907+), so the two parts
-- never race on one file.

CREATE TABLE IF NOT EXISTS vis_attribution_monthly (
    hotel_slug                 TEXT NOT NULL REFERENCES hotels (slug),
    month                      DATE NOT NULL,
    gbp_website_clicks         BIGINT,
    direct_bookings            INT,
    direct_room_revenue_minor  BIGINT,
    commission_avoided_minor   BIGINT,                -- NULL when no reference OTA is known
    reference_ota              TEXT,
    currency                   CHAR(3) NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, month)
);
