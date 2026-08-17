# AGENTS.md

Guidance for AI coding agents working in this repo.

## Project overview

MathCollection is split into two pieces:

1. A small Flask webapp that accepts image / PDF uploads and saves them
   under `data/<user>/raw/`. It also enqueues each file in a Postgres
   queue (keyed by `user_id`) and shows the saved problems.
2. An offline Python worker ([worker/](worker/)) that drains the per-file
   scan queue and, independently, sweeps a flat backlog of unclassified
   problems, via the `claude-agent-sdk`. The webapp never invokes the
   agent on upload.

Each problem is persisted as one JSON file under
`data/<user>/problems/`. The browser renders math via KaTeX auto-render.

The worker runs two independent jobs (see [worker/agent/AGENTS.md](worker/agent/AGENTS.md)):

1. **Image scan** (`scan_image`, per file, tracked in `raw_files`) reads
   the source image, identifies each distinct problem, and saves it as a
   partial record (`category='unclassified'`).
2. **Batch classify** (`classify_pending_problems`, over a flat backlog of
   unclassified problems, tracked in `classify_tasks`) picks category and
   subcategory for a batch of problems in one agent session and calls
   `save_classification` once per problem. Full re-solving (with a
   written-out solution) is ad hoc only, via
   `POST /problems/<id>/refine` — see
   [webapp/src/lib/agent/AGENTS.md](webapp/src/lib/agent/AGENTS.md).

## Layout

Three top-level Python packages — `common/` (shared library), `webapp/`
(Flask app), `worker/` (offline daemon) — plus `backfill/` (one-shot CLI
maintenance). `webapp/` and `worker/` both depend on `common/`; neither
depends on the other.

**Shared library:**

- [common/storage/](common/storage/) — JSON-on-disk problem store,
  Postgres metadata index, raw-file queue, classify-task queue, per-user
  paths, stats aggregations. The Postgres connection pool lives in
  [common/storage/db.py](common/storage/db.py). See
  [common/storage/AGENTS.md](common/storage/AGENTS.md).
- [common/db_setup/](common/db_setup/) — `schema.sql` (single Postgres
  schema for all tables) + initialization. `ensure_schema()` applies the
  DDL once per process; `init_user()` backfills one user's problems from
  JSON when the schema version advances; `sync_all_users()` fans that over
  every user. `python -m common.db_setup` runs the sync and is the
  deploy-time entry (called from [docker_run.sh](docker_run.sh)) — request
  handling never does a DB sync.
- [common/agent_util.py](common/agent_util.py) — `MODEL`, `log_message`,
  `PROMPTS_DIR`, `MAX_BUFFER_SIZE`. Imported by both `worker.agent` and
  the webapp's `lib.agent.refine`.
- [common/figures.py](common/figures.py) — crops normalized bboxes out
  of source images / PDF pages into per-user `figures/`.
- [common/prompts/](common/prompts/) — shared prompts (`solver.md`,
  `classifier.md`, `math_category.md`, `refine.md`). The worker-only
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

- [worker/](worker/) — daemon that drains the per-user raw-file scan
  queue and, independently, the flat classify-task backlog. Contains its
  own [worker/agent/](worker/agent/) package (orchestrator + classifier +
  in-process MCP store) and [worker/prompts/](worker/prompts/)
  (orchestrator.md). See [worker/AGENTS.md](worker/AGENTS.md).

**Data layout** (per user; created by `init_user()`):

- `data/<user>/problems/<uuid>.json` — canonical, append-only.
- `data/<user>/figures/<uuid>.png` — cropped figures referenced by problems.
- `data/<user>/raw/<sha256>.<ext>` — raw uploaded images / PDFs.

The problem index, raw-file queue, classify-task queue, tags, and
category-edit log now live in a single Postgres database (schema
`math_collection`), with every row carrying a `user_id` column rather
than a per-user database file. Connect via `DATABASE_URL` (see
[.env.example](.env.example)). The `problems` and `problem_tags` tables
are derived from the JSON and rebuild on backfill; `tags`,
`category_edits`, `raw_files`, and `classify_tasks` are authoritative.

- [webapp/requirements.txt](webapp/requirements.txt), [.env.example](.env.example).

## Run

All three entry points need the repo root on `PYTHONPATH` so absolute
imports rooted at the repo (e.g. `from common import ...`,
`from webapp.src.web import auth`, `from worker.agent import ...`)
resolve.

```bash
source .venv/bin/activate
pip install -r webapp/requirements.txt
cp .env.example .env    # ANTHROPIC_API_KEY, FLASK_SECRET_KEY,
                        # GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
                        # DATABASE_URL (Postgres), PG_SCHEMA
# For Docker, also: cp .env.docker.example .env.docker and set DATABASE_URL to
# host.docker.internal (the Mac host). docker_run.sh / docker_run_worker.sh
# use .env.docker; local runs below use .env (localhost).

# 0. Apply the Postgres schema + sync all users' problems from JSON. Run once
#    per deploy (docker_run.sh does this automatically before the server).
PYTHONPATH=. python -m common.db_setup

# 1. Webapp (uploads + browsing)
PYTHONPATH=. python -m webapp.src.app                     # http://0.0.0.0:5001

# 2. Offline worker (separate shell — drains the raw-file queues)
PYTHONPATH=. python -m worker                             # daemon
PYTHONPATH=. python -m worker --once                      # drain once and exit

# 3. Backfill (one-shot CLI)
PYTHONPATH=. python -m backfill classify --email me@example.com
```

Run all commands from the repo root. The Docker image (built from
`webapp/Dockerfile`) sets `PYTHONPATH=/app` and copies `common/` and
`webapp/` into the container; build it from the repo root so the whole
package tree is in the build context.

There are no tests, linters, or CI configured.

## Conventions

- **Model**: `claude-sonnet-4-6` (see `MODEL` in
  [common/agent_util.py](common/agent_util.py)). Don't silently swap models.
- **Batch classify, one save per problem**: the orchestrator's image-scan
  session only extracts; it must never call `save_classification`. One
  batch-classify session covers many problems (see
  [worker/agent/classifier.py](worker/agent/classifier.py)) and MUST call
  `mcp__problem_store__save_classification` exactly once per `problem_id`
  it was given — the tool validates `problem_id` membership in the batch
  (see [worker/agent/problem_store.py](worker/agent/problem_store.py)).
- **Prompts**: shared templates live in [common/prompts/](common/prompts/)
  (solver — used only by the ad hoc refine flow now, classifier,
  math_category, refine); orchestrator-only prompt in
  [worker/prompts/](worker/prompts/). Don't splice override instructions
  in Python.
- **Dependency direction**: `common/` is the only package allowed to be
  imported by both `webapp/` and `worker/`. The webapp must never
  import from `worker/`, and the worker must never import from
  `webapp/src/`. Anything that needs to be shared moves into `common/`.
- **Difficulty**: `solve_time_seconds` (float seconds, real measured solve
  time) plus a `solve_time_estimated: bool` flag. Only set by the ad hoc
  `/refine` flow (which writes a real solution and measures elapsed time);
  the automated batch-classify pipeline never touches it.
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
  Postgres `problems` table is purely derived — deleting a user's rows is
  safe and they rebuild from JSON on next `init_user()`. The exception is the
  `category_edits` table, which is authoritative (it logs manual category
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

- `agent.scan_image` and `agent.classify_pending_problems` each call
  `asyncio.run`, so they can't be invoked from inside an existing event
  loop. The worker is fully synchronous; don't wrap it in one.
- Uploads succeed silently from the user's perspective until the worker
  catches up. If the worker isn't running, new files pile up in the
  `raw_files` table as `pending_image_scan` rows and no problems appear.
  Once scanned, problems can also sit `unclassified` if the classify job
  is failing — check `storage.status_counts()` (file scan) and
  `storage.classify_tasks.status_counts()` (classification), or query
  `raw_files` / `classify_tasks` directly by `user_id` and `status`, if
  things "stop working". The webapp's `/queue` and `/classify` pages
  surface both.
- `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and
  `DATABASE_URL` must be in the environment (loaded via `python-dotenv`)
  before the app starts. Postgres must be reachable at `DATABASE_URL`; the
  schema is created lazily by `ensure_schema()` on first use.
- All storage helpers require an active user context — calling them without
  `storage.set_current_user(...)` raises `RuntimeError`. The `login_required`
  decorator in [webapp/src/web/auth.py](webapp/src/web/auth.py) handles this for HTTP
  handlers.
- `uploads/` is gitignored except for `.gitkeep`; `data/<user>/problems/*.json`
  is also gitignored — saved problems are local-only.
