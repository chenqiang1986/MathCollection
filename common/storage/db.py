"""Postgres connection pool shared by every storage module.

A single Postgres database backs all users; rows are partitioned by a
`user_id` column (the sanitized-email slug from `paths.current_user_id`)
rather than by a per-user database. The DDL lives in
[../db_setup/schema.sql](../db_setup/schema.sql) and is applied by
`common.db_setup.setup.ensure_schema`.

Connection settings come from the environment:
  * DATABASE_URL — libpq connection string (required).
  * PG_SCHEMA    — schema to place every table in (default
                   ``math_collection``). Set as the connection
                   ``search_path`` so SQL never has to qualify names.

`connect()` hands out a pooled connection whose transaction commits when
the ``with`` block exits cleanly (or rolls back on exception) — the same
contract the old ``sqlite3.connect()`` context manager provided.
"""

import atexit
import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA = os.environ.get("PG_SCHEMA", "math_collection")

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL is not set; the Postgres storage layer needs it "
                "(e.g. postgresql://user:pass@localhost:5432/dbname)"
            )
        _pool = ConnectionPool(
            dsn,
            kwargs={
                "row_factory": dict_row,
                "options": f"-c search_path={SCHEMA}",
            },
            min_size=1,
            max_size=10,
            open=True,
        )
        # Close the pool's worker threads before interpreter finalization so
        # short-lived scripts don't emit a thread-join error at shutdown.
        atexit.register(_close_pool)
    return _pool


def _close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def connect():
    """Borrow a pooled connection. Use as ``with connect() as conn:`` —
    the transaction commits at block exit and the connection returns to
    the pool. Rows come back as dicts (``row["col"]``)."""
    return _get_pool().connection()
