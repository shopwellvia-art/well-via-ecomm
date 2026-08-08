-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; never run alembic
-- against this DB). Run ONCE, BEFORE deploying the category-hierarchy code: the
-- old deployed backend tolerates the extra column, the new code requires it.
-- Re-running fails harmlessly (duplicate-column / duplicate-key / duplicate-
-- constraint errors) without touching data. Verify first if unsure
-- (SHOW COLUMNS FROM categories LIKE 'parent_id';).
--
-- Rationale:
--   * categories.parent_id adds one level of nesting (parent categories and
--     their subcategories). The service layer enforces the single level; the
--     self-referential FK is the referential-integrity safety net.
--   * ON DELETE RESTRICT backstops the service rule that a parent with
--     children cannot be deleted ("Delete or move its subcategories first").
--   * INT signed, matching categories.id (Integer PK).

ALTER TABLE categories
  ADD COLUMN parent_id INT NULL DEFAULT NULL AFTER image_url;

ALTER TABLE categories
  ADD CONSTRAINT fk_categories_parent
  FOREIGN KEY (parent_id) REFERENCES categories (id) ON DELETE RESTRICT;

-- InnoDB auto-indexes the FK column; this named index supersedes that
-- auto-generated one (MySQL drops it silently) and matches the ORM mapping.
CREATE INDEX ix_categories_parent_id ON categories (parent_id);

-- Rollback:
--   ALTER TABLE categories DROP FOREIGN KEY fk_categories_parent;
--   DROP INDEX ix_categories_parent_id ON categories;
--   ALTER TABLE categories DROP COLUMN parent_id;
