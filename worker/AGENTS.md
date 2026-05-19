# Offline worker

Drains the per-user raw-file queues by running the agent on each pending
file. The webapp's `/upload` only saves the file and enqueues a row; this
worker is what actually invokes Claude.

Each file moves through five states:

```
pending_image_scan      -> processing_image_scan
    -> pending_problem_solve -> processing_problem_solve -> done | failed
```

Stage 1 (image scan) runs the orchestrator once and persists each
extracted problem as a partial record (`category='unclassified'`,
no solution). Stage 2 (problem solve) walks those partials and fans
them out to per-problem solver agents that update the records in place.

## Layout

- [`__main__.py`](__main__.py) — CLI entrypoint. `python -m worker` runs
  the daemon; `--once` drains every user once and exits (used for
  testing).
- [`run.py`](run.py) — main loop. Scans `data/<user>/` directories.
  For each user, claims the oldest `pending_image_scan` row first via
  `storage.claim_next_image_scan()` (drains new uploads into partials),
  otherwise the oldest `pending_problem_solve` row via
  `storage.claim_next_problem_solve()`, runs the matching agent stage
  (`agent.scan_image` / `agent.solve_pending_problems`), and advances /
  reverts / fails the row. After each pass, sleeps
  `IDLE_SLEEP_SECONDS` (60s) before rescanning.
- [`quota.py`](quota.py) — best-effort Claude rate-limit detection.
  `classify_error(exc)` returns a `QuotaSignal` saying whether the
  exception is a 429 / quota block and how long to wait before resuming.
  Looks at the exception class name, stringified message, embedded
  `retry-after` hints, and any `anthropic-ratelimit-*-reset` timestamps.
- [`agent/`](agent/) — orchestrator + solver + in-process MCP problem
  store. Only the worker imports this package. See
  [agent/AGENTS.md](agent/AGENTS.md).
- [`prompts/`](prompts/) — worker-only prompts. Currently just
  `orchestrator.md`. Shared prompts (`solver.md`, `math_category.md`,
  `refine.md`) live in `common/prompts/`. See
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

- **One stage per user per round.** The outer loop iterates every user,
  calls `_drain_user(email)` exactly once each (which advances at most
  one row through one stage), then sleeps. That gives cross-user fairness
  — a 1000-file backlog on user A never starves user B.
- **Image scan drains before problem solve.** Within one user,
  `_drain_user` picks `pending_image_scan` first so new uploads turn into
  partials quickly. Only when there is no scan work does it fall back to
  `pending_problem_solve`.
- **The claim helpers are atomic.** Both `claim_next_image_scan` and
  `claim_next_problem_solve` open `BEGIN IMMEDIATE`, pick the oldest row
  in that pending state, flip it to the matching `processing_*`, bump
  `attempts`, and commit. Two worker processes hitting the same user
  race safely.
- **Rate-limit handling reverts, doesn't fail.** When `classify_error`
  flags a 429, the row goes back to the matching pending state via
  `revert_image_scan` / `revert_problem_solve` so it retries after the
  sleep. Only non-quota exceptions mark the row `failed`.
- **Per-stage retry budget.** `MAX_ATTEMPTS` applies to each stage
  independently — moving from `processing_image_scan` to
  `pending_problem_solve` resets `attempts` to 0 so a solver glitch
  can't fail a row that scanned cleanly.
- **Stale `processing_*` rows are reclaimed at startup.** If the worker
  was killed mid-stage, the row would be stuck `processing_*` forever.
  `_reclaim_all_stale()` runs once on `run_forever()` start and flips
  every `processing_image_scan` row back to `pending_image_scan`, and
  every `processing_problem_solve` row back to `pending_problem_solve`.
  (`--once` does not reclaim — by design, so an operator can rerun
  without losing in-progress state from a parallel daemon.)
- **Per-user storage context.** Every call into `common.storage` requires
  `storage.set_current_user(email)` first. `_drain_user` and
  `_reclaim_all_stale` both wrap their work in
  `set_current_user(...) / reset_current_user(token)` via try/finally.

## Things to watch

- `agent.scan_image` and `agent.solve_pending_problems` each call
  `asyncio.run` internally, so the worker must stay synchronous
  top-to-bottom. Don't add an outer event loop.
- The worker scans `data/` by listing directory names; those names are
  already-sanitized email slugs (from `sanitize_email`). Passing them
  back through `set_current_user` is idempotent.
- `quota.py` errs on the side of waiting: any exception whose class
  name or message mentions `ratelimit`, `429`, `quota`, `overloaded`,
  etc. is treated as quota. If a legit-but-noisy error trips this, the
  worker will sleep an hour instead of failing the row — annoying but
  not destructive.
