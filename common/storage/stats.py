"""Aggregations powering the stats page."""

import math

from common.storage.db import connect
from common.storage.paths import current_user_id
from common.storage.vocab import DIFFICULTY_BUCKETS


def category_counts() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM problems "
            "WHERE user_id = %s GROUP BY category ORDER BY n DESC, category",
            (current_user_id(),),
        ).fetchall()
    return [{"category": r["category"], "count": r["n"]} for r in rows]


def subcategory_counts(category: str | None = None) -> list[dict]:
    """Counts grouped by subcategory, optionally filtered to one category."""
    where = " WHERE user_id = %s"
    params: list = [current_user_id()]
    if category:
        where += " AND category = %s"
        params.append(category.lower())
    with connect() as conn:
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
    where: list[str] = ["user_id = %s"]
    params: list = [current_user_id()]
    if category:
        where.append("category = %s")
        params.append(category.lower())
    if subcategory:
        where.append("subcategory = %s")
        params.append(subcategory.lower())
    clause = " WHERE " + " AND ".join(where)
    with connect() as conn:
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
    user = current_user_id()
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category, subcategory FROM problems "
            "WHERE user_id = %s ORDER BY category, subcategory",
            (user,),
        ).fetchall()
        max_time = conn.execute(
            "SELECT MAX(solve_time_seconds) AS m FROM problems WHERE user_id = %s",
            (user,),
        ).fetchone()["m"]
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM problems WHERE user_id = %s", (user,)
        ).fetchone()["n"]
        exam_rows = conn.execute(
            "SELECT DISTINCT source_exam, subexam FROM problems "
            "WHERE user_id = %s ORDER BY source_exam, subexam",
            (user,),
        ).fetchall()
        year_rows = conn.execute(
            "SELECT DISTINCT year FROM problems WHERE user_id = %s "
            "ORDER BY year DESC",
            (user,),
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
