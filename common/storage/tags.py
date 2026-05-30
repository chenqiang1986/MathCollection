"""Tag registry: customer-defined tag names with optional longer comments.

The `tags` table is authoritative for a tag's comment (it is not derivable
from the problem JSON, unlike the derived `problem_tags` mirror). Usage
counts are joined in from `problem_tags`. See [schema.sql](../db_setup/schema.sql).
"""

from datetime import datetime, timezone

from common.storage.db import connect
from common.storage.paths import current_user_id
from common.storage.vocab import normalize_tag


def list_tags(name: str | None = None) -> list[dict]:
    """Every registered tag with its comment and how many problems use it,
    most-used first. Powers the autocomplete hints on the UI. If `name` is
    given, returns just that one tag (empty list if unknown) — lets the UI
    re-check a single tag's usage count without re-fetching the whole list."""
    user = current_user_id()
    where = "WHERE t.user_id = %s"
    params = [user, user]  # first for the subquery, then the outer WHERE
    if name is not None:
        where += " AND t.name = %s"
        params.append(normalize_tag(name))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT t.name, t.comment, COALESCE(c.n, 0) AS n
            FROM tags t
            LEFT JOIN (
                SELECT tag, COUNT(*) AS n FROM problem_tags
                WHERE user_id = %s GROUP BY tag
            ) c ON c.tag = t.name
            {where}
            ORDER BY n DESC, t.name
            """,
            tuple(params),
        ).fetchall()
    return [
        {"name": r["name"], "comment": r["comment"], "count": r["n"]}
        for r in rows
    ]


def upsert_tag(name: str, comment: str = "") -> dict:
    """Register a tag (or update its comment). A blank comment never clears
    an existing one, so re-adding a tag without a description is harmless."""
    name = normalize_tag(name)
    if not name:
        raise ValueError("tag name is required")
    comment = (comment or "").strip()
    now = datetime.now(timezone.utc).isoformat()
    user = current_user_id()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tags (user_id, name, comment, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(user_id, name) DO UPDATE SET
                comment = CASE
                    WHEN excluded.comment != '' THEN excluded.comment
                    ELSE tags.comment
                END
            """,
            (user, name, comment, now),
        )
        row = conn.execute(
            "SELECT name, comment, created_at FROM tags "
            "WHERE user_id = %s AND name = %s",
            (user, name),
        ).fetchone()
    return {
        "name": row["name"],
        "comment": row["comment"],
        "created_at": row["created_at"],
    }


def delete_tag(name: str) -> bool:
    """Unregister a tag, returning False if it was never registered.

    Orphan-only: a tag still applied to one or more problems cannot be
    deleted — it would just auto-resurrect (empty comment) on the next
    upsert/backfill (see _sync_problem_tags in sql_index). Callers must
    strip it from those problems first; in-use deletes raise ValueError."""
    name = normalize_tag(name)
    if not name:
        raise ValueError("tag name is required")
    user = current_user_id()
    with connect() as conn:
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM problem_tags "
            "WHERE user_id = %s AND tag = %s",
            (user, name),
        ).fetchone()["n"]
        if used:
            raise ValueError(f"tag is still applied to {used} problem(s)")
        cur = conn.execute(
            "DELETE FROM tags WHERE user_id = %s AND name = %s",
            (user, name),
        )
        return cur.rowcount > 0
