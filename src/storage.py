import contextvars
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
UPLOADS_DIR = REPO_ROOT / "uploads"

FIGURE_PADDING = 0.015  # 1.5% margin on each side of the model's bbox

_CURRENT_USER: contextvars.ContextVar[str] = contextvars.ContextVar("current_user")

_EMAIL_SAFE_RE = re.compile(r"[^a-z0-9@._\-+]")


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
                    rec = json.loads(p.read_text())
                except json.JSONDecodeError:
                    continue
                _upsert_index_row(conn, rec)


def _upsert_index_row(conn: sqlite3.Connection, record: dict) -> None:
    filename = f"{record['id']}.json"
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
            record["id"],
            filename,
            (record.get("category") or "").lower(),
            record.get("solve_time_seconds"),
            1 if record.get("solve_time_estimated") else 0,
            record["created_at"],
        ),
    )


_CW_ROTATION = {
    0: None,
    90: Image.ROTATE_270,   # 90° clockwise = PIL ROTATE_270 (counter-clockwise)
    180: Image.ROTATE_180,
    270: Image.ROTATE_90,   # 270° clockwise = PIL ROTATE_90
}


def save_figure(
    source_image: str, bbox: list[float], rotation: int = 0
) -> str:
    """Crop a normalized [x0,y0,x1,y1] region from uploads/<source_image>,
    optionally rotate clockwise by `rotation` (one of 0/90/180/270), and
    save as a PNG under data/<user>/figures/. Returns the saved filename.

    `bbox` values are in [0,1] in the source image's frame; a small
    padding is added before clipping.
    """
    if len(bbox) != 4:
        raise ValueError(f"figure_bbox must have 4 values, got {len(bbox)}")
    rotation = int(rotation)
    if rotation not in _CW_ROTATION:
        raise ValueError(
            f"figure_rotation must be 0, 90, 180, or 270 (got {rotation})"
        )
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    x0 = max(0.0, x0 - FIGURE_PADDING)
    y0 = max(0.0, y0 - FIGURE_PADDING)
    x1 = min(1.0, x1 + FIGURE_PADDING)
    y1 = min(1.0, y1 + FIGURE_PADDING)

    src_path = UPLOADS_DIR / source_image
    if not src_path.exists():
        raise FileNotFoundError(f"source image not found: {src_path}")

    fdir = figures_dir()
    fdir.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        px_box = (
            int(round(x0 * w)),
            int(round(y0 * h)),
            int(round(x1 * w)),
            int(round(y1 * h)),
        )
        if px_box[2] - px_box[0] < 1 or px_box[3] - px_box[1] < 1:
            raise ValueError(f"figure_bbox crops to empty region: {bbox}")
        cropped = im.crop(px_box)

    transpose_op = _CW_ROTATION[rotation]
    if transpose_op is not None:
        cropped = cropped.transpose(transpose_op)

    filename = f"{uuid.uuid4()}.png"
    cropped.save(fdir / filename, "PNG")
    return filename


def figure_path(filename: str) -> Path:
    return figures_dir() / filename


def save_problem(
    problem_text: str,
    category: str,
    solution: str = "",
    source_image: str | None = None,
    figure_image: str | None = None,
    solve_time_seconds: float | None = None,
    solve_time_estimated: bool = False,
) -> dict:
    pdir = problems_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    problem_id = str(uuid.uuid4())
    record = {
        "id": problem_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "problem_text": problem_text,
        "category": category,
        "solve_time_seconds": solve_time_seconds,
        "solve_time_estimated": solve_time_estimated,
        "solution": solution,
        "source_image": source_image,
        "figure_image": figure_image or None,
    }
    path = pdir / f"{problem_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    with _connect() as conn:
        _upsert_index_row(conn, record)
    return record


def update_problem(problem_id: str, **fields) -> dict:
    path = problems_dir() / f"{problem_id}.json"
    record = json.loads(path.read_text())
    record.update(fields)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    with _connect() as conn:
        _upsert_index_row(conn, record)
    return record


def delete_problem(problem_id: str) -> bool:
    path = problems_dir() / f"{problem_id}.json"
    record = None
    if path.exists():
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError:
            record = None
        path.unlink()
    figure = (record or {}).get("figure_image") if record else None
    if figure:
        fpath = figures_dir() / figure
        if fpath.exists():
            fpath.unlink()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM problems WHERE id = ?", (problem_id,))
        deleted_rows = cur.rowcount
    return record is not None or deleted_rows > 0


def get_problem(problem_id: str) -> dict | None:
    path = problems_dir() / f"{problem_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def list_problems() -> list[dict]:
    pdir = problems_dir()
    if not pdir.exists():
        return []
    out = []
    for p in sorted(pdir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
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
) -> tuple[int, list[dict]]:
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


DIFFICULTY_BUCKETS = [
    ("Easy (<60s)", 0.0, 60.0),
    ("Medium (1–3m)", 60.0, 180.0),
    ("Hard (3–10m)", 180.0, 600.0),
    ("Very Hard (>10m)", 600.0, float("inf")),
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
    buckets = [{"label": label, "count": 0} for label, _, _ in DIFFICULTY_BUCKETS]
    unknown = 0
    for r in rows:
        t = r["solve_time_seconds"]
        if t is None:
            unknown += 1
            continue
        for i, (_, lo, hi) in enumerate(DIFFICULTY_BUCKETS):
            if lo <= t < hi:
                buckets[i]["count"] += 1
                break
    if unknown:
        buckets.append({"label": "Unknown", "count": unknown})
    return buckets


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
    if max_time is None:
        slider_max = 60
    else:
        import math

        slider_max = max(1, int(math.ceil(max_time)))
    return {
        "categories": cats,
        "max_time": slider_max,
        "total": total,
    }
