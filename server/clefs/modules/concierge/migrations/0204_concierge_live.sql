-- ─── Guest Concierge v2: the concierge.live switch (03 spec 5.4) ───
-- One row per hotel that is switched ON or OFF. Written only by
-- PUT /hotels/{slug}/concierge/live, which runs Trust's readiness gate for
-- ON (live.py), and by the grandfathering below. No row = not live.
--
-- WHY a table and not a key in hotel_module_settings['concierge']: that
-- JSON is also written by the generic PUT /module-settings/concierge
-- (core.settings.manage, no gate), so a flag stored there could be flipped
-- ON without the readiness gate.

CREATE TABLE IF NOT EXISTS cc_live (
    hotel_slug  TEXT PRIMARY KEY REFERENCES hotels (slug) ON DELETE CASCADE,
    live        BOOLEAN NOT NULL,
    -- 'switch' (PUT /live) or 'grandfathered' (this migration).
    source      TEXT NOT NULL DEFAULT 'switch' CHECK (source IN ('switch', 'grandfathered')),
    overridden  BOOLEAN NOT NULL DEFAULT FALSE,   -- ON through Trust's owner override
    run_id      TEXT,                              -- the readiness run it passed on, when any
    changed_by  BIGINT,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Grandfathering: every hotel that predates the rebuild (legacy_open_access,
-- set TRUE for all of them by 0003) was already serving guests, so it stays
-- live. Captured once, here, rather than read from legacy_open_access at
-- runtime: an owner locking the dashboard down later must not silently take
-- the guest concierge offline. Hotels created after this migration start
-- with no row (not live) and pass the gate once.
INSERT INTO cc_live (hotel_slug, live, source)
SELECT slug, TRUE, 'grandfathered' FROM hotels WHERE legacy_open_access
ON CONFLICT (hotel_slug) DO NOTHING;
