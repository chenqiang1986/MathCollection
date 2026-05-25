(function () {
  const PAGE_SIZE = 5;
  const CAN_UPLOAD = document.body.dataset.canUpload === "1";
  const URL_PREFIX = document.body.dataset.urlPrefix || "";

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
      submitBtn.textContent = "Uploading…";
      progress.classList.add("active");
    });
  }

  const filtersEl = document.getElementById("filters");
  const printBar = document.getElementById("print-bar");
  const catSel = document.getElementById("filter-category");
  const subSel = document.getElementById("filter-subcategory");
  const examSel = document.getElementById("filter-exam");
  const subexamSel = document.getElementById("filter-subexam");
  const yearSel = document.getElementById("filter-year");
  const figureSel = document.getElementById("filter-figure");
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
  let knownCategories = [];
  // Map { category: [subcategory, ...] } returned by /api/summary.
  let subcategoryMap = {};
  // Map { exam: [subexam, ...] } returned by /api/summary.
  let subexamMap = {};
  // Flat set of all subcategories across categories (for datalist on edit).
  let knownSubcategories = [];

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function titleCase(s) {
    return String(s || "").replace(/\b\w/g, c => c.toUpperCase());
  }

  function difficultyLabel(p) {
    const real = p.solve_time_seconds;
    const est = p.solve_time_estimated;
    if (real != null && est) {
      return `${real.toFixed(1)}s (est. ${est}s)`;
    }
    if (real != null) {
      return `${real.toFixed(1)}s`;
    }
    if (est) {
      return `~${est}s (est.)`;
    }
    return "";
  }

  function renderProblem(p) {
    const div = document.createElement("div");
    div.className = "problem";
    div.dataset.id = p.id;
    div.dataset.category = (p.category || "").toLowerCase();
    div.dataset.subcategory = (p.subcategory || "").toLowerCase();
    div.dataset.solveTime = p.solve_time_seconds == null ? "" : p.solve_time_seconds;

    const dlabel = difficultyLabel(p);
    const catRaw = p.category || "";
    const subRaw = p.subcategory || "";
    const catPart = titleCase(catRaw);
    const subPart = titleCase(subRaw);
    const editAttrs = CAN_UPLOAD ? ' class="editable" title="Double-click to edit"' : "";
    const catSpan = `<span data-field="category" data-value="${escapeHtml(catRaw)}"${editAttrs}>` +
      `${escapeHtml(catPart) || "(uncategorized)"}</span>`;
    let subSpan;
    if (subPart) {
      subSpan = ` &mdash; <span data-field="subcategory" data-value="${escapeHtml(subRaw)}"${editAttrs}>${escapeHtml(subPart)}</span>`;
    } else if (CAN_UPLOAD) {
      subSpan = ` &mdash; <span data-field="subcategory" data-value="" class="editable placeholder" title="Double-click to add">add subcategory</span>`;
    } else {
      subSpan = "";
    }
    const heading = catSpan + subSpan + (dlabel ? ` &middot; ${escapeHtml(dlabel)}` : "");

    const actionButtons = CAN_UPLOAD
      ? `<button type="button" class="refine-btn" title="${p.solution ? 'Refine solution with a hint' : 'Generate solution with a hint'}" aria-label="Refine solution">✨</button>` +
        `<button type="button" class="delete-btn" title="Delete this problem" aria-label="Delete this problem">🗑</button>`
      : "";

    const examText = p.source_exam && p.source_exam !== "Unknown" ? p.source_exam : "";
    const subexamText = (p.subexam || "").trim();
    const yearText = p.year && p.year !== "Unknown" ? p.year : "";
    const sourceLabel = [yearText, examText, subexamText].filter(Boolean).join(" · ");
    const sourceSpan = sourceLabel ? `<span class="source">${escapeHtml(sourceLabel)}</span>` : "";
    const rawLinkSpan = p.source_image
      ? `<span class="raw-link"><a href="${URL_PREFIX}/raw/${encodeURIComponent(p.source_image)}" target="_blank" rel="noopener">raw${p.source_page ? ` p${p.source_page}` : ""}</a></span>`
      : "";

    let html = actionButtons +
      `<h3>${heading}</h3>` +
      `<div class="meta">` +
        sourceSpan +
        rawLinkSpan +
        `<span>${escapeHtml((p.created_at || "").slice(0, 19).replace("T", " "))}</span>` +
        `<span>${escapeHtml(p.id.slice(0, 8))}</span>` +
      `</div>` +
      `<div class="rendered">${escapeHtml(p.problem_text)}</div>`;

    if (p.figure_image) {
      const adjustable = CAN_UPLOAD && p.source_image ? " adjustable" : "";
      const titleAttr = adjustable ? ' title="Double-click to adjust crop"' : "";
      html += `<div class="diagram"><img class="figure-image${adjustable}" src="/figures/${encodeURIComponent(p.figure_image)}" alt="figure"${titleAttr}></div>`;
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
    if (CAN_UPLOAD) {
      const ctaLabel = p.solution ? "Refine with hint" : "Generate solution with hint";
      html += `<div class="refine-panel" hidden>` +
        `<label class="refine-label" for="refine-hint-${p.id}">Tell Claude what to fix — it will pick one of: re-solve with your hint, re-crop the figure, or re-transcribe the problem text.</label>` +
        `<textarea class="refine-hint" id="refine-hint-${p.id}" rows="2" placeholder='e.g. "use the inscribed angle theorem", "the figure is cut off on the right", "the problem says 71 not 17"'></textarea>` +
        `<div class="refine-actions">` +
          `<button type="button" class="refine-submit">${ctaLabel}</button>` +
          `<button type="button" class="refine-cancel">Cancel</button>` +
          `<span class="refine-status progress-text"></span>` +
        `</div>` +
      `</div>`;
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
    if (subSel && subSel.value) params.set("subcategory", subSel.value);
    if (examSel && examSel.value) params.set("source_exam", examSel.value);
    if (subexamSel && subexamSel.value) params.set("subexam", subexamSel.value);
    if (yearSel && yearSel.value) params.set("year", yearSel.value);
    if (figureSel && figureSel.value) params.set("has_figure", figureSel.value);
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
    const active =
      (catSel && catSel.value) ||
      (subSel && subSel.value) ||
      (examSel && examSel.value) ||
      (subexamSel && subexamSel.value) ||
      (yearSel && yearSel.value) ||
      (figureSel && figureSel.value) ||
      rangeActive();
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
      const resp = await fetch(`${URL_PREFIX}/api/problems?${params.toString()}`);
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
      const resp = await fetch(`${URL_PREFIX}/api/problems/${encodeURIComponent(id)}`, { method: "DELETE" });
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
    const delBtn = ev.target.closest(".delete-btn");
    if (delBtn) {
      const problemEl = delBtn.closest(".problem");
      if (problemEl && problemEl.dataset.id) {
        deleteProblem(problemEl.dataset.id, problemEl);
      }
      return;
    }
    const refineBtn = ev.target.closest(".refine-btn");
    if (refineBtn) {
      const problemEl = refineBtn.closest(".problem");
      const panel = problemEl && problemEl.querySelector(".refine-panel");
      if (panel) {
        const hidden = panel.hasAttribute("hidden");
        if (hidden) {
          panel.removeAttribute("hidden");
          const ta = panel.querySelector(".refine-hint");
          if (ta) ta.focus();
        } else {
          panel.setAttribute("hidden", "");
        }
      }
      return;
    }
    const cancelBtn = ev.target.closest(".refine-cancel");
    if (cancelBtn) {
      const panel = cancelBtn.closest(".refine-panel");
      if (panel) panel.setAttribute("hidden", "");
      return;
    }
    const submitBtn = ev.target.closest(".refine-submit");
    if (submitBtn) {
      const problemEl = submitBtn.closest(".problem");
      if (problemEl && problemEl.dataset.id) {
        refineProblem(problemEl.dataset.id, problemEl);
      }
      return;
    }
  });

  listEl.addEventListener("dblclick", function (ev) {
    if (!CAN_UPLOAD) return;
    const figImg = ev.target.closest("img.figure-image.adjustable");
    if (figImg) {
      const problemEl = figImg.closest(".problem");
      if (problemEl && problemEl.dataset.id) {
        openFigureEditor(problemEl);
      }
      return;
    }
    const span = ev.target.closest("h3 .editable");
    if (!span) return;
    const problemEl = span.closest(".problem");
    if (!problemEl || problemEl.classList.contains("editing-category")) return;
    startCategoryEditor(problemEl);
  });

  function openFigureEditor(problemEl) {
    const editor = document.getElementById("figure-editor");
    if (!editor) return;
    const pageImg = editor.querySelector(".figure-editor-page");
    const stage = editor.querySelector(".figure-editor-stage");
    const selection = editor.querySelector(".figure-editor-selection");
    const saveBtn = editor.querySelector(".figure-editor-save");
    const cancelBtn = editor.querySelector(".figure-editor-cancel");
    const closeBtn = editor.querySelector(".figure-editor-close");
    const backdrop = editor.querySelector(".figure-editor-backdrop");
    const rotationSel = editor.querySelector(".figure-editor-rotation");
    const statusEl = editor.querySelector(".figure-editor-status");
    const prevBtn = editor.querySelector(".figure-editor-prev");
    const nextBtn = editor.querySelector(".figure-editor-next");
    const pageInfoEl = editor.querySelector(".figure-editor-page-info");

    const problemId = problemEl.dataset.id;
    let bbox = null; // [x0, y0, x1, y1] normalized in [0,1]
    let currentPage = null;
    let pageCount = null;

    selection.hidden = true;
    saveBtn.disabled = true;
    rotationSel.value = "0";
    statusEl.textContent = "Loading source page…";
    pageImg.removeAttribute("src");
    editor.removeAttribute("hidden");
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    if (pageInfoEl) pageInfoEl.textContent = "";

    function updatePagerUI() {
      if (pageInfoEl) {
        pageInfoEl.textContent = currentPage && pageCount
          ? `Page ${currentPage} of ${pageCount}`
          : "";
      }
      if (prevBtn) prevBtn.disabled = !currentPage || currentPage <= 1;
      if (nextBtn) nextBtn.disabled = !currentPage || !pageCount || currentPage >= pageCount;
    }

    async function loadPage(page) {
      bbox = null;
      selection.hidden = true;
      saveBtn.disabled = true;
      statusEl.textContent = "Loading source page…";
      const url = page == null
        ? `${URL_PREFIX}/api/problems/${encodeURIComponent(problemId)}/source_page?t=${Date.now()}`
        : `${URL_PREFIX}/api/problems/${encodeURIComponent(problemId)}/source_page?page=${page}&t=${Date.now()}`;
      try {
        const resp = await fetch(url);
        if (!resp.ok) {
          let msg = `HTTP ${resp.status}`;
          try { const err = await resp.json(); if (err && err.error) msg = err.error; } catch (_) {}
          throw new Error(msg);
        }
        const p = parseInt(resp.headers.get("X-Page") || "", 10);
        const tot = parseInt(resp.headers.get("X-Page-Count") || "", 10);
        if (p) currentPage = p;
        if (tot) pageCount = tot;
        const blob = await resp.blob();
        const prevUrl = pageImg.src;
        pageImg.src = URL.createObjectURL(blob);
        if (prevUrl && prevUrl.startsWith("blob:")) URL.revokeObjectURL(prevUrl);
      } catch (e) {
        statusEl.textContent = `Failed to load source page: ${e.message}`;
      }
      updatePagerUI();
    }

    pageImg.onload = () => {
      statusEl.textContent = "Click and drag to select the figure region.";
    };
    pageImg.onerror = () => {
      statusEl.textContent = "Failed to load source page.";
    };
    loadPage(null);

    function goPrev() {
      if (!currentPage || currentPage <= 1) return;
      loadPage(currentPage - 1);
    }
    function goNext() {
      if (!currentPage || !pageCount || currentPage >= pageCount) return;
      loadPage(currentPage + 1);
    }
    if (prevBtn) prevBtn.onclick = goPrev;
    if (nextBtn) nextBtn.onclick = goNext;

    function updateSelectionBox() {
      if (!bbox) { selection.hidden = true; return; }
      const rect = pageImg.getBoundingClientRect();
      const stageRect = stage.getBoundingClientRect();
      // Offset of the image inside the (possibly scrollable) stage.
      const offX = rect.left - stageRect.left + stage.scrollLeft;
      const offY = rect.top - stageRect.top + stage.scrollTop;
      const [x0, y0, x1, y1] = bbox;
      selection.style.left = (offX + x0 * rect.width) + "px";
      selection.style.top = (offY + y0 * rect.height) + "px";
      selection.style.width = ((x1 - x0) * rect.width) + "px";
      selection.style.height = ((y1 - y0) * rect.height) + "px";
      selection.hidden = false;
    }

    let dragging = false;
    let startX = 0, startY = 0;

    function pointToNorm(ev) {
      const rect = pageImg.getBoundingClientRect();
      const x = (ev.clientX - rect.left) / rect.width;
      const y = (ev.clientY - rect.top) / rect.height;
      return [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
    }

    function onMouseDown(ev) {
      if (ev.button !== 0) return;
      if (!pageImg.complete || !pageImg.naturalWidth) return;
      ev.preventDefault();
      const [nx, ny] = pointToNorm(ev);
      startX = nx; startY = ny;
      bbox = [nx, ny, nx, ny];
      dragging = true;
      updateSelectionBox();
    }
    function onMouseMove(ev) {
      if (!dragging) return;
      const [nx, ny] = pointToNorm(ev);
      bbox = [
        Math.min(startX, nx), Math.min(startY, ny),
        Math.max(startX, nx), Math.max(startY, ny),
      ];
      updateSelectionBox();
    }
    function onMouseUp() {
      if (!dragging) return;
      dragging = false;
      if (bbox && (bbox[2] - bbox[0] > 0.005) && (bbox[3] - bbox[1] > 0.005)) {
        saveBtn.disabled = false;
        statusEl.textContent = "Press Save to re-crop, or drag again to redraw.";
      } else {
        bbox = null;
        saveBtn.disabled = true;
        selection.hidden = true;
        statusEl.textContent = "Selection too small — drag a larger region.";
      }
    }
    function onResize() { updateSelectionBox(); }

    stage.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    window.addEventListener("resize", onResize);
    stage.addEventListener("scroll", onResize);

    function close() {
      editor.setAttribute("hidden", "");
      stage.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      window.removeEventListener("resize", onResize);
      stage.removeEventListener("scroll", onResize);
      document.removeEventListener("keydown", onKey);
      pageImg.onload = null;
      pageImg.onerror = null;
      if (prevBtn) prevBtn.onclick = null;
      if (nextBtn) nextBtn.onclick = null;
      if (pageImg.src && pageImg.src.startsWith("blob:")) {
        URL.revokeObjectURL(pageImg.src);
      }
      pageImg.removeAttribute("src");
    }
    function onKey(ev) {
      if (ev.key === "Escape") { ev.preventDefault(); close(); }
    }
    document.addEventListener("keydown", onKey);
    cancelBtn.onclick = close;
    closeBtn.onclick = close;
    backdrop.onclick = close;

    saveBtn.onclick = async () => {
      if (!bbox) return;
      saveBtn.disabled = true;
      cancelBtn.disabled = true;
      statusEl.textContent = "Saving…";
      try {
        const resp = await fetch(`${URL_PREFIX}/api/problems/${encodeURIComponent(problemId)}/figure_bbox`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            bbox,
            rotation: parseInt(rotationSel.value, 10) || 0,
            page: currentPage,
          }),
        });
        if (!resp.ok) {
          let msg = `HTTP ${resp.status}`;
          try { const err = await resp.json(); if (err && err.error) msg = err.error; } catch (_) {}
          throw new Error(msg);
        }
        const data = await resp.json();
        if (data.problem) {
          const fresh = renderProblem(data.problem);
          problemEl.replaceWith(fresh);
          renderMath(fresh);
        }
        close();
      } catch (e) {
        saveBtn.disabled = false;
        cancelBtn.disabled = false;
        statusEl.textContent = `Failed: ${e.message}`;
      }
    };
  }

  const NEW_SENTINEL = "__new__";

  function startCategoryEditor(problemEl) {
    const h3 = problemEl.querySelector("h3");
    if (!h3) return;
    const catSpan = h3.querySelector('[data-field="category"]');
    const subSpan = h3.querySelector('[data-field="subcategory"]');
    const currentCat = catSpan ? (catSpan.dataset.value || "") : "";
    const currentSub = subSpan ? (subSpan.dataset.value || "") : "";
    const originalHeadingHtml = h3.innerHTML;

    problemEl.classList.add("editing-category");

    const editor = document.createElement("span");
    editor.className = "cat-editor";

    let catCtrl = buildCategoryControl(currentCat);
    let subCtrl = buildSubcategoryControl(readValue(catCtrl), currentSub);

    catCtrl.addEventListener("change", onCategoryChange);

    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "cat-editor-save";
    saveBtn.textContent = "Save";

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "cat-editor-cancel";
    cancelBtn.textContent = "Cancel";

    const statusEl = document.createElement("span");
    statusEl.className = "cat-editor-status";

    editor.appendChild(catCtrl);
    editor.appendChild(document.createTextNode(" / "));
    editor.appendChild(subCtrl);
    editor.appendChild(saveBtn);
    editor.appendChild(cancelBtn);
    editor.appendChild(statusEl);

    h3.innerHTML = "";
    h3.appendChild(editor);
    focusControl(catCtrl);

    function onCategoryChange() {
      if (catCtrl.tagName === "SELECT" && catCtrl.value === NEW_SENTINEL) {
        const inp = makeFreeformInput("all-categories", "new category", "");
        catCtrl.replaceWith(inp);
        catCtrl = inp;
        focusControl(catCtrl);
      }
      const newCatValue = readValue(catCtrl);
      const preservedSub = readValue(subCtrl);
      const valid = subcategoryMap[newCatValue] || [];
      const keep = preservedSub === "" || valid.indexOf(preservedSub) !== -1;
      const replacement = buildSubcategoryControl(newCatValue, keep ? preservedSub : "");
      subCtrl.replaceWith(replacement);
      subCtrl = replacement;
    }

    editor.addEventListener("change", (e) => {
      if (e.target === subCtrl && subCtrl.tagName === "SELECT" && subCtrl.value === NEW_SENTINEL) {
        const inp = makeFreeformInput("all-subcategories", "subcategory (blank for none)", "");
        subCtrl.replaceWith(inp);
        subCtrl = inp;
        focusControl(subCtrl);
      }
    });

    editor.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); cancel(); }
      else if (e.key === "Enter") { e.preventDefault(); save(); }
    });

    cancelBtn.addEventListener("click", cancel);
    saveBtn.addEventListener("click", save);

    function cancel() {
      h3.innerHTML = originalHeadingHtml;
      problemEl.classList.remove("editing-category");
    }

    async function save() {
      const newCat = readValue(catCtrl);
      const newSub = readValue(subCtrl);
      if (!newCat) { statusEl.textContent = "Category required."; return; }
      if (newCat === currentCat.toLowerCase() && newSub === currentSub.toLowerCase()) {
        cancel();
        return;
      }
      saveBtn.disabled = true;
      cancelBtn.disabled = true;
      catCtrl.disabled = true;
      subCtrl.disabled = true;
      statusEl.textContent = "Saving…";

      try {
        const resp = await fetch(`${URL_PREFIX}/api/problems/${encodeURIComponent(problemEl.dataset.id)}/category`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category: newCat, subcategory: newSub }),
        });
        if (!resp.ok) {
          let msg = `HTTP ${resp.status}`;
          try { const err = await resp.json(); if (err && err.error) msg = err.error; } catch (_) {}
          throw new Error(msg);
        }
        const data = await resp.json();
        if (data.problem) {
          recordKnownCategoryPair(data.problem.category, data.problem.subcategory || "");
          refreshCategoryDatalist();
          refreshSubcategoryDatalist();
          refreshSubcategorySelect();
          const fresh = renderProblem(data.problem);
          problemEl.replaceWith(fresh);
          renderMath(fresh);
        } else {
          cancel();
        }
      } catch (e) {
        saveBtn.disabled = false;
        cancelBtn.disabled = false;
        catCtrl.disabled = false;
        subCtrl.disabled = false;
        statusEl.textContent = `Failed: ${e.message}`;
      }
    }
  }

  function readValue(ctrl) {
    const v = (ctrl.value || "").trim().toLowerCase();
    return v === NEW_SENTINEL ? "" : v;
  }

  function focusControl(ctrl) {
    ctrl.focus();
    if (ctrl.tagName === "INPUT") ctrl.select();
  }

  function buildCategoryControl(currentValue) {
    const sel = document.createElement("select");
    sel.className = "cat-editor-cat";
    const cats = knownCategories.slice();
    if (currentValue && cats.indexOf(currentValue) === -1) cats.push(currentValue);
    cats.sort();
    cats.forEach(c => sel.appendChild(makeOption(c, titleCase(c), c === currentValue)));
    sel.appendChild(makeOption(NEW_SENTINEL, "+ Add new category…", false));
    return sel;
  }

  function buildSubcategoryControl(category, currentValue) {
    const sel = document.createElement("select");
    sel.className = "cat-editor-sub";
    sel.appendChild(makeOption("", "(none)", !currentValue));
    const subs = (subcategoryMap[category] || []).slice();
    if (currentValue && subs.indexOf(currentValue) === -1) subs.push(currentValue);
    subs.sort();
    subs.forEach(s => sel.appendChild(makeOption(s, titleCase(s), s === currentValue)));
    sel.appendChild(makeOption(NEW_SENTINEL, "+ Add new subcategory…", false));
    return sel;
  }

  function makeFreeformInput(datalistId, placeholder, value) {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.className = "cat-editor-freeform";
    inp.value = value;
    inp.placeholder = placeholder;
    inp.autocomplete = "off";
    inp.setAttribute("list", datalistId);
    return inp;
  }

  function makeOption(value, label, selected) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    if (selected) opt.selected = true;
    return opt;
  }

  function recordKnownCategoryPair(cat, sub) {
    if (cat && knownCategories.indexOf(cat) === -1) {
      knownCategories.push(cat); knownCategories.sort();
    }
    if (cat) {
      const subs = subcategoryMap[cat] || (subcategoryMap[cat] = []);
      if (sub && subs.indexOf(sub) === -1) { subs.push(sub); subs.sort(); }
    }
    if (sub && knownSubcategories.indexOf(sub) === -1) {
      knownSubcategories.push(sub); knownSubcategories.sort();
    }
  }

  function refreshCategoryDatalist() {
    const dl = document.getElementById("all-categories");
    if (!dl) return;
    dl.innerHTML = "";
    knownCategories.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c;
      dl.appendChild(opt);
    });
  }

  function refreshSubcategoryDatalist() {
    const dl = document.getElementById("all-subcategories");
    if (!dl) return;
    dl.innerHTML = "";
    knownSubcategories.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      dl.appendChild(opt);
    });
  }

  function refreshSubexamSelect() {
    // Populate the subexam dropdown from the currently selected exam.
    // With no exam selected, show every distinct subexam across all exams.
    if (!subexamSel) return;
    const previousValue = subexamSel.value;
    const exam = examSel && examSel.value;
    let options;
    if (exam) {
      options = (subexamMap[exam] || []).slice();
    } else {
      const seen = new Set();
      options = [];
      Object.values(subexamMap).forEach(list => {
        list.forEach(s => {
          if (!seen.has(s)) { seen.add(s); options.push(s); }
        });
      });
      options.sort();
    }
    subexamSel.innerHTML = `<option value="">All</option>`;
    options.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s.replace(/\b\w/g, ch => ch.toUpperCase());
      subexamSel.appendChild(opt);
    });
    if (previousValue && options.indexOf(previousValue) !== -1) {
      subexamSel.value = previousValue;
    } else {
      subexamSel.value = "";
    }
  }

  function refreshSubcategorySelect() {
    // Populate the subcategory filter dropdown based on the currently
    // selected category. With no category selected, show every known
    // subcategory across all categories.
    if (!subSel) return;
    const previousValue = subSel.value;
    const cat = catSel && catSel.value;
    let options;
    if (cat) {
      options = (subcategoryMap[cat] || []).slice();
    } else {
      const seen = new Set();
      options = [];
      Object.values(subcategoryMap).forEach(list => {
        list.forEach(s => {
          if (!seen.has(s)) { seen.add(s); options.push(s); }
        });
      });
      options.sort();
    }
    subSel.innerHTML = `<option value="">All</option>`;
    options.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s.replace(/\b\w/g, ch => ch.toUpperCase());
      subSel.appendChild(opt);
    });
    // Preserve the previous selection if it's still valid.
    if (previousValue && options.indexOf(previousValue) !== -1) {
      subSel.value = previousValue;
    } else {
      subSel.value = "";
    }
  }

  async function refineProblem(id, problemEl) {
    const panel = problemEl.querySelector(".refine-panel");
    if (!panel) return;
    const hintEl = panel.querySelector(".refine-hint");
    const submitBtn = panel.querySelector(".refine-submit");
    const cancelBtn = panel.querySelector(".refine-cancel");
    const statusEl = panel.querySelector(".refine-status");
    const refineBtn = problemEl.querySelector(".refine-btn");
    const hint = (hintEl && hintEl.value || "").trim();
    if (!hint) {
      statusEl.textContent = "Please describe what to fix before submitting.";
      if (hintEl) hintEl.focus();
      return;
    }

    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    if (refineBtn) refineBtn.disabled = true;
    if (hintEl) hintEl.disabled = true;
    statusEl.textContent = "Generating — this can take a minute…";

    let data;
    try {
      const resp = await fetch(`${URL_PREFIX}/api/problems/${encodeURIComponent(id)}/refine`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hint })
      });
      if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try { const err = await resp.json(); if (err && err.error) msg = err.error; } catch (_) {}
        throw new Error(msg);
      }
      data = await resp.json();
    } catch (e) {
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      if (refineBtn) refineBtn.disabled = false;
      if (hintEl) hintEl.disabled = false;
      statusEl.textContent = `Failed: ${e.message}`;
      return;
    }

    if (data.problem) {
      const fresh = renderProblem(data.problem);
      problemEl.replaceWith(fresh);
      renderMath(fresh);
      const newDetails = fresh.querySelector("details");
      if (newDetails) newDetails.open = true;
    } else {
      statusEl.textContent = "Updated.";
    }
  }

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
      const resp = await fetch(`${URL_PREFIX}/api/summary`);
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
    populateSelect(examSel, summary.exams || []);
    subexamMap = summary.subexams || {};
    refreshSubexamSelect();
    populateSelect(yearSel, summary.years || []);
    knownCategories = (summary.categories || []).slice();
    subcategoryMap = summary.subcategories || {};
    const subSet = new Set();
    Object.values(subcategoryMap).forEach(list => list.forEach(s => subSet.add(s)));
    knownSubcategories = Array.from(subSet).sort();
    refreshCategoryDatalist();
    refreshSubcategoryDatalist();
    refreshSubcategorySelect();

    filtersEl.hidden = false;
    printBar.hidden = false;
    syncSlider();
  }

  if (catSel) catSel.addEventListener("change", () => {
    refreshSubcategorySelect();
    onFilterChange();
  });
  if (subSel) subSel.addEventListener("change", onFilterChange);
  if (examSel) examSel.addEventListener("change", () => {
    refreshSubexamSelect();
    onFilterChange();
  });
  if (subexamSel) subexamSel.addEventListener("change", onFilterChange);
  if (yearSel) yearSel.addEventListener("change", onFilterChange);
  if (figureSel) figureSel.addEventListener("change", onFilterChange);
  if (minInput) minInput.addEventListener("input", onSliderInput);
  if (maxInput) maxInput.addEventListener("input", onSliderInput);

  function populateSelect(sel, values) {
    if (!sel) return;
    const previous = sel.value;
    sel.innerHTML = `<option value="">All</option>`;
    values.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      sel.appendChild(opt);
    });
    if (previous && values.indexOf(previous) !== -1) {
      sel.value = previous;
    }
  }

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
        const resp = await fetch(`${URL_PREFIX}/api/sample?${params.toString()}`);
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
