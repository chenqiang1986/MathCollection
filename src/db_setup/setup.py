"""Per-user SQLite DB initialization.

Apply [schema.sql](schema.sql) to the current user's index DB. When the
schema file declares a version newer than what the DB has been backfilled
to, re-upsert every row from the JSON files so columns added by the new
ALTER statements pick up real values instead of placeholder defaults.
Caller must have already bound the user context via
`storage.set_current_user(...)`.
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
        _apply_schema(conn)
        schema_v, data_v = conn.execute(
            "SELECT schema_version, data_version FROM schema_version"
        ).fetchone()
    if schema_v == data_v:
        return
    _backfill_problems()
    with _connect() as conn:
        conn.execute(
            "UPDATE schema_version SET data_version = ?", (schema_v,)
        )


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Run schema.sql one statement at a time, tolerating duplicate-column
    errors when ALTER TABLE statements are re-executed on an already-
    migrated DB. (SQLite has no `ADD COLUMN IF NOT EXISTS`.)"""
    for stmt in _split_statements(SCHEMA_FILE.read_text()):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise


def _split_statements(sql: str):
    """Split a SQL script on ';'. Strip `--` line comments first so an
    incidental `;` inside a comment doesn't break a statement in half."""
    stripped = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    for raw in stripped.split(";"):
        stmt = raw.strip()
        if stmt:
            yield stmt


def _backfill_problems() -> None:
    pdir = problems_dir()
    if not pdir.exists():
        return
    with _connect() as conn:
        for p in sorted(pdir.glob("*.json")):
            try:
                data = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            _upsert_index_row(conn, Problem.from_dict(data))
