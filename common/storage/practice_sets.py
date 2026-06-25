"""Authoritative Postgres-backed practice set storage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from common.storage.db import connect
from common.storage.paths import current_user_id

PRACTICE_SET_ORDER_KEYS: tuple[str, ...] = (
    "year",
    "exam",
    "category+subcategory",
    "random",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary_from_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "series_name": row.get("series_name") or "",
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
            ps.series_name,
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


def _clean_series_name(series_name: str) -> str:
    return " ".join((series_name or "").split()).strip()


def _series_key(series_name: str) -> str:
    return _clean_series_name(series_name).lower()


def normalize_practice_set_order_by(raw: object) -> list[str]:
    if raw is None or raw == "":
        return ["random"]
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise ValueError("order_by must be a list of strings")
    out: list[str] = []
    aliases = {
        "category_subcategory": "category+subcategory",
        "category + subcategory": "category+subcategory",
    }
    for item in items:
        if not isinstance(item, str):
            raise ValueError("order_by must be a list of strings")
        key = aliases.get(" ".join(item.split()).strip().lower(), item)
        key = " ".join(key.split()).strip().lower()
        if key not in PRACTICE_SET_ORDER_KEYS:
            raise ValueError(
                "order_by keys must be one of: "
                + ", ".join(PRACTICE_SET_ORDER_KEYS)
            )
        if key not in out:
            out.append(key)
    return out or ["random"]


def practice_series_problem_ids(
    series_name: str,
    *,
    exclude_practice_set_id: str | None = None,
) -> list[str]:
    key = _series_key(series_name)
    if not key:
        return []
    params: list[str] = [current_user_id(), key]
    exclude_clause = ""
    if exclude_practice_set_id:
        exclude_clause = " AND ps.id <> %s"
        params.append(exclude_practice_set_id)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT psp.problem_id
            FROM practice_set_problems psp
            JOIN practice_sets ps
              ON ps.user_id = psp.user_id
             AND ps.id = psp.practice_set_id
            WHERE ps.user_id = %s
              AND ps.series_key = %s
              {exclude_clause}
            ORDER BY psp.problem_id ASC
            """,
            tuple(params),
        ).fetchall()
    return [row["problem_id"] for row in rows]


def list_practice_sets() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                ps.id,
                ps.name,
                ps.series_name,
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
    problem_ids: list[str],
    requested_count: int,
    name: str,
    series_name: str = "",
) -> dict:
    now = _now_iso()
    practice_set_id = str(uuid.uuid4())
    user = current_user_id()
    clean_name = " ".join((name or "").split()).strip()
    clean_series_name = _clean_series_name(series_name)
    series_key = _series_key(clean_series_name)
    excluded_ids = set(practice_series_problem_ids(clean_series_name))
    deduped_ids: list[str] = []
    for problem_id in problem_ids:
        if (
            problem_id
            and problem_id not in deduped_ids
            and problem_id not in excluded_ids
        ):
            deduped_ids.append(problem_id)
    if not deduped_ids:
        raise ValueError("practice set must contain at least one problem")
    if not clean_name:
        raise ValueError("practice set name is required")

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO practice_sets
                (
                    user_id,
                    id,
                    name,
                    series_name,
                    series_key,
                    requested_count,
                    created_at,
                    updated_at
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user,
                practice_set_id,
                clean_name,
                clean_series_name,
                series_key,
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
        summary = _practice_set_summary(conn, practice_set_id)
        if not summary:
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
            series_name = summary.get("series_name") or ""
            if series_name:
                duplicate_in_series = conn.execute(
                    """
                    SELECT 1
                    FROM practice_set_problems psp
                    JOIN practice_sets ps
                      ON ps.user_id = psp.user_id
                     AND ps.id = psp.practice_set_id
                    WHERE ps.user_id = %s
                      AND ps.series_key = %s
                      AND ps.id <> %s
                      AND psp.problem_id = %s
                    LIMIT 1
                    """,
                    (user, _series_key(series_name), practice_set_id, problem_id),
                ).fetchone()
                if duplicate_in_series:
                    raise ValueError("problem already appears in another set in this series")
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
