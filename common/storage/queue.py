"""Per-user SQLite queue tracking raw files awaiting agent processing.

Lifecycle per file:

    pending_image_scan      (uploaded; waiting for the orchestrator scan)
        -> processing_image_scan
        -> pending_problem_solve   (partials persisted; waiting for solver)
        -> processing_problem_solve
        -> done | failed

The webapp's `/upload` inserts new rows at `pending_image_scan`; the
offline worker in `worker/` claims rows at either pending state, drives
the appropriate stage, and advances or reverts them. Schema lives in
[../../db_setup/queue_schema.sql](../../db_setup/queue_schema.sql).
"""

import sqlite3
from datetime import datetime, timezone
from typing import Literal, NamedTuple

EnqueueResult = Literal["new", "retried", "skipped"]
Stage = Literal["image_scan", "problem_solve"]

PENDING_IMAGE_SCAN = "pending_image_scan"
PROCESSING_IMAGE_SCAN = "processing_image_scan"
PENDING_PROBLEM_SOLVE = "pending_problem_solve"
PROCESSING_PROBLEM_SOLVE = "processing_problem_solve"
DONE = "done"
FAILED = "failed"

_PENDING_STATES = (PENDING_IMAGE_SCAN, PENDING_PROBLEM_SOLVE)
_PROCESSING_STATES = (PROCESSING_IMAGE_SCAN, PROCESSING_PROBLEM_SOLVE)
_IN_FLIGHT_STATES = _PENDING_STATES + _PROCESSING_STATES

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
    """Insert a fresh row at `pending_image_scan`, or re-queue an existing
    `failed`/`done` row back to that state. Rows already in any in-flight
    state are left alone.

    Returns "new" for a fresh insert, "retried" if a terminal row was
    flipped back to start, "skipped" if the row was already in flight."""
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
                VALUES (?, ?, ?, ?)
                """,
                (filename, 1 if with_solution else 0, PENDING_IMAGE_SCAN, _now()),
            )
            conn.execute("COMMIT")
            return "new"
        if existing["status"] in (FAILED, DONE):
            conn.execute(
                """
                UPDATE raw_files
                SET status = ?,
                    with_solution = ?,
                    queued_at = ?,
                    started_at = NULL,
                    finished_at = NULL,
                    last_error = NULL
                WHERE filename = ?
                """,
                (PENDING_IMAGE_SCAN, 1 if with_solution else 0, _now(), filename),
            )
            conn.execute("COMMIT")
            return "retried"
        conn.execute("COMMIT")
        return "skipped"


def _claim_pending(conn: sqlite3.Connection, pending: str, processing: str) -> QueueItem | None:
    row = conn.execute(
        """
        SELECT * FROM raw_files
        WHERE status = ?
        ORDER BY queued_at ASC
        LIMIT 1
        """,
        (pending,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        """
        UPDATE raw_files
        SET status = ?,
            attempts = attempts + 1,
            started_at = ?,
            last_error = NULL
        WHERE filename = ?
        """,
        (processing, _now(), row["filename"]),
    )
    updated = conn.execute(
        "SELECT * FROM raw_files WHERE filename = ?", (row["filename"],)
    ).fetchone()
    return _row_to_item(updated)


def claim_next_image_scan() -> QueueItem | None:
    """Pick the oldest `pending_image_scan` row, flip it to
    `processing_image_scan`, bump attempts. Returns None if none pending."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = _claim_pending(conn, PENDING_IMAGE_SCAN, PROCESSING_IMAGE_SCAN)
        conn.execute("COMMIT")
    return item


def claim_next_problem_solve() -> QueueItem | None:
    """Pick the oldest `pending_problem_solve` row, flip it to
    `processing_problem_solve`, bump attempts. Returns None if none pending."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = _claim_pending(conn, PENDING_PROBLEM_SOLVE, PROCESSING_PROBLEM_SOLVE)
        conn.execute("COMMIT")
    return item


def advance_to_problem_solve(filename: str) -> None:
    """`processing_image_scan` → `pending_problem_solve`. Resets attempts
    so the solve stage gets its own retry budget independent of scan."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = ?, started_at = NULL, attempts = 0, last_error = NULL
            WHERE filename = ?
            """,
            (PENDING_PROBLEM_SOLVE, filename),
        )


def mark_done(filename: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = ?, finished_at = ?, last_error = NULL
            WHERE filename = ?
            """,
            (DONE, _now(), filename),
        )


def mark_failed(filename: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = ?, finished_at = ?, last_error = ?
            WHERE filename = ?
            """,
            (FAILED, _now(), error, filename),
        )


def retry_failed(filename: str) -> bool:
    """Flip a `failed` row back to `pending_image_scan` so the worker picks
    it up again. Preserves `with_solution`; clears `last_error`/timestamps
    and resets `attempts`. Returns True if a failed row was retried, False
    if the row didn't exist or wasn't in `failed`."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM raw_files WHERE filename = ?", (filename,)
        ).fetchone()
        if row is None or row["status"] != FAILED:
            conn.execute("COMMIT")
            return False
        conn.execute(
            """
            UPDATE raw_files
            SET status = ?,
                queued_at = ?,
                started_at = NULL,
                finished_at = NULL,
                attempts = 0,
                last_error = NULL
            WHERE filename = ?
            """,
            (PENDING_IMAGE_SCAN, _now(), filename),
        )
        conn.execute("COMMIT")
    return True


def revert_image_scan(filename: str, error: str | None = None) -> None:
    """`processing_image_scan` → `pending_image_scan` (no attempt consumed
    from the user's POV — attempts counter stays as-is for visibility)."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = ?, started_at = NULL, last_error = ?
            WHERE filename = ?
            """,
            (PENDING_IMAGE_SCAN, error, filename),
        )


def revert_problem_solve(filename: str, error: str | None = None) -> None:
    """`processing_problem_solve` → `pending_problem_solve`."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = ?, started_at = NULL, last_error = ?
            WHERE filename = ?
            """,
            (PENDING_PROBLEM_SOLVE, error, filename),
        )


def pending_count() -> int:
    """Total files awaiting any stage (image_scan or problem_solve)."""
    with _connect() as conn:
        placeholders = ",".join("?" * len(_PENDING_STATES))
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM raw_files WHERE status IN ({placeholders})",
            _PENDING_STATES,
        ).fetchone()
    return int(row["n"])


def status_counts() -> dict[str, int]:
    """Return row counts grouped by status."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM raw_files GROUP BY status"
        ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}


def list_items(
    statuses: tuple[str, ...] | None = None, limit: int = 200
) -> list[QueueItem]:
    """Return queue items, optionally filtered to specific statuses.

    Ordered so in-flight work surfaces first: any processing → any pending
    (oldest first) → failed/done (most recently finished first). `limit`
    caps the total rows returned across all statuses."""
    order = (
        "CASE status "
        f"WHEN '{PROCESSING_PROBLEM_SOLVE}' THEN 0 "
        f"WHEN '{PROCESSING_IMAGE_SCAN}' THEN 1 "
        f"WHEN '{PENDING_PROBLEM_SOLVE}' THEN 2 "
        f"WHEN '{PENDING_IMAGE_SCAN}' THEN 3 "
        f"WHEN '{FAILED}' THEN 4 "
        f"WHEN '{DONE}' THEN 5 "
        "ELSE 6 END"
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
    """At worker startup, move any `processing_*` rows back to the matching
    `pending_*` so a crashed prior run's in-flight work gets retried instead
    of stuck forever. Returns the number of rows reclaimed."""
    with _connect() as conn:
        cur1 = conn.execute(
            """
            UPDATE raw_files
            SET status = ?, started_at = NULL,
                last_error = 'reclaimed from stale processing_image_scan'
            WHERE status = ?
            """,
            (PENDING_IMAGE_SCAN, PROCESSING_IMAGE_SCAN),
        )
        cur2 = conn.execute(
            """
            UPDATE raw_files
            SET status = ?, started_at = NULL,
                last_error = 'reclaimed from stale processing_problem_solve'
            WHERE status = ?
            """,
            (PENDING_PROBLEM_SOLVE, PROCESSING_PROBLEM_SOLVE),
        )
        return cur1.rowcount + cur2.rowcount
