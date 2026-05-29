"""Per-user log of manual category edits.

This table is the authoritative source for category-edit history (unlike the
`problems` table in the same DB, which is derived from the JSON files and
safe to rebuild). Keep enough denormalized context (problem_text, solution
at edit time) so each row stands on its own as a training example, even if
the underlying problem is later edited again or deleted.

Schema lives in [db_setup/schema.sql](../../db_setup/schema.sql).
"""

from datetime import datetime, timezone

from common.storage.db import connect
from common.storage.paths import current_user_id


def record_category_edit(
    problem_id: str,
    problem_text: str,
    solution: str,
    from_category: str,
    to_category: str,
    from_subcategory: str = "",
    to_subcategory: str = "",
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO category_edits
                (user_id, problem_id, problem_text, solution, from_category,
                 to_category, from_subcategory, to_subcategory, edited_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                current_user_id(),
                problem_id,
                problem_text,
                solution or "",
                (from_category or "").lower(),
                (to_category or "").lower(),
                (from_subcategory or "").lower(),
                (to_subcategory or "").lower(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def category_edit_examples(
    from_category: str,
    limit: int = 5,
    from_subcategory: str | None = None,
) -> list[dict]:
    """Most recent user edits that moved a problem AWAY from `from_category`
    (and, if provided, `from_subcategory`)."""
    sql = (
        "SELECT problem_text, solution, from_category, to_category, "
        "from_subcategory, to_subcategory, edited_at "
        "FROM category_edits WHERE user_id = %s AND from_category = %s"
    )
    params: list = [current_user_id(), (from_category or "").lower()]
    if from_subcategory:
        sql += " AND from_subcategory = %s"
        params.append(from_subcategory.lower())
    sql += " ORDER BY edited_at DESC LIMIT %s"
    params.append(max(1, int(limit)))
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
