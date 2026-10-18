-- Profit Guard outputs (02 spec 3.1, 3.4-3.7; 00-PLAN Profit Guard): findings
-- and the recovery ledger, dispute packets, detector tuning, the monthly
-- channel economics rollup, the public audit leads, the Tourism Dirham
-- ledger (Canon: td_ledger lives in Profit Guard) and the Leakage Index
-- samples. The data part of the lane ships the schema so every later part
-- (detectors, disputes, public audit) builds on one shape.

-- ─── Findings and the recovery ledger ───

CREATE TABLE IF NOT EXISTS pfg_findings (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    detector           TEXT NOT NULL,                -- L1..L15, T1..T7, TX1, INQ (Inquiry Guard leaks)
    -- sha1(detector|ota|ota_res_id_norm|doc_no or month): re-runs update, never duplicate.
    fingerprint        TEXT NOT NULL,
    ota_code           TEXT,
    reservation_id     BIGINT REFERENCES reservations (id) ON DELETE SET NULL,
    statement_line_id  BIGINT REFERENCES pfg_ota_statement_lines (id) ON DELETE SET NULL,
    subject_ref        TEXT,
    class              TEXT NOT NULL CHECK (class IN ('RECOVERABLE', 'PREVENTABLE', 'STRUCTURAL', 'COMPLIANCE')),
    amount_minor       BIGINT NOT NULL DEFAULT 0,
    currency           CHAR(3) NOT NULL,
    recovered_minor    BIGINT NOT NULL DEFAULT 0,
    confidence         NUMERIC(4, 3) NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
    calc               JSONB NOT NULL DEFAULT '[]',  -- ordered CalcStep list
    evidence           JSONB NOT NULL DEFAULT '[]',  -- row ids, file ids, clause refs
    deadline_at        TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'DETECTED'
                       CHECK (status IN ('DETECTED', 'ACCEPTED', 'DISPUTED', 'RECOVERED', 'REJECTED', 'UNRESOLVED',
                                         'DISMISSED')),
    dismiss_reason     TEXT CHECK (dismiss_reason IS NULL OR dismiss_reason IN ('FALSE_POSITIVE_DATA',
                                   'CONTRACT_EXCEPTION', 'ALREADY_HANDLED', 'TOO_SMALL', 'NOT_WORTH_DISPUTING', 'OTHER')),
    dismiss_note       TEXT,
    -- internal: never signalled or exported (Canon 19 for Inquiry Guard leaks).
    internal_only      BOOLEAN NOT NULL DEFAULT FALSE,
    source             TEXT NOT NULL DEFAULT 'detector',
    signal_id          BIGINT,
    action_id          BIGINT,
    dispute_packet_id  BIGINT,
    first_run_id       TEXT,
    last_run_id        TEXT,
    verified_by        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, fingerprint)
);
CREATE INDEX IF NOT EXISTS pfg_findings_status_idx ON pfg_findings (hotel_slug, status, detector);
CREATE INDEX IF NOT EXISTS pfg_findings_deadline_idx ON pfg_findings (hotel_slug, deadline_at)
    WHERE status IN ('DETECTED', 'ACCEPTED', 'DISPUTED');

CREATE TABLE IF NOT EXISTS pfg_finding_events (
    id            BIGSERIAL PRIMARY KEY,
    finding_id    BIGINT NOT NULL REFERENCES pfg_findings (id) ON DELETE CASCADE,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    from_status   TEXT,
    to_status     TEXT NOT NULL,
    actor_user_id BIGINT REFERENCES users (id) ON DELETE SET NULL,
    actor         TEXT,                               -- 'system', 'statement', a detector key, ...
    channel       TEXT,
    note          TEXT,
    amount_minor  BIGINT,
    evidence      JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS pfg_finding_events_finding_idx ON pfg_finding_events (finding_id, at);

-- ─── Dispute packets ───

CREATE TABLE IF NOT EXISTS pfg_dispute_packets (
    id             BIGSERIAL PRIMARY KEY,
    hotel_slug     TEXT NOT NULL REFERENCES hotels (slug),
    ota_code       TEXT NOT NULL,
    finding_ids    BIGINT[] NOT NULL DEFAULT '{}',
    total_minor    BIGINT NOT NULL DEFAULT 0,
    currency       CHAR(3) NOT NULL,
    status         TEXT NOT NULL DEFAULT 'DRAFT'
                   CHECK (status IN ('DRAFT', 'APPROVED', 'SUBMITTED', 'ACKNOWLEDGED', 'CREDITED', 'PARTIAL',
                                     'REJECTED')),
    html_file_id   UUID REFERENCES files (id) ON DELETE SET NULL,
    pdf_file_id    UUID REFERENCES files (id) ON DELETE SET NULL,
    extranet_text  TEXT,
    email_subject  TEXT,
    email_body     TEXT,
    approved_by    BIGINT REFERENCES users (id) ON DELETE SET NULL,
    approved_at    TIMESTAMPTZ,
    submitted_at   TIMESTAMPTZ,
    follow_up_at   TIMESTAMPTZ,
    outcome_note   TEXT,
    action_id      BIGINT,
    created_by     BIGINT REFERENCES users (id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS pfg_dispute_packets_hotel_idx ON pfg_dispute_packets (hotel_slug, status);

-- ─── Detector tuning (Beta prior feedback, 02 spec 3.4) ───

CREATE TABLE IF NOT EXISTS pfg_detector_tuning (
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    detector          TEXT NOT NULL,
    min_amount_minor  BIGINT,
    confidence_floor  NUMERIC(4, 3),
    prior_a           NUMERIC(8, 3) NOT NULL DEFAULT 4,
    prior_b           NUMERIC(8, 3) NOT NULL DEFAULT 1,
    suppressed_until  TIMESTAMPTZ,
    params            JSONB NOT NULL DEFAULT '{}',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, detector)
);

-- ─── Channel economics (monthly rollup, allocated by stayed night) ───

CREATE TABLE IF NOT EXISTS pfg_channel_economics (
    hotel_slug               TEXT NOT NULL REFERENCES hotels (slug),
    month                    DATE NOT NULL,          -- first day of the month, hotel-local
    channel                  TEXT NOT NULL,
    room_nights              INT NOT NULL DEFAULT 0,
    gross_minor              BIGINT NOT NULL DEFAULT 0,
    room_rev_minor           BIGINT NOT NULL DEFAULT 0,
    sc_minor                 BIGINT NOT NULL DEFAULT 0,
    pass_through_tax_minor   BIGINT NOT NULL DEFAULT 0,
    commission_minor         BIGINT NOT NULL DEFAULT 0,
    commission_on_tax_minor  BIGINT NOT NULL DEFAULT 0,
    payment_cost_minor       BIGINT NOT NULL DEFAULT 0,
    fx_loss_minor            BIGINT NOT NULL DEFAULT 0,
    cancellation_cost_minor  BIGINT NOT NULL DEFAULT 0,
    program_cost_minor       BIGINT NOT NULL DEFAULT 0,
    net_minor                BIGINT NOT NULL DEFAULT 0,
    avg_discount_depth       NUMERIC(7, 4),
    commission_basis         TEXT,                   -- statement | expected | mixed | none
    currency                 CHAR(3) NOT NULL,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, month, channel)
);

-- ─── Public "Audit one OTA statement" leads (02 spec 3.11) ───

-- No hotel yet: the sandbox audit runs with hotel_slug NULL in core ingest.
-- Everything here is purged at 30 days by pg.public_audit_purge.
CREATE TABLE IF NOT EXISTS pfg_lead_audits (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash           TEXT NOT NULL UNIQUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at           TIMESTAMPTZ NOT NULL,
    ip_hash              TEXT,
    file_id              UUID REFERENCES files (id) ON DELETE SET NULL,
    batch_id             BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    ota_code             TEXT,
    status               TEXT NOT NULL DEFAULT 'PENDING',
    report               JSONB NOT NULL DEFAULT '{}',
    lead_name            TEXT,
    hotel_name           TEXT,
    whatsapp_e164        TEXT,
    email                TEXT,
    consent_contact      BOOLEAN NOT NULL DEFAULT FALSE,
    consent_index        BOOLEAN NOT NULL DEFAULT FALSE,
    notified_founder_at  TIMESTAMPTZ,
    unlocked_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS pfg_lead_audits_expiry_idx ON pfg_lead_audits (expires_at);

-- ─── Tourism Dirham ledger (Canon: td_ledger lives in Profit Guard) ───

CREATE TABLE IF NOT EXISTS pfg_td_ledger (
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    month               DATE NOT NULL,
    liable_room_nights  INT NOT NULL DEFAULT 0,
    td_expected_minor   BIGINT NOT NULL DEFAULT 0,
    td_collected_minor  BIGINT NOT NULL DEFAULT 0,
    td_remitted_minor   BIGINT NOT NULL DEFAULT 0,
    currency            CHAR(3) NOT NULL,
    remitted_on         DATE,
    remit_reference     TEXT,
    due_date            DATE,
    status              TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'REMITTED', 'MISMATCH')),
    updated_by          BIGINT REFERENCES users (id) ON DELETE SET NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, month)
);

-- ─── UAE OTA Leakage Index samples (Canon K5: hidden until n >= 30) ───

-- Anonymised: no hotel name, no guest data, only consented audits.
CREATE TABLE IF NOT EXISTS pfg_index_samples (
    id                BIGSERIAL PRIMARY KEY,
    audit_id          UUID REFERENCES pfg_lead_audits (id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ota_code          TEXT NOT NULL,
    emirate           TEXT,
    property_band     TEXT,
    period_month      DATE,
    lines             INT NOT NULL DEFAULT 0,
    commission_minor  BIGINT NOT NULL DEFAULT 0,
    leak_minor        BIGINT NOT NULL DEFAULT 0,
    leak_rate         NUMERIC(9, 6),
    currency          CHAR(3) NOT NULL,
    detector_counts   JSONB NOT NULL DEFAULT '{}'
);
