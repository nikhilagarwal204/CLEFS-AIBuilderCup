-- 0671_guests_journey_templates_offers: the fixed template library and the
-- owner's offers (04 spec 3.2 and 3.6).
--
-- Templates are seeded per hotel on first use (journeys/templates.py), EN and
-- AR, WhatsApp and email, pre-sorted by the Meta category we expect. A
-- template sends only once the owner approved that version (approved_at) and,
-- for WhatsApp, Meta reports it APPROVED (brief.api.wa_template_info).
--
-- Offers are the owner-approved "campaign" behind J3 and J8. The per-day send
-- cap is the dial's limit (04 spec 1.4), never a column here.

CREATE TABLE IF NOT EXISTS gst_journey_templates (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    step               TEXT NOT NULL CHECK (step IN ('j1', 'j2', 'j3', 'j4', 'j5', 'j7', 'j8')),
    channel            TEXT NOT NULL CHECK (channel IN ('whatsapp', 'email')),
    meta_name          TEXT,                               -- WhatsApp template name at Meta
    version            INT NOT NULL,
    language           TEXT NOT NULL CHECK (language IN ('en', 'ar')),
    category_expected  TEXT NOT NULL CHECK (category_expected IN ('UTILITY', 'MARKETING')),
    category_meta      TEXT,                               -- what Meta says (template_category_check)
    meta_status        TEXT,                               -- APPROVED | PENDING | REJECTED | PAUSED | DISABLED
    quality            TEXT,
    subject            TEXT,                               -- email only
    body               TEXT NOT NULL,
    buttons            JSONB NOT NULL DEFAULT '[]',
    variables          JSONB NOT NULL DEFAULT '[]',        -- [{n, source, max}] in {{n}} order
    lint               JSONB NOT NULL DEFAULT '{}',
    approved_by        BIGINT,
    approved_at        TIMESTAMPTZ,
    submitted_at       TIMESTAMPTZ,
    synced_at          TIMESTAMPTZ,
    active             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, step, channel, language, version)
);

CREATE TABLE IF NOT EXISTS gst_journey_offers (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    step          TEXT NOT NULL CHECK (step IN ('j3', 'j8')),
    title_en      TEXT NOT NULL,
    title_ar      TEXT,
    price_minor   BIGINT,
    discount_pct  NUMERIC(5, 2),
    currency      CHAR(3) NOT NULL,
    code_prefix   TEXT,
    valid_from    DATE NOT NULL,
    valid_to      DATE NOT NULL,
    approved_by   BIGINT,
    approved_at   TIMESTAMPTZ,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (valid_to >= valid_from),
    CHECK (price_minor IS NULL OR price_minor >= 0),
    CHECK (discount_pct IS NULL OR (discount_pct > 0 AND discount_pct <= 100))
);
CREATE INDEX IF NOT EXISTS gst_journey_offers_live_idx ON gst_journey_offers (hotel_slug, step, valid_from, valid_to) WHERE active;
