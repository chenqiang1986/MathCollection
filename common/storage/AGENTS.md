# Storage package

File-backed problem store (JSON on disk) with a derived index in Postgres.
A single Postgres database backs every user; rows are partitioned by a
`user_id` column (the sanitized-email slug from `paths.current_user_id`),
not by a per-user database. Connection settings come from `DATABASE_URL`
and `PG_SCHEMA` (default `math_collection`).

## Files

- [__init__.py](__init__.py) — public surface; re-exports every helper
  used by callers. Always import from `common.storage`, not from
  submodules, so the internal layout stays free to move.
- [db.py](db.py) — Postgres connection pool. `connect()` hands out a
  pooled connection used as `with connect() as conn:`; the transaction
  commits at block exit (the contract the old `sqlite3.connect()` context
  manager provided). Rows come back as dicts (`row["col"]`). Every other
  module in this package imports `connect` from here.
- [vocab.py](vocab.py) — record types only (`Problem` dataclass,
  `Bucket` NamedTuple, `DIFFICULTY_BUCKETS`) plus the `normalize_tag` /
  `normalize_tags` helpers (trim + lowercase + dedup, shared by storage and
  the API). No runtime deps so any module can import it without pulling in
  the database driver or filesystem code.
- [paths.py](paths.py) — user-context binding (`set_current_user`,
  `reset_current_user`, `current_user_id`) and per-user path helpers
  (`user_dir`, `problems_dir`, `figures_dir`, `figure_path`,
  `raw_uploads_dir`, `raw_upload_path`). All file paths live under
  `data/<email>/` so the GCS-Fuse mount on Cloud Run persists them; never
  hardcode a path or write outside `data/`. `current_user_id()` returns the
  same slug used as the `user_id` column value in every Postgres row.
- [problem_io.py](problem_io.py) — JSON read/write for problem records.
  `save_problem` / `update_problem` both mirror into the Postgres index.
  `delete_problem` removes the JSON, the figure (if any), and the index row.
- [sql_index.py](sql_index.py) — filter-aware `query_index` /
  `sample_index` used by the API, plus the shared `_upsert_index_row`
  helper. Every query is scoped to the active user via `user_id`. Assumes
  the schema is already in place; DDL lives in
  [../db_setup/schema.sql](../db_setup/schema.sql) and is applied by
  `common.db_setup.setup.ensure_schema()` (lazily, once per process).
  `init_user()` runs it and then backfills one user's problems from JSON;
  `sync_all_users()` fans that over every user and is the deploy-time entry
  (`python -m common.db_setup`). Request handling never does a DB sync.
- [queue.py](queue.py) — per-user raw-file queue used by the offline
  worker's image-scan job only. Three-state lifecycle
  (`pending_image_scan` → `processing_image_scan` → `done | failed`).
  Public ops: `enqueue_raw`, `claim_next_image_scan`, `mark_done`,
  `mark_failed`, `revert_image_scan`, `reclaim_stale_processing`,
  `pending_count`. The `raw_files` table shares the same Postgres
  database as the index (DDL in
  [../db_setup/schema.sql](../db_setup/schema.sql)). `claim_next_image_scan`
  uses a `SELECT ... FOR UPDATE SKIP LOCKED` subquery so two workers
  polling the same user claim different rows instead of racing.
- [classify_tasks.py](classify_tasks.py) — per-user classify-task queue,
  independent of `raw_files`/source file. Tracks a flat backlog of
  `problems` rows with `category='unclassified'`. Four-state lifecycle
  (`pending` → `processing` → `done | failed`). Accessed as
  `storage.classify_tasks.<fn>` (not flat re-exported) since its op names
  (`mark_done`, `mark_failed`, `retry_failed`, `status_counts`,
  `list_items`, `reclaim_stale_processing`) collide with `queue.py`'s;
  only the `ClassifyTask` type and `CLASSIFY_*` status constants are flat
  re-exported. `seed_pending()` lazily inserts a `pending` row for any
  unclassified problem missing one (self-heals problems that predate this
  table); `claim_batch(n)` claims up to `n` rows in one round trip.
- [category_edits.py](category_edits.py) — append-only log of manual
  category corrections (`record_category_edit`, `category_edit_examples`).
  Lives in the same Postgres database as the index but is **authoritative**,
  not derived — unlike the `problems` table, it can't be rebuilt from the JSON
  files. Each row denormalizes problem_text + solution at edit time so
  examples stand on their own as training context for the recategorization
  reviewer.
- [stats.py](stats.py) — aggregations powering the stats page
  (`category_counts`, `difficulty_distribution`, `index_summary`).
- [tags.py](tags.py) — customer-defined tag registry (`list_tags`,
  `upsert_tag`). The `tags` table holds each tag's optional comment and is
  **authoritative** (like `category_edits`); the `problem_tags` table mirrors
  each problem's `tags` list and is **derived** (rebuilt by `_upsert_index_row`
  → `_sync_problem_tags`, which also auto-registers any unseen tag). Tag
  filtering (OR semantics) is in `_build_where` in [sql_index.py](sql_index.py).

## Data layout

```
data/
└── <sanitized-email>/
    ├── problems/   <uuid>.json — canonical, append-only
    ├── figures/    <uuid>.png  — cropped figures referenced by problems
    └── raw/        <sha256>.<ext> — uploaded sources awaiting worker
```

The index (`problems`, `problem_tags`), queue (`raw_files`), and the
authoritative `tags` / `category_edits` tables live in Postgres (schema
`math_collection`), one database for all users, every row tagged with a
`user_id` column equal to the `data/<sanitized-email>/` slug.

`sanitize_email` lowercases and replaces filesystem-unsafe chars; the
non-whitelisted "guest" bucket also lives under `data/guest/` (user_id
`guest`).

## Conventions

- **JSON is canonical, Postgres is derived — for the `problems` and
  `problem_tags` tables.** Deleting a user's `problems` rows is safe; the
  next deploy-time `python -m common.db_setup` (or a direct
  `common.db_setup.setup.init_user` under that user's context) ensures the
  schema and backfills from the JSON files. The `category_edits` and `tags`
  tables are the **exception**: they're authoritative and not rebuildable,
  so don't truncate them casually if a user has edited categories or named
  tags.
- **The `raw_files` and `classify_tasks` queue tables are authoritative.**
  They track per-file / per-problem status, attempts, and errors — none of
  which can be rebuilt from the filesystem. Deleting a user's `raw_files`
  rows forces every raw file to re-enqueue on next upload (or it stays
  unprocessed forever). The `problems` table uses `(source_image, seq_no)`
  to dedupe at the agent layer, so even if the queue is wiped and a file
  is reprocessed, already-saved problems are skipped. Deleting
  `classify_tasks` rows is lower-stakes — `seed_pending()` recreates a
  `pending` row for any problem still `category='unclassified'` the next
  time the classify job runs. `raw_files.with_solution` is a vestigial
  column (unused since classification stopped writing solutions
  automatically — full solves are ad hoc via `/refine`); left in place
  rather than dropped.
- **Backfill is per-user and version-gated.** `schema_version` holds the
  latest version this schema declares; `user_data_version` holds, per user,
  the version that user's rows were last backfilled to. When they differ,
  `init_user()` re-upserts every problem from that user's JSON and bumps the
  user's row. Bump the literal in `schema.sql` after adding a column.
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

- Don't query Postgres directly from outside this package — go through
  `query_index` / `sample_index` so the user-context check and `user_id`
  scoping run. Every SQL statement in this package must filter by `user_id`.
- Don't add an edit endpoint without an explicit ask; the current design is
  append-only.
- Don't import Flask or the agent here.
