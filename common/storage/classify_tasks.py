"""Postgres queue tracking problems awaiting classification, independent of
which source file they came from.

Lifecycle per problem:

    pending -> processing -> done | failed

Rows are seeded lazily by `seed_pending()` (called by the classify batch job
before each claim) from any `problems` row with `category='unclassified'`
that doesn't have a `classify_tasks` row yet — this also covers problems
that predate this table. Rows are scoped to the active user via `user_id`.
`claim_batch` uses `SELECT ... FOR UPDATE SKIP LOCKED` so concurrent batch
runs don't grab the same problem. Schema lives in
[../db_setup/schema.sql](../db_setup/schema.sql).
"""

import uuid
from datetime import datetime, timezone
from typing import NamedTuple

from common.storage.db import connect
from common.storage.paths import current_user_id

CLASSIFY_PENDING = "pending"
CLASSIFY_PROCESSING = "processing"
CLASSIFY_FAILED = "failed"
CLASSIFY_DONE = "done"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClassifyTask(NamedTuple):
    problem_id: str
    status: str
    batch_id: str | None
    attempts: int
    last_error: str | None
    queued_at: str
    started_at: str | None
    finished_at: str | None


def _row_to_task(row: dict) -> ClassifyTask:
    return ClassifyTask(
        problem_id=row["problem_id"],
        status=row["status"],
        batch_id=row["batch_id"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        queued_at=row["queued_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def seed_pending() -> int:
    """Insert a `pending` row for every unclassified problem that doesn't
    already have a classify_tasks row. Safe to call repeatedly. Returns the
    number of rows inserted."""
    user = current_user_id()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO classify_tasks (user_id, problem_id, status, queued_at)
            SELECT %s, id, %s, created_at FROM problems
            WHERE user_id = %s AND category = 'unclassified'
            ON CONFLICT (user_id, problem_id) DO NOTHING
            """,
            (user, CLASSIFY_PENDING, user),
        )
        return cur.rowcount


def claim_batch(n: int) -> list[ClassifyTask]:
    """Claim up to `n` oldest `pending` rows for the active user in one
    round trip, flipping them to `processing` under a fresh `batch_id`."""
    with connect() as conn:
        rows = conn.execute(
            """
            UPDATE classify_tasks t
            SET status = %s,
                attempts = t.attempts + 1,
                started_at = %s,
                batch_id = %s,
                last_error = NULL
            FROM (
                SELECT user_id, problem_id FROM classify_tasks
                WHERE user_id = %s AND status = %s
                ORDER BY queued_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            ) claimed
            WHERE (t.user_id, t.problem_id) = (claimed.user_id, claimed.problem_id)
            RETURNING t.*
            """,
            (
                CLASSIFY_PROCESSING,
                _now(),
                str(uuid.uuid4()),
                current_user_id(),
                CLASSIFY_PENDING,
                max(1, int(n)),
            ),
        ).fetchall()
    return [_row_to_task(r) for r in rows]


def delete(problem_id: str) -> None:
    """Remove a problem's classify_tasks row, if any. Called when the
    problem itself is deleted so a still-pending/processing task doesn't
    keep getting claimed forever."""
    with connect() as conn:
        conn.execute(
            "DELETE FROM classify_tasks WHERE user_id = %s AND problem_id = %s",
            (current_user_id(), problem_id),
        )


def mark_done(problem_id: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE classify_tasks
            SET status = %s, finished_at = %s, last_error = NULL
            WHERE user_id = %s AND problem_id = %s
            """,
            (CLASSIFY_DONE, _now(), current_user_id(), problem_id),
        )


def mark_failed(problem_id: str, error: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE classify_tasks
            SET status = %s, finished_at = %s, last_error = %s
            WHERE user_id = %s AND problem_id = %s
            """,
            (CLASSIFY_FAILED, _now(), error, current_user_id(), problem_id),
        )


def revert_to_pending(problem_id: str, error: str | None = None) -> None:
    """`processing` -> `pending` (attempts stays as-is for visibility)."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE classify_tasks
            SET status = %s, started_at = NULL, last_error = %s
            WHERE user_id = %s AND problem_id = %s
            """,
            (CLASSIFY_PENDING, error, current_user_id(), problem_id),
        )


def retry_failed(problem_id: str) -> bool:
    """Flip a `failed` row back to `pending`. Returns True if a failed row
    was retried, False if it didn't exist or wasn't `failed`."""
    user = current_user_id()
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM classify_tasks WHERE user_id = %s AND problem_id = %s "
            "FOR UPDATE",
            (user, problem_id),
        ).fetchone()
        if row is None or row["status"] != CLASSIFY_FAILED:
            return False
        conn.execute(
            """
            UPDATE classify_tasks
            SET status = %s, queued_at = %s, started_at = NULL,
                finished_at = NULL, attempts = 0, last_error = NULL
            WHERE user_id = %s AND problem_id = %s
            """,
            (CLASSIFY_PENDING, _now(), user, problem_id),
        )
    return True


def status_counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM classify_tasks "
            "WHERE user_id = %s GROUP BY status",
            (current_user_id(),),
        ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}


def list_items(
    statuses: tuple[str, ...] | None = None, limit: int = 200
) -> list[ClassifyTask]:
    """Return classify_tasks rows, optionally filtered to specific statuses.
    Ordered so in-flight work surfaces first: processing -> pending (oldest
    first) -> failed/done (most recently finished first)."""
    order = (
        "CASE status "
        f"WHEN '{CLASSIFY_PROCESSING}' THEN 0 "
        f"WHEN '{CLASSIFY_PENDING}' THEN 1 "
        f"WHEN '{CLASSIFY_FAILED}' THEN 2 "
        f"WHEN '{CLASSIFY_DONE}' THEN 3 "
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
            SELECT * FROM classify_tasks
            {where}
            ORDER BY {order},
                     COALESCE(finished_at, started_at, queued_at) DESC,
                     queued_at ASC
            LIMIT %s
            """,
            (*params, int(limit)),
        ).fetchall()
    return [_row_to_task(r) for r in rows]


def reclaim_stale_processing() -> int:
    """At worker startup, move any `processing` rows back to `pending` so a
    crashed prior run's in-flight work gets retried. Returns the number of
    rows reclaimed."""
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE classify_tasks
            SET status = %s, started_at = NULL,
                last_error = 'reclaimed from stale processing'
            WHERE user_id = %s AND status = %s
            """,
            (CLASSIFY_PENDING, current_user_id(), CLASSIFY_PROCESSING),
        )
        return cur.rowcount
