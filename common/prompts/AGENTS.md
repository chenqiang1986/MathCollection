# Prompts (shared)

System prompts shared across the webapp's `refine` agent, the worker's
batch classifier, and `backfill/classify`. **Edit these instead of
splicing override strings in Python.**

The orchestrator's (image-scan) prompt is worker-only and lives in
[../../worker/prompts/orchestrator.md](../../worker/prompts/orchestrator.md);
it is not loaded by anything in this directory.

## Files

- [math_category.md](math_category.md) — closed list of allowed
  `(category, subcategory)` pairs. `{% include %}`'d by both
  [classifier.md](classifier.md) and [solver.md](solver.md) so every
  entry point picks from a fixed vocabulary instead of inventing
  synonyms. Also read directly by
  [../../backfill/classify.py](../../backfill/classify.py). Edit this
  file (not `classifier.md`/`solver.md`) to add/rename categories.
- [classifier.md](classifier.md) — Jinja2 template for the worker's
  batch classifier (see
  [../../worker/agent/classifier.py](../../worker/agent/classifier.py)).
  One session covers a *batch* of problems, each tagged with an explicit
  `problem_id` in the user prompt. Must instruct the model to call
  `lookup_category_edits` once per problem before `save_classification`
  for that problem, and to call `save_classification` exactly once per
  `problem_id` given — never skipped, never invented — matching the
  membership/ordering checks in
  [../../worker/agent/problem_store.py](../../worker/agent/problem_store.py)'s
  `build_classify_store`.
- [solver.md](solver.md) — Jinja2 template rendered with
  `with_solution=True`, used only by the webapp's ad hoc refine flow now
  (see [refine.md](refine.md) below and
  [../../webapp/src/lib/agent/refine.py](../../webapp/src/lib/agent/refine.py)).
  The automated pipeline no longer calls it — full re-solving only
  happens on demand via `POST /problems/<id>/refine`. Kept in this shared
  directory (not moved under `webapp/`) because it's still
  `{% include %}`'d by [refine.md](refine.md).
- [refine.md](refine.md) — Jinja2 template for the webapp's refine
  agent (see
  [../../webapp/src/lib/agent/refine.py](../../webapp/src/lib/agent/refine.py)).
  `{% include %}`s `solver.md`.

## Conventions

- **Math delimiters in prompts must say `$...$` / `$$...$$`** — the page
  renders KaTeX with exactly those delimiters. A literal USD dollar sign
  must be escaped as `\$`, otherwise the renderer will treat it as the
  opening of a math span.
- **Image scan must delegate**, not classify or solve — that's
  `save_parsed_problem` only, in
  [../../worker/prompts/orchestrator.md](../../worker/prompts/orchestrator.md).
  **The batch classifier must call `save_classification` once per problem
  in its batch**, no more, no fewer — `worker/agent/classifier.py` treats
  a batch as incomplete (and reverts/retries the unsaved problems) if the
  count doesn't match.
- **Figure bbox/rotation contract** is defined in the orchestrator
  prompt at
  [../../worker/prompts/orchestrator.md](../../worker/prompts/orchestrator.md):
  normalized `[x0, y0, x1, y1]` in `[0, 1]` with `x0<x1`, `y0<y1`, plus
  a clockwise rotation of `0`/`90`/`180`/`270`. Empty list + `0` means
  no figure. Keep these in sync with
  [../figures.py](../figures.py).

## Don't

- Don't paste prompt overrides into Python. If a rule needs to vary, use
  Jinja2 in the template.
- Don't change the tool names referenced in these prompts
  (`mcp__problem_store__save_parsed_problem`,
  `mcp__problem_store__list_subexams`,
  `mcp__problem_store__save_classification`,
  `mcp__problem_store__lookup_category_edits`) without updating the MCP
  servers in
  [../../worker/agent/problem_store.py](../../worker/agent/problem_store.py).
