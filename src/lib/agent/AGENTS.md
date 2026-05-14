# Agent package

Two-tier Claude Agent SDK pipeline that turns one uploaded image into N
saved problem records.

## Files

- [__init__.py](__init__.py) — public surface (`process_image`,
  `ProcessImageResult`, `build_problem_store`, `MODEL`, `log_message`).
- [orchestrator.py](orchestrator.py) — outer agent. Reads the image, identifies
  distinct problems, dispatches each one to `mcp__solver__solve_and_save`,
  returns a `ProcessImageResult(saved, summary)`. Sync wrapper
  `process_image(...)` calls `asyncio.run` internally — do not call it from
  inside an existing event loop.
- [solver.py](solver.py) — inner agent. The `solve_and_save` MCP tool spawns a
  fresh `query()` per problem with the solver system prompt, optionally crops
  a figure via [../../figures.py](../../figures.py), measures wall-clock
  duration, and patches the saved record's `solve_time_seconds` when
  `with_solution=True`.
- [problem_store.py](problem_store.py) — in-process MCP server exposing
  `save_problem`. Its tool schema is shaped at runtime by `with_solution`
  (see below). Keep the schema in sync with `storage.save_problem`'s kwargs.
- [recategorize.py](recategorize.py) — optional second-pass reviewer invoked
  by the solver after the inner save. If the per-user `category_edits` table
  has prior edits away from the AI's chosen category, runs a tiny no-tools
  agent (system prompt: `prompts/recategorize.md`) that outputs `KEEP` or
  `SWITCH: <cat>`. Switches via `storage.update_problem` on `SWITCH`.
  Only runs in the extract path; refine intentionally skips it.
- [util.py](util.py) — shared constants and the `log_message` printer used
  for tracing every assistant / tool / result message.

## Flow

```
process_image(image_path, source_image, with_solution)
  └─ orchestrator query (system: prompts/orchestrator.md)
       ├─ Read(image_path)
       └─ for each problem:
            mcp__solver__solve_and_save(problem_text, figure_bbox, figure_rotation)
              ├─ figures.save_figure(...) if bbox non-empty
              └─ inner solver query (system: prompts/solver.md rendered with with_solution)
                   ├─ Read(figure) if figure was cropped
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
- `ORCHESTRATOR_MAX_TURNS = 20`, `SOLVER_MAX_TURNS = 6`. Bump only with a
  reason — runaway tool loops are the failure mode.
- The orchestrator's allowed tools are exactly
  `["Read", "mcp__solver__solve_and_save"]`. The inner solver's are
  `["mcp__problem_store__save_problem"]` plus `"Read"` only when a figure
  was successfully cropped.
- `save_problem` schema:
  - `with_solution=True`: `{problem_text, category, solution}` —
    `solve_time_seconds` is filled in afterwards from measured wall-clock.
  - `with_solution=False`: `{problem_text, category, solve_time_seconds}` —
    Claude's own estimate.
- `solve_and_save` always takes `figure_bbox: list` and `figure_rotation: int`.
  Empty bbox means no figure. Bad bbox returns a tool error so the
  orchestrator can retry with corrected coordinates.
- Every assistant / tool / result message is logged via `log_message` for
  debuggability. Keep the truncation budget small (300 chars) so logs stay
  readable.

## Don't

- Don't let the orchestrator solve problems itself — it must delegate every
  problem via `solve_and_save`.
- Don't batch multiple problems into one `solve_and_save` call.
- Don't append model overrides in Python; edit
  [../../prompts/](../../prompts/) instead.
- Don't import the Flask layer from here. Agent code should depend on
  `lib.storage` and `figures` only.
