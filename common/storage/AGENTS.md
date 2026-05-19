# Storage package

Per-user, file-backed problem store with a derived SQLite index.

## Files

- [__init__.py](__init__.py) — public surface; re-exports every helper
  used by callers. Always import from `common.storage`, not from
  submodules, so the internal layout stays free to move.
- [vocab.py](vocab.py) — record types only (`Problem` dataclass,
  `Bucket` NamedTuple, `DIFFICULTY_BUCKETS`). No runtime deps so any module
  can import it without pulling in sqlite3 or filesystem code.
- [paths.py](paths.py) — user-context binding (`set_current_user`,
  `reset_current_user`) and per-user path helpers
  (`user_dir`, `problems_dir`, `figures_dir`, `index_path`,
  `figure_path`, `raw_uploads_dir`, `raw_upload_path`). All paths live
  under `data/<email>/` so the GCS-Fuse mount on Cloud Run persists
  them; never hardcode a path or write outside `data/`.
- [problem_io.py](problem_io.py) — JSON read/write for problem records.
  `save_problem` / `update_problem` both mirror into the SQLite index.
  `delete_problem` removes the JSON, the figure (if any), and the index row.
- [sql_index.py](sql_index.py) — filter-aware `query_index` /
  `sample_index` used by the API, plus the shared `_connect` /
  `_upsert_index_row` helpers. Assumes the schema is already in place;
  DDL lives in [../db_setup/schema.sql](../db_setup/schema.sql) and is
  applied by `common.db_setup.setup.init_user()` from `/auth/callback`
  on login.
- [queue.py](queue.py) — per-user raw-file queue used by the offline
  worker. Five-state lifecycle (`pending_image_scan` →
  `processing_image_scan` → `pending_problem_solve` →
  `processing_problem_solve` → `done | failed`). Public ops:
  `enqueue_raw`, `claim_next_image_scan`, `claim_next_problem_solve`,
  `advance_to_problem_solve`, `mark_done`, `mark_failed`,
  `revert_image_scan`, `revert_problem_solve`,
  `reclaim_stale_processing`, `pending_count`. Lives in `raw_queue.db`
  (separate from `problems_index.db`); DDL lives in
  [../db_setup/queue_schema.sql](../db_setup/queue_schema.sql) and is
  also applied by `init_user()`. Both `claim_next_*` helpers use
  `BEGIN IMMEDIATE` so two workers polling the same user don't race.
- [category_edits.py](category_edits.py) — append-only log of manual
  category corrections (`record_category_edit`, `category_edit_examples`).
  Lives in the same SQLite DB as the index but is **authoritative**, not
  derived — unlike the `problems` table, it can't be rebuilt from the JSON
  files. Each row denormalizes problem_text + solution at edit time so
  examples stand on their own as training context for the recategorization
  reviewer.
- [stats.py](stats.py) — aggregations powering the stats page
  (`category_counts`, `difficulty_distribution`, `index_summary`).

## Data layout

```
data/
└── <sanitized-email>/
    ├── problems/         <uuid>.json — canonical, append-only
    ├── figures/          <uuid>.png  — cropped figures referenced by problems
    ├── raw/              <sha256>.<ext> — uploaded sources awaiting worker
    ├── problems_index.db SQLite mirror, auto-rebuilt from JSON if missing
    └── raw_queue.db      SQLite queue tracking raw-file processing state
```

`sanitize_email` lowercases and replaces filesystem-unsafe chars; the
non-whitelisted "guest" bucket also lives under `data/guest/`.

## Conventions

- **JSON is canonical, SQLite is derived — for the `problems` table.**
  Deleting `problems_index.db` is safe for the index; the next login
  re-runs `common.db_setup.setup.init_user`, which recreates the schema and backfills
  from the JSON files. The `category_edits` table in the same DB is the
  **exception**: it's authoritative and is not rebuildable, so don't drop
  the DB casually if a user has edited categories.
- **`raw_queue.db` is authoritative.** It tracks per-file status,
  with_solution, attempts, and errors — none of which can be rebuilt
  from the filesystem. Deleting it forces every raw file to re-enqueue
  on next upload (or it stays unprocessed forever). The `problems` table
  uses `(source_image, seq_no)` to dedupe at the agent layer, so even if
  the queue is wiped and a file is reprocessed, already-saved problems
  are skipped.
- **Every write to a problem JSON file must mirror into the index** —
  that's what `_upsert_index_row` is for. Don't write a record without
  upserting, or `/api/problems` will miss it.
- **User context is required.** Calling any path-aware helper without
  `storage.set_current_user(email)` first raises `RuntimeError`. The
  `login_required` decorator in
  [../../webapp/src/web/auth.py](../../webapp/src/web/auth.py) wraps
  every HTTP handler with this; if you add a CLI or background job,
  call `set_current_user` yourself.
- **`Problem.from_dict` ignores unknown keys.** Adding a field is
  backwards-compatible; renaming is not — old JSON files lose the renamed
  field silently. Migrate by reading + rewriting if you rename.
- **Filter semantics in `_build_where`:** if the requested `[min_time,
  max_time]` covers `full_range_max`, the time filter is dropped so rows
  with `solve_time_seconds IS NULL` aren't excluded. Keep this in sync with
  the slider behavior in
  [../../webapp/src/static/js/app.js](../../webapp/src/static/js/app.js).
- **Difficulty buckets** (`DIFFICULTY_BUCKETS`) are half-open `[lo, hi)`;
  the last bucket's `hi` is `inf`. Edit labels there, not in JS.

## Don't

- Don't query SQLite directly from outside this package — go through
  `query_index` / `sample_index` so the user-context check runs.
- Don't add an edit endpoint without an explicit ask; the current design is
  append-only.
- Don't import Flask or the agent here.
