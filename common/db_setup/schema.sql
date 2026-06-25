-- Postgres schema for MathCollection. A single database backs every user;
-- rows are partitioned by a `user_id` column (the sanitized-email slug from
-- common.storage.paths.current_user_id), not by a per-user database.
--
-- Applied by common.db_setup.setup.ensure_schema against the database in
-- DATABASE_URL, inside the schema named by PG_SCHEMA (default math_collection).
-- ensure_schema creates that schema and sets the search_path before running
-- this file, so the DDL below is unqualified and never names the schema.
-- setup.py runs this file one statement at a time (split on ';').
--
-- Schema evolution policy
-- -----------------------
-- `schema_version` holds one integer: the latest version declared by this
-- file. `user_data_version` holds, per user, the version that user's rows
-- were last backfilled to from the JSON files. When a user's data_version is
-- behind, setup.py re-upserts every problem from that user's JSON files and
-- bumps their row. To add a column: add an `ALTER TABLE ... ADD COLUMN IF NOT
-- EXISTS` after its CREATE TABLE and bump the literal in the final UPDATE.
--
-- The `problems` and `problem_tags` tables are DERIVED from the JSON files and
-- safe to rebuild. `tags` and `category_edits` are AUTHORITATIVE (not
-- derivable from JSON) and must never be cleared by a backfill. `raw_files` is
-- authoritative queue state for the offline worker.

CREATE TABLE IF NOT EXISTS schema_version (
    schema_version INTEGER NOT NULL
);
INSERT INTO schema_version (schema_version)
    SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);

CREATE TABLE IF NOT EXISTS user_data_version (
    user_id TEXT PRIMARY KEY,
    data_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS problems (
    user_id TEXT NOT NULL,
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL DEFAULT '',
    -- solve_time_seconds: real measured elapsed seconds (NULL until a
    --   solution is produced).
    -- solve_time_estimated: agent's pre-solve estimate in seconds (0 means
    --   no estimate). Both can co-exist to compare estimate vs. real.
    solve_time_seconds DOUBLE PRECISION,
    solve_time_estimated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    source_exam TEXT NOT NULL DEFAULT 'Unknown',
    subexam TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT 'Unknown',
    has_figure INTEGER NOT NULL DEFAULT 0,
    source_image TEXT,
    seq_no INTEGER
);
CREATE INDEX IF NOT EXISTS idx_problems_user ON problems(user_id);
CREATE INDEX IF NOT EXISTS idx_category ON problems(user_id, category);
CREATE INDEX IF NOT EXISTS idx_category_sub ON problems(user_id, category, subcategory);
CREATE INDEX IF NOT EXISTS idx_created_at ON problems(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_solve_time ON problems(user_id, solve_time_seconds);
CREATE INDEX IF NOT EXISTS idx_source_exam ON problems(user_id, source_exam);
CREATE INDEX IF NOT EXISTS idx_source_exam_subexam ON problems(user_id, source_exam, subexam);
CREATE INDEX IF NOT EXISTS idx_year ON problems(user_id, year);
CREATE INDEX IF NOT EXISTS idx_has_figure ON problems(user_id, has_figure);
CREATE INDEX IF NOT EXISTS idx_source_image_seq ON problems(user_id, source_image, seq_no);

CREATE TABLE IF NOT EXISTS category_edits (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    problem_text TEXT NOT NULL,
    solution TEXT NOT NULL DEFAULT '',
    from_category TEXT NOT NULL,
    to_category TEXT NOT NULL,
    from_subcategory TEXT NOT NULL DEFAULT '',
    to_subcategory TEXT NOT NULL DEFAULT '',
    edited_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edits_from ON category_edits(user_id, from_category);
CREATE INDEX IF NOT EXISTS idx_edits_from_sub ON category_edits(user_id, from_category, from_subcategory);

-- problem_tags: mirror of each problem's `tags` list (source of truth lives in
-- the problem JSON). Fully derived — rebuilt on every upsert/backfill — so it
-- is safe to drop. Drives tag filtering and per-tag usage counts.
CREATE TABLE IF NOT EXISTS problem_tags (
    user_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (problem_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_problem_tags_tag ON problem_tags(user_id, tag);

-- tags: per-user registry of customer-defined tags plus an optional longer
-- comment. Like category_edits, authoritative — NOT derivable from problem
-- JSON — so a backfill must never clear it. Tags applied to a problem are
-- auto-registered here (empty comment) if not already present.
CREATE TABLE IF NOT EXISTS tags (
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);

-- raw_files: authoritative queue of uploaded sources awaiting worker
-- processing. Cannot be rebuilt from the filesystem (carries with_solution,
-- attempts, errors, timestamps). Lifecycle:
--   pending_image_scan -> processing_image_scan -> pending_problem_solve
--   -> processing_problem_solve -> done | failed
CREATE TABLE IF NOT EXISTS raw_files (
    user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    with_solution INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (user_id, filename)
);
CREATE INDEX IF NOT EXISTS idx_status_queued ON raw_files(user_id, status, queued_at);

-- practice_sets: authoritative saved selections of problems for printing and
-- later manual curation. Unlike `problems`, these rows are not derivable from
-- JSON and must persist across deploys/backfills.
CREATE TABLE IF NOT EXISTS practice_sets (
    user_id TEXT NOT NULL,
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    series_name TEXT NOT NULL DEFAULT '',
    series_key TEXT NOT NULL DEFAULT '',
    requested_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
ALTER TABLE practice_sets ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE practice_sets ADD COLUMN IF NOT EXISTS series_name TEXT NOT NULL DEFAULT '';
ALTER TABLE practice_sets ADD COLUMN IF NOT EXISTS series_key TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_practice_sets_user_id_id ON practice_sets(user_id, id);
CREATE INDEX IF NOT EXISTS idx_practice_sets_user_updated ON practice_sets(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_practice_sets_user_series
    ON practice_sets(user_id, series_key, updated_at);

CREATE TABLE IF NOT EXISTS practice_set_problems (
    user_id TEXT NOT NULL,
    practice_set_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (practice_set_id, problem_id)
);
CREATE INDEX IF NOT EXISTS idx_practice_set_problems_user_set
    ON practice_set_problems(user_id, practice_set_id, position, added_at);
CREATE INDEX IF NOT EXISTS idx_practice_set_problems_user_problem
    ON practice_set_problems(user_id, problem_id);

-- Bump this literal whenever a new ALTER above changes the row shape. The next
-- init_user() detects DATA_VERSION < SCHEMA_VERSION per user and re-backfills.
UPDATE schema_version SET schema_version = 4;
