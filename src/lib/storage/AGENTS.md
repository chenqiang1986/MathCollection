# Storage package

Per-user, file-backed problem store with a derived SQLite index.

## Files

- [__init__.py](__init__.py) — public surface; re-exports every helper used
  by callers. Always import from `lib.storage`, not from submodules, so the
  internal layout stays free to move.
- [vocab.py](vocab.py) — record types only (`Problem` dataclass,
  `Bucket` NamedTuple, `DIFFICULTY_BUCKETS`). No runtime deps so any module
  can import it without pulling in sqlite3 or filesystem code.
- [paths.py](paths.py) — user-context binding (`set_current_user`,
  `reset_current_user`) and per-user path helpers
  (`user_dir`, `problems_dir`, `figures_dir`, `index_path`,
  `figure_path`). All other storage modules call these — never hardcode a
  path.
- [problem_io.py](problem_io.py) — JSON read/write for problem records.
  `save_problem` / `update_problem` both mirror into the SQLite index.
  `delete_problem` removes the JSON, the figure (if any), and the index row.
- [sql_index.py](sql_index.py) — SQLite schema, init/backfill, and the
  filter-aware `query_index` / `sample_index` used by the API.
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
    └── problems_index.db SQLite mirror, auto-rebuilt from JSON if missing
```

`sanitize_email` lowercases and replaces filesystem-unsafe chars; the
non-whitelisted "guest" bucket also lives under `data/guest/`.

## Conventions

- **JSON is canonical, SQLite is derived — for the `problems` table.**
  Deleting `problems_index.db` is safe for the index; `init_index()`
  rebuilds it from the JSON files. The `category_edits` table in the same
  DB is the **exception**: it's authoritative and is not rebuildable, so
  don't drop the DB casually if a user has edited categories.
- **Every write to a problem JSON file must mirror into the index** —
  that's what `_upsert_index_row` is for. Don't write a record without
  upserting, or `/api/problems` will miss it.
- **User context is required.** Calling any path-aware helper without
  `storage.set_current_user(email)` first raises `RuntimeError`. The
  `login_required` decorator in [../../web/auth.py](../../web/auth.py)
  wraps every HTTP handler with this; if you add a CLI or background job,
  call `set_current_user` yourself.
- **`Problem.from_dict` ignores unknown keys.** Adding a field is
  backwards-compatible; renaming is not — old JSON files lose the renamed
  field silently. Migrate by reading + rewriting if you rename.
- **Filter semantics in `_build_where`:** if the requested `[min_time,
  max_time]` covers `full_range_max`, the time filter is dropped so rows
  with `solve_time_seconds IS NULL` aren't excluded. Keep this in sync with
  the slider behavior in [../../static/js/app.js](../../static/js/app.js).
- **Difficulty buckets** (`DIFFICULTY_BUCKETS`) are half-open `[lo, hi)`;
  the last bucket's `hi` is `inf`. Edit labels there, not in JS.

## Don't

- Don't query SQLite directly from outside this package — go through
  `query_index` / `sample_index` so the user-context check runs.
- Don't add an edit endpoint without an explicit ask; the current design is
  append-only.
- Don't import Flask or the agent here.
