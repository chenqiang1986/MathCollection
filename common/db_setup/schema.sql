-- Per-user SQLite schema for the problems index and category-edit log.
-- Applied by db_setup/setup.py against <data>/<user>/problems_index.db.
--
-- Schema evolution policy
-- -----------------------
-- The `schema_version` table holds two integers:
--   * SCHEMA_VERSION — the latest version declared by this file.
--   * DATA_VERSION   — the version the on-disk rows were last backfilled to.
-- When they differ, setup.py runs a backfill from the JSON files and then
-- sets DATA_VERSION = SCHEMA_VERSION. To add a new column:
--   1. Append an `ALTER TABLE ... ADD COLUMN` statement after the
--      `CREATE TABLE` that defines the table.
--   2. Bump the SCHEMA_VERSION literal in the final `UPDATE` at the end of
--      this file.
-- Old DBs missing some of those columns pick them up on the next startup.
-- Duplicate-column errors from re-running ALTER on already-migrated DBs
-- are tolerated by the loader in setup.py.

CREATE TABLE IF NOT EXISTS schema_version (
    schema_version INTEGER NOT NULL,
    data_version INTEGER NOT NULL DEFAULT 0
);
INSERT INTO schema_version (schema_version, data_version)
    SELECT 0, 0 WHERE NOT EXISTS (SELECT 1 FROM schema_version);

CREATE TABLE IF NOT EXISTS problems (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    category TEXT NOT NULL,
    -- solve_time_seconds: real measured elapsed seconds (NULL until a
    --   solution is produced).
    -- solve_time_estimated: agent's pre-solve estimate in seconds (0 means
    --   no estimate). Both can co-exist to compare estimate vs. real.
    solve_time_seconds REAL,
    solve_time_estimated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
ALTER TABLE problems ADD COLUMN subcategory TEXT NOT NULL DEFAULT '';
ALTER TABLE problems ADD COLUMN source_exam TEXT NOT NULL DEFAULT 'Unknown';
ALTER TABLE problems ADD COLUMN year TEXT NOT NULL DEFAULT 'Unknown';
ALTER TABLE problems ADD COLUMN has_figure INTEGER NOT NULL DEFAULT 0;
ALTER TABLE problems ADD COLUMN source_image TEXT;
ALTER TABLE problems ADD COLUMN seq_no INTEGER;
ALTER TABLE problems ADD COLUMN subexam TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_category ON problems(category);
CREATE INDEX IF NOT EXISTS idx_category_sub ON problems(category, subcategory);
CREATE INDEX IF NOT EXISTS idx_created_at ON problems(created_at);
CREATE INDEX IF NOT EXISTS idx_solve_time ON problems(solve_time_seconds);
CREATE INDEX IF NOT EXISTS idx_source_exam ON problems(source_exam);
CREATE INDEX IF NOT EXISTS idx_source_exam_subexam ON problems(source_exam, subexam);
CREATE INDEX IF NOT EXISTS idx_year ON problems(year);
CREATE INDEX IF NOT EXISTS idx_has_figure ON problems(has_figure);
CREATE INDEX IF NOT EXISTS idx_source_image_seq ON problems(source_image, seq_no);

CREATE TABLE IF NOT EXISTS category_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id TEXT NOT NULL,
    problem_text TEXT NOT NULL,
    solution TEXT NOT NULL DEFAULT '',
    from_category TEXT NOT NULL,
    to_category TEXT NOT NULL,
    edited_at TEXT NOT NULL
);
ALTER TABLE category_edits ADD COLUMN from_subcategory TEXT NOT NULL DEFAULT '';
ALTER TABLE category_edits ADD COLUMN to_subcategory TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_edits_from ON category_edits(from_category);
CREATE INDEX IF NOT EXISTS idx_edits_from_sub ON category_edits(from_category, from_subcategory);

-- problem_tags: mirror of each problem's `tags` list (source of truth lives
-- in the problem JSON). Fully derived — rebuilt on every upsert/backfill —
-- so it is safe to drop. Drives tag filtering and per-tag usage counts.
CREATE TABLE IF NOT EXISTS problem_tags (
    problem_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (problem_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_problem_tags_tag ON problem_tags(tag);

-- tags: registry of customer-defined tags plus an optional longer comment.
-- Like category_edits, this is authoritative — NOT derivable from problem
-- JSON — so a backfill must never clear it. Tags applied to a problem are
-- auto-registered here (empty comment) if not already present.
CREATE TABLE IF NOT EXISTS tags (
    name TEXT PRIMARY KEY,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Bump the literal below whenever you add a new ALTER above. The next
-- startup will detect DATA_VERSION < SCHEMA_VERSION and trigger a backfill.
UPDATE schema_version SET schema_version = 8;
