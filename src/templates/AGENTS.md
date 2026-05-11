# Templates

Server-rendered HTML. The pages do very little — they're shells the JS in
[../static/js/](../static/js/) fills via `/api/...` fetches.

## Files

- [index.html](index.html) — upload form (whitelist-only), agent-summary
  banner after a run, and empty containers (`#problem-list`, `#pagination`,
  filters, print bar) that [../static/js/app.js](../static/js/app.js)
  populates. KaTeX auto-render is loaded from a CDN with SRI hashes.
- [stats.html](stats.html) — two empty `<div class="bar-chart">` slots
  filled by [../static/js/stats.js](../static/js/stats.js) (categories
  bar chart + per-category difficulty distribution).

## Conventions

- **Auth state** comes from the `current_user` / `can_upload` context
  processor in [../web/auth.py](../web/auth.py). Use those, not session
  reads, in templates.
- **KaTeX delimiters** are `$...$` (inline) and `$$...$$` (display) — must
  match what the agent prompts produce and what
  [../static/js/app.js](../static/js/app.js) configures.
- **Problem list is not server-rendered.** Don't add a server-side loop
  over problems — that would duplicate the client renderer and drift.
- **Flash messages** use `(category, msg)` tuples; the existing
  `{% with msgs = get_flashed_messages(with_categories=true) %}` block in
  `index.html` handles them.

## Don't

- Don't inline JS or CSS — put it under [../static/](../static/).
- Don't add a sign-in form here; OAuth redirect via
  `url_for('auth.login')` is the only entry point.
