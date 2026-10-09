-- 0133_service_proof: completion photos and their Gemini vision check
-- (05 spec, decisions 8 and 9).
--
-- The photo itself lives in the Cloud Storage bucket named by PROOF_BUCKET
-- (object_key gs://bucket/key; the bucket's lifecycle rule deletes it after
-- 7 days) or, without a bucket, in `data` here (tests, local work), where the
-- daily service.proof_retention job drops it after 7 days. The row stays as
-- the record of what was checked and what Gemini saw.

CREATE TABLE IF NOT EXISTS svc_request_proofs (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    request_id       BIGINT NOT NULL,
    staff_member_id  INT,
    device_id        BIGINT,
    -- the staff app's retry key: the same upload twice is checked once
    client_event_id  UUID UNIQUE,
    content_type     TEXT NOT NULL,
    size_bytes       INT NOT NULL CHECK (size_bytes >= 0),
    sha256           TEXT NOT NULL,
    storage          TEXT NOT NULL CHECK (storage IN ('db', 'gcs')),
    object_key       TEXT,
    data             BYTEA,
    -- verified: Gemini saw the job done; rejected: it did not (the job stays
    -- open); unverified: the check could not run (a supervisor reviews)
    status           TEXT NOT NULL CHECK (status IN ('verified', 'rejected', 'unverified')),
    match            BOOLEAN,
    confidence       REAL,
    reason           TEXT,
    observed         TEXT,
    model            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- when the photo's bytes were deleted (retention)
    deleted_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS svc_request_proofs_request_idx ON svc_request_proofs (request_id, id DESC);
CREATE INDEX IF NOT EXISTS svc_request_proofs_retention_idx ON svc_request_proofs (created_at) WHERE deleted_at IS NULL;
