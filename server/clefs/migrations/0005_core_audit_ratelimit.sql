-- 0005_core_audit_ratelimit: append-only, hash-chained audit log + DB-backed rate limits.
--
-- audit_log belongs to the foundation (Canon 4). The Trust lane adds chain
-- verification, the viewer, DSR and retention on top of what is defined here.

CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL PRIMARY KEY,
    hotel_slug   TEXT,                     -- NULL for platform events (e.g. a failed login)
    at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_type   TEXT NOT NULL,            -- user | device | service | anonymous | system
    actor_id     TEXT,
    actor_label  TEXT,
    action       TEXT NOT NULL,            -- "<area>.<verb>", e.g. auth.login, core.settings.update
    entity_type  TEXT,
    entity_id    TEXT,
    before       JSONB,
    after        JSONB,
    ip           TEXT,
    user_agent   TEXT,
    request_id   TEXT,
    meta         JSONB NOT NULL DEFAULT '{}',
    prev_hash    BYTEA,
    row_hash     BYTEA NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_hotel_id_idx ON audit_log (hotel_slug, id);
CREATE INDEX IF NOT EXISTS audit_log_hotel_at_idx ON audit_log (hotel_slug, at DESC);
CREATE INDEX IF NOT EXISTS audit_log_entity_idx ON audit_log (entity_type, entity_id);

-- The one definition of a row's hash. The chain is per hotel_slug (NULL is its
-- own chain): row_hash = sha256(prev_hash || canonical text of the row). Trust's
-- verifier must call this same function rather than re-implementing it.
CREATE OR REPLACE FUNCTION audit_row_hash(prev BYTEA, r audit_log) RETURNS BYTEA
LANGUAGE sql STABLE AS $$
    SELECT sha256(
        COALESCE(prev, ''::BYTEA) ||
        convert_to(concat_ws(E'\x1f',
            r.id::TEXT,
            COALESCE(r.hotel_slug, ''),
            (EXTRACT(EPOCH FROM r.at) * 1000000)::BIGINT::TEXT,
            r.actor_type,
            COALESCE(r.actor_id, ''),
            COALESCE(r.actor_label, ''),
            r.action,
            COALESCE(r.entity_type, ''),
            COALESCE(r.entity_id, ''),
            COALESCE(r.before::TEXT, ''),
            COALESCE(r.after::TEXT, ''),
            COALESCE(r.ip, ''),
            COALESCE(r.user_agent, ''),
            COALESCE(r.request_id, ''),
            r.meta::TEXT
        ), 'UTF8')
    )
$$;

CREATE OR REPLACE FUNCTION audit_log_chain() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    prev BYTEA;
BEGIN
    -- Writers to one hotel's chain are serialized so prev_hash always names the
    -- row that is really before this one. The id is re-drawn under the lock so
    -- id order equals chain order even if another transaction drew an earlier
    -- id and committed later.
    PERFORM pg_advisory_xact_lock(hashtext('clefs_audit:' || COALESCE(NEW.hotel_slug, '')));
    NEW.id := nextval(pg_get_serial_sequence('audit_log', 'id'));
    IF NEW.hotel_slug IS NULL THEN
        SELECT row_hash INTO prev FROM audit_log WHERE hotel_slug IS NULL ORDER BY id DESC LIMIT 1;
    ELSE
        SELECT row_hash INTO prev FROM audit_log WHERE hotel_slug = NEW.hotel_slug ORDER BY id DESC LIMIT 1;
    END IF;
    NEW.prev_hash := prev;
    NEW.row_hash := audit_row_hash(prev, NEW);
    RETURN NEW;
END $$;

-- Append-only. The only way around it is SET LOCAL clefs.audit_maintenance = 'on'
-- inside a transaction, reserved for Trust's retention job (which records what
-- it removed in its own deletion log).
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('clefs.audit_maintenance', true) = 'on' THEN
        IF TG_LEVEL = 'ROW' THEN
            RETURN COALESCE(NEW, OLD);
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only (% blocked)', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE OR REPLACE TRIGGER audit_log_chain_bi
    BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_chain();
CREATE OR REPLACE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();

-- Sliding-window rate limits shared by every API process (the old in-memory
-- dict did not survive Vercel's multiple instances). One row per allowed hit.
CREATE TABLE IF NOT EXISTS rate_limits (
    id   BIGSERIAL PRIMARY KEY,
    key  TEXT NOT NULL,
    at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS rate_limits_key_at_idx ON rate_limits (key, at);
