"""Public surface for the storage package — re-exports submodule symbols."""

from .paths import (
    DATA_DIR,
    REPO_ROOT,
    figure_path,
    figures_dir,
    index_path,
    problems_dir,
    queue_path,
    raw_upload_path,
    raw_uploads_dir,
    reset_current_user,
    sanitize_email,
    set_current_user,
    user_dir,
)
from .category_edits import category_edit_examples, record_category_edit
from .problem_io import (
    delete_problem,
    get_problem,
    list_problems,
    save_problem,
    update_problem,
)
from .queue import (
    QueueItem,
    claim_next,
    enqueue_raw,
    mark_done,
    mark_failed,
    pending_count,
    reclaim_stale_processing,
    revert_to_pending,
)
from .sql_index import existing_seq_nos, query_index, sample_index
from .stats import (
    category_counts,
    difficulty_distribution,
    index_summary,
    subcategory_counts,
)
from .vocab import DIFFICULTY_BUCKETS, Bucket, Problem

__all__ = [
    "DATA_DIR",
    "DIFFICULTY_BUCKETS",
    "Bucket",
    "Problem",
    "QueueItem",
    "REPO_ROOT",
    "category_counts",
    "category_edit_examples",
    "claim_next",
    "delete_problem",
    "difficulty_distribution",
    "enqueue_raw",
    "existing_seq_nos",
    "figure_path",
    "figures_dir",
    "get_problem",
    "index_path",
    "index_summary",
    "list_problems",
    "mark_done",
    "mark_failed",
    "pending_count",
    "problems_dir",
    "query_index",
    "queue_path",
    "raw_upload_path",
    "raw_uploads_dir",
    "reclaim_stale_processing",
    "record_category_edit",
    "reset_current_user",
    "revert_to_pending",
    "sample_index",
    "sanitize_email",
    "save_problem",
    "set_current_user",
    "subcategory_counts",
    "update_problem",
    "user_dir",
]
