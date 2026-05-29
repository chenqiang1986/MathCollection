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
from common.storage.paths import (
    DATA_DIR,
    current_user_id,
    problems_dir,
    reset_current_user,
    set_current_user,
)
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


def sync_all_users() -> dict[str, int]:
    """Apply the schema, then run `init_user()` for every user directory under
    DATA_DIR — the deploy-time equivalent of the per-login backfill, fanned out
    over all users. The per-user version gate means users already at the
    current schema are a cheap no-op, so this is safe to run on every deploy.
    Returns {user_id: problem-JSON count}."""
    ensure_schema()
    counts: dict[str, int] = {}
    if not DATA_DIR.exists():
        return counts
    for child in sorted(DATA_DIR.iterdir()):
        if not child.is_dir():
            continue
        user = child.name
        token = set_current_user(user)
        try:
            init_user()
            pdir = problems_dir()
            counts[user] = len(list(pdir.glob("*.json"))) if pdir.exists() else 0
        finally:
            reset_current_user(token)
    return counts


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
            _upsert_index_row(conn, Problem.from_dict(data))
