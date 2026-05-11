"""Public surface for the storage package — re-exports submodule symbols."""

from .paths import (
    DATA_DIR,
    REPO_ROOT,
    UPLOADS_DIR,
    figure_path,
    figures_dir,
    index_path,
    problems_dir,
    reset_current_user,
    sanitize_email,
    set_current_user,
    user_dir,
)
from .problem_io import (
    delete_problem,
    get_problem,
    list_problems,
    save_problem,
    update_problem,
)
from .sql_index import init_index, query_index, sample_index
from .stats import category_counts, difficulty_distribution, index_summary
from .vocab import DIFFICULTY_BUCKETS, Bucket, Problem

__all__ = [
    "DATA_DIR",
    "DIFFICULTY_BUCKETS",
    "Bucket",
    "Problem",
    "REPO_ROOT",
    "UPLOADS_DIR",
    "category_counts",
    "delete_problem",
    "difficulty_distribution",
    "figure_path",
    "figures_dir",
    "get_problem",
    "index_path",
    "index_summary",
    "init_index",
    "list_problems",
    "problems_dir",
    "query_index",
    "reset_current_user",
    "sample_index",
    "sanitize_email",
    "save_problem",
    "set_current_user",
    "update_problem",
    "user_dir",
]
