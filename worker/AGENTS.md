# Offline worker

Drains the per-user raw-file scan queue and, independently, a flat
backlog of unclassified problems. The webapp's `/upload` only saves the
file and enqueues a row; this worker is what actually invokes Claude.

Each file moves through three states:

```
pending_image_scan -> processing_image_scan -> done | failed
```

Each problem, independent of which file it came from, moves through its
own four states:

```
pending -> processing -> done | failed
```

Image scan runs the orchestrator once and persists each extracted problem
as a partial record (`category='unclassified'`, no solution). Batch
classify claims a bounded slice of the unclassified backlog and fans it
out to concurrent batch-classify sessions that each cover many problems
in one system prompt, updating category/subcategory in place. Full
re-solving (a written-out solution) is not part of the automated
pipeline — it's ad hoc only, via `POST /problems/<id>/refine`
(`webapp/src/lib/agent/refine.py`).

## Layout

- [`__main__.py`](__main__.py) — CLI entrypoint. `python -m worker` runs
  the daemon; `--once` drains every user once and exits (used for
  testing).
- [`run.py`](run.py) — main loop. Scans `data/<user>/` directories. For
  each user, `_drain_user` does at most one unit of image-scan work
  (claims the oldest `pending_image_scan` row via
  `storage.claim_next_image_scan()`, runs `agent.scan_image`, advances /
  reverts / fails the row) and one round of batch-classify work
  (`agent.classify_pending_problems()`, bounded to
  `CLASSIFY_CONCURRENCY * CLASSIFY_BATCH_SIZE` problems per call — see
  `worker/agent/orchestrator.py`). After each pass over every user,
  sleeps `IDLE_SLEEP_SECONDS` (60s) before rescanning.
- [`quota.py`](quota.py) — best-effort Claude rate-limit detection.
  `detect_in_message(message)` inspects the SDK's `RateLimitEvent`
  messages and returns a `QuotaHit` (with a `reset_at` timestamp) when the
  request was rejected for quota. `later_reset(a, b)` picks the
  furthest-out of two reset timestamps, used when aggregating quota hits
  across concurrent batches/users.
- [`agent/`](agent/) — orchestrator (image scan) + classifier (batch
  classify) + in-process MCP problem store. Only the worker imports this
  package. See [agent/AGENTS.md](agent/AGENTS.md).
- [`prompts/`](prompts/) — worker-only prompts. Currently just
  `orchestrator.md`. Shared prompts (`solver.md`, `classifier.md`,
  `math_category.md`, `refine.md`) live in `common/prompts/`. See
  [prompts/AGENTS.md](prompts/AGENTS.md).

## How it runs

```bash
# Run from the repo root. PYTHONPATH=. is so `from common import ...`
# resolves; the worker no longer depends on webapp/src.
PYTHONPATH=. python -m worker            # daemon
PYTHONPATH=. python -m worker --once     # drain once
```

`.env` is loaded by `python-dotenv` so `ANTHROPIC_API_KEY` is picked up
the same way the webapp does it.

## Conventions

- **One image-scan row + one classify round per user per outer-loop
  pass.** The outer loop iterates every user, calls `_drain_user(email)`
  exactly once each (which does at most one scan-row transition and one
  bounded classify round), then sleeps. That gives cross-user fairness —
  a large backlog on user A never starves user B, and a single
  `classify_pending_problems()` call can't run away with an unbounded
  amount of a user's backlog.
- **The scan claim is atomic.** `claim_next_image_scan` opens a
  transaction, picks the oldest `pending_image_scan` row, flips it to
  `processing_image_scan`, bumps `attempts`, and commits. Two worker
  processes hitting the same user race safely (`FOR UPDATE SKIP LOCKED`).
  `classify_tasks.claim_batch` does the equivalent at problem grain,
  claiming up to `n` rows in one round trip.
- **Rate-limit handling reverts, doesn't fail.** When quota is hit, the
  scan row goes back to `pending_image_scan` via `revert_image_scan` so
  it retries after the sleep. Classify quota hits are handled inside
  `classify_pending_problems`: every problem in an affected batch that
  wasn't saved is reverted to `pending`. Only non-quota exceptions mark a
  row/problem `failed`.
- **Per-job retry budget.** `MAX_ATTEMPTS` (in `run.py`) governs the file
  scan; `CLASSIFY_MAX_ATTEMPTS` (in `agent/orchestrator.py`) governs
  classification, independently and per problem.
- **Stale `processing_*` rows are reclaimed at startup.** If the worker
  was killed mid-job, a row/task would be stuck `processing` forever.
  `_reclaim_all_stale()` runs once on `run_forever()` start and flips
  every `processing_image_scan` `raw_files` row back to
  `pending_image_scan`, and every `processing` `classify_tasks` row back
  to `pending`. (`--once` does not reclaim — by design, so an operator
  can rerun without losing in-progress state from a parallel daemon.)
- **Per-user storage context.** Every call into `common.storage` requires
  `storage.set_current_user(email)` first. `_drain_user` and
  `_reclaim_all_stale` both wrap their work in
  `set_current_user(...) / reset_current_user(token)` via try/finally.

## Things to watch

- `agent.scan_image` and `agent.classify_pending_problems` each call
  `asyncio.run` internally, so the worker must stay synchronous
  top-to-bottom. Don't add an outer event loop.
- The worker scans `data/` by listing directory names; those names are
  already-sanitized email slugs (from `sanitize_email`). Passing them
  back through `set_current_user` is idempotent.
- `worker.quota.detect_in_message` only fires on the SDK's own
  `RateLimitEvent` (status `"rejected"`) — it does not pattern-match
  exception text. An unhandled exception from a stage is treated as a
  real failure (counts toward the retry budget), not a quota hit.
