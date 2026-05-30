"""Postgres metadata index over the per-user problem JSON files.

Schema lives in [../db_setup/schema.sql](../db_setup/schema.sql) and is applied
by `common.db_setup.setup.ensure_schema`. This module assumes the schema is
already in place. Every query is scoped to the active user via the `user_id`
column (see `common.storage.paths.current_user_id`).
"""

from common.storage.db import connect
from common.storage.paths import current_user_id
from common.storage.vocab import Problem, normalize_tags


def _upsert_index_row(conn, problem: Problem) -> None:
    user = current_user_id()
    conn.execute(
        """
        INSERT INTO problems
            (user_id, id, filename, category, subcategory, solve_time_seconds,
             solve_time_estimated, created_at, source_exam, subexam, year,
             has_figure, source_image, seq_no)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(id) DO UPDATE SET
            user_id = excluded.user_id,
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
            user,
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


def _sync_problem_tags(conn, problem: Problem) -> None:
    """Mirror `problem.tags` into the derived problem_tags table and ensure
    each tag exists in the authoritative `tags` registry (empty comment if
    new — an explicit POST /api/tags can add the comment later)."""
    user = current_user_id()
    conn.execute("DELETE FROM problem_tags WHERE problem_id = %s", (problem.id,))
    tags = normalize_tags(problem.tags)
    if not tags:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO problem_tags (user_id, problem_id, tag) VALUES (%s, %s, %s) "
            "ON CONFLICT (problem_id, tag) DO NOTHING",
            [(user, problem.id, t) for t in tags],
        )
        cur.executemany(
            "INSERT INTO tags (user_id, name, comment, created_at) "
            "VALUES (%s, %s, '', %s) ON CONFLICT (user_id, name) DO NOTHING",
            [(user, t, problem.created_at) for t in tags],
        )


def problems_by_source_and_category(
    source_image: str, category: str
) -> list[str]:
    """Return problem IDs for `source_image` filtered to `category`. Used
    by the solver stage to find partials (category='unclassified') saved
    by the scan stage. Ordered by seq_no so retries see them in source
    order."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id FROM problems
            WHERE user_id = %s AND source_image = %s AND category = %s
            ORDER BY COALESCE(seq_no, 0) ASC, created_at ASC
            """,
            (current_user_id(), source_image, category.lower()),
        ).fetchall()
    return [r["id"] for r in rows]


def distinct_subexams(source_exam: str) -> list[tuple[str, int]]:
    """Return the named subexams already used under `source_exam` for the
    active user, as `(subexam, problem_count)` ordered most-used first.

    Drives the orchestrator's `list_subexams` tool: by showing what prior
    runs already wrote (and how often), a new run can reuse an existing
    label verbatim instead of inventing a fresh spelling — keeping the
    `subexam` value consistent across runs. The empty string (no
    sub-event) is excluded; it is not a reusable label."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT subexam, COUNT(*) AS n FROM problems "
            "WHERE user_id = %s AND source_exam = %s AND subexam <> '' "
            "GROUP BY subexam ORDER BY n DESC, subexam ASC",
            (current_user_id(), source_exam),
        ).fetchall()
    return [(r["subexam"], int(r["n"])) for r in rows]


def existing_seq_nos(source_image: str) -> set[int]:
    """Return the set of seq_no values already saved for this source_image.
    Used by the orchestrator to skip re-solving problems that were already
    extracted from the same source file."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT seq_no FROM problems "
            "WHERE user_id = %s AND source_image = %s AND seq_no IS NOT NULL",
            (current_user_id(), source_image),
        ).fetchall()
    return {int(r["seq_no"]) for r in rows}


def _build_where(
    min_time: float | None,
    max_time: float | None,
    full_range_max: float | None = None,
    source_exam: str | None = None,
    subexam: str | None = None,
    year: str | None = None,
    has_figure: bool | None = None,
    tags: list[str] | None = None,
    cat_subcat: list[tuple[str, str]] | None = None,
) -> tuple[str, list]:
    """Build a WHERE clause (always scoped to the active user). If
    min_time/max_time covers the full slider range, do not exclude rows with
    NULL solve_time_seconds. Multiple tags match with OR semantics (a problem
    qualifies if it carries any of them). cat_subcat is a list of
    (category, subcategory) pairs matched with OR semantics; an empty
    subcategory in a pair matches the whole category."""
    where: list[str] = ["user_id = %s"]
    params: list = [current_user_id()]
    if cat_subcat:
        clauses: list[str] = []
        for cat, sub in cat_subcat:
            if sub:
                clauses.append("(category = %s AND subcategory = %s)")
                params.append(cat.lower())
                params.append(sub.lower())
            else:
                clauses.append("category = %s")
                params.append(cat.lower())
        where.append("(" + " OR ".join(clauses) + ")")
    if source_exam:
        where.append("source_exam = %s")
        params.append(source_exam)
    if subexam:
        where.append("subexam = %s")
        params.append(subexam)
    if year:
        where.append("year = %s")
        params.append(year)
    if has_figure is True:
        where.append("has_figure = 1")
    elif has_figure is False:
        where.append("has_figure = 0")
    if tags:
        placeholders = ", ".join("%s" for _ in tags)
        where.append(
            "id IN (SELECT problem_id FROM problem_tags "
            f"WHERE user_id = %s AND tag IN ({placeholders}))"
        )
        params.append(current_user_id())
        params.extend(tags)
    range_active = False
    if min_time is not None and (full_range_max is None or min_time > 0):
        range_active = True
    if max_time is not None and (full_range_max is None or max_time < full_range_max):
        range_active = True
    if range_active:
        if min_time is not None:
            where.append("solve_time_seconds IS NOT NULL AND solve_time_seconds >= %s")
            params.append(min_time)
        if max_time is not None:
            where.append("solve_time_seconds IS NOT NULL AND solve_time_seconds <= %s")
            params.append(max_time)
    return " WHERE " + " AND ".join(where), params


def query_index(
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
    cat_subcat: list[tuple[str, str]] | None = None,
) -> tuple[int, list[str]]:
    where_clause, params = _build_where(
        min_time, max_time, full_range_max,
        source_exam, subexam, year, has_figure, tags, cat_subcat,
    )
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    offset = (page - 1) * page_size
    with connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS total FROM problems{where_clause}", params
        ).fetchone()["total"]
        rows = conn.execute(
            f"SELECT id FROM problems{where_clause} "
            f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (*params, page_size, offset),
        ).fetchall()
    ids = [r["id"] for r in rows]
    return total, ids


def sample_index(
    n: int,
    min_time: float | None = None,
    max_time: float | None = None,
    full_range_max: float | None = None,
    source_exam: str | None = None,
    subexam: str | None = None,
    year: str | None = None,
    has_figure: bool | None = None,
    tags: list[str] | None = None,
    cat_subcat: list[tuple[str, str]] | None = None,
) -> list[str]:
    where_clause, params = _build_where(
        min_time, max_time, full_range_max,
        source_exam, subexam, year, has_figure, tags, cat_subcat,
    )
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM problems{where_clause} ORDER BY RANDOM() LIMIT %s",
            (*params, max(1, int(n))),
        ).fetchall()
    return [r["id"] for r in rows]
