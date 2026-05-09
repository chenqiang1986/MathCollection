# AGENTS.md

Guidance for AI coding agents working in this repo.

## Project overview

MathCollection is a small Flask app that accepts an image upload, hands it to a
Claude agent (via the `claude-agent-sdk`), and stores any math problems the
agent extracts. Each problem is persisted as one JSON file under
`data/problems/`. The browser renders math via KaTeX auto-render.

## Layout

- [src/app.py](src/app.py) — Flask app, upload endpoint, page rendering.
- [src/agent.py](src/agent.py) — Claude Agent SDK wiring: system prompt, the
  in-process MCP `save_problem` tool, async `query()` loop, message logging.
- [src/storage.py](src/storage.py) — file-backed JSON store under
  `data/problems/`, plus a SQLite metadata index at
  `data/problems_index.db` (id, filename, category, solve_time_seconds,
  solve_time_estimated, created_at). The DB is auto-built from the JSON
  files on first run if missing; JSON files remain canonical. Every
  `save_problem` / `update_problem` upserts a matching row.
- [src/templates/index.html](src/templates/index.html) — upload form +
  empty containers wired up by JS (KaTeX rendering of `$...$` / `$$...$$`).
  The problem list is no longer server-rendered.
- [src/static/js/app.js](src/static/js/app.js) — fetches `/api/summary`,
  `/api/problems` (paginated, 5/page), and `/api/sample` (random sample
  for "Print as PDF") and renders cards client-side.
- [data/problems/](data/problems/) — one `<uuid>.json` per saved problem.
- [uploads/](uploads/) — raw uploaded images (gitignored).
- [requirements.txt](requirements.txt), [.env.example](.env.example).

## Run

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set ANTHROPIC_API_KEY, FLASK_SECRET_KEY
python src/app.py      # serves on http://0.0.0.0:5001
```

There are no tests, linters, or CI configured.

## Conventions

- Model: `claude-sonnet-4-6` (see `MODEL` in [src/agent.py](src/agent.py)).
  Don't silently swap models.
- The agent must call `mcp__problem_store__save_problem` once per problem.
  The tool's schema is shaped at runtime by `with_solution` (see
  `_build_problem_store` in [src/agent.py](src/agent.py)): with a solution,
  it takes `problem_text, category, solution, solution_svg`; without, it
  takes `problem_text, category, solve_time_seconds` (Claude's own time
  estimate). Keep these in sync with [src/storage.py](src/storage.py)'s
  `save_problem`.
- The solver system prompt
  [src/prompts/solver.md](src/prompts/solver.md) is a Jinja2 template
  rendered with `with_solution`; do not append override instructions in
  Python — adjust the template instead.
- Difficulty is unified as `solve_time_seconds` (float, in seconds) plus a
  `solve_time_estimated: bool` flag. With `with_solution=True`, the value
  is the measured wall-clock duration of the inner query (estimated=False);
  with `with_solution=False`, Claude estimates it (estimated=True).
- Math in `problem_text` and `solution` must use `$...$` / `$$...$$` so KaTeX
  renders it on the page. Don't switch delimiters without updating
  [src/templates/index.html](src/templates/index.html).
- Allowed image extensions and the 10 MB cap live at the top of
  [src/app.py](src/app.py).
- Problem records are append-only JSON files. The SQLite index DB
  (`data/problems_index.db`) is purely derived — deleting it is safe; it
  rebuilds from the JSON files on next startup. There is no edit/delete
  flow — don't add one without being asked.
- The frontend pages problems via `/api/problems?page=&page_size=&category=&min_time=&max_time=&range_max=`.
  `range_max` is the slider's full-range upper bound; if the requested
  `[min_time, max_time]` covers it, the time filter is dropped so rows
  with `solve_time_seconds == NULL` aren't excluded. "Print as PDF" hits
  `/api/sample?n=&...` with the same filters — keep it server-side, since
  the page only holds 5 problems at a time.

## Things to watch

- `agent.process_image` calls `asyncio.run`, so it can't be invoked from inside
  an existing event loop.
- `ANTHROPIC_API_KEY` must be in the environment (loaded via `python-dotenv`)
  before the agent runs; otherwise the SDK call fails.
- `uploads/` is gitignored except for `.gitkeep`; `data/problems/*.json` is
  also gitignored — saved problems are local-only.
