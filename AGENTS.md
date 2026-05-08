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
  `data/problems/`.
- [src/templates/index.html](src/templates/index.html) — single page (upload
  form + problem list, KaTeX rendering of `$...$` / `$$...$$`).
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
  Keep that tool's signature (`problem_text`, `category`, `difficulty`,
  `solution`) in sync with [src/storage.py](src/storage.py)'s `save_problem`.
- `difficulty` is a fixed enum: `elementary`, `middle school`, `high school`,
  `undergraduate`, `graduate`, `olympiad`. Update the system prompt if you
  change it.
- Math in `problem_text` and `solution` must use `$...$` / `$$...$$` so KaTeX
  renders it on the page. Don't switch delimiters without updating
  [src/templates/index.html](src/templates/index.html).
- Allowed image extensions and the 10 MB cap live at the top of
  [src/app.py](src/app.py).
- Problem records are append-only JSON files. There is no DB and no edit/delete
  flow — don't add one without being asked.

## Things to watch

- `agent.process_image` calls `asyncio.run`, so it can't be invoked from inside
  an existing event loop.
- `ANTHROPIC_API_KEY` must be in the environment (loaded via `python-dotenv`)
  before the agent runs; otherwise the SDK call fails.
- `uploads/` is gitignored except for `.gitkeep`; `data/problems/*.json` is
  also gitignored — saved problems are local-only.
