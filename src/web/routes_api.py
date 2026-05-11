"""JSON API endpoints under /api."""

from flask import Blueprint, jsonify, request

from lib import storage

from .auth import login_required, upload_allowed_required

DEFAULT_PAGE_SIZE = 5
MAX_PAGE_SIZE = 50
MAX_SAMPLE_SIZE = 200

bp = Blueprint("api", __name__, url_prefix="/api")


def _parse_float(name: str) -> float | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_filters() -> dict:
    category = request.args.get("category") or None
    full_range_max = _parse_float("range_max")
    return {
        "category": category,
        "min_time": _parse_float("min_time"),
        "max_time": _parse_float("max_time"),
        "full_range_max": full_range_max,
    }


@bp.route("/stats/categories", methods=["GET"])
@login_required
def stats_categories():
    return jsonify({"categories": storage.category_counts()})


@bp.route("/stats/difficulty", methods=["GET"])
@login_required
def stats_difficulty():
    category = request.args.get("category") or None
    return jsonify(
        {
            "category": category,
            "buckets": storage.difficulty_distribution(category),
        }
    )


@bp.route("/summary", methods=["GET"])
@login_required
def summary():
    return jsonify(storage.index_summary())


@bp.route("/problems", methods=["GET"])
@login_required
def problems():
    filters = _parse_filters()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = int(request.args.get("page_size", DEFAULT_PAGE_SIZE))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    page_size = max(1, min(MAX_PAGE_SIZE, page_size))

    total, ids = storage.query_index(
        page=page, page_size=page_size, **filters
    )
    rows = [
        p.to_dict() for p in (storage.get_problem(pid) for pid in ids) if p
    ]
    return jsonify(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "problems": rows,
        }
    )


@bp.route("/problems/<problem_id>", methods=["DELETE"])
@login_required
@upload_allowed_required
def delete_problem(problem_id):
    deleted = storage.delete_problem(problem_id)
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": problem_id})


@bp.route("/sample", methods=["GET"])
@login_required
def sample():
    filters = _parse_filters()
    try:
        n = int(request.args.get("n", DEFAULT_PAGE_SIZE))
    except ValueError:
        n = DEFAULT_PAGE_SIZE
    n = max(1, min(MAX_SAMPLE_SIZE, n))
    ids = storage.sample_index(n, **filters)
    rows = [
        p.to_dict() for p in (storage.get_problem(pid) for pid in ids) if p
    ]
    return jsonify({"problems": rows})
