"""Aggregations powering the stats page."""

import math

from .sql_index import _connect
from .vocab import DIFFICULTY_BUCKETS


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
