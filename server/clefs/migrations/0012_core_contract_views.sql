-- 0012_core_contract_views: read-only contract views other modules build on.
--
-- service_requests_v1 is what Amenities, Brief and Ask read instead of the
-- legacy staff_requests table (01 spec E7). The Service module may
-- CREATE OR REPLACE it (for example to read its new state/source/floor columns)
-- but must keep these columns, in this order, with these meanings:
--
--   id, hotel_slug, room, floor, request_type, department, priority, status,
--   source, created_at, acknowledged_at, resolved_at, assignee_label
--
-- Legacy timestamps are epoch-ms BIGINT (Canon 3); the view exposes TIMESTAMPTZ.
-- floor: the room register first, else inferred from the number (502 -> 5,
-- 1204 -> 12), which is what core.rooms.infer_floor does.
-- acknowledged_at: legacy accepted_at, the moment a person took the request.

CREATE OR REPLACE VIEW service_requests_v1 AS
SELECT
    r.id,
    r.hotel_slug,
    r.room,
    COALESCE(
        hr.floor,
        CASE WHEN substring(r.room FROM '^[0-9]+') ~ '^[0-9]{3,}$'
             THEN (left(substring(r.room FROM '^[0-9]+'), length(substring(r.room FROM '^[0-9]+')) - 2))::BIGINT::TEXT
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
LEFT JOIN hotel_rooms hr ON hr.hotel_slug = r.hotel_slug AND hr.room_number = r.room;
