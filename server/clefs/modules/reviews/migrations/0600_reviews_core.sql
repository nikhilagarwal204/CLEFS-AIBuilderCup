-- 0600_reviews_core: review sources, reviews, derived analysis, reply drafts
-- and the competitor place list (03 spec 4.1; 04 spec 1.4 and 5.4).
--
-- Names follow the registry prefix (rev_): the 03 spec's review_sources /
-- reviews / review_derived / review_replies / competitor_sets are
-- rev_sources / rev_reviews / rev_derived / rev_replies / rev_competitors.
-- Tenant key is hotel_slug (Canon 1); timestamps are TIMESTAMPTZ (Canon 3).

-- ─── Sources ───
-- One row per connection: serpapi_google (Google Maps through core.serpapi),
-- gbp (Google Business Profile through core.oauth google_business),
-- tripadvisor (Content API), csv (Booking.com / Agoda extranet exports) and
-- email (forwarded review notifications to <alias>+reviews@).
-- auto_reply_consent records who allowed unattended replies and when: the
-- GBP API policy needs the owner's prior specific and express consent.
CREATE TABLE IF NOT EXISTS rev_sources (
    id                    BIGSERIAL PRIMARY KEY,
    hotel_slug            TEXT NOT NULL REFERENCES hotels(slug),
    kind                  TEXT NOT NULL
                          CHECK (kind IN ('serpapi_google', 'gbp', 'tripadvisor', 'csv', 'email')),
    platform              TEXT NOT NULL,
    label                 TEXT,
    config                JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                TEXT NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'paused', 'error', 'disconnected')),
    auto_reply_consent    BOOLEAN NOT NULL DEFAULT FALSE,
    auto_reply_consent_by BIGINT,
    auto_reply_consent_at TIMESTAMPTZ,
    last_synced_at        TIMESTAMPTZ,
    last_error            TEXT,
    created_by            BIGINT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One live connection per kind: the email and gbp sources are per hotel, and
-- two serpapi rows would bill the same place twice.
CREATE UNIQUE INDEX IF NOT EXISTS rev_sources_hotel_kind_uq
    ON rev_sources (hotel_slug, kind) WHERE kind <> 'csv';
CREATE INDEX IF NOT EXISTS rev_sources_hotel_idx ON rev_sources (hotel_slug);

-- ─── Reviews ───
-- raw_text and author_display are the personal part: nulled at
-- raw_expires_at (ingest + 30 d) by reviews.raw_purge. The rating, dates and
-- rev_derived stay, so trends and metrics survive the purge.
CREATE TABLE IF NOT EXISTS rev_reviews (
    id                  BIGSERIAL PRIMARY KEY,
    hotel_slug          TEXT NOT NULL REFERENCES hotels(slug),
    source_id           BIGINT REFERENCES rev_sources(id) ON DELETE SET NULL,
    platform            TEXT NOT NULL,
    external_id         TEXT NOT NULL,
    author_display      TEXT,
    title               TEXT,
    rating              NUMERIC(4,1),
    rating_scale        INT NOT NULL DEFAULT 5 CHECK (rating_scale IN (5, 10)),
    rating_norm_5       NUMERIC(2,1),
    language            TEXT,
    posted_at           TIMESTAMPTZ,
    raw_text            TEXT,
    raw_expires_at      TIMESTAMPTZ,
    raw_purged_at       TIMESTAMPTZ,
    has_owner_response  BOOLEAN NOT NULL DEFAULT FALSE,
    owner_response_at   TIMESTAMPTZ,
    room_hint           TEXT,
    stay_date_guess     DATE,
    platform_ref        TEXT,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    analyzed_at         TIMESTAMPTZ,
    analysis_attempts   INT NOT NULL DEFAULT 0,
    UNIQUE (hotel_slug, platform, external_id)
);
CREATE INDEX IF NOT EXISTS rev_reviews_posted_idx ON rev_reviews (hotel_slug, posted_at DESC);
CREATE INDEX IF NOT EXISTS rev_reviews_unanalyzed_idx ON rev_reviews (hotel_slug, id) WHERE analyzed_at IS NULL;
CREATE INDEX IF NOT EXISTS rev_reviews_raw_expiry_idx ON rev_reviews (raw_expires_at)
    WHERE raw_text IS NOT NULL OR author_display IS NOT NULL;

-- ─── Derived analysis ───
-- themes: [{key, department, polarity positive|negative|neutral, summary_en}].
-- linked_request_ids: service_requests_v1 ids in the same room, department
-- and stay window ("Guest reported AC twice").
CREATE TABLE IF NOT EXISTS rev_derived (
    review_id          BIGINT PRIMARY KEY REFERENCES rev_reviews(id) ON DELETE CASCADE,
    hotel_slug         TEXT NOT NULL REFERENCES hotels(slug),
    sentiment          NUMERIC(4,3),
    themes             JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary_en         TEXT,
    severity           TEXT NOT NULL DEFAULT 'none'
                       CHECK (severity IN ('none', 'low', 'medium', 'high')),
    negative           BOOLEAN NOT NULL DEFAULT FALSE,
    linked_request_ids BIGINT[] NOT NULL DEFAULT '{}',
    model              TEXT,
    prompt_version     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rev_derived_hotel_idx ON rev_derived (hotel_slug);

-- ─── Reply drafts ───
-- One live draft per review (a redraft replaces the draft in place);
-- copied = a person copied it to post by hand, posted = sent through GBP.
CREATE TABLE IF NOT EXISTS rev_replies (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels(slug),
    review_id         BIGINT NOT NULL REFERENCES rev_reviews(id) ON DELETE CASCADE,
    draft_text        TEXT NOT NULL,
    language          TEXT,
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'approved', 'copied', 'posted', 'discarded')),
    needs_approval    BOOLEAN NOT NULL DEFAULT TRUE,
    edited            BOOLEAN NOT NULL DEFAULT FALSE,
    checks            JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_id         BIGINT,
    approved_by       BIGINT,
    approved_at       TIMESTAMPTZ,
    posted_at         TIMESTAMPTZ,
    posted_via        TEXT CHECK (posted_via IN ('gbp', 'copy', 'dial')),
    platform_reply_id TEXT,
    model             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS rev_replies_live_uq ON rev_replies (review_id) WHERE status <> 'discarded';
CREATE INDEX IF NOT EXISTS rev_replies_hotel_idx ON rev_replies (hotel_slug, status);

-- ─── Competitor places ───
-- A Google place_id may be stored (Places policy); rating and
-- userRatingCount may not, so the widget fetches them live every time.
CREATE TABLE IF NOT EXISTS rev_competitors (
    hotel_slug TEXT PRIMARY KEY REFERENCES hotels(slug),
    place_ids  TEXT[] NOT NULL DEFAULT '{}',
    own_place_id TEXT,
    updated_by BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
