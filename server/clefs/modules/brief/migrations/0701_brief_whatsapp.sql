-- Owner Brief on WhatsApp (02 spec 6.2, Canon: WhatsApp; 04 spec 3.4/4.6 template doors).
-- Sending, pacing, the 24 h window, webhook dedupe and statuses are the
-- foundation's (clefs.notify: outbound_messages, inbound_messages,
-- whatsapp_windows). Brief keeps only what the foundation lacks: the Meta
-- template registry, the WABA a hotel connected, owner opt-in, the button
-- nonces, the Adjust conversation state and the delivery fallback ledger.

-- ─── Accounts: a hotel's own WhatsApp Business account (NULL row = platform, from settings) ───

CREATE TABLE IF NOT EXISTS brf_wa_accounts (
    hotel_slug          TEXT PRIMARY KEY REFERENCES hotels (slug),
    waba_id             TEXT NOT NULL,
    phone_number_id     TEXT,
    display_phone       TEXT,
    quality             TEXT,                               -- GREEN | YELLOW | RED | UNKNOWN (Meta)
    tier                TEXT,                               -- messaging limit tier (Meta)
    billing_ready       BOOLEAN,                            -- payment method on the WABA (04 spec 3.5); NULL = unknown
    billing_checked_at  TIMESTAMPTZ,
    connected_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Templates: what we submitted and what Meta says about it ───

CREATE TABLE IF NOT EXISTS brf_wa_templates (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT REFERENCES hotels (slug),       -- NULL = the platform WABA (owner_brief_v1, ...)
    name               TEXT NOT NULL,
    language           TEXT NOT NULL,
    category_expected  TEXT NOT NULL CHECK (category_expected IN ('UTILITY', 'MARKETING', 'AUTHENTICATION')),
    category           TEXT,                                -- Meta's current category (may be recategorised)
    status             TEXT NOT NULL DEFAULT 'DRAFT',       -- DRAFT | PENDING | APPROVED | REJECTED | PAUSED | DISABLED | ...
    quality            TEXT,                                -- GREEN | YELLOW | RED | UNKNOWN
    rejected_reason    TEXT,
    components         JSONB NOT NULL DEFAULT '[]',
    meta_template_id   TEXT,
    submitted_at       TIMESTAMPTZ,
    last_synced_at     TIMESTAMPTZ,
    alerted_state      TEXT,                                -- "<status>|<category>" last alerted, so each change alerts once
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS brf_wa_templates_uniq
    ON brf_wa_templates ((COALESCE(hotel_slug, '')), name, language);

-- ─── Contacts: owner and manager opt-in to the WhatsApp brief ───

CREATE TABLE IF NOT EXISTS brf_wa_contacts (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    user_id         BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    phone_e164      TEXT NOT NULL,
    opted_in_at     TIMESTAMPTZ,
    opt_in_method   TEXT,                                   -- console | whatsapp | account
    categories      TEXT[] NOT NULL DEFAULT '{brief}',
    opted_out_at    TIMESTAMPTZ,
    opt_out_method  TEXT,                                   -- console | whatsapp_stop
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, user_id)
);

-- ─── Nonces: one per issued button, bound to its recipient ───
-- base32(HMAC(key, action_id|verb|run_id|user_id|exp))[:12]. State-changing
-- verbs are single-use through core actions.decide(nonce=...) (action_events
-- nonce index); used_at records it here too for the navigation verbs' audit.

CREATE TABLE IF NOT EXISTS brf_wa_nonces (
    nonce       TEXT PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    user_id     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    run_id      BIGINT REFERENCES brf_runs (id) ON DELETE CASCADE,
    action_id   BIGINT REFERENCES actions (id) ON DELETE CASCADE,
    item_no     SMALLINT,
    verb        TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS brf_wa_nonces_expiry_idx ON brf_wa_nonces (expires_at);

-- ─── Sessions: the one open question per person (AWAITING_ADJUST, ...) ───

CREATE TABLE IF NOT EXISTS brf_wa_sessions (
    user_id     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    state       TEXT NOT NULL CHECK (state IN ('awaiting_adjust')),
    data        JSONB NOT NULL DEFAULT '{}',                -- {action_id, field, min_minor, max_minor, currency, nonce}
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Deliveries: each WhatsApp brief send, watched for the fallback ───

CREATE TABLE IF NOT EXISTS brf_deliveries (
    id            BIGSERIAL PRIMARY KEY,
    run_id        BIGINT NOT NULL REFERENCES brf_runs (id) ON DELETE CASCADE,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    user_id       BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    channel       TEXT NOT NULL CHECK (channel IN ('whatsapp')),
    outbox_id     BIGINT,                                   -- outbound_messages.id (no FK: notify owns it)
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'delivered', 'fell_back', 'fallback_failed')),
    reason        TEXT,                                     -- why it fell back: failed | not_delivered_30m
    fallback      JSONB NOT NULL DEFAULT '[]',              -- [{channel, ok, status, outbox_id}]
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ,
    UNIQUE (run_id, user_id, channel)
);
CREATE INDEX IF NOT EXISTS brf_deliveries_pending_idx ON brf_deliveries (created_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS brf_deliveries_outbox_idx ON brf_deliveries (outbox_id) WHERE status = 'pending';
