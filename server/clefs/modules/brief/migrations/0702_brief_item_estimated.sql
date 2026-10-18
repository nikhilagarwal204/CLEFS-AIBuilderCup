-- Estimated impacts are labelled "est." in the brief (04 spec 4.5: inquiry
-- money at risk; 02 spec 4.3: Demand's "estimated opportunity"). The flag is
-- decided once, when the run is assembled, from the source signal
-- (impact_kind revenue_upside, a Demand signal) and stored with the item so
-- a run re-rendered later (magic page, WhatsApp fallback, email) says the
-- same thing it said when it was built.

ALTER TABLE brf_items ADD COLUMN IF NOT EXISTS impact_estimated BOOLEAN NOT NULL DEFAULT FALSE;
