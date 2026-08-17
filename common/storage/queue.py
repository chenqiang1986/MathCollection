"""Postgres queue tracking raw files awaiting the orchestrator scan.

Lifecycle per file:

    pending_image_scan      (uploaded; waiting for the orchestrator scan)
        -> processing_image_scan
        -> done | failed

Classification runs separately, over a flat backlog of problems rather
than per file — see [classify_tasks.py](classify_tasks.py). The webapp's
`/upload` inserts new rows at `pending_image_scan`; the offline worker in
`worker/` claims pending rows, drives the scan, and advances or reverts
them. Rows are scoped to the active user via the `user_id` column.
`claim_next_image_scan` uses `SELECT ... FOR UPDATE SKIP LOCKED` so two
workers polling the same user don't grab the same row. Schema lives in
[../db_setup/schema.sql](../db_setup/schema.sql).
"""

from datetime import datetime, timezone
from typing import Literal, NamedTuple

from common.storage.db import connect
from common.storage.paths import current_user_id

EnqueueResult = Literal["new", "retried", "skipped"]

PENDING_IMAGE_SCAN = "pending_image_scan"
PROCESSING_IMAGE_SCAN = "processing_image_scan"
DONE = "done"
FAILED = "failed"

_PENDING_STATES = (PENDING_IMAGE_SCAN,)
_PROCESSING_STATES = (PROCESSING_IMAGE_SCAN,)
_IN_FLIGHT_STATES = _PENDING_STATES + _PROCESSING_STATES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QueueItem(NamedTuple):
    filename: str
    status: str
    attempts: int
    last_error: str | None
    queued_at: str
    started_at: str | None
    finished_at: str | None


def _row_to_item(row: dict) -> QueueItem:
    return QueueItem(
        filename=row["filename"],
        status=row["status"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        queued_at=row["queued_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def enqueue_raw(filename: str) -> EnqueueResult:
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
                INSERT INTO raw_files (user_id, filename, status, queued_at)
                VALUES (%s, %s, %s, %s)
                """,
                (user, filename, PENDING_IMAGE_SCAN, _now()),
            )
            return "new"
        if existing["status"] in (FAILED, DONE):
            conn.execute(
                """
                UPDATE raw_files
                SET status = %s,
                    queued_at = %s,
                    started_at = NULL,
                    finished_at = NULL,
                    last_error = NULL
                WHERE user_id = %s AND filename = %s
                """,
                (PENDING_IMAGE_SCAN, _now(), user, filename),
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
    it up again. Clears `last_error`/timestamps and resets `attempts`.
    Returns True if a failed row was retried, False if the row didn't
    exist or wasn't in `failed`."""
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


def pending_count() -> int:
    """Total files awaiting scan."""
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

    Ordered so in-flight work surfaces first: processing → pending (oldest
    first) → failed/done (most recently finished first). `limit` caps the
    total rows returned across all statuses."""
    order = (
        "CASE status "
        f"WHEN '{PROCESSING_IMAGE_SCAN}' THEN 0 "
        f"WHEN '{PENDING_IMAGE_SCAN}' THEN 1 "
        f"WHEN '{FAILED}' THEN 2 "
        f"WHEN '{DONE}' THEN 3 "
        "ELSE 4 END"
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
    """At worker startup, move any `processing_image_scan` rows back to
    `pending_image_scan` so a crashed prior run's in-flight work gets
    retried instead of stuck forever. Returns the number of rows
    reclaimed."""
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE raw_files
            SET status = %s, started_at = NULL,
                last_error = 'reclaimed from stale processing_image_scan'
            WHERE user_id = %s AND status = %s
            """,
            (PENDING_IMAGE_SCAN, current_user_id(), PROCESSING_IMAGE_SCAN),
        )
        return cur.rowcount
