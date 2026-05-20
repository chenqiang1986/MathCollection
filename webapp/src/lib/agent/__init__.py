"""Webapp-side agent surface: just refine.

The orchestrator + solver pipeline used by the offline worker
(`scan_image` and `solve_pending_problems`) lives at `worker/agent/`.
The webapp's only remaining agent dependency is `refine_problem`, hit by
`POST /api/problems/<id>/refine`. Shared agent helpers (`MODEL`,
`log_message`, `PROMPTS_DIR`, `MAX_BUFFER_SIZE`) have moved to
`common.agent_util` so the worker doesn't depend on the webapp.
"""

from webapp.src.lib.agent.refine import refine_problem

__all__ = ["refine_problem"]
