"""Aggregations powering the stats page."""

import math

from common.storage.sql_index import _connect
from common.storage.vocab import DIFFICULTY_BUCKETS


def category_counts() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM problems "
            "GROUP BY category ORDER BY n DESC, category"
        ).fetchall()
    return [{"category": r["category"], "count": r["n"]} for r in rows]


def subcategory_counts(category: str | None = None) -> list[dict]:
    """Counts grouped by subcategory, optionally filtered to one category."""
    where = ""
    params: list = []
    if category:
        where = " WHERE category = ?"
        params.append(category.lower())
    with _connect() as conn:
        rows = conn.execute(
            "SELECT category, subcategory, COUNT(*) AS n FROM problems"
            f"{where} GROUP BY category, subcategory "
            "ORDER BY category, n DESC, subcategory",
            params,
        ).fetchall()
    return [
        {
            "category": r["category"],
            "subcategory": r["subcategory"],
            "count": r["n"],
        }
        for r in rows
    ]


def difficulty_distribution(
    category: str | None = None, subcategory: str | None = None
) -> list[dict]:
    where: list[str] = []
    params: list = []
    if category:
        where.append("category = ?")
        params.append(category.lower())
    if subcategory:
        where.append("subcategory = ?")
        params.append(subcategory.lower())
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT solve_time_seconds FROM problems{clause}", params
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
        rows = conn.execute(
            "SELECT DISTINCT category, subcategory FROM problems "
            "ORDER BY category, subcategory"
        ).fetchall()
        max_time = conn.execute(
            "SELECT MAX(solve_time_seconds) FROM problems"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
        exam_rows = conn.execute(
            "SELECT DISTINCT source_exam, subexam FROM problems "
            "ORDER BY source_exam, subexam"
        ).fetchall()
        year_rows = conn.execute(
            "SELECT DISTINCT year FROM problems ORDER BY year DESC"
        ).fetchall()
    # Group subcategories under their parent category, preserving order.
    cat_map: dict[str, list[str]] = {}
    for r in rows:
        subs = cat_map.setdefault(r["category"], [])
        sub = r["subcategory"] or ""
        if sub and sub not in subs:
            subs.append(sub)
    categories = list(cat_map.keys())
    slider_max = 60 if max_time is None else max(1, int(math.ceil(max_time)))
    # Build distinct exam list + per-exam subexam map (preserves SQL order).
    exam_map: dict[str, list[str]] = {}
    for r in exam_rows:
        exam = r["source_exam"]
        if not exam:
            continue
        subs = exam_map.setdefault(exam, [])
        sub = r["subexam"] or ""
        if sub and sub not in subs:
            subs.append(sub)
    exams = list(exam_map.keys())
    years = [r["year"] for r in year_rows if r["year"]]
    return {
        "categories": categories,
        "subcategories": cat_map,
        "max_time": slider_max,
        "total": total,
        "exams": exams,
        "subexams": exam_map,
        "years": years,
    }
