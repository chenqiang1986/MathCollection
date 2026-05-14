# Prompts

System prompts for the two Claude agents. **Edit these instead of splicing
override strings in Python.**

## Files

- [orchestrator.md](orchestrator.md) — plain text. Loaded once and used as
  the `system_prompt` for the outer agent (see
  [../lib/agent/orchestrator.py](../lib/agent/orchestrator.py)).
- [solver.md](solver.md) — Jinja2 template rendered with one variable,
  `with_solution: bool` (see [../lib/agent/solver.py](../lib/agent/solver.py)).
  The two branches must stay aligned with the corresponding `save_problem`
  tool schema in
  [../lib/agent/problem_store.py](../lib/agent/problem_store.py):
  - `with_solution=True` → solver writes a `solution`; tool takes
    `{problem_text, category, solution}`.
  - `with_solution=False` → solver estimates `solve_time_seconds`; tool takes
    `{problem_text, category, solve_time_seconds}`.
  Both branches must also instruct the solver to call
  `lookup_category_edits` before `save_problem` — the save tool refuses the
  first call until the lookup has been invoked.

## Conventions

- **Math delimiters in prompts must say `$...$` / `$$...$$`** — the page
  renders KaTeX with exactly those delimiters. A literal USD dollar sign
  must be escaped as `\$`, otherwise the renderer will treat it as the
  opening of a math span.
- **The orchestrator must delegate**, not solve. The solver must call its
  `save_problem` tool **exactly once**. If you loosen either rule in the
  prompt, the agent loop and post-processing in
  [../lib/agent/solver.py](../lib/agent/solver.py) (which asserts
  `len(saved) == 1`) will break.
- **Figure bbox/rotation contract** is defined in
  [orchestrator.md](orchestrator.md): normalized `[x0, y0, x1, y1]` in
  `[0, 1]` with `x0<x1`, `y0<y1`, plus a clockwise rotation of
  `0`/`90`/`180`/`270`. Empty list + `0` means no figure. Keep these in
  sync with [../figures.py](../figures.py).

## Don't

- Don't paste prompt overrides into Python. If a rule is conditional on
  `with_solution`, express it with Jinja2 in `solver.md`.
- Don't change the tool names referenced in these prompts
  (`mcp__solver__solve_and_save`, `mcp__problem_store__save_problem`,
  `mcp__problem_store__lookup_category_edits`) without updating the MCP
  servers.
