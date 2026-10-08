-- 0104_service_rollups_view: the nightly rollup and the v2 reading of the
-- service_requests_v1 contract view (03 spec 1.1 "0006", 01 spec E7).

-- ─── Daily rollups (00:30 hotel time, the previous local day) ───
-- Grouping columns are NOT NULL with '' / 0 for "none", so they can form the
-- primary key and a re-run replaces its day.

CREATE TABLE IF NOT EXISTS svc_daily_rollups (
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    local_date          DATE NOT NULL,
    department_key      TEXT NOT NULL DEFAULT '',
    floor               TEXT NOT NULL DEFAULT '',
    shift_key           TEXT NOT NULL DEFAULT '',
    staff_id            INT NOT NULL DEFAULT 0,
    source              TEXT NOT NULL DEFAULT '',
    language            TEXT NOT NULL DEFAULT '',
    n                   INT NOT NULL DEFAULT 0,
    n_acked             INT NOT NULL DEFAULT 0,
    ack_p50_s           INT,
    ack_p90_s           INT,
    resolve_p50_s       INT,
    n_ack_breach        INT NOT NULL DEFAULT 0,
    n_resolve_breach    INT NOT NULL DEFAULT 0,
    n_reopened          INT NOT NULL DEFAULT 0,
    n_confirmed_yes     INT NOT NULL DEFAULT 0,
    n_confirmed_no      INT NOT NULL DEFAULT 0,
    recovery_cost_minor BIGINT NOT NULL DEFAULT 0,
    currency            CHAR(3),
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, local_date, department_key, floor, shift_key, staff_id, source, language)
);

-- ─── service_requests_v1 (v2 reading) ───
-- Same 13 columns, same order and meanings (the contract; CREATE OR REPLACE
-- cannot drop or reorder columns, and 0012 must still be able to replace this
-- view in the foundation's migration tests). What changes:
--   floor: the room register, then the floor Service stored at intake, then
--          the number (NUMERIC, never BIGINT: a 20-digit guest-typed room
--          must not break every query on the view, CONTRACTS Part 2 0013).
--   source: the real channel (legacy rows keep the column default 'legacy').
--   acknowledged_at: the v2 acknowledgement for v2 rows; for pre-v2 rows
--          (intake_version NULL) the legacy accepted_at, as before.

CREATE OR REPLACE VIEW service_requests_v1 AS
SELECT
    r.id,
    r.hotel_slug,
    r.room,
    COALESCE(
        hr.floor,
        r.floor,
        CASE WHEN substring(r.room FROM '^[0-9]+') ~ '^[0-9]{3,}$'
             THEN (left(substring(r.room FROM '^[0-9]+'), length(substring(r.room FROM '^[0-9]+')) - 2))::NUMERIC::TEXT
        END
    ) AS floor,
    r.type AS request_type,
    r.category AS department,
    r.priority,
    r.status,
    r.source,
    to_timestamp(r."timestamp" / 1000.0) AS created_at,
    CASE
        WHEN r.intake_version IS NULL THEN
            CASE WHEN r.accepted_at IS NOT NULL THEN to_timestamp(r.accepted_at / 1000.0) END
        WHEN r.acknowledged_at IS NOT NULL THEN to_timestamp(r.acknowledged_at / 1000.0)
    END AS acknowledged_at,
    CASE WHEN r.resolved_at IS NOT NULL THEN to_timestamp(r.resolved_at / 1000.0) END AS resolved_at,
    r.assigned_to AS assignee_label
FROM staff_requests r
LEFT JOIN hotel_rooms hr ON hr.hotel_slug = r.hotel_slug AND hr.room_number = r.room;
