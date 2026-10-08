-- 0551_upsell_orders: quotes, orders, order lines and the upsell funnel
-- (03 spec 2.4 place_order and 2.5; Canon 2 money, Canon 9 ownership).
--
-- Why quotes are their own table: place_order(confirm=true) must name a quote
-- the same session saw within 10 minutes. The quote keeps the priced snapshot
-- the guest agreed to, so a confirm never re-prices behind the guest's back,
-- and UNIQUE(quote_id) on orders makes a double tap place one order.

-- ─── Quotes ───

CREATE TABLE IF NOT EXISTS ups_quotes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hotel_slug              TEXT NOT NULL REFERENCES hotels (slug),
    -- Concierge session id, "public:<room>" for the guest menu page, or NULL
    -- for the frozen api.quote (then the room + basket are the session).
    session_ref             TEXT,
    room                    TEXT NOT NULL,
    lines                   JSONB NOT NULL,
    -- sha256 of the normalised basket (item ids and quantities).
    lines_hash              TEXT NOT NULL,
    price_display           TEXT NOT NULL CHECK (price_display IN ('inclusive', 'plus_plus')),
    convention              CHAR(1) NOT NULL CHECK (convention IN ('A', 'B', 'C')),
    rates                   JSONB NOT NULL DEFAULT '{}',
    subtotal_minor          BIGINT NOT NULL,
    service_charge_minor    BIGINT NOT NULL DEFAULT 0,
    municipality_fee_minor  BIGINT NOT NULL DEFAULT 0,
    vat_minor               BIGINT NOT NULL DEFAULT 0,
    total_minor             BIGINT NOT NULL,
    currency                CHAR(3) NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ NOT NULL,
    order_id                BIGINT
);
CREATE INDEX IF NOT EXISTS ups_quotes_room_idx ON ups_quotes (hotel_slug, room, created_at DESC);

-- ─── Orders ───

CREATE TABLE IF NOT EXISTS ups_orders (
    id                      BIGSERIAL PRIMARY KEY,
    hotel_slug              TEXT NOT NULL REFERENCES hotels (slug),
    -- NULL when the basket spans outlets.
    outlet_id               BIGINT REFERENCES ups_outlets (id) ON DELETE SET NULL,
    quote_id                UUID UNIQUE REFERENCES ups_quotes (id),
    session_ref             TEXT,
    room                    TEXT NOT NULL,
    source                  TEXT NOT NULL,
    -- The linked F&B request in staff_requests (Service's table, so no FK).
    request_id              BIGINT,
    status                  TEXT NOT NULL DEFAULT 'placed'
                            CHECK (status IN ('placed', 'accepted', 'preparing', 'delivered', 'cancelled', 'refunded')),
    price_display           TEXT NOT NULL CHECK (price_display IN ('inclusive', 'plus_plus')),
    subtotal_minor          BIGINT NOT NULL,
    service_charge_minor    BIGINT NOT NULL DEFAULT 0,
    municipality_fee_minor  BIGINT NOT NULL DEFAULT 0,
    vat_minor               BIGINT NOT NULL DEFAULT 0,
    total_minor             BIGINT NOT NULL,
    currency                CHAR(3) NOT NULL,
    payment                 TEXT NOT NULL DEFAULT 'room_charge'
                            CHECK (payment IN ('room_charge', 'cash', 'card', 'complimentary')),
    paid_at                 TIMESTAMPTZ,
    -- FALSE: a legacy QR without a room key; staff confirm at the door.
    verified_room           BOOLEAN NOT NULL DEFAULT TRUE,
    notes                   TEXT,
    language                TEXT,
    cancel_reason           TEXT,
    status_changed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ups_orders_hotel_idx ON ups_orders (hotel_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS ups_orders_open_idx ON ups_orders (hotel_slug, status)
    WHERE status IN ('placed', 'accepted', 'preparing');

CREATE TABLE IF NOT EXISTS ups_order_items (
    id                      BIGSERIAL PRIMARY KEY,
    order_id                BIGINT NOT NULL REFERENCES ups_orders (id) ON DELETE CASCADE,
    hotel_slug              TEXT NOT NULL REFERENCES hotels (slug),
    menu_item_id            BIGINT REFERENCES ups_menu_items (id) ON DELETE SET NULL,
    -- Snapshots: an item renamed or repriced later never rewrites history.
    name_snapshot           TEXT NOT NULL,
    name_ar_snapshot        TEXT,
    unit_price_minor        BIGINT NOT NULL,
    qty                     INT NOT NULL CHECK (qty BETWEEN 1 AND 50),
    line_total_minor        BIGINT NOT NULL,
    currency                CHAR(3) NOT NULL,
    notes                   TEXT,
    upsell_event_id         BIGINT,
    -- The retention clock for guest-typed notes (Trust's purge reads it).
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ups_order_items_order_idx ON ups_order_items (order_id);

-- ─── Upsell funnel ───
-- One row per (session, item, state): shown -> accepted -> paid -> fulfilled,
-- or refunded. Repeated "shown" beacons from one session collapse (the
-- partial unique index), so the funnel counts guests, not re-renders.

CREATE TABLE IF NOT EXISTS ups_upsell_events (
    id                      BIGSERIAL PRIMARY KEY,
    hotel_slug              TEXT NOT NULL REFERENCES hotels (slug),
    kind                    TEXT NOT NULL CHECK (kind IN ('pairing', 'upgrade', 'late_checkout', 'spa', 'other')),
    item_id                 BIGINT REFERENCES ups_menu_items (id) ON DELETE SET NULL,
    source_item_id          BIGINT REFERENCES ups_menu_items (id) ON DELETE SET NULL,
    session_ref             TEXT,
    room                    TEXT,
    order_id                BIGINT REFERENCES ups_orders (id) ON DELETE SET NULL,
    state                   TEXT NOT NULL CHECK (state IN ('shown', 'accepted', 'paid', 'fulfilled', 'refunded')),
    value_minor             BIGINT,
    currency                CHAR(3),
    source                  TEXT,
    at                      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS ups_upsell_events_once_uq
    ON ups_upsell_events (hotel_slug, session_ref, item_id, state)
    WHERE session_ref IS NOT NULL AND item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ups_upsell_events_hotel_idx ON ups_upsell_events (hotel_slug, at DESC);
CREATE INDEX IF NOT EXISTS ups_upsell_events_order_idx ON ups_upsell_events (order_id) WHERE order_id IS NOT NULL;
