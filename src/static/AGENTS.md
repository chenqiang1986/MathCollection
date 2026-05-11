# Static assets

Vanilla JS + CSS. No build step, no framework.

## Files

- [js/app.js](js/app.js) — index page client. Fetches `/api/summary` once
  to populate the category dropdown and slider range, then fetches
  `/api/problems?page=&page_size=&category=&min_time=&max_time=&range_max=`
  on every filter/page change. Renders each problem card and runs KaTeX
  auto-render on it. "Print as PDF" calls `/api/sample?n=&...` so a
  larger random selection (not just the current page of 5) gets rendered
  into `#print-container`.
- [js/stats.js](js/stats.js) — stats page client. Fetches
  `/api/stats/categories` and `/api/stats/difficulty?category=`; clicking
  a category bar re-fetches the difficulty chart for that slice.
- [css/style.css](css/style.css) — single stylesheet for both pages
  (including a `@media print` block used by "Print as PDF").

## Conventions

- **PAGE_SIZE = 5** in [js/app.js](js/app.js) must match `DEFAULT_PAGE_SIZE`
  in [../web/routes_api.py](../web/routes_api.py). The server clamps but
  the slider/pager UX is designed around 5.
- **KaTeX delimiters**: configured in `KATEX_OPTS` to match what the agent
  writes (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`). Don't add new
  delimiters without changing the prompts.
- **Range slider full-range = no filter**: when both handles are at
  `[0, sliderMax]`, the client still sends `min_time`/`max_time` plus
  `range_max=sliderMax` so the server drops the time filter and includes
  rows with `solve_time_seconds IS NULL`. Don't strip the params on the
  client.
- **Print path**: collect HTML into `#print-container`, swap a body class,
  call `window.print()`, then restore. Don't pop a new window — KaTeX
  needs to render in the same document.

## Don't

- Don't introduce a bundler / framework for this scale.
- Don't fetch all problems at once — pagination + sample is the contract.
- Don't write to localStorage / cookies for filter state without a real
  reason; the URL/query approach is simpler and shareable.
