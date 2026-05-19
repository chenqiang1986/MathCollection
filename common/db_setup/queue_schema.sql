-- Per-user SQLite schema for the offline-worker raw-file queue.
-- Applied by db_setup/setup.py against <data>/<user>/raw_queue.db.
--
-- This DB tracks the lifecycle of files uploaded into data/<user>/raw/
-- so the offline worker (worker/) knows which ones still need agent
-- processing. It is separate from problems_index.db: that one is a
-- derived mirror of the JSON files; this one is authoritative for
-- queue state and cannot be rebuilt from the filesystem alone (it
-- carries with_solution, attempts, errors, timestamps).
--
-- Lifecycle:
--   pending_image_scan      (just uploaded, awaiting scan)
--   processing_image_scan   (scan in flight)
--   pending_problem_solve   (scan persisted partials, awaiting solve)
--   processing_problem_solve (solver running across the partials)
--   done | failed

CREATE TABLE IF NOT EXISTS schema_version (
    schema_version INTEGER NOT NULL
);
INSERT INTO schema_version (schema_version)
    SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM schema_version);

CREATE TABLE IF NOT EXISTS raw_files (
    filename TEXT PRIMARY KEY,
    with_solution INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_status_queued ON raw_files(status, queued_at);

-- v1 -> v2: split single 'pending'/'processing' into per-stage states.
-- Reverts any in-flight pre-v2 row back to the start of the pipeline.
UPDATE raw_files SET status = 'pending_image_scan' WHERE status = 'pending';
UPDATE raw_files SET status = 'pending_image_scan' WHERE status = 'processing';

UPDATE schema_version SET schema_version = 2;
