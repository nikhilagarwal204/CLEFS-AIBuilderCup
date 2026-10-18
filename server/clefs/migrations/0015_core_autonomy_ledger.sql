-- 0015_core_autonomy_ledger: the promotion-suggestion lifecycle of the
-- autonomy dial (04 spec 1.2 and 1.5).
--
--   action_autonomy_events.status             open | accepted | dismissed | superseded,
--                                             promotion_suggested rows only
--   action_autonomy_events.resolves_event_id  a promotion_accepted / promotion_dismissed row
--                                             points at the suggestion it answers
--   one open suggestion per (hotel, action type), enforced by a partial unique index
--
-- Why a new file: 0011 is applied on the dev and verify databases, and an
-- applied migration is never edited. Before this file the "open" suggestion was
-- inferred as "the type's latest dial event", so any later event (even a
-- no-op PUT) silently killed it, and nothing stopped two concurrent review
-- runs from opening two. The backfill below replays exactly that old rule, so
-- no suggestion changes meaning on upgrade.
--
-- Idempotent throughout, and free of percent signs (the runner may hand the
-- file to the driver with parameters).

-- ─── Columns ───

ALTER TABLE action_autonomy_events ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE action_autonomy_events ADD COLUMN IF NOT EXISTS resolves_event_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'action_autonomy_events'::regclass AND conname = 'action_autonomy_events_resolves_fk'
    ) THEN
        ALTER TABLE action_autonomy_events ADD CONSTRAINT action_autonomy_events_resolves_fk
            FOREIGN KEY (resolves_event_id) REFERENCES action_autonomy_events (id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'action_autonomy_events'::regclass AND conname = 'action_autonomy_events_status_check'
    ) THEN
        ALTER TABLE action_autonomy_events ADD CONSTRAINT action_autonomy_events_status_check
            CHECK (status IN ('open', 'accepted', 'dismissed', 'superseded'));
    END IF;
END
$$;

-- ─── Backfill (the old "latest event" rule, made explicit) ───

-- An accept or a dismissal answered the suggestion right before it: the old
-- code refused both unless the suggestion was the type's latest event.
UPDATE action_autonomy_events r
SET resolves_event_id = (
    SELECT s.id FROM action_autonomy_events s
    WHERE s.hotel_slug = r.hotel_slug AND s.action_type = r.action_type
      AND s.kind = 'promotion_suggested' AND s.id < r.id
    ORDER BY s.id DESC
    LIMIT 1
)
WHERE r.kind IN ('promotion_accepted', 'promotion_dismissed') AND r.resolves_event_id IS NULL;

UPDATE action_autonomy_events s
SET status = CASE
    WHEN EXISTS (SELECT 1 FROM action_autonomy_events r
                 WHERE r.resolves_event_id = s.id AND r.kind = 'promotion_accepted') THEN 'accepted'
    WHEN EXISTS (SELECT 1 FROM action_autonomy_events r
                 WHERE r.resolves_event_id = s.id AND r.kind = 'promotion_dismissed') THEN 'dismissed'
    WHEN EXISTS (SELECT 1 FROM action_autonomy_events n
                 WHERE n.hotel_slug = s.hotel_slug AND n.action_type = s.action_type AND n.id > s.id) THEN 'superseded'
    ELSE 'open'
END
WHERE s.kind = 'promotion_suggested' AND s.status IS NULL;

-- ─── Invariants ───

-- status belongs to suggestions and every suggestion has one.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'action_autonomy_events'::regclass AND conname = 'action_autonomy_events_status_kind_check'
    ) THEN
        ALTER TABLE action_autonomy_events ADD CONSTRAINT action_autonomy_events_status_kind_check
            CHECK ((kind = 'promotion_suggested') = (status IS NOT NULL));
    END IF;
END
$$;

-- At most one open suggestion per hotel and type: core.autonomy_review may run
-- from the worker and a cron tick at once, and the loser must fail, not add a
-- second card to the brief.
CREATE UNIQUE INDEX IF NOT EXISTS action_autonomy_events_open_suggestion_idx
    ON action_autonomy_events (hotel_slug, action_type)
    WHERE kind = 'promotion_suggested' AND status = 'open';

-- The 30-day quiet period after a dismissal looks these up per type.
CREATE INDEX IF NOT EXISTS action_autonomy_events_kind_idx
    ON action_autonomy_events (hotel_slug, action_type, kind, at DESC);
