# Worker prompts

Prompts that are read **only** by the worker's agent code. Shared
prompts (`solver.md`, `math_category.md`, `refine.md`) stay in
[../../common/prompts/](../../common/prompts/) because the
webapp's `refine` agent and `backfill/classify` also depend on them.

## Files

- [orchestrator.md](orchestrator.md) — system prompt for the outer
  orchestrator agent (see [../agent/orchestrator.py](../agent/orchestrator.py)).
  Loaded as plain text. Defines the per-problem field contract emitted
  by `mcp__orchestrator__report_problems`, including the figure
  bbox/rotation conventions consumed by
  [../../common/figures.py](../../common/figures.py).

## Conventions

- Math delimiters: `$...$` / `$$...$$`. Literal USD dollar sign must be
  escaped as `\$` so KaTeX doesn't treat it as a math span opener.
- Don't add prompt overrides in Python — edit this file instead.
