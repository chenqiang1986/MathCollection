"""Per-user SQLite DB initialization.

Apply [schema.sql](schema.sql) to the current user's index DB and backfill
the `problems` table from their JSON files if empty. Caller must have
already bound the user context via `storage.set_current_user(...)`.
"""

import json
import sqlite3
from pathlib import Path

from lib.storage.paths import index_path, problems_dir
from lib.storage.sql_index import _connect, _upsert_index_row
from lib.storage.vocab import Problem

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


def init_user() -> None:
    db = index_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        # Migrate first so subsequent CREATE INDEX statements in schema.sql
        # that reference new columns don't fail on legacy DBs.
        _migrate_add_subcategory(conn)
        conn.executescript(SCHEMA_FILE.read_text())
    _backfill_problems()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _migrate_add_subcategory(conn: sqlite3.Connection) -> None:
    """Add subcategory columns to DBs created before the two-layer schema.
    SQLite has no `ADD COLUMN IF NOT EXISTS`, so check PRAGMA first.
    Runs before schema.sql is (re-)applied, so the new CREATE INDEX
    statements in schema.sql can safely reference the new columns."""
    if _table_exists(conn, "problems"):
        problem_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(problems)").fetchall()
        }
        if "subcategory" not in problem_cols:
            conn.execute(
                "ALTER TABLE problems ADD COLUMN subcategory TEXT NOT NULL DEFAULT ''"
            )
    if _table_exists(conn, "category_edits"):
        edit_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(category_edits)").fetchall()
        }
        if "from_subcategory" not in edit_cols:
            conn.execute(
                "ALTER TABLE category_edits "
                "ADD COLUMN from_subcategory TEXT NOT NULL DEFAULT ''"
            )
        if "to_subcategory" not in edit_cols:
            conn.execute(
                "ALTER TABLE category_edits "
                "ADD COLUMN to_subcategory TEXT NOT NULL DEFAULT ''"
            )


def _backfill_problems() -> None:
    pdir = problems_dir()
    if not pdir.exists():
        return
    with _connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0] > 0:
            return
        for p in sorted(pdir.glob("*.json")):
            try:
                data = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            _upsert_index_row(conn, Problem.from_dict(data))
