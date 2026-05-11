# Web package

Flask blueprints. [src/app.py](../app.py) is just the factory; everything
HTTP-facing lives here.

## Files

- [auth.py](auth.py) — Google OAuth (authlib) flow, session handling, and
  the `login_required` / `upload_allowed_required` decorators. Also exports
  `current_user()` and `storage_email()` (maps non-whitelisted users to the
  shared `guest` bucket). Whitelist: `UPLOAD_WHITELIST` at the top of the
  file.
- [routes_pages.py](routes_pages.py) — server-rendered HTML
  (`/`, `/stats`). Pages do almost no work; the client fetches data from
  `/api/...`.
- [routes_api.py](routes_api.py) — JSON endpoints under `/api`:
  `summary`, `problems` (paginated), `problems/<id>` (DELETE, whitelist-only),
  `sample` (random for print-to-PDF), `stats/categories`, `stats/difficulty`.
  All gated by `@login_required`.
- [uploads.py](uploads.py) — `POST /upload` (whitelist-only): saves the
  raw image to `uploads/`, calls `agent.process_image(...)`, re-renders
  the index with the result summary. Also serves per-user figure PNGs at
  `/figures/<filename>`.

## Conventions

- **Every handler that touches storage is decorated with `@login_required`.**
  That decorator binds `storage.set_current_user(storage_email(email))`
  for the duration of the request and calls `storage.init_index()` so the
  SQLite mirror is ready. Don't call storage directly without this wrapper.
- **`upload_allowed_required` goes AFTER `login_required`.** Login first,
  then check the whitelist. Apply it on `/upload` and any write endpoint.
- **Filters come in as query params**, parsed by `_parse_filters()` in
  [routes_api.py](routes_api.py). Forward all four keys
  (`category`, `min_time`, `max_time`, `full_range_max`) into
  `storage.query_index` / `sample_index` so NULL-time handling stays
  consistent.
- **Pagination defaults** live at the top of [routes_api.py](routes_api.py):
  `DEFAULT_PAGE_SIZE=5`, `MAX_PAGE_SIZE=50`, `MAX_SAMPLE_SIZE=200`. Match
  these in JS if you change them.
- **HTML vs JSON branching for unauth:** decorators redirect HTML GETs to
  `/login` and return JSON 401/403 otherwise. Keep that branch when adding
  new handlers so the frontend can detect logout cleanly.

## Don't

- Don't add an `edit` endpoint — the data model is append-only by design.
- Don't read uploads from the request path; use `UPLOAD_DIR` (resolved
  relative to the repo root) so behavior is consistent across blueprints.
- Don't import the agent at module top-level in places that don't need it;
  it pulls in `claude_agent_sdk` which is heavy.
