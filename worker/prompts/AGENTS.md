# Worker prompts

Prompts that are read **only** by the worker's image-scan job. Shared
prompts (`solver.md`, `classifier.md`, `math_category.md`, `refine.md`)
stay in [../../common/prompts/](../../common/prompts/) because they're
each depended on by more than one entry point (the webapp's `refine`
agent, the worker's batch classifier, `backfill/classify`).

## Files

- [orchestrator.md](orchestrator.md) — system prompt for the image-scan
  agent (see [../agent/orchestrator.py](../agent/orchestrator.py)).
  Loaded as plain text. Defines the per-problem field contract emitted
  by `mcp__problem_store__save_parsed_problem`, including the figure
  bbox/rotation conventions consumed by
  [../../common/figures.py](../../common/figures.py).

## Conventions

- Math delimiters: `$...$` / `$$...$$`. Literal USD dollar sign must be
  escaped as `\$` so KaTeX doesn't treat it as a math span opener.
- Don't add prompt overrides in Python — edit this file instead.
