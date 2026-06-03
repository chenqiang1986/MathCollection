"""Authoritative Postgres-backed practice set storage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from common.storage.db import connect
from common.storage.paths import current_user_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary_from_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "requested_count": int(row["requested_count"] or 0),
        "problem_count": int(row["problem_count"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _practice_set_summary(conn, practice_set_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT
            ps.id,
            ps.name,
            ps.requested_count,
            ps.created_at,
            ps.updated_at,
            COUNT(psp.problem_id) AS problem_count
        FROM practice_sets ps
        LEFT JOIN practice_set_problems psp
            ON psp.user_id = ps.user_id AND psp.practice_set_id = ps.id
        WHERE ps.user_id = %s AND ps.id = %s
        GROUP BY ps.id, ps.requested_count, ps.created_at, ps.updated_at
        """,
        (current_user_id(), practice_set_id),
    ).fetchone()
    return _summary_from_row(row) if row else None


def list_practice_sets() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                ps.id,
                ps.name,
                ps.requested_count,
                ps.created_at,
                ps.updated_at,
                COUNT(psp.problem_id) AS problem_count
            FROM practice_sets ps
            LEFT JOIN practice_set_problems psp
                ON psp.user_id = ps.user_id AND psp.practice_set_id = ps.id
            WHERE ps.user_id = %s
            GROUP BY ps.id, ps.requested_count, ps.created_at, ps.updated_at
            ORDER BY ps.updated_at DESC, ps.created_at DESC
            """,
            (current_user_id(),),
        ).fetchall()
    return [_summary_from_row(row) for row in rows]


def get_practice_set(practice_set_id: str) -> dict | None:
    from common.storage.problem_io import get_problem

    with connect() as conn:
        summary = _practice_set_summary(conn, practice_set_id)
        if not summary:
            return None
        rows = conn.execute(
            """
            SELECT problem_id
            FROM practice_set_problems
            WHERE user_id = %s AND practice_set_id = %s
            ORDER BY position ASC, added_at ASC
            """,
            (current_user_id(), practice_set_id),
        ).fetchall()
    problem_ids = [row["problem_id"] for row in rows]
    problems = [
        problem.to_dict()
        for problem in (get_problem(problem_id) for problem_id in problem_ids)
        if problem
    ]
    return {
        **summary,
        "problem_ids": problem_ids,
        "problems": problems,
    }


def create_practice_set(
    problem_ids: list[str], requested_count: int, name: str
) -> dict:
    now = _now_iso()
    practice_set_id = str(uuid.uuid4())
    user = current_user_id()
    clean_name = " ".join((name or "").split()).strip()
    deduped_ids: list[str] = []
    for problem_id in problem_ids:
        if problem_id and problem_id not in deduped_ids:
            deduped_ids.append(problem_id)
    if not deduped_ids:
        raise ValueError("practice set must contain at least one problem")
    if not clean_name:
        raise ValueError("practice set name is required")

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO practice_sets
                (user_id, id, name, requested_count, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user,
                practice_set_id,
                clean_name,
                max(0, int(requested_count)),
                now,
                now,
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO practice_set_problems
                    (user_id, practice_set_id, problem_id, position, added_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (user, practice_set_id, problem_id, idx + 1, now)
                    for idx, problem_id in enumerate(deduped_ids)
                ],
            )
    detail = get_practice_set(practice_set_id)
    if not detail:
        raise RuntimeError("created practice set could not be reloaded")
    return detail


def add_problem_to_practice_set(practice_set_id: str, problem_id: str) -> dict | None:
    user = current_user_id()
    now = _now_iso()
    with connect() as conn:
        if not _practice_set_summary(conn, practice_set_id):
            return None
        problem_exists = conn.execute(
            "SELECT 1 FROM problems WHERE user_id = %s AND id = %s",
            (user, problem_id),
        ).fetchone()
        if not problem_exists:
            raise ValueError("problem not found")
        existing = conn.execute(
            """
            SELECT 1
            FROM practice_set_problems
            WHERE user_id = %s AND practice_set_id = %s AND problem_id = %s
            """,
            (user, practice_set_id, problem_id),
        ).fetchone()
        if not existing:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(position), 0) AS max_position
                FROM practice_set_problems
                WHERE user_id = %s AND practice_set_id = %s
                """,
                (user, practice_set_id),
            ).fetchone()
            next_position = int(row["max_position"] or 0) + 1
            conn.execute(
                """
                INSERT INTO practice_set_problems
                    (user_id, practice_set_id, problem_id, position, added_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user, practice_set_id, problem_id, next_position, now),
            )
            conn.execute(
                "UPDATE practice_sets SET updated_at = %s WHERE user_id = %s AND id = %s",
                (now, user, practice_set_id),
            )
    return get_practice_set(practice_set_id)


def remove_problem_from_practice_set(practice_set_id: str, problem_id: str) -> dict | None:
    user = current_user_id()
    now = _now_iso()
    with connect() as conn:
        if not _practice_set_summary(conn, practice_set_id):
            return None
        cur = conn.execute(
            """
            DELETE FROM practice_set_problems
            WHERE user_id = %s AND practice_set_id = %s AND problem_id = %s
            """,
            (user, practice_set_id, problem_id),
        )
        if cur.rowcount:
            conn.execute(
                "UPDATE practice_sets SET updated_at = %s WHERE user_id = %s AND id = %s",
                (now, user, practice_set_id),
            )
    return get_practice_set(practice_set_id)


def delete_practice_set(practice_set_id: str) -> bool:
    user = current_user_id()
    with connect() as conn:
        conn.execute(
            "DELETE FROM practice_set_problems WHERE user_id = %s AND practice_set_id = %s",
            (user, practice_set_id),
        )
        cur = conn.execute(
            "DELETE FROM practice_sets WHERE user_id = %s AND id = %s",
            (user, practice_set_id),
        )
    return cur.rowcount > 0
