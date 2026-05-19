"""Worker-only agent code: the two-stage scan + solve pipeline that turns
one uploaded image into N saved problems. Lives under [worker/](../)
because it is invoked only by the offline worker — the webapp request
path no longer touches the agent SDK on upload.

Stage 1 (`scan_image`) saves partial problems with category
`unclassified`. Stage 2 (`solve_pending_problems`) walks those partials
and updates them with category/subcategory/solution.

Shared agent helpers (`MODEL`, `log_message`, `PROMPTS_DIR`) stay in
[../../common/agent_util.py](../../common/agent_util.py) because the
webapp's `refine_problem` and `backfill/classify` also use them. The
solver prompt template lives in the shared `webapp/src/prompts/` dir for
the same reason — refine.md `{% include %}`s it. The orchestrator prompt
is worker-only and lives in [../prompts/orchestrator.md](../prompts/orchestrator.md).
"""

from .orchestrator import (
    ProcessImageResult,
    StageResult,
    scan_image,
    solve_pending_problems,
)
from .problem_store import UNCLASSIFIED_CATEGORY, build_problem_store

__all__ = [
    "ProcessImageResult",
    "StageResult",
    "UNCLASSIFIED_CATEGORY",
    "build_problem_store",
    "scan_image",
    "solve_pending_problems",
]
