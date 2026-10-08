-- 0006_core_rooms_channels: room types, the room register, and booking channels.
--
-- Created in Wave 0a because Service (floors, service state, QR keys) and the
-- domain lane (stays, occupancy, channel economics) both need them in parallel.
-- hotel_rooms carries the union of both designs' columns. The rest of core/stays
-- (reservations, occupancy_daily, ...) lands in the core-domain range 0020-0099.

CREATE TABLE IF NOT EXISTS room_types (
    hotel_slug     TEXT NOT NULL REFERENCES hotels (slug),
    code           TEXT NOT NULL,
    name           TEXT NOT NULL,
    bedrooms       SMALLINT NOT NULL DEFAULT 1,
    max_occupancy  SMALLINT,
    room_count     INT,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, code)
);

CREATE TABLE IF NOT EXISTS hotel_rooms (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    -- TEXT to match staff_requests.room and guest_rooms.room_number ("502", "PH-1")
    room_number     TEXT NOT NULL,
    floor           TEXT,
    floor_inferred  BOOLEAN NOT NULL DEFAULT FALSE,
    wing            TEXT,
    room_type_code  TEXT,
    -- Tourism Dirham is charged per bedroom for hotel apartments.
    bedrooms        SMALLINT NOT NULL DEFAULT 1,
    extension       TEXT,
    service_state   TEXT NOT NULL DEFAULT 'in_service' CHECK (service_state IN ('in_service', 'ooo', 'oos')),
    ooo_reason      TEXT,
    -- Bumping this invalidates every printed QR key for the room.
    qr_key_version  INT NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, room_number),
    -- MATCH SIMPLE: a room with no type code is allowed.
    FOREIGN KEY (hotel_slug, room_type_code) REFERENCES room_types (hotel_slug, code)
);
CREATE INDEX IF NOT EXISTS hotel_rooms_floor_idx ON hotel_rooms (hotel_slug, floor) WHERE is_active;

CREATE TABLE IF NOT EXISTS channels (
    hotel_slug             TEXT NOT NULL REFERENCES hotels (slug),
    -- BOOKING | AGODA | EXPEDIA | DIRECT_WEB | DIRECT_PHONE | WALKIN | CORPORATE | GDS | OTHER | ...
    code                   TEXT NOT NULL,
    name                   TEXT NOT NULL,
    kind                   TEXT NOT NULL DEFAULT 'other'
                           CHECK (kind IN ('ota', 'direct', 'gds', 'wholesale', 'corporate', 'walk_in', 'other')),
    default_collect_model  TEXT CHECK (default_collect_model IS NULL OR default_collect_model IN ('OTA_COLLECT', 'HOTEL_COLLECT')),
    is_active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, code)
);
