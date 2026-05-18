# AGENTS.md

Guidance for AI coding agents working in this repo.

## Project overview

MathCollection is a small Flask app that accepts an image upload, hands it to a
two-tier Claude agent (via the `claude-agent-sdk`), and stores any math problems
the agent extracts. Each problem is persisted as one JSON file under
`data/<user>/problems/`. The browser renders math via KaTeX auto-render.

The agent runs in two layers (see [webapp/src/lib/agent/AGENTS.md](webapp/src/lib/agent/AGENTS.md)):

1. **Orchestrator** reads the source image, identifies each distinct problem,
   and dispatches each one to `solve_and_save`.
2. **Solver** runs in a fresh agent context per problem, classifies, optionally
   solves it, optionally reads a cropped figure, then calls `save_problem`.

## Layout

- [webapp/src/app.py](webapp/src/app.py) — Flask app factory; registers blueprints from
  [webapp/src/web/](webapp/src/web/).
- [webapp/src/figures.py](webapp/src/figures.py) — crops a normalized bbox out of an uploaded
  image and saves it under `data/<user>/figures/` as a PNG.
- [webapp/src/web/](webapp/src/web/) — Flask blueprints (auth, page routes, JSON API,
  uploads). See [webapp/src/web/AGENTS.md](webapp/src/web/AGENTS.md).
- [webapp/src/lib/agent/](webapp/src/lib/agent/) — Claude Agent SDK wiring (orchestrator,
  solver, in-process MCP tools, message logging). See
  [webapp/src/lib/agent/AGENTS.md](webapp/src/lib/agent/AGENTS.md).
- [webapp/src/lib/storage/](webapp/src/lib/storage/) — JSON-on-disk problem store, SQLite
  metadata index, per-user paths, stats aggregations. See
  [webapp/src/lib/storage/AGENTS.md](webapp/src/lib/storage/AGENTS.md).
- [webapp/src/prompts/](webapp/src/prompts/) — orchestrator + solver system prompts
  (`solver.md` is a Jinja2 template). See
  [webapp/src/prompts/AGENTS.md](webapp/src/prompts/AGENTS.md).
- [webapp/src/templates/](webapp/src/templates/) — Jinja2 HTML for the index + stats pages.
- [webapp/src/static/js/](webapp/src/static/js/) — vanilla JS for client-side rendering,
  filtering, pagination, and print-to-PDF.
- `data/<user>/problems/` — one `<uuid>.json` per saved problem.
- `data/<user>/figures/` — cropped figure PNGs referenced by problems.
- `data/<user>/problems_index.db` — derived SQLite index (auto-rebuilt from
  the JSON files if missing).
- `uploads/` — raw uploaded images (gitignored).
- [webapp/requirements.txt](webapp/requirements.txt), [.env.example](.env.example).

## Run

```bash
source .venv/bin/activate
pip install -r webapp/requirements.txt
cp .env.example .env    # ANTHROPIC_API_KEY, FLASK_SECRET_KEY,
                        # GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
python webapp/src/app.py       # serves on http://0.0.0.0:5001
```

There are no tests, linters, or CI configured.

## Conventions

- **Model**: `claude-sonnet-4-6` (see `MODEL` in
  [webapp/src/lib/agent/util.py](webapp/src/lib/agent/util.py)). Don't silently swap models.
- **Two-tier agent**: the orchestrator only dispatches; it must never call
  `save_problem` directly. The solver MUST call
  `mcp__problem_store__save_problem` exactly once per invocation. The tool
  schema is shaped at runtime by `with_solution` (see
  [webapp/src/lib/agent/problem_store.py](webapp/src/lib/agent/problem_store.py)).
- **Prompts**: edit the templates in [webapp/src/prompts/](webapp/src/prompts/) — do not
  splice override instructions in Python. `solver.md` is rendered as Jinja2
  with `with_solution`.
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
  [webapp/src/figures.py](webapp/src/figures.py).
- **Uploads**: allowed extensions and the 10 MB cap are at the top of
  [webapp/src/web/uploads.py](webapp/src/web/uploads.py) and [webapp/src/app.py](webapp/src/app.py).
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
  an existing event loop.
- `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET` must be
  in the environment (loaded via `python-dotenv`) before the app starts.
- All storage helpers require an active user context — calling them without
  `storage.set_current_user(...)` raises `RuntimeError`. The `login_required`
  decorator in [webapp/src/web/auth.py](webapp/src/web/auth.py) handles this for HTTP
  handlers.
- `uploads/` is gitignored except for `.gitkeep`; `data/<user>/problems/*.json`
  is also gitignored — saved problems are local-only.
