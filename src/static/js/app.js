(function () {
  const PAGE_SIZE = 5;

  const KATEX_OPTS = {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "$", right: "$", display: false },
      { left: "\\(", right: "\\)", display: false }
    ],
    throwOnError: false,
    ignoredTags: ["script", "noscript", "style", "textarea", "code"]
  };

  function renderMath(el) {
    if (window.renderMathInElement) {
      window.renderMathInElement(el, KATEX_OPTS);
    }
  }

  // Upload form spinner — keep existing behavior.
  const form = document.getElementById("upload-form");
  const submitBtn = document.getElementById("process-btn");
  const progress = document.getElementById("progress-wrap");
  if (form) {
    form.addEventListener("submit", function () {
      submitBtn.disabled = true;
      submitBtn.textContent = "Processing…";
      progress.classList.add("active");
    });
  }

  const filtersEl = document.getElementById("filters");
  const printBar = document.getElementById("print-bar");
  const catSel = document.getElementById("filter-category");
  const minInput = document.getElementById("filter-time-min");
  const maxInput = document.getElementById("filter-time-max");
  const sliderEl = document.querySelector(".range-slider");
  const fillEl = sliderEl ? sliderEl.querySelector(".range-fill") : null;
  const displayEl = document.getElementById("filter-time-display");
  const countEl = document.getElementById("filter-count");
  const totalCountEl = document.getElementById("total-count");
  const listEl = document.getElementById("problem-list");
  const paginationEl = document.getElementById("pagination");
  const pagePrev = document.getElementById("page-prev");
  const pageNext = document.getElementById("page-next");
  const pageInfoEl = document.getElementById("page-info");

  let sliderMax = 60;
  let currentPage = 1;
  let lastTotal = 0;

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function difficultyLabel(p) {
    if (p.solve_time_seconds == null) return "";
    const prefix = p.solve_time_estimated ? "~" : "";
    const suffix = p.solve_time_estimated ? " (est.)" : "";
    return `${prefix}${p.solve_time_seconds.toFixed(1)}s${suffix}`;
  }

  function renderProblem(p) {
    const div = document.createElement("div");
    div.className = "problem";
    div.dataset.id = p.id;
    div.dataset.category = (p.category || "").toLowerCase();
    div.dataset.solveTime = p.solve_time_seconds == null ? "" : p.solve_time_seconds;

    const dlabel = difficultyLabel(p);
    const heading = `${(p.category || "").replace(/\b\w/g, c => c.toUpperCase())}` +
      (dlabel ? ` &middot; ${escapeHtml(dlabel)}` : "");

    let html = `<button type="button" class="delete-btn" title="Delete this problem" aria-label="Delete this problem">🗑</button>` +
      `<h3>${heading}</h3>` +
      `<div class="meta">` +
        `<span>${escapeHtml(p.id.slice(0, 8))}</span>` +
        `<span>${escapeHtml((p.created_at || "").slice(0, 19).replace("T", " "))}</span>` +
      `</div>` +
      `<div class="rendered">${escapeHtml(p.problem_text)}</div>`;

    if (p.figure_image) {
      html += `<div class="diagram"><img src="/figures/${encodeURIComponent(p.figure_image)}" alt="figure"></div>`;
    } else if (p.diagram_svg) {
      html += `<div class="diagram">${p.diagram_svg}</div>`;
    }
    if (p.solution) {
      html += `<details><summary>Solution</summary>` +
        `<div class="rendered">${escapeHtml(p.solution)}</div>`;
      if (p.solution_svg) {
        html += `<div class="diagram">${p.solution_svg}</div>`;
      }
      html += `</details>`;
    }
    div.innerHTML = html;
    return div;
  }

  function rangeActive() {
    if (!minInput || !maxInput) return false;
    const lo = parseFloat(minInput.value);
    const hi = parseFloat(maxInput.value);
    return lo > 0 || hi < sliderMax;
  }

  function currentFilterParams() {
    const params = new URLSearchParams();
    if (catSel && catSel.value) params.set("category", catSel.value);
    if (minInput) params.set("min_time", minInput.value);
    if (maxInput) params.set("max_time", maxInput.value);
    params.set("range_max", String(sliderMax));
    return params;
  }

  function syncSlider() {
    if (!minInput || !maxInput) return;
    let lo = parseFloat(minInput.value);
    let hi = parseFloat(maxInput.value);
    if (lo > hi) {
      if (document.activeElement === minInput) {
        hi = lo; maxInput.value = String(hi);
      } else {
        lo = hi; minInput.value = String(lo);
      }
    }
    if (fillEl && sliderMax > 0) {
      fillEl.style.left = (lo / sliderMax * 100) + "%";
      fillEl.style.right = (100 - hi / sliderMax * 100) + "%";
    }
    if (displayEl) {
      displayEl.textContent = `${lo}s – ${hi}s`;
    }
  }

  function updatePaginationUI(total) {
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;
    pageInfoEl.textContent = total === 0
      ? "No problems"
      : `Page ${currentPage} of ${totalPages}`;
    pagePrev.disabled = currentPage <= 1;
    pageNext.disabled = currentPage >= totalPages;
    paginationEl.hidden = total <= PAGE_SIZE;
  }

  function updateFilterCount(total) {
    if (!countEl) return;
    const active = (catSel && catSel.value) || rangeActive();
    countEl.textContent = active ? `Matching: ${total}` : "";
  }

  let fetchSeq = 0;
  async function loadPage() {
    const seq = ++fetchSeq;
    listEl.innerHTML = `<p><em>Loading…</em></p>`;
    const params = currentFilterParams();
    params.set("page", String(currentPage));
    params.set("page_size", String(PAGE_SIZE));
    let data;
    try {
      const resp = await fetch(`/api/problems?${params.toString()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
    } catch (e) {
      if (seq !== fetchSeq) return;
      listEl.innerHTML = `<p><em>Failed to load: ${escapeHtml(e.message)}</em></p>`;
      return;
    }
    if (seq !== fetchSeq) return;

    lastTotal = data.total;
    if (totalCountEl) totalCountEl.textContent = String(data.total);
    updateFilterCount(data.total);

    listEl.innerHTML = "";
    if (!data.problems.length) {
      listEl.innerHTML = `<p><em>${data.total === 0 ? "No problems yet." : "No problems match the current filters."}</em></p>`;
    } else {
      const frag = document.createDocumentFragment();
      data.problems.forEach(p => frag.appendChild(renderProblem(p)));
      listEl.appendChild(frag);
      renderMath(listEl);
    }

    updatePaginationUI(data.total);
  }

  async function deleteProblem(id, problemEl) {
    if (!confirm("Delete this problem? This cannot be undone.")) return;
    const btn = problemEl.querySelector(".delete-btn");
    if (btn) btn.disabled = true;
    try {
      const resp = await fetch(`/api/problems/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (e) {
      if (btn) btn.disabled = false;
      alert(`Delete failed: ${e.message}`);
      return;
    }
    await loadSummary();
    await loadPage();
  }

  listEl.addEventListener("click", function (ev) {
    const btn = ev.target.closest(".delete-btn");
    if (!btn) return;
    const problemEl = btn.closest(".problem");
    if (!problemEl || !problemEl.dataset.id) return;
    deleteProblem(problemEl.dataset.id, problemEl);
  });

  let filterDebounce = null;
  function onFilterChange() {
    clearTimeout(filterDebounce);
    filterDebounce = setTimeout(() => {
      currentPage = 1;
      loadPage();
    }, 150);
  }

  function onSliderInput() {
    syncSlider();
    onFilterChange();
  }

  async function loadSummary() {
    let summary;
    try {
      const resp = await fetch("/api/summary");
      summary = await resp.json();
    } catch (e) {
      filtersEl.hidden = true;
      printBar.hidden = true;
      return;
    }

    if (summary.total === 0) {
      filtersEl.hidden = true;
      printBar.hidden = true;
      if (totalCountEl) totalCountEl.textContent = "0";
      return;
    }

    sliderMax = summary.max_time || 60;
    if (sliderEl) sliderEl.dataset.max = String(sliderMax);
    if (minInput) {
      minInput.max = String(sliderMax);
      minInput.value = "0";
    }
    if (maxInput) {
      maxInput.max = String(sliderMax);
      maxInput.value = String(sliderMax);
    }

    if (catSel) {
      catSel.innerHTML = `<option value="">All</option>`;
      summary.categories.forEach(c => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c.replace(/\b\w/g, ch => ch.toUpperCase());
        catSel.appendChild(opt);
      });
    }

    filtersEl.hidden = false;
    printBar.hidden = false;
    syncSlider();
  }

  if (catSel) catSel.addEventListener("change", onFilterChange);
  if (minInput) minInput.addEventListener("input", onSliderInput);
  if (maxInput) maxInput.addEventListener("input", onSliderInput);

  pagePrev.addEventListener("click", () => {
    if (currentPage > 1) {
      currentPage--;
      loadPage();
    }
  });
  pageNext.addEventListener("click", () => {
    const totalPages = Math.max(1, Math.ceil(lastTotal / PAGE_SIZE));
    if (currentPage < totalPages) {
      currentPage++;
      loadPage();
    }
  });

  // Print as PDF: backend samples N matching problems across the full filtered
  // set (not just the current page), renders them off-screen, then prints.
  const printContainer = document.getElementById("print-container");
  const printBtn = document.getElementById("print-btn");
  const printCountInput = document.getElementById("print-count");
  const printStatus = document.getElementById("print-status");

  function clearPrintSelection() {
    document.body.classList.remove("printing");
    printContainer.innerHTML = "";
  }

  if (printBtn) {
    printBtn.addEventListener("click", async function () {
      let n = parseInt(printCountInput.value, 10);
      if (isNaN(n) || n < 1) n = 1;
      printStatus.textContent = "Sampling…";
      const params = currentFilterParams();
      params.set("n", String(n));
      let data;
      try {
        const resp = await fetch(`/api/sample?${params.toString()}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        data = await resp.json();
      } catch (e) {
        printStatus.textContent = `Sample failed: ${e.message}`;
        return;
      }
      if (!data.problems || !data.problems.length) {
        printStatus.textContent = "No problems match the current filters.";
        return;
      }

      printContainer.innerHTML = "";
      data.problems.forEach(p => {
        const el = renderProblem(p);
        el.classList.add("print-selected");
        printContainer.appendChild(el);
      });
      renderMath(printContainer);

      printStatus.textContent = `Printing ${data.problems.length} problem(s)…`;
      document.body.classList.add("printing");
      const cleanup = () => {
        clearPrintSelection();
        printStatus.textContent = "";
        window.removeEventListener("afterprint", cleanup);
      };
      window.addEventListener("afterprint", cleanup);
      // Give the browser a tick to lay out KaTeX before opening print dialog.
      setTimeout(() => window.print(), 50);
    });
  }

  document.addEventListener("DOMContentLoaded", async function () {
    await loadSummary();
    await loadPage();
  });
})();
