-- Per-user SQLite schema for the problems index and category-edit log.
-- Applied by db_setup/main.py against <data>/<user>/problems_index.db.

CREATE TABLE IF NOT EXISTS problems (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    category TEXT NOT NULL,
    solve_time_seconds REAL,
    solve_time_estimated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_category ON problems(category);
CREATE INDEX IF NOT EXISTS idx_created_at ON problems(created_at);
CREATE INDEX IF NOT EXISTS idx_solve_time ON problems(solve_time_seconds);

CREATE TABLE IF NOT EXISTS category_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id TEXT NOT NULL,
    problem_text TEXT NOT NULL,
    solution TEXT NOT NULL DEFAULT '',
    from_category TEXT NOT NULL,
    to_category TEXT NOT NULL,
    edited_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edits_from ON category_edits(from_category);
