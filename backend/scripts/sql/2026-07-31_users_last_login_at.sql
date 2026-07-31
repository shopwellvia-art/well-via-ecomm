-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; never run alembic
-- against this DB). Run ONCE, BEFORE deploying the customers/team split: the
-- currently deployed backend tolerates the extra column, the new code requires
-- it. Re-running fails harmlessly (duplicate-column error) without touching
-- data. Verify first if unsure (SHOW COLUMNS FROM users LIKE 'last_login_at';).
--
-- Rationale:
--   * Nothing in the schema records when an account last signed in. Refresh
--     sessions live in Redis under a TTL, so once they expire the information
--     is gone for good — "when was this account last used" is unanswerable
--     today, for both the staff directory and the customer directory.
--   * Written once per fresh login in AuthService._issue_tokens (the single
--     choke point for password, 2FA-completion and Google logins). Deliberately
--     NOT written on token refresh, which would make it "last API call".
--   * NULL means "has not logged in since this column existed" — it must be
--     rendered as unknown, never as "never logged in".
--   * DATETIME (not TIMESTAMP): the column must accept NULL indefinitely and
--     must never be touched by MySQL's auto-update-on-row-change behaviour.

ALTER TABLE users
  ADD COLUMN last_login_at DATETIME NULL DEFAULT NULL AFTER is_admin;

-- Rollback:
--   ALTER TABLE users DROP COLUMN last_login_at;
