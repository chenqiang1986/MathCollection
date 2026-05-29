"""Postgres queue tracking raw files awaiting agent processing.

Lifecycle per file:

    pending_image_scan      (uploaded; waiting for the orchestrator scan)
        -> processing_image_scan
        -> pending_problem_solve   (partials persisted; waiting for solver)
        -> processing_problem_solve
        -> done | failed

The webapp's `/upload` inserts new rows at `pending_image_scan`; the
offline worker in `worker/` claims rows at either pending state, drives
the appropriate stage, and advances or reverts them. Rows are scoped to
the active user via the `user_id` column. The `claim_next_*` helpers use
`SELECT ... FOR UPDATE SKIP LOCKED` so two workers polling the same user
don't grab the same row. Schema lives in
[../db_setup/schema.sql](../db_setup/schema.sql).
"""

from datetime import datetime, timezone
from typing import Literal, NamedTuple

from common.storage.db import connect
from common.storage.paths import current_user_id

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


def _row_to_item(row: dict) -> QueueItem:
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
    user = current_user_id()
    with connect() as conn:
        existing = conn.execute(
            "SELECT status FROM raw_files WHERE user_id = %s AND filename = %s "
            "FOR UPDATE",
            (user, filename),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO raw_files
                    (user_id, filename, with_solution, status, queued_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user, filename, 1 if with_solution else 0, PENDING_IMAGE_SCAN, _now()),
            )
            return "new"
        if existing["status"] in (FAILED, DONE):
            conn.execute(
                """
                UPDATE raw_files
                SET status = %s,
                    with_solution = %s,
                    queued_at = %s,
                    started_at = NULL,
                    finished_at = NULL,
                    last_error = NULL
                WHERE user_id = %s AND filename = %s
                """,
                (PENDING_IMAGE_SCAN, 1 if with_solution else 0, _now(), user, filename),
            )
            return "retried"
        return "skipped"


def _claim_pending(conn, pending: str, processing: str) -> QueueItem | None:
    """Atomically claim the oldest row in `pending` for the active user and
    flip it to `processing`. `FOR UPDATE SKIP LOCKED` lets concurrent workers
    skip a row another worker already holds instead of blocking."""
    row = conn.execute(
        """
        UPDATE raw_files
        SET status = %s,
            attempts = attempts + 1,
            started_at = %s,
            last_error = NULL
        WHERE (user_id, filename) = (
            SELECT user_id, filename FROM raw_files
            WHERE user_id = %s AND status = %s
            ORDER BY queued_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        (processing, _now(), current_user_id(), pending),
    ).fetchone()
    return _row_to_item(row) if row is not None else None


def claim_next_image_scan() -> QueueItem | None:
    """Pick the oldest `pending_image_scan` row, flip it to
    `processing_image_scan`, bump attempts. Returns None if none pending."""
    with connect() as conn:
        return _claim_pending(conn, PENDING_IMAGE_SCAN, PROCESSING_IMAGE_SCAN)


def claim_next_problem_solve() -> QueueItem | None:
    """Pick the oldest `pending_problem_solve` row, flip it to
    `processing_problem_solve`, bump attempts. Returns None if none pending."""
    with connect() as conn:
        return _claim_pending(conn, PENDING_PROBLEM_SOLVE, PROCESSING_PROBLEM_SOLVE)


def advance_to_problem_solve(filename: str) -> None:
    """`processing_image_scan` → `pending_problem_solve`. Resets attempts
    so the solve stage gets its own retry budget independent of scan."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = %s, started_at = NULL, attempts = 0, last_error = NULL
            WHERE user_id = %s AND filename = %s
            """,
            (PENDING_PROBLEM_SOLVE, current_user_id(), filename),
        )


def mark_done(filename: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = %s, finished_at = %s, last_error = NULL
            WHERE user_id = %s AND filename = %s
            """,
            (DONE, _now(), current_user_id(), filename),
        )


def mark_failed(filename: str, error: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = %s, finished_at = %s, last_error = %s
            WHERE user_id = %s AND filename = %s
            """,
            (FAILED, _now(), error, current_user_id(), filename),
        )


def retry_failed(filename: str) -> bool:
    """Flip a `failed` row back to `pending_image_scan` so the worker picks
    it up again. Preserves `with_solution`; clears `last_error`/timestamps
    and resets `attempts`. Returns True if a failed row was retried, False
    if the row didn't exist or wasn't in `failed`."""
    user = current_user_id()
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM raw_files WHERE user_id = %s AND filename = %s "
            "FOR UPDATE",
            (user, filename),
        ).fetchone()
        if row is None or row["status"] != FAILED:
            return False
        conn.execute(
            """
            UPDATE raw_files
            SET status = %s,
                queued_at = %s,
                started_at = NULL,
                finished_at = NULL,
                attempts = 0,
                last_error = NULL
            WHERE user_id = %s AND filename = %s
            """,
            (PENDING_IMAGE_SCAN, _now(), user, filename),
        )
    return True


def revert_image_scan(filename: str, error: str | None = None) -> None:
    """`processing_image_scan` → `pending_image_scan` (no attempt consumed
    from the user's POV — attempts counter stays as-is for visibility)."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = %s, started_at = NULL, last_error = %s
            WHERE user_id = %s AND filename = %s
            """,
            (PENDING_IMAGE_SCAN, error, current_user_id(), filename),
        )


def revert_problem_solve(filename: str, error: str | None = None) -> None:
    """`processing_problem_solve` → `pending_problem_solve`."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE raw_files
            SET status = %s, started_at = NULL, last_error = %s
            WHERE user_id = %s AND filename = %s
            """,
            (PENDING_PROBLEM_SOLVE, error, current_user_id(), filename),
        )


def pending_count() -> int:
    """Total files awaiting any stage (image_scan or problem_solve)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_files "
            "WHERE user_id = %s AND status = ANY(%s)",
            (current_user_id(), list(_PENDING_STATES)),
        ).fetchone()
    return int(row["n"])


def status_counts() -> dict[str, int]:
    """Return row counts grouped by status."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM raw_files "
            "WHERE user_id = %s GROUP BY status",
            (current_user_id(),),
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
    where = "WHERE user_id = %s"
    params: list = [current_user_id()]
    if statuses:
        where += " AND status = ANY(%s)"
        params.append(list(statuses))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM raw_files
            {where}
            ORDER BY {order},
                     COALESCE(finished_at, started_at, queued_at) DESC,
                     queued_at ASC
            LIMIT %s
            """,
            (*params, int(limit)),
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def reclaim_stale_processing() -> int:
    """At worker startup, move any `processing_*` rows back to the matching
    `pending_*` so a crashed prior run's in-flight work gets retried instead
    of stuck forever. Returns the number of rows reclaimed."""
    user = current_user_id()
    with connect() as conn:
        cur1 = conn.execute(
            """
            UPDATE raw_files
            SET status = %s, started_at = NULL,
                last_error = 'reclaimed from stale processing_image_scan'
            WHERE user_id = %s AND status = %s
            """,
            (PENDING_IMAGE_SCAN, user, PROCESSING_IMAGE_SCAN),
        )
        cur2 = conn.execute(
            """
            UPDATE raw_files
            SET status = %s, started_at = NULL,
                last_error = 'reclaimed from stale processing_problem_solve'
            WHERE user_id = %s AND status = %s
            """,
            (PENDING_PROBLEM_SOLVE, user, PROCESSING_PROBLEM_SOLVE),
        )
        return cur1.rowcount + cur2.rowcount
