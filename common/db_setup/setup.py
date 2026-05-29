"""Postgres schema setup and per-user JSON backfill.

`ensure_schema()` applies [schema.sql](schema.sql) once per process (it is
idempotent — every statement is CREATE ... IF NOT EXISTS). `init_user()`
ensures the schema exists, then — if the current user's rows were backfilled
to an older schema version than this file declares — re-upserts every problem
from that user's JSON files so columns added by a newer schema pick up real
values. Caller must have bound the user context via
`storage.set_current_user(...)` first.
"""

import json
from pathlib import Path

from common.storage.db import SCHEMA, connect
from common.storage.paths import current_user_id, problems_dir
from common.storage.sql_index import _upsert_index_row
from common.storage.vocab import Problem

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"

_schema_ready = False


def ensure_schema() -> None:
    """Apply schema.sql once per process. Cheap and idempotent, but guarded
    so we don't replay the DDL on every login / queue poll."""
    global _schema_ready
    if _schema_ready:
        return
    with connect() as conn:
        # Create the target schema and point this connection at it before
        # applying the (schema-unqualified) DDL. SCHEMA is the single source
        # of truth for the name; db.py sets the same value as every pooled
        # connection's search_path.
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
        conn.execute(f'SET search_path TO "{SCHEMA}"')
        for stmt in _split_statements(SCHEMA_FILE.read_text()):
            conn.execute(stmt)
    _schema_ready = True


def init_user() -> None:
    ensure_schema()
    user = current_user_id()
    with connect() as conn:
        schema_v = conn.execute(
            "SELECT schema_version FROM schema_version"
        ).fetchone()["schema_version"]
        row = conn.execute(
            "SELECT data_version FROM user_data_version WHERE user_id = %s",
            (user,),
        ).fetchone()
        data_v = row["data_version"] if row else 0
    if schema_v == data_v:
        return
    _backfill_problems()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO user_data_version (user_id, data_version)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET data_version = excluded.data_version
            """,
            (user, schema_v),
        )


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
    with connect() as conn:
        for p in sorted(pdir.glob("*.json")):
            try:
                data = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if _migrate_solve_time(data):
                p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            _upsert_index_row(conn, Problem.from_dict(data))


def _migrate_solve_time(data: dict) -> bool:
    """Convert legacy `solve_time_estimated: bool` records to the new
    convention where `solve_time_estimated` is an integer seconds estimate
    and `solve_time_seconds` is reserved for real measured elapsed time.
    Returns True when the dict was modified."""
    est = data.get("solve_time_estimated")
    if not isinstance(est, bool):
        return False
    if est:
        st = data.get("solve_time_seconds")
        data["solve_time_estimated"] = int(round(st)) if st is not None else 0
        data["solve_time_seconds"] = None
    else:
        data["solve_time_estimated"] = 0
    return True
