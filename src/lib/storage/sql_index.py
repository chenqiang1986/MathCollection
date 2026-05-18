"""SQLite metadata index over the per-user problem JSON files.

Schema lives in [src/db_setup/schema.sql](../../db_setup/schema.sql) and is
applied by `python -m db_setup.main <email>`. This module assumes the DB
already exists.
"""

import sqlite3

from .paths import index_path, user_dir
from .vocab import Problem


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
             solve_time_estimated, created_at, source_exam, year, has_figure,
             source_image, seq_no)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            filename = excluded.filename,
            category = excluded.category,
            subcategory = excluded.subcategory,
            solve_time_seconds = excluded.solve_time_seconds,
            solve_time_estimated = excluded.solve_time_estimated,
            created_at = excluded.created_at,
            source_exam = excluded.source_exam,
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
            problem.year or "Unknown",
            1 if (problem.figure_image or "").strip() else 0,
            problem.source_image or None,
            problem.seq_no if problem.seq_no is not None else None,
        ),
    )


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
    year: str | None = None,
    has_figure: bool | None = None,
) -> tuple[str, list]:
    """Build a WHERE clause. If min_time/max_time covers the full slider range,
    do not exclude rows with NULL solve_time_seconds."""
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
    if year:
        where.append("year = ?")
        params.append(year)
    if has_figure is True:
        where.append("has_figure = 1")
    elif has_figure is False:
        where.append("has_figure = 0")
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
    year: str | None = None,
    has_figure: bool | None = None,
) -> tuple[int, list[str]]:
    where_clause, params = _build_where(
        category, subcategory, min_time, max_time, full_range_max,
        source_exam, year, has_figure,
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
    year: str | None = None,
    has_figure: bool | None = None,
) -> list[str]:
    where_clause, params = _build_where(
        category, subcategory, min_time, max_time, full_range_max,
        source_exam, year, has_figure,
    )
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM problems{where_clause} ORDER BY RANDOM() LIMIT ?",
            (*params, max(1, int(n))),
        ).fetchall()
    return [r["id"] for r in rows]
