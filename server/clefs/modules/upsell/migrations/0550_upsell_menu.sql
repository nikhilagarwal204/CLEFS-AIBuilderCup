-- 0550_upsell_menu: outlets, menu items and upsell pairings (03 spec 2.5,
-- moved to the upsell module by Canon 9).
--
-- Why these shapes differ from the spec sketch: the tenant key is hotel_slug
-- (Canon 1), prices are BIGINT minor units + CHAR(3) currency (Canon 2) and
-- instants are TIMESTAMPTZ (Canon 3). The image is a clefs.files row (UUID),
-- so uploads, dedupe, retention and signed links come from the foundation.

-- ─── Outlets ───

CREATE TABLE IF NOT EXISTS ups_outlets (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    -- Stable key the concierge tools and CSV imports name ("ird", "pool-bar").
    key               TEXT NOT NULL CHECK (key ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
    name_en           TEXT NOT NULL,
    name_ar           TEXT,
    kind              TEXT NOT NULL DEFAULT 'in_room_dining'
                      CHECK (kind IN ('in_room_dining', 'restaurant', 'bar', 'cafe', 'pool', 'spa', 'minibar', 'other')),
    -- Opening windows in hotel-local time: [{"from": "07:00", "to": "23:00",
    -- "days_mask": 127}]. Empty = always open. A window whose "to" is before
    -- its "from" runs past midnight (a 22:00-02:00 bar).
    hours             JSONB NOT NULL DEFAULT '[]',
    delivers_to_room  BOOLEAN NOT NULL DEFAULT TRUE,
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    sort              INT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, key)
);

-- ─── Menu items ───

CREATE TABLE IF NOT EXISTS ups_menu_items (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    outlet_id         BIGINT NOT NULL REFERENCES ups_outlets (id) ON DELETE CASCADE,
    -- The hotel's own code (POS PLU). A CSV import upserts on it.
    sku               TEXT,
    category          TEXT NOT NULL DEFAULT 'Other',
    category_ar       TEXT,
    name_en           TEXT NOT NULL,
    name_ar           TEXT,
    desc_en           TEXT,
    desc_ar           TEXT,
    -- What the menu shows. Whether that figure already contains service
    -- charge, municipality fee and VAT is the hotel's price_display setting.
    price_minor       BIGINT NOT NULL CHECK (price_minor >= 0),
    currency          CHAR(3) NOT NULL,
    image_file_id     UUID REFERENCES files (id) ON DELETE SET NULL,
    -- Item hours inside the outlet's hours; NULL = the outlet's hours.
    available_from    TIME,
    available_to      TIME,
    -- Bit 0 = Monday ... bit 6 = Sunday (ISO weekday - 1). 127 = every day.
    days_mask         SMALLINT NOT NULL DEFAULT 127 CHECK (days_mask BETWEEN 0 AND 127),
    dietary           TEXT[] NOT NULL DEFAULT '{}' CHECK (dietary <@ ARRAY[
                          'vegan', 'vegetarian', 'gluten_free', 'dairy_free', 'halal', 'spicy',
                          'contains_nuts', 'contains_pork', 'contains_shellfish'
                      ]::TEXT[]),
    alcohol           BOOLEAN NOT NULL DEFAULT FALSE,
    prep_min          INT CHECK (prep_min BETWEEN 0 AND 600),
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    sort              INT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ups_menu_items_outlet_idx ON ups_menu_items (hotel_slug, outlet_id, sort, id);
CREATE UNIQUE INDEX IF NOT EXISTS ups_menu_items_sku_uq ON ups_menu_items (outlet_id, sku) WHERE sku IS NOT NULL;

-- ─── Upsell pairings ───

CREATE TABLE IF NOT EXISTS ups_upsell_pairs (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    item_id           BIGINT NOT NULL REFERENCES ups_menu_items (id) ON DELETE CASCADE,
    suggest_item_id   BIGINT NOT NULL REFERENCES ups_menu_items (id) ON DELETE CASCADE,
    kind              TEXT NOT NULL DEFAULT 'pairing' CHECK (kind IN ('pairing', 'upgrade')),
    reason_en         TEXT,
    reason_ar         TEXT,
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    sort              INT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (item_id, suggest_item_id),
    CHECK (item_id <> suggest_item_id)
);
CREATE INDEX IF NOT EXISTS ups_upsell_pairs_hotel_idx ON ups_upsell_pairs (hotel_slug, item_id);
