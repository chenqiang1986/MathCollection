# Agent package

Two-stage pipeline that turns one uploaded image into N saved problem records:
one orchestrator LLM call parses the source into a structured problem list,
then a plain Python `asyncio.gather` fans the list out to per-problem
solver agents.

## Files

- [__init__.py](__init__.py) — public surface (`process_image`,
  `ProcessImageResult`, `build_problem_store`, `MODEL`, `log_message`).
- [orchestrator.py](orchestrator.py) — outer driver. Runs ONE orchestrator
  query whose only tool is `mcp__orchestrator__report_problems`; the model
  reads the image and calls it once with the full structured list. The
  Python loop then `asyncio.gather`s `solve_problem` over that list with a
  `SOLVER_CONCURRENCY`-sized semaphore. Sync wrapper `process_image(...)`
  calls `asyncio.run` internally — do not call it from inside an existing
  event loop. No natural-language branching happens between problems, so
  there is no inter-problem agent loop.
- [solver.py](solver.py) — per-problem inner agent. `solve_problem(parsed,
  source_image, with_solution)` crops the figure via
  [../../figures.py](../../figures.py) if `figure_bbox` is non-empty, then
  runs a fresh `query()` with the solver system prompt, measures
  wall-clock duration, and patches `solve_time_seconds` on the saved
  record when `with_solution=True`.
- [problem_store.py](problem_store.py) — in-process MCP server exposing
  `save_problem` and `lookup_category_edits`. The `save_problem` schema is
  shaped at runtime by `with_solution` (see below); keep it in sync with
  `storage.save_problem`'s kwargs. `lookup_category_edits` wraps
  `storage.category_edit_examples` — the solver must call it once with its
  tentative category before `save_problem` will accept; `save_problem`
  returns `is_error` until then. This replaces the older post-save
  reviewer agent. Refine uses its own update-only store and intentionally
  skips this check.
- [refine.py](refine.py) — agent for in-place correction of an existing
  saved problem. Driven by a single LLM call whose system prompt is
  [../../prompts/refine.md](../../prompts/refine.md) and whose tools are
  three structured-output actions (one MCP server, `refine_store`):
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
- [util.py](util.py) — shared constants and the `log_message` printer used
  for tracing every assistant / tool / result message.

## Flow

```
process_image(image_path, source_image, with_solution)
  ├─ orchestrator query (system: prompts/orchestrator.md)
  │    ├─ Read(image_path)
  │    └─ mcp__orchestrator__report_problems(problems=[...])    # exactly once
  │
  └─ asyncio.gather over parsed problems (bounded by SOLVER_CONCURRENCY):
       solve_problem(parsed, source_image, with_solution)
         ├─ figures.save_figure(...) if bbox non-empty
         └─ inner solver query (system: prompts/solver.md rendered with with_solution)
              ├─ Read(figure) if figure was cropped
              ├─ mcp__problem_store__lookup_category_edits(category)
              │    └─ storage.category_edit_examples(...)  →  list[dict]
              └─ mcp__problem_store__save_problem(...)
                   └─ storage.save_problem(...)  →  Problem
```

After the inner query returns, when `with_solution=True` the solver patches
`solve_time_seconds` to the measured elapsed time and sets
`solve_time_estimated=False`. With `with_solution=False`, the model's own
estimate is kept and `solve_time_estimated=True`.

## Conventions

- `MODEL = "claude-sonnet-4-6"` lives in [util.py](util.py); don't hardcode it
  elsewhere.
- `ORCHESTRATOR_MAX_TURNS = 4` (Read + report_problems + a small slack
  margin), `SOLVER_MAX_TURNS = 7`, `SOLVER_CONCURRENCY = 4`,
  `REFINE_MAX_TURNS = 8` (one extra for the routing decision plus an
  optional Read of source + figure). Bump only with a reason — runaway
  tool loops or rate-limit pressure are the failure modes.
- The orchestrator's allowed tools are exactly
  `["Read", "mcp__orchestrator__report_problems"]`. The inner solver's are
  `["mcp__problem_store__save_problem",
  "mcp__problem_store__lookup_category_edits"]` plus `"Read"` only when a
  figure was successfully cropped.
- `report_problems` takes a single `problems: list` arg; per-problem
  fields are documented in [../../prompts/orchestrator.md](../../prompts/orchestrator.md).
  Empty `figure_bbox` means no figure. Crop errors are caught per problem
  by the dispatch loop, which logs them and skips that problem — the
  batch as a whole still returns.
- `save_problem` schema:
  - `with_solution=True`: `{problem_text, category, solution}` —
    `solve_time_seconds` is filled in afterwards from measured wall-clock.
  - `with_solution=False`: `{problem_text, category, solve_time_seconds}` —
    Claude's own estimate.
- Every assistant / tool / result message is logged via `log_message` for
  debuggability. Keep the truncation budget small (300 chars) so logs stay
  readable.

## Don't

- Don't put any natural-language decision step between problems back into
  the orchestrator — it's intentionally a single parse call followed by
  deterministic Python fan-out. If a future feature genuinely needs the
  model to react to a solver result, that's a different agent, not a
  re-promotion of the orchestrator.
- Don't append model overrides in Python; edit
  [../../prompts/](../../prompts/) instead.
- Don't import the Flask layer from here. Agent code should depend on
  `lib.storage` and `figures` only.
