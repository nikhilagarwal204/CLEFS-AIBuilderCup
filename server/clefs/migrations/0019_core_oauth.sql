-- 0019_core_oauth: hotel connections to third-party accounts (04 spec F6).
--
-- One connection per (hotel, provider): Google Business Profile (shared by
-- Visibility and Reviews, so an owner connects Google once), Xero, QuickBooks
-- Online and Zoho Books. Tokens are sealed with clefs.core.privacy.seal
-- (AES-256-GCM, PII_ENC_KEY); the database never holds a usable token.
--
-- Nothing here uses a percent sign, so the file is safe whatever way the
-- runner hands it to the driver.

-- ─── Connections ───

CREATE TABLE IF NOT EXISTS oauth_connections (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    provider           TEXT NOT NULL,             -- google_business | xero | qbo | zoho
    -- The provider-side account: QBO realmId from the callback; the Xero
    -- tenant id once Finance has read /connections (set_account_ref).
    account_ref        TEXT,
    scopes             TEXT[] NOT NULL DEFAULT '{}',
    access_token_enc   BYTEA,                     -- privacy.seal; NULL once revoked or disconnected
    refresh_token_enc  BYTEA,
    expires_at         TIMESTAMPTZ,               -- of the access token
    -- active: usable. expired: no refresh token left. revoked: the provider
    -- refused the refresh (invalid_grant). disconnected: a person removed it.
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'expired', 'revoked', 'disconnected')),
    -- Provider extras that are not secrets (Zoho accounts server and API
    -- domain, the QBO environment).
    meta               JSONB NOT NULL DEFAULT '{}',
    last_error         TEXT,
    connected_by       BIGINT REFERENCES users (id) ON DELETE SET NULL,
    connected_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at       TIMESTAMPTZ,
    last_refreshed_at  TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, provider)
);

-- ─── Pending authorisations ───
-- The raw state only travels in the provider redirect; the row is found by
-- its sha256, deleted on first use (single use) and refused after expires_at.
-- The callback takes the hotel from here, never from its query string.

CREATE TABLE IF NOT EXISTS oauth_states (
    state_hash         BYTEA PRIMARY KEY,
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    provider           TEXT NOT NULL,
    user_id            BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    next_path          TEXT,
    -- The exact redirect_uri of the authorize request (the token exchange
    -- must repeat it byte for byte) and the sealed PKCE verifier.
    redirect_uri       TEXT NOT NULL,
    code_verifier_enc  BYTEA,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at         TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS oauth_states_expires_idx ON oauth_states (expires_at);
