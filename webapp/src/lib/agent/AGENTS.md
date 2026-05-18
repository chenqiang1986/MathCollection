# Agent package (webapp-side)

Only the **refine** flow lives here. The two-tier orchestrator + solver
pipeline used by the offline worker is in
[../../../../worker/agent/](../../../../worker/agent/). Shared agent
helpers (`MODEL`, `log_message`, `PROMPTS_DIR`, `MAX_BUFFER_SIZE`) live
in [../../../../common/agent_util.py](../../../../common/agent_util.py).

## Files

- [__init__.py](__init__.py) — public surface for the webapp:
  `refine_problem` only.
- [refine.py](refine.py) — agent for in-place correction of an existing
  saved problem. Driven by a single LLM call whose system prompt is
  [../../../../common/prompts/refine.md](../../../../common/prompts/refine.md)
  and whose tools are three structured-output actions (one MCP server,
  `refine_store`):
  `resolve_with_hint` (rewrites `category`/`subcategory`/`solution`),
  `update_figure_bbox` (re-crops the figure via `figures.save_figure` and
  rewrites `figure_image`/`figure_bbox`; deletes the old crop), and
  `update_problem_text` (rewrites `problem_text`). The agent picks
  exactly one based on the user's free-form request — this is the
  decision boundary, not a Python heuristic. Empty user messages raise
  before any LLM call. Bbox and text actions do NOT re-solve; the user
  triggers a follow-up `resolve_with_hint` if they want the solution
  updated. `Read` is allowed when either `source_image` or
  `figure_image` is present; the source's filesystem path and the
  problem's `source_page` are surfaced in the prompt so the model can
  re-crop/re-transcribe.

## Conventions

- `MODEL` and friends are imported from
  [../../../../common/agent_util.py](../../../../common/agent_util.py);
  don't hardcode the model name here.
- `REFINE_MAX_TURNS = 8` (one extra for the routing decision plus an
  optional Read of source + figure).
- Refine, when it produces a solution on a problem that had no prior
  real solve time, patches `solve_time_seconds` to the refine elapsed
  time.
- Refine uses its own update-only `refine_store`; it intentionally
  skips the `lookup_category_edits` check that the worker's solver
  enforces.

## Don't

- Don't append model overrides in Python; edit
  [../../../../common/prompts/refine.md](../../../../common/prompts/refine.md) instead.
- Don't import the Flask layer from here.
- Don't put the orchestrator/solver code back here — it lives in
  [../../../../worker/agent/](../../../../worker/agent/) so the webapp
  process doesn't load the orchestrator's SDK setup at import time.
