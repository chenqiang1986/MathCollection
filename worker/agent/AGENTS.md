# Worker agent package

Two-stage pipeline that turns one uploaded image into N saved problem
records. The two stages are independent functions driven by the worker
runner so a quota hit or partial result in one stage can't roll back the
other.

- **Stage 1 — `scan_image(image_path, source_image)`.** One orchestrator
  LLM call extracts every problem out of the source and persists each as
  a partial record (`category='unclassified'`, no solution) via
  `build_problem_store(mode="parsed")`. Saved by the runner as the
  `processing_image_scan -> pending_problem_solve` transition.
- **Stage 2 — `solve_pending_problems(source_image, with_solution)`.**
  Looks up partials by `(source_image, category='unclassified')` and
  fans them out to per-problem solver agents with
  `SOLVER_CONCURRENCY`-sized concurrency. Each solver updates one
  partial in place via `build_problem_store(mode="solved",
  existing_problem_id=...)`. Saved by the runner as
  `processing_problem_solve -> done`.

## Files

- [__init__.py](__init__.py) — public surface (`scan_image`,
  `solve_pending_problems`, `StageResult` / `ProcessImageResult`,
  `build_problem_store`, `UNCLASSIFIED_CATEGORY`).
- [orchestrator.py](orchestrator.py) — the two stage drivers
  (`scan_image`, `solve_pending_problems`) plus their `_async`
  implementations. Sync wrappers each call `asyncio.run` internally — do
  not call from inside an existing event loop.
- [solver.py](solver.py) — per-problem inner agent. `solve_problem(partial,
  with_solution)` takes a partial `storage.Problem` saved by stage 1,
  runs a fresh `query()` with the solver system prompt against the
  partial's text + figure, measures wall-clock duration, and patches
  `solve_time_seconds` on the updated record when `with_solution=True`.
- [problem_store.py](problem_store.py) — in-process MCP server with two
  modes:
  - `mode="parsed"` exposes `save_parsed_problem`. Each call crops the
    figure (if any) and inserts a partial via `storage.save_problem`
    with `category='unclassified'`, `solution=''`. Calls with a
    `seq_no` already saved for `source_image` are skipped so retries
    don't duplicate.
  - `mode="solved"` exposes `save_problem` and `lookup_category_edits`.
    `save_problem` calls `storage.update_problem(existing_problem_id,
    ...)` so the partial keeps its ID, seq_no, figure_image, and
    source metadata. `lookup_category_edits` must run first before
    `save_problem` will accept, same as before.

## Prompts

- The orchestrator prompt is worker-local at
  [../prompts/orchestrator.md](../prompts/orchestrator.md). Only
  stage 1 reads it; no other component depends on it.
- The solver prompt template lives in the shared
  [../../common/prompts/solver.md](../../common/prompts/solver.md)
  because the webapp's `refine.md` `{% include %}`s it. `solver.py`
  loads it via `common.agent_util.PROMPTS_DIR`. Keep them in one place
  to avoid drift.

## Flow

```
Stage 1 — scan_image(image_path, source_image)
  └─ orchestrator query (system: worker/prompts/orchestrator.md)
       ├─ Read(image_path)
       └─ mcp__problem_store__save_parsed_problem(...)   # once per problem
            └─ figures.save_figure(...) if bbox non-empty
            └─ storage.save_problem(category='unclassified', solution='')

Stage 2 — solve_pending_problems(source_image, with_solution)
  └─ storage.problems_by_source_and_category(source_image, 'unclassified')
  └─ asyncio.gather over partials (bounded by SOLVER_CONCURRENCY):
       solve_problem(partial, with_solution)
         └─ inner solver query (system: common/prompts/solver.md)
              ├─ Read(figure) if partial.figure_image is set
              ├─ mcp__problem_store__lookup_category_edits(category)
              └─ mcp__problem_store__save_problem(...)
                   └─ storage.update_problem(existing_problem_id, ...)
```

Time fields follow this convention: `solve_time_seconds` stores the
real measured elapsed time (NULL on partials, set when the solver
finishes); `solve_time_estimated` stores the model's pre-solve estimate
in integer seconds. When `with_solution=True` the solver patches
`solve_time_seconds` to the measured elapsed time after the inner query
returns; when `with_solution=False` the model writes
`solve_time_estimated` itself via `save_problem`.

## Conventions

- `ORCHESTRATOR_MAX_TURNS = 4`, `SOLVER_MAX_TURNS = 7`,
  `SOLVER_CONCURRENCY = 4`. Bump only with a reason — runaway tool
  loops or rate-limit pressure are the failure modes.
- Stage 1's allowed tools are exactly
  `["Read", "mcp__problem_store__save_parsed_problem"]`. The inner
  solver's are `["mcp__problem_store__save_problem",
  "mcp__problem_store__lookup_category_edits"]` plus `"Read"` only when
  the partial has a `figure_image`.
- `save_problem` schema (stage 2):
  - `with_solution=True`: `{problem_text, category, subcategory,
    solution}` — `solve_time_seconds` is filled in afterwards from
    measured wall-clock.
  - `with_solution=False`: `{problem_text, category, subcategory,
    solve_time_estimated}` — Claude's own integer-seconds estimate.
- Every assistant / tool / result message is logged via `log_message`
  for debuggability.

## Don't

- Don't merge the two stages back into a single `process_image` call.
  The queue's per-stage retry budget and per-stage quota reversion both
  rely on the split.
- Don't move `solver.md` into `worker/prompts/`. It's `{% include %}`'d
  by `common/prompts/refine.md`, so it must stay reachable by the
  shared Jinja loader.
- Don't import from `webapp/src/`. Worker agent code may depend only on
  `common.*` and the `claude_agent_sdk` / `PIL` / `pypdfium2` packages.
- Don't run the solver against a problem whose category is something
  other than `unclassified` from this entry point — `solve_pending_problems`
  is the only caller and it filters by that placeholder. If a caller
  needs to re-classify an already-saved problem, that's the refine flow,
  not this one.
