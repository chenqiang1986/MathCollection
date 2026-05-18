"""Per-user SQLite queue tracking raw files awaiting agent processing.

The webapp's `/upload` enqueues each saved raw file as `pending`; the
offline worker in `worker/` claims rows one at a time, runs the agent,
and marks them `done` or `failed`. Schema lives in
[../../db_setup/queue_schema.sql](../../db_setup/queue_schema.sql).
"""

import sqlite3
from datetime import datetime, timezone
from typing import Literal, NamedTuple

EnqueueResult = Literal["new", "retried", "skipped"]

from .paths import queue_path, user_dir


def _connect() -> sqlite3.Connection:
    user_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(queue_path())
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QueueItem(NamedTuple):
    filename: str
    with_solution: bool
    status: str
    attempts: int
    last_error: str | None
    queued_at: str
    started_at: str | None
    finished_at: str | None


def _row_to_item(row: sqlite3.Row) -> QueueItem:
    return QueueItem(
        filename=row["filename"],
        with_solution=bool(row["with_solution"]),
        status=row["status"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        queued_at=row["queued_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def enqueue_raw(filename: str, with_solution: bool) -> EnqueueResult:
    """Insert a pending row for `filename`, or re-queue an existing
    `failed`/`done` row so the user can retry or re-run it. Rows already in
    `pending` or `processing` are left alone.

    Returns "new" for a fresh insert, "retried" if an existing terminal row
    was flipped back to pending, "skipped" if the row was already in flight."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT status FROM raw_files WHERE filename = ?", (filename,)
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO raw_files
                    (filename, with_solution, status, queued_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (filename, 1 if with_solution else 0, _now()),
            )
            conn.execute("COMMIT")
            return "new"
        if existing["status"] in ("failed", "done"):
            conn.execute(
                """
                UPDATE raw_files
                SET status = 'pending',
                    with_solution = ?,
                    queued_at = ?,
                    started_at = NULL,
                    finished_at = NULL,
                    last_error = NULL
                WHERE filename = ?
                """,
                (1 if with_solution else 0, _now(), filename),
            )
            conn.execute("COMMIT")
            return "retried"
        conn.execute("COMMIT")
        return "skipped"


def claim_next() -> QueueItem | None:
    """Atomically pick the oldest pending row, flip it to `processing`, and
    bump attempts. Returns the claimed row, or None if nothing pending."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM raw_files
            WHERE status = 'pending'
            ORDER BY queued_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            """
            UPDATE raw_files
            SET status = 'processing',
                attempts = attempts + 1,
                started_at = ?,
                last_error = NULL
            WHERE filename = ?
            """,
            (_now(), row["filename"]),
        )
        conn.execute("COMMIT")
        # Re-read to capture updated columns.
        updated = conn.execute(
            "SELECT * FROM raw_files WHERE filename = ?", (row["filename"],)
        ).fetchone()
    return _row_to_item(updated)


def mark_done(filename: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = 'done', finished_at = ?, last_error = NULL
            WHERE filename = ?
            """,
            (_now(), filename),
        )


def mark_failed(filename: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = 'failed', finished_at = ?, last_error = ?
            WHERE filename = ?
            """,
            (_now(), error, filename),
        )


def revert_to_pending(filename: str, error: str | None = None) -> None:
    """Put a `processing` row back to `pending` without consuming an attempt
    in the user's mind — used when a transient block (rate limit) interrupts
    the run. The attempts counter is left as-is so retry history stays
    visible."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = 'pending', started_at = NULL, last_error = ?
            WHERE filename = ?
            """,
            (error, filename),
        )


def pending_count() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_files WHERE status = 'pending'"
        ).fetchone()
    return int(row["n"])


def status_counts() -> dict[str, int]:
    """Return row counts grouped by status (pending/processing/done/failed)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM raw_files GROUP BY status"
        ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}


def list_items(
    statuses: tuple[str, ...] | None = None, limit: int = 200
) -> list[QueueItem]:
    """Return queue items, optionally filtered to specific statuses.

    Ordered so in-flight work surfaces first: processing → pending (oldest
    first) → failed/done (most recently finished first). `limit` caps the
    total rows returned across all statuses."""
    order = (
        "CASE status "
        "WHEN 'processing' THEN 0 "
        "WHEN 'pending' THEN 1 "
        "WHEN 'failed' THEN 2 "
        "WHEN 'done' THEN 3 "
        "ELSE 4 END"
    )
    params: tuple = ()
    where = ""
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        where = f"WHERE status IN ({placeholders})"
        params = tuple(statuses)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM raw_files
            {where}
            ORDER BY {order},
                     COALESCE(finished_at, started_at, queued_at) DESC,
                     queued_at ASC
            LIMIT ?
            """,
            params + (int(limit),),
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def reclaim_stale_processing() -> int:
    """Move any `processing` rows back to `pending` — used at worker startup
    so rows abandoned by a crashed/killed prior run get retried instead of
    stuck forever. Returns the number of rows reclaimed."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE raw_files
            SET status = 'pending', started_at = NULL,
                last_error = 'reclaimed from stale processing'
            WHERE status = 'processing'
            """
        )
        return cur.rowcount
