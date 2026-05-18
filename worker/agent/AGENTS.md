# Worker agent package

Two-stage pipeline that turns one uploaded image into N saved problem
records: one orchestrator LLM call parses the source into a structured
problem list, then a plain Python `asyncio.gather` fans the list out to
per-problem solver agents. Lives under `worker/` because only the
offline worker calls into it — the webapp's request path never imports
this package.

## Files

- [__init__.py](__init__.py) — public surface (`process_image`,
  `process_images`, `ProcessImageInput`, `ProcessImageResult`,
  `build_problem_store`).
- [orchestrator.py](orchestrator.py) — outer driver. Runs ONE
  orchestrator query whose only tool is
  `mcp__orchestrator__report_problems`; the model reads the image and
  calls it once with the full structured list. The Python loop stamps
  each parsed problem with `seq_no` (its 1-indexed position in the
  source), drops any `(source_image, seq_no)` already present in the
  index, and then `asyncio.gather`s `solve_problem` over the remaining
  list with a `SOLVER_CONCURRENCY`-sized semaphore. Sync wrapper
  `process_image(...)` calls `asyncio.run` internally — do not call it
  from inside an existing event loop.
- [solver.py](solver.py) — per-problem inner agent. `solve_problem(parsed,
  source_image, with_solution)` crops the figure via
  [../../common/figures.py](../../common/figures.py) if
  `figure_bbox` is non-empty, then runs a fresh `query()` with the
  solver system prompt, measures wall-clock duration, and patches
  `solve_time_seconds` on the saved record when `with_solution=True`.
- [problem_store.py](problem_store.py) — in-process MCP server exposing
  `save_problem` and `lookup_category_edits`. The `save_problem` schema
  is shaped at runtime by `with_solution`. `lookup_category_edits`
  wraps `storage.category_edit_examples` — the solver must call it once
  with its tentative category before `save_problem` will accept;
  `save_problem` returns `is_error` until then.

## Prompts

- The orchestrator prompt is worker-local at
  [../prompts/orchestrator.md](../prompts/orchestrator.md). Only
  `orchestrator.py` reads it; no other component depends on it.
- The solver prompt template lives in the shared
  [../../common/prompts/solver.md](../../common/prompts/solver.md)
  because the webapp's `refine.md` `{% include %}`s it. `solver.py`
  loads it via `common.agent_util.PROMPTS_DIR`. Keep them in one place
  to avoid drift.

## Flow

```
process_image(image_path, source_image, with_solution)
  ├─ orchestrator query (system: worker/prompts/orchestrator.md)
  │    ├─ Read(image_path)
  │    └─ mcp__orchestrator__report_problems(problems=[...])   # exactly once
  │
  └─ asyncio.gather over parsed problems (bounded by SOLVER_CONCURRENCY):
       solve_problem(parsed, source_image, with_solution)
         ├─ figures.save_figure(...) if bbox non-empty
         └─ inner solver query (system: common/prompts/solver.md)
              ├─ Read(figure) if figure was cropped
              ├─ mcp__problem_store__lookup_category_edits(category)
              └─ mcp__problem_store__save_problem(...)
```

Time fields follow this convention: `solve_time_seconds` stores the
real measured elapsed time (NULL until a solution is produced);
`solve_time_estimated` stores the model's pre-solve estimate in integer
seconds. When `with_solution=True` the solver patches
`solve_time_seconds` to the measured elapsed time after the inner query
returns; when `with_solution=False` the model writes
`solve_time_estimated` itself via `save_problem`.

## Conventions

- `ORCHESTRATOR_MAX_TURNS = 4`, `SOLVER_MAX_TURNS = 7`,
  `SOLVER_CONCURRENCY = 4`. Bump only with a reason — runaway tool
  loops or rate-limit pressure are the failure modes.
- The orchestrator's allowed tools are exactly
  `["Read", "mcp__orchestrator__report_problems"]`. The inner solver's
  are `["mcp__problem_store__save_problem",
  "mcp__problem_store__lookup_category_edits"]` plus `"Read"` only when
  a figure was successfully cropped.
- `save_problem` schema:
  - `with_solution=True`: `{problem_text, category, subcategory,
    solution}` — `solve_time_seconds` is filled in afterwards from
    measured wall-clock.
  - `with_solution=False`: `{problem_text, category, subcategory,
    solve_time_estimated}` — Claude's own integer-seconds estimate.
- Every assistant / tool / result message is logged via `log_message`
  for debuggability.

## Don't

- Don't put any natural-language decision step between problems back
  into the orchestrator — it's intentionally a single parse call
  followed by deterministic Python fan-out.
- Don't move `solver.md` into `worker/prompts/`. It's `{% include %}`'d
  by `common/prompts/refine.md`, so it must stay reachable by the
  shared Jinja loader.
- Don't import from `webapp/src/`. Worker agent code may depend only on
  `common.*` and the `claude_agent_sdk` / `PIL` / `pypdfium2` packages.
