# AGENTS.md

Guidance for AI coding agents working in this repo.

## Project overview

MathCollection is split into two pieces:

1. A small Flask webapp that accepts image / PDF uploads and saves them
   under `data/<user>/raw/`. It also enqueues each file in a per-user
   SQLite queue and shows the saved problems.
2. An offline Python worker ([worker/](worker/)) that scans the queues
   across all users, runs the two-tier Claude agent on each pending file
   (via the `claude-agent-sdk`), and saves the extracted problems. The
   webapp never invokes the agent on upload.

Each problem is persisted as one JSON file under
`data/<user>/problems/`. The browser renders math via KaTeX auto-render.

The agent runs in two layers (see [webapp/src/lib/agent/AGENTS.md](webapp/src/lib/agent/AGENTS.md)):

1. **Orchestrator** reads the source image, identifies each distinct problem,
   and dispatches each one to `solve_and_save`.
2. **Solver** runs in a fresh agent context per problem, classifies, optionally
   solves it, optionally reads a cropped figure, then calls `save_problem`.

## Layout

Three top-level Python packages — `common/` (shared library), `webapp/`
(Flask app), `worker/` (offline daemon) — plus `backfill/` (one-shot CLI
maintenance). `webapp/` and `worker/` both depend on `common/`; neither
depends on the other.

**Shared library:**

- [common/storage/](common/storage/) — JSON-on-disk problem store,
  SQLite metadata index, raw-file queue, per-user paths, stats
  aggregations. See [common/storage/AGENTS.md](common/storage/AGENTS.md).
- [common/db_setup/](common/db_setup/) — schema files + per-user DB
  initialization. `init_user()` applies both `schema.sql`
  (problems_index.db) and `queue_schema.sql` (raw_queue.db).
- [common/agent_util.py](common/agent_util.py) — `MODEL`, `log_message`,
  `PROMPTS_DIR`, `MAX_BUFFER_SIZE`. Imported by both `worker.agent` and
  the webapp's `lib.agent.refine`.
- [common/figures.py](common/figures.py) — crops normalized bboxes out
  of source images / PDF pages into per-user `figures/`.
- [common/prompts/](common/prompts/) — shared prompts
  (`solver.md`, `math_category.md`, `refine.md`). The worker-only
  `orchestrator.md` lives in [worker/prompts/](worker/prompts/).
  See [common/prompts/AGENTS.md](common/prompts/AGENTS.md).

**Webapp (Flask, uploads + browsing):**

- [webapp/src/app.py](webapp/src/app.py) — app factory; registers
  blueprints from [webapp/src/web/](webapp/src/web/).
- [webapp/src/web/](webapp/src/web/) — Flask blueprints (auth, pages,
  JSON API, uploads). See [webapp/src/web/AGENTS.md](webapp/src/web/AGENTS.md).
- [webapp/src/lib/agent/](webapp/src/lib/agent/) — webapp-only agent
  surface: `refine_problem` (re-classify / re-crop / re-transcribe an
  existing saved problem). See [webapp/src/lib/agent/AGENTS.md](webapp/src/lib/agent/AGENTS.md).
- [webapp/src/templates/](webapp/src/templates/), [webapp/src/static/js/](webapp/src/static/js/) — Jinja2 HTML + vanilla JS frontend.

**Worker (offline agent runner):**

- [worker/](worker/) — daemon that drains the per-user raw-file queues
  by running the agent on each pending file. Contains its own
  [worker/agent/](worker/agent/) package (orchestrator + solver) and
  [worker/prompts/](worker/prompts/) (orchestrator.md). See
  [worker/AGENTS.md](worker/AGENTS.md).

**Data layout** (per user; created by `init_user()`):

- `data/<user>/problems/<uuid>.json` — canonical, append-only.
- `data/<user>/figures/<uuid>.png` — cropped figures referenced by problems.
- `data/<user>/raw/<sha256>.<ext>` — raw uploaded images / PDFs.
- `data/<user>/problems_index.db` — derived SQLite index (rebuilt from
  JSON on next startup if missing).
- `data/<user>/raw_queue.db` — authoritative queue of raw files awaiting
  worker processing.

- [webapp/requirements.txt](webapp/requirements.txt), [.env.example](.env.example).

## Run

All three entry points need the repo root on `PYTHONPATH` so
`from common import ...` resolves; the webapp also needs `webapp/src`
on `PYTHONPATH` so the `lib.agent` package (refine) and `web/` package
resolve.

```bash
source .venv/bin/activate
pip install -r webapp/requirements.txt
cp .env.example .env    # ANTHROPIC_API_KEY, FLASK_SECRET_KEY,
                        # GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

# 1. Webapp (uploads + browsing)
PYTHONPATH=.:webapp/src python webapp/src/app.py          # http://0.0.0.0:5001

# 2. Offline worker (separate shell — drains the raw-file queues)
PYTHONPATH=. python -m worker                             # daemon
PYTHONPATH=. python -m worker --once                      # drain once and exit

# 3. Backfill (one-shot CLI)
PYTHONPATH=. python -m backfill classify --email me@example.com
```

Run all commands from the repo root. The Docker image (built from
`webapp/Dockerfile`) sets `PYTHONPATH=/app` and copies both `common/`
and `webapp/src/` into the container; build it from the repo root so
both directories are in the build context.

There are no tests, linters, or CI configured.

## Conventions

- **Model**: `claude-sonnet-4-6` (see `MODEL` in
  [common/agent_util.py](common/agent_util.py)). Don't silently swap models.
- **Two-tier agent**: the orchestrator only dispatches; it must never call
  `save_problem` directly. The solver MUST call
  `mcp__problem_store__save_problem` exactly once per invocation. The tool
  schema is shaped at runtime by `with_solution` (see
  [worker/agent/problem_store.py](worker/agent/problem_store.py)).
- **Prompts**: shared templates live in [common/prompts/](common/prompts/)
  (solver, math_category, refine); orchestrator-only prompt in
  [worker/prompts/](worker/prompts/). Don't splice override instructions
  in Python — `solver.md` is a Jinja2 template rendered with
  `with_solution`.
- **Dependency direction**: `common/` is the only package allowed to be
  imported by both `webapp/` and `worker/`. The webapp must never
  import from `worker/`, and the worker must never import from
  `webapp/src/`. Anything that needs to be shared moves into `common/`.
- **Difficulty**: unified as `solve_time_seconds` (float seconds) plus a
  `solve_time_estimated: bool` flag. With `with_solution=True`, the value
  is the measured wall-clock duration of the inner solver (estimated=False);
  with `with_solution=False`, Claude estimates it (estimated=True).
- **Math delimiters**: `problem_text` and `solution` must use `$...$` /
  `$$...$$` so KaTeX renders. Don't switch delimiters without updating
  [webapp/src/templates/index.html](webapp/src/templates/index.html) and
  [webapp/src/static/js/app.js](webapp/src/static/js/app.js).
- **Figures**: the orchestrator passes `figure_bbox` (normalized [x0,y0,x1,y1]
  in [0,1]) and `figure_rotation` (0/90/180/270 clockwise) per problem; an
  empty `figure_bbox` means no figure. Cropping/rotation lives in
  [common/figures.py](common/figures.py).
- **Uploads**: allowed extensions and the 10 MB cap are at the top of
  [webapp/src/web/uploads.py](webapp/src/web/uploads.py) and [webapp/src/app.py](webapp/src/app.py).
  `/upload` only saves bytes + enqueues; the worker does the agent work.
- **Storage is mostly append-only**: problem JSON files are canonical; the
  SQLite `problems` table is purely derived — deleting it is safe and it
  rebuilds on next startup. The one exception is the `category_edits`
  table in the same DB, which is authoritative (it logs manual category
  corrections and feeds the recategorization agent step); don't drop it.
  Delete and `POST /api/problems/<id>/category` (manual category edit) are
  the only write endpoints beyond upload; don't add other UI edit flows
  without being asked.
- **API surface**: the frontend pages problems via
  `/api/problems?page=&page_size=&category=&min_time=&max_time=&range_max=`.
  `range_max` is the slider's full-range upper bound; if the requested
  `[min_time, max_time]` covers it, the time filter is dropped so rows with
  `solve_time_seconds == NULL` aren't excluded. "Print as PDF" hits
  `/api/sample?n=&...` with the same filters — keep it server-side.
- **Auth + per-user data**: Google OAuth (authlib) gates everything; each
  request binds `storage.set_current_user(...)` so every storage call writes
  under `data/<sanitized-email>/`. The whitelist of upload-allowed emails is
  `UPLOAD_WHITELIST` in [webapp/src/web/auth.py](webapp/src/web/auth.py); non-whitelisted
  signed-in users share a `guest` user dir and can only browse.

## Things to watch

- `agent.process_image` calls `asyncio.run`, so it can't be invoked from inside
  an existing event loop. The worker is fully synchronous; don't wrap it in one.
- Uploads succeed silently from the user's perspective until the worker
  catches up. If the worker isn't running, new files pile up in
  `raw_queue.db` as `pending` rows and no problems appear. Check
  `pending_count()` per user or `select * from raw_files where status =
  'pending'` if uploads "stop working".
- `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET` must be
  in the environment (loaded via `python-dotenv`) before the app starts.
- All storage helpers require an active user context — calling them without
  `storage.set_current_user(...)` raises `RuntimeError`. The `login_required`
  decorator in [webapp/src/web/auth.py](webapp/src/web/auth.py) handles this for HTTP
  handlers.
- `uploads/` is gitignored except for `.gitkeep`; `data/<user>/problems/*.json`
  is also gitignored — saved problems are local-only.
