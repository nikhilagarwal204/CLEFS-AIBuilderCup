-- ─── Guest Concierge v2: structured bilingual knowledge base ───
-- 03 spec 2.5 (kb_sections, kb_entries, kb_revisions, kb_unknowns) under the
-- Canon keys: hotel_slug tenant key, TIMESTAMPTZ, cc_ prefix. 04 spec 5.5/7.2
-- adds fact_key, the closed vocabulary Visibility diffs against the web and
-- Google (price_band is Visibility-only and never stored here).
--
-- Why entries carry a status instead of an `approved` boolean: an entry can
-- be pending (drafted by a person, the legacy import or Visibility), approved
-- (the only state the prompt and kb_facts read), rejected, or archived (an
-- approved entry superseded by a newer one for the same fact, kept for the
-- audit trail and for kb_entry_status on an old proposal).

CREATE TABLE IF NOT EXISTS cc_kb_sections (
    id          BIGSERIAL PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    key         TEXT NOT NULL,
    title_en    TEXT NOT NULL,
    title_ar    TEXT,
    sort        INT NOT NULL DEFAULT 0,
    -- Required sections gate the Trust readiness check (03 spec 5.4).
    required    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, key)
);

-- One row per import run (legacy hotels.knowledge_base text, pasted text or
-- a PDF). Entries it creates point back here so the owner approves a whole
-- import in one step, which flips the hotel to kb_mode = structured.
CREATE TABLE IF NOT EXISTS cc_kb_imports (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    source           TEXT NOT NULL CHECK (source IN ('legacy_text', 'text', 'pdf')),
    status           TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed', 'approved')),
    file_id          TEXT,
    text_chars       INT,
    method           TEXT,            -- ai | paragraphs (the deterministic fallback)
    entries_created  INT NOT NULL DEFAULT 0,
    error            TEXT,
    requested_by     BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    approved_at      TIMESTAMPTZ,
    approved_by      BIGINT
);
CREATE INDEX IF NOT EXISTS cc_kb_imports_hotel_idx ON cc_kb_imports (hotel_slug, created_at DESC);

CREATE TABLE IF NOT EXISTS cc_kb_entries (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    section_id       BIGINT REFERENCES cc_kb_sections (id) ON DELETE SET NULL,
    import_id        BIGINT REFERENCES cc_kb_imports (id) ON DELETE SET NULL,
    kind             TEXT NOT NULL DEFAULT 'fact' CHECK (kind IN ('fact', 'faq', 'policy', 'local_place', 'venue_note')),
    question_en      TEXT,
    answer_en        TEXT NOT NULL,
    question_ar      TEXT,
    answer_ar        TEXT,
    tags             TEXT[] NOT NULL DEFAULT '{}',
    fact_key         TEXT CHECK (
        fact_key IS NULL
        OR fact_key IN ('checkin_time', 'checkout_time', 'parking', 'pool', 'breakfast', 'star_rating', 'pets', 'wifi')
    ),
    -- Normalised value for a keyed fact ("15:00", "paid", "true"), the value
    -- Visibility compares; the free-text answer stays what guests hear.
    fact_value       TEXT,
    location_slug    TEXT,            -- venue notes belong to one hotel_locations row
    source           TEXT NOT NULL DEFAULT 'console',
    source_url       TEXT,
    source_label     TEXT,
    valid_from       DATE,
    valid_to         DATE,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'archived')),
    version          INT NOT NULL DEFAULT 1,
    approved_by      BIGINT,
    approved_at      TIMESTAMPTZ,
    rejected_reason  TEXT,
    created_by_type  TEXT,
    created_by_id    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- English stemming for the EN text, the simple config for Arabic (no
    -- Arabic stemmer ships with Postgres). Only immutable expressions are
    -- allowed in a generated column, so tags stay out of it.
    search           TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(question_en, '') || ' ' || coalesce(answer_en, ''))
        || to_tsvector('simple', coalesce(question_ar, '') || ' ' || coalesce(answer_ar, ''))
    ) STORED,
    -- A local recommendation must say where it comes from (03 spec 2.1 block 7).
    CONSTRAINT cc_kb_entries_local_place_source CHECK (kind <> 'local_place' OR source_url IS NOT NULL),
    CONSTRAINT cc_kb_entries_fact_value CHECK (fact_key IS NULL OR fact_value IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS cc_kb_entries_hotel_idx ON cc_kb_entries (hotel_slug, status, section_id);
CREATE INDEX IF NOT EXISTS cc_kb_entries_search_idx ON cc_kb_entries USING GIN (search);
-- One live answer per structured fact: approving a new check-in time
-- archives the old one first (kb.approve_entry), and this index makes a
-- second approved row impossible even under a race.
CREATE UNIQUE INDEX IF NOT EXISTS cc_kb_entries_fact_uq
    ON cc_kb_entries (hotel_slug, fact_key) WHERE status = 'approved' AND fact_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS cc_kb_revisions (
    id          BIGSERIAL PRIMARY KEY,
    entry_id    BIGINT NOT NULL REFERENCES cc_kb_entries (id) ON DELETE CASCADE,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    version     INT NOT NULL,
    change      TEXT NOT NULL,        -- created | edited | approved | rejected | archived
    snapshot    JSONB NOT NULL,
    actor_type  TEXT,
    actor_id    TEXT,
    at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cc_kb_revisions_entry_idx ON cc_kb_revisions (entry_id, id);

-- ─── Unknowns: questions the concierge could not answer ───
-- A cluster per distinct question (normalised text, fuzzy-matched on insert,
-- merged hourly). Five or more in 7 days raise concierge.kb_gap.
CREATE TABLE IF NOT EXISTS cc_kb_unknowns (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    cluster_key      TEXT NOT NULL,
    title            TEXT NOT NULL,   -- the suggested question shown in the console
    samples          JSONB NOT NULL DEFAULT '[]',
    languages        TEXT[] NOT NULL DEFAULT '{}',
    count            INT NOT NULL DEFAULT 0,
    first_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'dismissed', 'merged')),
    merged_into      BIGINT REFERENCES cc_kb_unknowns (id) ON DELETE SET NULL,
    kb_entry_id      BIGINT REFERENCES cc_kb_entries (id) ON DELETE SET NULL,
    answered_at      TIMESTAMPTZ,
    dismissed_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS cc_kb_unknowns_open_idx ON cc_kb_unknowns (hotel_slug, status, last_at DESC);

CREATE TABLE IF NOT EXISTS cc_kb_unknown_events (
    id          BIGSERIAL PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    unknown_id  BIGINT NOT NULL REFERENCES cc_kb_unknowns (id) ON DELETE CASCADE,
    session_id  UUID,
    text        TEXT NOT NULL,        -- the guest's question (PII class: transcript)
    text_norm   TEXT NOT NULL,
    language    TEXT,
    at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cc_kb_unknown_events_idx ON cc_kb_unknown_events (unknown_id, at DESC);
CREATE INDEX IF NOT EXISTS cc_kb_unknown_events_hotel_idx ON cc_kb_unknown_events (hotel_slug, at DESC);
