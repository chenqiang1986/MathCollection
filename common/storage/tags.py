"""Tag registry: customer-defined tag names with optional longer comments.

The `tags` table is authoritative for a tag's comment (it is not derivable
from the problem JSON, unlike the derived `problem_tags` mirror). Usage
counts are joined in from `problem_tags`. See [schema.sql](../db_setup/schema.sql).
"""

from datetime import datetime, timezone

from common.storage.sql_index import _connect
from common.storage.vocab import normalize_tag


def list_tags() -> list[dict]:
    """Every registered tag with its comment and how many problems use it,
    most-used first. Powers the autocomplete hints on the UI."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT t.name, t.comment, COALESCE(c.n, 0) AS n
            FROM tags t
            LEFT JOIN (
                SELECT tag, COUNT(*) AS n FROM problem_tags GROUP BY tag
            ) c ON c.tag = t.name
            ORDER BY n DESC, t.name
            """
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
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tags (name, comment, created_at) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                comment = CASE
                    WHEN excluded.comment != '' THEN excluded.comment
                    ELSE tags.comment
                END
            """,
            (name, comment, now),
        )
        row = conn.execute(
            "SELECT name, comment, created_at FROM tags WHERE name = ?", (name,)
        ).fetchone()
    return {
        "name": row["name"],
        "comment": row["comment"],
        "created_at": row["created_at"],
    }
