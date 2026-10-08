-- 0013_core_action_states: Canon amendment K1 (the failed / expired action
-- states and actions.expires_at) and the service_requests_v1 floor fix.
--
-- Why a new file: 0011 and 0012 were already applied to databases (the dev
-- and verify databases hold their first checksums), and an applied migration
-- is never edited. The runner skips an applied version, so a K1 edit to 0011
-- would never reach those databases and every expire or fail would hit the old
-- status CHECK. Every statement here is idempotent: on a database whose 0011
-- already carried K1 (fresh databases while that edit is in the tree) it
-- changes nothing.
--
-- Nothing here uses a percent sign, so the file is safe whatever way the
-- runner hands it to the driver.

-- ─── Action states (Canon K1) ───
-- observed: the dial logged the action and nothing ran (04 spec 1.1).
-- failed:   the executor declined (ok=false) or raised on its last attempt.
-- expired:  still proposed or approved (or snoozed from either) at expires_at.

ALTER TABLE actions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- Drop every CHECK that constrains exactly the status column, whatever it was
-- named, then add the K1 list under the conventional name. Found by column
-- number rather than by name so an unexpected name cannot leave the old,
-- narrower CHECK in place next to the new one.
DO $$
DECLARE
    c record;
BEGIN
    FOR c IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attname = 'status'
        WHERE con.conrelid = 'actions'::regclass AND con.contype = 'c' AND con.conkey = ARRAY[att.attnum]
    LOOP
        EXECUTE 'ALTER TABLE actions DROP CONSTRAINT ' || quote_ident(c.conname);
    END LOOP;
END
$$;

ALTER TABLE actions ADD CONSTRAINT actions_status_check CHECK (status IN (
    -- main path
    'proposed', 'approved', 'assigned', 'in_progress', 'done', 'verified', 'not_verified',
    -- side states (observed: the dial logged it and nothing else, 04 spec 1.1)
    'snoozed', 'rejected', 'cancelled', 'observed',
    -- terminal side states (Canon K1)
    'failed', 'expired',
    -- dispute path
    'submitted', 'awaiting_counterparty', 'rejected_by_counterparty', 'unresolved'));

-- core.actions.expire scans this every 5 minutes. Snoozed rows are included:
-- a quote the owner snoozed past its validity must still lapse. Rebuilt rather
-- than IF NOT EXISTS because an edited 0011 created it with a narrower
-- predicate that the planner could not use for snoozed rows.
DROP INDEX IF EXISTS actions_expiry_idx;
CREATE INDEX actions_expiry_idx ON actions (expires_at)
    WHERE expires_at IS NOT NULL AND status IN ('proposed', 'approved', 'snoozed');

-- ─── service_requests_v1: floor without a bigint overflow ───
-- room is guest-supplied text (ROOM_RE allows 24 digits). The first 0012
-- inferred the floor through ::BIGINT, so one 20-digit "room" made every query
-- on the view fail with "value out of range for type bigint". NUMERIC has no
-- such limit and prints the same text for every real floor.
--
-- Replaced only while the view is still the foundation's legacy shape (the
-- constant source 'legacy' and the BIGINT cast). The Service module may
-- CREATE OR REPLACE this view from its own range (0100-0199); on a database
-- where that already happened this block must not undo it.
DO $$
DECLARE
    def text;
BEGIN
    IF to_regclass('service_requests_v1') IS NULL THEN
        RETURN;
    END IF;
    def := pg_get_viewdef('service_requests_v1'::regclass);
    IF position('::bigint' IN def) = 0 OR position('''legacy''::text AS source' IN def) = 0 THEN
        RETURN;
    END IF;
    EXECUTE $view$
        CREATE OR REPLACE VIEW service_requests_v1 AS
        SELECT
            r.id,
            r.hotel_slug,
            r.room,
            COALESCE(
                hr.floor,
                CASE WHEN substring(r.room FROM '^[0-9]+') ~ '^[0-9]{3,}$'
                     THEN (left(substring(r.room FROM '^[0-9]+'), length(substring(r.room FROM '^[0-9]+')) - 2))::NUMERIC::TEXT
                END
            ) AS floor,
            r.type AS request_type,
            r.category AS department,
            r.priority,
            r.status,
            'legacy'::TEXT AS source,
            to_timestamp(r.timestamp / 1000.0) AS created_at,
            CASE WHEN r.accepted_at IS NOT NULL THEN to_timestamp(r.accepted_at / 1000.0) END AS acknowledged_at,
            CASE WHEN r.resolved_at IS NOT NULL THEN to_timestamp(r.resolved_at / 1000.0) END AS resolved_at,
            r.assigned_to AS assignee_label
        FROM staff_requests r
        LEFT JOIN hotel_rooms hr ON hr.hotel_slug = r.hotel_slug AND hr.room_number = r.room
    $view$;
END
$$;
