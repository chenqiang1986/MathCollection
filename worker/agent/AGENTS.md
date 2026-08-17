# Worker agent package

Two independent jobs, each driven by the worker runner so a quota hit or
partial result in one can't roll back the other:

- **Image scan — `scan_image(image_path, source_image)`.** One
  orchestrator LLM call extracts every problem out of the source and
  persists each as a partial record (`category='unclassified'`, no
  solution) via `build_scan_store`. Tracked per file in `raw_files`
  (`common.storage.queue`); saved by the runner as the
  `processing_image_scan -> done` transition.
- **Batch classify — `classify_pending_problems(batch_size, concurrency)`.**
  Claims a flat backlog of `unclassified` problems (independent of
  source file — see `common.storage.classify_tasks`), bounded to
  `concurrency * batch_size` per call, and fans them out to that many
  concurrent batch-classify sessions. Each session
  (`worker.agent.classifier.classify_batch`) covers many problems in one
  system prompt and calls `save_classification` once per problem via
  `build_classify_store`. Bookkeeping (`done` / reverted / `failed`) is
  applied per problem directly against `classify_tasks`, independent of
  any `raw_files` row.

Full re-solving (a written-out solution) is not part of this package at
all anymore — it's the webapp's ad hoc refine flow
(`webapp/src/lib/agent/refine.py`, `POST /problems/<id>/refine`), which
reuses the shared `common/prompts/solver.md` template directly.

## Files

- [__init__.py](__init__.py) — public surface (`scan_image`,
  `classify_pending_problems`, `StageResult`, `NO_CLASSIFY_WORK_SUMMARY`,
  `build_scan_store`, `build_classify_store`, `UNCLASSIFIED_CATEGORY`).
- [orchestrator.py](orchestrator.py) — `scan_image` and
  `classify_pending_problems` plus their `_async` implementations. Sync
  wrappers each call `asyncio.run` internally — do not call from inside
  an existing event loop. Owns the batch-classify tunables
  (`CLASSIFY_BATCH_SIZE`, `CLASSIFY_CONCURRENCY`, `CLASSIFY_MAX_ATTEMPTS`).
- [classifier.py](classifier.py) — `classify_batch(problem_ids)`: loads
  each problem fresh from storage, builds one prompt enumerating all of
  them (tagged with `problem_id`, plus a figure path where present), runs
  one `query()` against `build_classify_store`, and returns a
  `StageResult`. Does not raise on quota — it detects the SDK's
  `RateLimitEvent` mid-stream, stops, and returns whatever was saved so
  far with `hit_quota_limit=True` (unlike the old per-problem solver,
  this can't just raise, since a partial batch still has real progress to
  report back).
- [problem_store.py](problem_store.py) — in-process MCP servers:
  - `build_scan_store(source_image, saved)` exposes `save_parsed_problem`
    and `list_subexams`. Each `save_parsed_problem` call crops the figure
    (if any) and inserts a partial via `storage.save_problem` with
    `category='unclassified'`, `solution=''`. Calls with a `seq_no`
    already saved for `source_image` are skipped so retries don't
    duplicate.
  - `build_classify_store(problem_ids, saved)` exposes
    `save_classification` and `lookup_category_edits` for one batch
    session. `save_classification` validates `problem_id` is a member of
    the batch and hasn't already been saved, and requires a preceding
    `lookup_category_edits` call (tracked via a lookup/save counter, not
    a single boolean flag, since one session covers many problems).
- [results.py](results.py) — `StageResult`, the shared return type for
  both jobs. Kept out of `orchestrator.py` so `classifier.py` can return
  it without an import cycle (`orchestrator.py` imports `classify_batch`
  from `classifier.py`).

## Prompts

- The orchestrator (image-scan) prompt is worker-local at
  [../prompts/orchestrator.md](../prompts/orchestrator.md). Only
  `scan_image` reads it; no other component depends on it.
- The classifier prompt template lives in the shared
  [../../common/prompts/classifier.md](../../common/prompts/classifier.md)
  (next to `math_category.md`, which it `{% include %}`s) so
  `classifier.py` can reuse `common.agent_util.PROMPTS_DIR`'s
  `FileSystemLoader` without a second loader.
- `solver.md` (also in `common/prompts/`) is no longer read by anything
  in this package — it's `{% include %}`'d only by `refine.md` for the
  webapp's ad hoc refine flow.

## Flow

```
Image scan — scan_image(image_path, source_image)
  └─ orchestrator query (system: worker/prompts/orchestrator.md)
       ├─ Read(image_path)
       └─ mcp__problem_store__save_parsed_problem(...)   # once per problem
            └─ figures.save_figure(...) if bbox non-empty
            └─ storage.save_problem(category='unclassified', solution='')

Batch classify — classify_pending_problems(batch_size, concurrency)
  └─ storage.classify_tasks.seed_pending()   # backfill any untracked unclassified problems
  └─ up to `concurrency` calls to storage.classify_tasks.claim_batch(batch_size)
  └─ asyncio.gather over the claimed batches:
       classify_batch(problem_ids)
         └─ one classifier query (system: common/prompts/classifier.md)
              ├─ Read(figure) per problem that has one
              ├─ mcp__problem_store__lookup_category_edits(category)   # once per problem
              └─ mcp__problem_store__save_classification(problem_id, ...)  # once per problem
                   └─ storage.update_problem(problem_id, category=..., subcategory=...)
  └─ per problem in each batch: mark_done / revert_to_pending / mark_failed
     against classify_tasks, based on whether it was saved, whether the
     batch hit quota, and CLASSIFY_MAX_ATTEMPTS
```

## Conventions

- `ORCHESTRATOR_MAX_TURNS = 20`. Classifier turns scale with batch size
  (`TURNS_PER_PROBLEM * len(problems)`, floored at `MIN_MAX_TURNS`) since
  one session now covers many problems — see `classifier.py`.
- Image scan's allowed tools are exactly `["Read",
  "mcp__problem_store__list_subexams",
  "mcp__problem_store__save_parsed_problem"]`. A classify batch's are
  `["mcp__problem_store__save_classification",
  "mcp__problem_store__lookup_category_edits"]` plus `"Read"` only when
  at least one problem in the batch has a `figure_image`.
- `save_classification` schema: `{problem_id, category, subcategory}` —
  no `problem_text` or `solution`, since the DB already has the text and
  classification never writes a solution. This is the token savings this
  package exists for: one system prompt (incl. the whole category
  taxonomy) shared across a whole batch instead of resent per problem.
- Every assistant / tool / result message is logged via `log_message`
  for debuggability.

## Don't

- Don't merge image scan and batch classify back into a single
  per-file call. The queue's per-job retry budget and per-job quota
  reversion both rely on the split, and classify being file-independent
  is the point (see [../../AGENTS.md](../../AGENTS.md) for why).
- Don't move `classifier.md` out of `common/prompts/` — it needs to sit
  next to `math_category.md` for the shared Jinja loader's `{% include %}`
  to resolve.
- Don't import from `webapp/src/`. Worker agent code may depend only on
  `common.*` and the `claude_agent_sdk` / `PIL` / `pypdfium2` packages.
- Don't classify a problem whose category is something other than
  `unclassified` from this entry point — `classify_pending_problems`
  only ever claims rows seeded from `category='unclassified'` problems.
  Re-classifying an already-categorized problem is the refine flow, not
  this one.
