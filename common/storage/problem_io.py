"""JSON blob read/write for problem records (with mirrored index upserts)."""

import json
import uuid
from datetime import datetime, timezone

from common.storage.paths import figures_dir, problems_dir
from common.storage.sql_index import _connect, _upsert_index_row
from common.storage.vocab import Problem, normalize_tags


def _write_problem_file(problem: Problem) -> None:
    pdir = problems_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{problem.id}.json"
    path.write_text(json.dumps(problem.to_dict(), indent=2, ensure_ascii=False))


def save_problem(
    problem_text: str,
    category: str,
    subcategory: str = "",
    solution: str = "",
    source_image: str | None = None,
    source_page: int | None = None,
    seq_no: int | None = None,
    source_exam: str = "Unknown",
    subexam: str = "",
    year: str = "Unknown",
    figure_image: str | None = None,
    figure_bbox: list[float] | None = None,
    figure_page: int | None = None,
    solve_time_seconds: float | None = None,
    solve_time_estimated: int = 0,
    tags: list[str] | None = None,
) -> Problem:
    problem = Problem(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc).isoformat(),
        problem_text=problem_text,
        category=category,
        subcategory=subcategory or "",
        solve_time_seconds=solve_time_seconds,
        solve_time_estimated=solve_time_estimated,
        solution=solution,
        source_image=source_image,
        source_page=source_page,
        seq_no=seq_no,
        source_exam=source_exam or "Unknown",
        subexam=subexam or "",
        year=year or "Unknown",
        figure_image=figure_image or None,
        figure_bbox=figure_bbox or None,
        figure_page=figure_page,
        tags=normalize_tags(tags),
    )
    _write_problem_file(problem)
    with _connect() as conn:
        _upsert_index_row(conn, problem)
    return problem


def update_problem(problem_id: str, **fields) -> Problem:
    path = problems_dir() / f"{problem_id}.json"
    data = json.loads(path.read_text())
    data.update(fields)
    problem = Problem.from_dict(data)
    _write_problem_file(problem)
    with _connect() as conn:
        _upsert_index_row(conn, problem)
    return problem


def delete_problem(problem_id: str) -> bool:
    path = problems_dir() / f"{problem_id}.json"
    problem: Problem | None = None
    if path.exists():
        try:
            problem = Problem.from_dict(json.loads(path.read_text()))
        except json.JSONDecodeError:
            problem = None
        path.unlink()
    figure = problem.figure_image if problem else None
    if figure:
        fpath = figures_dir() / figure
        if fpath.exists():
            fpath.unlink()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM problems WHERE id = ?", (problem_id,))
        deleted_rows = cur.rowcount
        conn.execute("DELETE FROM problem_tags WHERE problem_id = ?", (problem_id,))
    return problem is not None or deleted_rows > 0


def get_problem(problem_id: str) -> Problem | None:
    path = problems_dir() / f"{problem_id}.json"
    if not path.exists():
        return None
    try:
        return Problem.from_dict(json.loads(path.read_text()))
    except json.JSONDecodeError:
        return None


def list_problems() -> list[Problem]:
    pdir = problems_dir()
    if not pdir.exists():
        return []
    out: list[Problem] = []
    for p in sorted(pdir.glob("*.json")):
        try:
            out.append(Problem.from_dict(json.loads(p.read_text())))
        except json.JSONDecodeError:
            continue
    return out
