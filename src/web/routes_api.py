"""JSON API endpoints under /api."""

from flask import Blueprint, jsonify, request

from lib import agent, storage

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
    subcategory = request.args.get("subcategory") or None
    source_exam = request.args.get("source_exam") or None
    year = request.args.get("year") or None
    full_range_max = _parse_float("range_max")
    raw_has_figure = request.args.get("has_figure")
    if raw_has_figure == "1":
        has_figure: bool | None = True
    elif raw_has_figure == "0":
        has_figure = False
    else:
        has_figure = None
    return {
        "category": category,
        "subcategory": subcategory,
        "min_time": _parse_float("min_time"),
        "max_time": _parse_float("max_time"),
        "full_range_max": full_range_max,
        "source_exam": source_exam,
        "year": year,
        "has_figure": has_figure,
    }


@bp.route("/stats/categories", methods=["GET"])
@login_required
def stats_categories():
    return jsonify({"categories": storage.category_counts()})


@bp.route("/stats/subcategories", methods=["GET"])
@login_required
def stats_subcategories():
    category = request.args.get("category") or None
    return jsonify(
        {
            "category": category,
            "subcategories": storage.subcategory_counts(category),
        }
    )


@bp.route("/stats/difficulty", methods=["GET"])
@login_required
def stats_difficulty():
    category = request.args.get("category") or None
    subcategory = request.args.get("subcategory") or None
    return jsonify(
        {
            "category": category,
            "subcategory": subcategory,
            "buckets": storage.difficulty_distribution(category, subcategory),
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


@bp.route("/problems/<problem_id>/category", methods=["POST"])
@login_required
@upload_allowed_required
def update_category(problem_id):
    problem = storage.get_problem(problem_id)
    if not problem:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    new_category = (payload.get("category") or "").strip().lower()
    if not new_category:
        return jsonify({"error": "category is required"}), 400
    # subcategory is optional; an empty string clears it.
    raw_sub = payload.get("subcategory")
    if raw_sub is None:
        new_subcategory = (problem.subcategory or "").lower()
    else:
        new_subcategory = raw_sub.strip().lower()
    old_category = (problem.category or "").lower()
    old_subcategory = (problem.subcategory or "").lower()
    if new_category == old_category and new_subcategory == old_subcategory:
        return jsonify({"problem": problem.to_dict()})
    updated = storage.update_problem(
        problem_id, category=new_category, subcategory=new_subcategory
    )
    storage.record_category_edit(
        problem_id=problem_id,
        problem_text=problem.problem_text,
        solution=problem.solution,
        from_category=old_category,
        to_category=new_category,
        from_subcategory=old_subcategory,
        to_subcategory=new_subcategory,
    )
    return jsonify({"problem": updated.to_dict()})


@bp.route("/problems/<problem_id>/refine", methods=["POST"])
@login_required
@upload_allowed_required
def refine_problem(problem_id):
    problem = storage.get_problem(problem_id)
    if not problem:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    hint = (payload.get("hint") or "").strip()
    if not hint:
        return jsonify({"error": "hint is required"}), 400
    try:
        updated = agent.refine_problem(problem, hint=hint)
    except Exception as e:
        return jsonify({"error": f"agent error: {e}"}), 500
    return jsonify({"problem": updated.to_dict()})


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
