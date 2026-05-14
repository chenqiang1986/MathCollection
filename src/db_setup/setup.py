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
        conn.executescript(SCHEMA_FILE.read_text())
    _backfill_problems()


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
