import contextvars
import dataclasses
import json
import math
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
UPLOADS_DIR = REPO_ROOT / "uploads"

_CURRENT_USER: contextvars.ContextVar[str] = contextvars.ContextVar("current_user")

_EMAIL_SAFE_RE = re.compile(r"[^a-z0-9@._\-+]")


@dataclass
class Problem:
    id: str
    created_at: str
    problem_text: str
    category: str
    solve_time_seconds: float | None = None
    solve_time_estimated: bool = False
    solution: str = ""
    source_image: str | None = None
    figure_image: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Problem":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


def sanitize_email(email: str) -> str:
    """Lowercase and replace filesystem-unsafe characters."""
    if not email:
        raise ValueError("email is required")
    safe = _EMAIL_SAFE_RE.sub("_", email.strip().lower())
    if not safe:
        raise ValueError(f"email sanitizes to empty string: {email!r}")
    return safe


def set_current_user(email: str) -> contextvars.Token:
    """Bind the current user for subsequent storage calls in this context."""
    return _CURRENT_USER.set(sanitize_email(email))


def reset_current_user(token: contextvars.Token) -> None:
    _CURRENT_USER.reset(token)


def _user_slug() -> str:
    try:
        return _CURRENT_USER.get()
    except LookupError as e:
        raise RuntimeError(
            "storage called without an active user; call set_current_user first"
        ) from e


def user_dir() -> Path:
    return DATA_DIR / _user_slug()


def problems_dir() -> Path:
    return user_dir() / "problems"


def figures_dir() -> Path:
    return user_dir() / "figures"


def figure_path(filename: str) -> Path:
    return figures_dir() / filename


def index_path() -> Path:
    return user_dir() / "problems_index.db"


def _connect() -> sqlite3.Connection:
    user_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_index() -> None:
    """Create tables and backfill from problems/*.json for the current user."""
    pdir = problems_dir()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS problems (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                category TEXT NOT NULL,
                solve_time_seconds REAL,
                solve_time_estimated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON problems(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON problems(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_solve_time ON problems(solve_time_seconds)")
        count = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
        if count == 0 and pdir.exists():
            for p in sorted(pdir.glob("*.json")):
                try:
                    data = json.loads(p.read_text())
                except json.JSONDecodeError:
                    continue
                _upsert_index_row(conn, Problem.from_dict(data))


def _upsert_index_row(conn: sqlite3.Connection, problem: Problem) -> None:
    conn.execute(
        """
        INSERT INTO problems
            (id, filename, category, solve_time_seconds, solve_time_estimated, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            filename = excluded.filename,
            category = excluded.category,
            solve_time_seconds = excluded.solve_time_seconds,
            solve_time_estimated = excluded.solve_time_estimated,
            created_at = excluded.created_at
        """,
        (
            problem.id,
            f"{problem.id}.json",
            (problem.category or "").lower(),
            problem.solve_time_seconds,
            1 if problem.solve_time_estimated else 0,
            problem.created_at,
        ),
    )


def _write_problem_file(problem: Problem) -> None:
    pdir = problems_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{problem.id}.json"
    path.write_text(json.dumps(problem.to_dict(), indent=2, ensure_ascii=False))


def save_problem(
    problem_text: str,
    category: str,
    solution: str = "",
    source_image: str | None = None,
    figure_image: str | None = None,
    solve_time_seconds: float | None = None,
    solve_time_estimated: bool = False,
) -> Problem:
    problem = Problem(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc).isoformat(),
        problem_text=problem_text,
        category=category,
        solve_time_seconds=solve_time_seconds,
        solve_time_estimated=solve_time_estimated,
        solution=solution,
        source_image=source_image,
        figure_image=figure_image or None,
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


def _build_where(
    category: str | None,
    min_time: float | None,
    max_time: float | None,
    full_range_max: float | None = None,
) -> tuple[str, list]:
    """Build a WHERE clause. If min_time/max_time covers the full slider range,
    do not exclude rows with NULL solve_time_seconds."""
    where: list[str] = []
    params: list = []
    if category:
        where.append("category = ?")
        params.append(category.lower())
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
    min_time: float | None = None,
    max_time: float | None = None,
    page: int = 1,
    page_size: int = 5,
    full_range_max: float | None = None,
) -> tuple[int, list[str]]:
    where_clause, params = _build_where(category, min_time, max_time, full_range_max)
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
    min_time: float | None = None,
    max_time: float | None = None,
    full_range_max: float | None = None,
) -> list[str]:
    where_clause, params = _build_where(category, min_time, max_time, full_range_max)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM problems{where_clause} ORDER BY RANDOM() LIMIT ?",
            (*params, max(1, int(n))),
        ).fetchall()
    return [r["id"] for r in rows]


class Bucket(NamedTuple):
    label: str
    lo: float
    hi: float


DIFFICULTY_BUCKETS: list[Bucket] = [
    Bucket("Easy (<60s)", 0.0, 60.0),
    Bucket("Medium (1–3m)", 60.0, 180.0),
    Bucket("Hard (3–10m)", 180.0, 600.0),
    Bucket("Very Hard (>10m)", 600.0, float("inf")),
]


def category_counts() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM problems "
            "GROUP BY category ORDER BY n DESC, category"
        ).fetchall()
    return [{"category": r["category"], "count": r["n"]} for r in rows]


def difficulty_distribution(category: str | None = None) -> list[dict]:
    where = ""
    params: list = []
    if category:
        where = " WHERE category = ?"
        params.append(category.lower())
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT solve_time_seconds FROM problems{where}", params
        ).fetchall()
    counts = [{"label": b.label, "count": 0} for b in DIFFICULTY_BUCKETS]
    unknown = 0
    for r in rows:
        t = r["solve_time_seconds"]
        if t is None:
            unknown += 1
            continue
        for i, bucket in enumerate(DIFFICULTY_BUCKETS):
            if bucket.lo <= t < bucket.hi:
                counts[i]["count"] += 1
                break
    if unknown:
        counts.append({"label": "Unknown", "count": unknown})
    return counts


def index_summary() -> dict:
    with _connect() as conn:
        cats = [
            r["category"]
            for r in conn.execute(
                "SELECT DISTINCT category FROM problems ORDER BY category"
            ).fetchall()
        ]
        max_time = conn.execute(
            "SELECT MAX(solve_time_seconds) FROM problems"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
    slider_max = 60 if max_time is None else max(1, int(math.ceil(max_time)))
    return {
        "categories": cats,
        "max_time": slider_max,
        "total": total,
    }
