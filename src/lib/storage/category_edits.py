"""Per-user log of manual category edits.

This table is the authoritative source for category-edit history (unlike the
`problems` table in the same DB, which is derived from the JSON files and
safe to rebuild). Keep enough denormalized context (problem_text, solution
at edit time) so each row stands on its own as a training example, even if
the underlying problem is later edited again or deleted.

Schema lives in [src/db_setup/schema.sql](../../db_setup/schema.sql).
"""

from datetime import datetime, timezone

from .sql_index import _connect


def record_category_edit(
    problem_id: str,
    problem_text: str,
    solution: str,
    from_category: str,
    to_category: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO category_edits
                (problem_id, problem_text, solution, from_category,
                 to_category, edited_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                problem_id,
                problem_text,
                solution or "",
                (from_category or "").lower(),
                (to_category or "").lower(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def category_edit_examples(
    from_category: str, limit: int = 5
) -> list[dict]:
    """Most recent user edits that moved a problem AWAY from `from_category`."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT problem_text, solution, from_category, to_category, edited_at
            FROM category_edits
            WHERE from_category = ?
            ORDER BY edited_at DESC
            LIMIT ?
            """,
            ((from_category or "").lower(), max(1, int(limit))),
        ).fetchall()
    return [dict(r) for r in rows]
