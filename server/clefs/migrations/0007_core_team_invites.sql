-- 0007_core_team_invites: adding an existing account to a hotel is an invitation.
--
-- users is global: one person, one identity (email, WhatsApp number) across
-- every hotel they work with. A manager at hotel B who types the email of an
-- account that already exists must not silently attach it, see its stored
-- name, phone and last sign-in, or edit that identity. So the membership is
-- created as a pending invitation (is_active FALSE, which every access check
-- already requires to be TRUE) and becomes a membership when the person
-- accepts it from their own session.
--
--   pending   invited_at IS NOT NULL AND accepted_at IS NULL (is_active FALSE)
--   accepted  accepted_at IS NOT NULL, or a membership created directly
--             (a new account, a Clefs admin, the CLI): invited_at IS NULL
--
-- invite_name is what the inviter typed. The team list shows it instead of the
-- account's own name until the invitation is accepted.

ALTER TABLE hotel_memberships
    ADD COLUMN IF NOT EXISTS invited_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS invited_by   BIGINT REFERENCES users (id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS invite_name  TEXT,
    ADD COLUMN IF NOT EXISTS accepted_at  TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS hotel_memberships_invites_idx ON hotel_memberships (user_id)
    WHERE invited_at IS NOT NULL AND accepted_at IS NULL;
