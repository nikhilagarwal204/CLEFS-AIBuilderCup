-- 0676_guests_message_costs: what each journey message cost (04 spec 3.10,
-- guests.journey_cost). Not in the 04 spec DDL: the metric must be plain,
-- parameterized SQL, and the rule that prices a message (category, window,
-- the 2026-10-01 service charge, the free allowance applied in send order)
-- needs the outbox and the rate card at delivery time. So the price is
-- decided once, when Meta reports the message delivered, and kept here.
--
-- Vendor cost is kept in micro-units of the rate's currency (Canon K3), and
-- once more in micro-units of the hotel's currency for the metric, so a
-- sub-fils rate never rounds to zero when summed.

CREATE TABLE IF NOT EXISTS gst_message_costs (
    id                   BIGSERIAL PRIMARY KEY,
    hotel_slug           TEXT NOT NULL REFERENCES hotels (slug),
    plan_id              BIGINT REFERENCES gst_journey_plans (id),
    message_id           BIGINT REFERENCES gst_journey_messages (id),
    outbound_message_id  BIGINT NOT NULL UNIQUE,
    step                 TEXT,
    channel              TEXT NOT NULL,
    charged_category     TEXT NOT NULL CHECK (charged_category IN ('marketing', 'utility', 'service', 'free')),
    market               CHAR(2),
    rate_micros          BIGINT NOT NULL DEFAULT 0,
    rate_currency        CHAR(3) NOT NULL DEFAULT 'USD',
    cost_micros          BIGINT NOT NULL DEFAULT 0,        -- in the hotel's currency
    currency             CHAR(3) NOT NULL,                 -- the hotel's currency
    charged_at           TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS gst_message_costs_hotel_idx ON gst_message_costs (hotel_slug, charged_at);
