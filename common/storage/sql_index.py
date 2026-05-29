"""SQLite metadata index over the per-user problem JSON files.

Schema lives in [src/db_setup/schema.sql](../../db_setup/schema.sql) and is
applied by `python -m db_setup.main <email>`. This module assumes the DB
already exists.
"""

import sqlite3

from common.storage.paths import index_path, user_dir
from common.storage.vocab import Problem, normalize_tags


def _connect() -> sqlite3.Connection:
    user_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index_path())
    conn.row_factory = sqlite3.Row
    return conn


def _upsert_index_row(conn: sqlite3.Connection, problem: Problem) -> None:
    conn.execute(
        """
        INSERT INTO problems
            (id, filename, category, subcategory, solve_time_seconds,
             solve_time_estimated, created_at, source_exam, subexam, year,
             has_figure, source_image, seq_no)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            filename = excluded.filename,
            category = excluded.category,
            subcategory = excluded.subcategory,
            solve_time_seconds = excluded.solve_time_seconds,
            solve_time_estimated = excluded.solve_time_estimated,
            created_at = excluded.created_at,
            source_exam = excluded.source_exam,
            subexam = excluded.subexam,
            year = excluded.year,
            has_figure = excluded.has_figure,
            source_image = excluded.source_image,
            seq_no = excluded.seq_no
        """,
        (
            problem.id,
            f"{problem.id}.json",
            (problem.category or "").lower(),
            (problem.subcategory or "").lower(),
            problem.solve_time_seconds,
            int(problem.solve_time_estimated or 0),
            problem.created_at,
            problem.source_exam or "Unknown",
            problem.subexam or "",
            problem.year or "Unknown",
            1 if (problem.figure_image or "").strip() else 0,
            problem.source_image or None,
            problem.seq_no if problem.seq_no is not None else None,
        ),
    )
    _sync_problem_tags(conn, problem)


def _sync_problem_tags(conn: sqlite3.Connection, problem: Problem) -> None:
    """Mirror `problem.tags` into the derived problem_tags table and ensure
    each tag exists in the authoritative `tags` registry (empty comment if
    new — an explicit POST /api/tags can add the comment later)."""
    conn.execute("DELETE FROM problem_tags WHERE problem_id = ?", (problem.id,))
    tags = normalize_tags(problem.tags)
    if not tags:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO problem_tags (problem_id, tag) VALUES (?, ?)",
        [(problem.id, t) for t in tags],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO tags (name, comment, created_at) VALUES (?, '', ?)",
        [(t, problem.created_at) for t in tags],
    )


def problems_by_source_and_category(
    source_image: str, category: str
) -> list[str]:
    """Return problem IDs for `source_image` filtered to `category`. Used
    by the solver stage to find partials (category='unclassified') saved
    by the scan stage. Ordered by seq_no so retries see them in source
    order."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id FROM problems
            WHERE source_image = ? AND category = ?
            ORDER BY COALESCE(seq_no, 0) ASC, created_at ASC
            """,
            (source_image, category.lower()),
        ).fetchall()
    return [r["id"] for r in rows]


def existing_seq_nos(source_image: str) -> set[int]:
    """Return the set of seq_no values already saved for this source_image.
    Used by the orchestrator to skip re-solving problems that were already
    extracted from the same source file."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT seq_no FROM problems "
            "WHERE source_image = ? AND seq_no IS NOT NULL",
            (source_image,),
        ).fetchall()
    return {int(r["seq_no"]) for r in rows}


def _build_where(
    category: str | None,
    subcategory: str | None,
    min_time: float | None,
    max_time: float | None,
    full_range_max: float | None = None,
    source_exam: str | None = None,
    subexam: str | None = None,
    year: str | None = None,
    has_figure: bool | None = None,
    tags: list[str] | None = None,
) -> tuple[str, list]:
    """Build a WHERE clause. If min_time/max_time covers the full slider range,
    do not exclude rows with NULL solve_time_seconds. Multiple tags match with
    OR semantics (a problem qualifies if it carries any of them)."""
    where: list[str] = []
    params: list = []
    if category:
        where.append("category = ?")
        params.append(category.lower())
    if subcategory:
        where.append("subcategory = ?")
        params.append(subcategory.lower())
    if source_exam:
        where.append("source_exam = ?")
        params.append(source_exam)
    if subexam:
        where.append("subexam = ?")
        params.append(subexam)
    if year:
        where.append("year = ?")
        params.append(year)
    if has_figure is True:
        where.append("has_figure = 1")
    elif has_figure is False:
        where.append("has_figure = 0")
    if tags:
        placeholders = ", ".join("?" for _ in tags)
        where.append(
            "id IN (SELECT problem_id FROM problem_tags "
            f"WHERE tag IN ({placeholders}))"
        )
        params.extend(tags)
    range_active = False
    if min_time is not None and (full_range_max is None or min_time > 0):
        range_active = True
    if max_time is not None and (full_range_max is None or max_time < full_range_max):
        range_active = True
    if range_active:
        if min_time is not None:
            where.append("solve_time_seconds IS NOT NULL AND solve_time_seconds >= ?")
            params.append(min_time)
        if max_time is not None:
            where.append("solve_time_seconds IS NOT NULL AND solve_time_seconds <= ?")
            params.append(max_time)
    if not where:
        return "", params
    return " WHERE " + " AND ".join(where), params


def query_index(
    category: str | None = None,
    subcategory: str | None = None,
    min_time: float | None = None,
    max_time: float | None = None,
    page: int = 1,
    page_size: int = 5,
    full_range_max: float | None = None,
    source_exam: str | None = None,
    subexam: str | None = None,
    year: str | None = None,
    has_figure: bool | None = None,
    tags: list[str] | None = None,
) -> tuple[int, list[str]]:
    where_clause, params = _build_where(
        category, subcategory, min_time, max_time, full_range_max,
        source_exam, subexam, year, has_figure, tags,
    )
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    offset = (page - 1) * page_size
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM problems{where_clause}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT id FROM problems{where_clause} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
    ids = [r["id"] for r in rows]
    return total, ids


def sample_index(
    n: int,
    category: str | None = None,
    subcategory: str | None = None,
    min_time: float | None = None,
    max_time: float | None = None,
    full_range_max: float | None = None,
    source_exam: str | None = None,
    subexam: str | None = None,
    year: str | None = None,
    has_figure: bool | None = None,
    tags: list[str] | None = None,
) -> list[str]:
    where_clause, params = _build_where(
        category, subcategory, min_time, max_time, full_range_max,
        source_exam, subexam, year, has_figure, tags,
    )
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM problems{where_clause} ORDER BY RANDOM() LIMIT ?",
            (*params, max(1, int(n))),
        ).fetchall()
    return [r["id"] for r in rows]
