"""Worker-only agent code: image scan + batch classify, the two independent
jobs that turn uploaded images into categorized problems. Lives under
[worker/](../) because it is invoked only by the offline worker — the
webapp request path no longer touches the agent SDK on upload.

`scan_image` saves partial problems with category `unclassified`, tracked
per source file. `classify_pending_problems` sweeps a flat backlog of
`unclassified` problems (independent of source file) and updates them
with category/subcategory in batched sessions.

Shared agent helpers (`MODEL`, `log_message`, `PROMPTS_DIR`) stay in
[../../common/agent_util.py](../../common/agent_util.py) because the
webapp's `refine_problem` also uses them. The classifier prompt template
lives in the shared `common/prompts/` dir for the same reason
(`math_category.md` is `{% include %}`d by both). The orchestrator prompt
is worker-only and lives in [../prompts/orchestrator.md](../prompts/orchestrator.md).
"""

from worker.agent.orchestrator import (
    NO_CLASSIFY_WORK_SUMMARY,
    StageResult,
    classify_pending_problems,
    scan_image,
)
from worker.agent.problem_store import (
    UNCLASSIFIED_CATEGORY,
    build_classify_store,
    build_scan_store,
)

__all__ = [
    "NO_CLASSIFY_WORK_SUMMARY",
    "StageResult",
    "UNCLASSIFIED_CATEGORY",
    "build_classify_store",
    "build_scan_store",
    "classify_pending_problems",
    "scan_image",
]
