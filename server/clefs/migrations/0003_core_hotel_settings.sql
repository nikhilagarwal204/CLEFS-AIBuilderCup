-- 0003_core_hotel_settings: typed core settings on hotels, plus per-module settings.
--
-- Existing rows keep India defaults (timezone Asia/Kolkata, INR, en-IN) because
-- every hotel that exists today is an Indian pilot or prospect. New UAE hotels
-- are created with explicit values by `python -m clefs.cli create-hotel`.
-- Modules never ALTER hotels: anything module-specific goes in
-- hotel_module_settings.settings, validated by that module's Pydantic model.

ALTER TABLE hotels
    ADD COLUMN IF NOT EXISTS country CHAR(2) NOT NULL DEFAULT 'IN',
    ADD COLUMN IF NOT EXISTS emirate TEXT,
    ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    ADD COLUMN IF NOT EXISTS currency CHAR(3) NOT NULL DEFAULT 'INR',
    ADD COLUMN IF NOT EXISTS locale TEXT NOT NULL DEFAULT 'en-IN',
    ADD COLUMN IF NOT EXISTS guest_languages TEXT[] NOT NULL DEFAULT '{en}',
    -- ISO weekdays (Mon=1 .. Sun=7). UAE weekend is Sat-Sun since 2022.
    ADD COLUMN IF NOT EXISTS weekend_days SMALLINT[] NOT NULL DEFAULT '{6,7}',
    ADD COLUMN IF NOT EXISTS lat NUMERIC(9,6),
    ADD COLUMN IF NOT EXISTS lng NUMERIC(9,6),
    ADD COLUMN IF NOT EXISTS address TEXT,
    -- A night audit rolls the business date at this local time, not at midnight.
    ADD COLUMN IF NOT EXISTS business_day_cutoff TIME NOT NULL DEFAULT '04:00',
    ADD COLUMN IF NOT EXISTS brief_time_local TIME NOT NULL DEFAULT '07:00',
    ADD COLUMN IF NOT EXISTS tax_profile JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS whatsapp_phone_number_id TEXT,
    ADD COLUMN IF NOT EXISTS whatsapp_display_number TEXT,
    ADD COLUMN IF NOT EXISTS front_desk_phone TEXT,
    ADD COLUMN IF NOT EXISTS inbound_email_alias TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS legacy_open_access BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hotels_status_check') THEN
        ALTER TABLE hotels ADD CONSTRAINT hotels_status_check
            CHECK (status IN ('active', 'pilot', 'demo', 'archived'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hotels_country_check') THEN
        ALTER TABLE hotels ADD CONSTRAINT hotels_country_check CHECK (country ~ '^[A-Z]{2}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hotels_currency_check') THEN
        ALTER TABLE hotels ADD CONSTRAINT hotels_currency_check CHECK (currency ~ '^[A-Z]{3}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hotels_emirate_check') THEN
        ALTER TABLE hotels ADD CONSTRAINT hotels_emirate_check
            CHECK (emirate IS NULL OR emirate IN ('dubai', 'abu_dhabi', 'sharjah', 'ajman', 'rak', 'fujairah', 'uaq'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hotels_weekend_days_check') THEN
        ALTER TABLE hotels ADD CONSTRAINT hotels_weekend_days_check
            CHECK (weekend_days <@ '{1,2,3,4,5,6,7}'::SMALLINT[]);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS hotels_inbound_email_alias_key
    ON hotels (inbound_email_alias) WHERE inbound_email_alias IS NOT NULL;

-- Grandfathering: every hotel that exists when this runs is a legacy Indian
-- property whose dashboard and staff phone pages work without login today.
-- They stay open until the Service lane moves them onto paired devices; hotels
-- created after this migration default to FALSE (locked).
UPDATE hotels SET legacy_open_access = TRUE;

CREATE TABLE IF NOT EXISTS hotel_module_settings (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    module      TEXT NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    settings    JSONB NOT NULL DEFAULT '{}',
    updated_by  BIGINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, module)
);

-- Printed legacy QR codes carry no ?k= key. These hotels already expose the
-- roster through legacy_open_access, so keeping per-room personalization on for
-- them changes nothing about exposure and keeps the Indian pilot's /stay page
-- behaving as it does today. New hotels default to no PII without a key.
INSERT INTO hotel_module_settings (hotel_slug, module, settings)
SELECT slug, 'concierge', '{"legacy_qr_personalization": true}'::JSONB
FROM hotels
WHERE legacy_open_access
ON CONFLICT (hotel_slug, module) DO NOTHING;
