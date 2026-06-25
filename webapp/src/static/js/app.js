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

  const PREVIEW_KATEX_OPTS = {
    ...KATEX_OPTS,
    delimiters: [
      { left: "$$", right: "$$", display: false },
      { left: "\\[", right: "\\]", display: false },
      { left: "$", right: "$", display: false },
      { left: "\\(", right: "\\)", display: false }
    ]
  };

  function renderMath(el, opts = KATEX_OPTS) {
    if (window.renderMathInElement) {
      window.renderMathInElement(el, opts);
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
  const filtersToggle = document.getElementById("filters-toggle");
  const practiceBar = document.getElementById("practice-bar");
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
  const practiceSetSelect = document.getElementById("practice-set-select");
  const practicePrintBtn = document.getElementById("practice-print-btn");
  const practiceStatus = document.getElementById("practice-status");
  const practiceSetPanel = document.getElementById("practice-set-panel");
  const practiceSetTitle = document.getElementById("practice-set-title");
  const practiceSetMeta = document.getElementById("practice-set-meta");
  const practiceSetList = document.getElementById("practice-set-list");
  const practiceDeleteBtn = document.getElementById("practice-delete-btn");
  const printHeaderTitle = document.getElementById("print-header-title");
  const practiceCreateModal = document.getElementById("practice-create-modal");
  const practiceCreateSeriesInput = document.getElementById("practice-create-series");
  const practiceCreateNameInput = document.getElementById("practice-create-name");
  const practiceCreateCountInput = document.getElementById("practice-create-count");
  const practiceCreateSubmit = document.getElementById("practice-create-submit");
  const practiceCreateCancel = document.getElementById("practice-create-cancel");
  const practiceCreateError = document.getElementById("practice-create-error");
  const practiceSeriesNames = document.getElementById("practice-series-names");

  let sliderMax = 60;
  let currentPage = 1;
  let lastTotal = 0;
  let knownCategories = [];
  // Map { category: [subcategory, ...] } returned by /api/summary.
  let subcategoryMap = {};
  // Map { exam: [subexam, ...] } returned by /api/summary.
  let subexamMap = {};
  // Ordered list of distinct exams returned by /api/summary.
  let examList = [];
  // Ordered list of distinct years returned by /api/summary.
  let yearList = [];
  // Flat set of all subcategories across categories (for datalist on edit).
  let knownSubcategories = [];
  // Tag registry from /api/tags: ordered [{name, comment, count}] for the
  // datalist, plus a name→comment map for fast tooltip lookups.
  let knownTags = [];
  let tagCommentMap = {};
  let practiceSets = [];
  let activePracticeSet = null;
  let activePracticeProblemIds = new Set();
  const PRACTICE_CREATE_SENTINEL = "__create_new__";

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Promise-based replacements for the native alert()/confirm() popups. Builds a
  // styled overlay on the fly and resolves when the user picks an action: true
  // for confirm, false for cancel/backdrop/Escape. alertDialog has no cancel.
  function showModal({ title, message, confirmText, cancelText, danger }) {
    return new Promise(resolve => {
      const overlay = document.createElement("div");
      overlay.className = "app-modal";
      const cancel = cancelText
        ? `<button type="button" class="app-modal-cancel">${escapeHtml(cancelText)}</button>`
        : "";
      overlay.innerHTML =
        `<div class="app-modal-backdrop"></div>` +
        `<div class="app-modal-dialog" role="dialog" aria-modal="true">` +
          (title ? `<h3 class="app-modal-title">${escapeHtml(title)}</h3>` : "") +
          `<p class="app-modal-message">${escapeHtml(message)}</p>` +
          `<div class="app-modal-actions">` + cancel +
            `<button type="button" class="app-modal-confirm${danger ? " danger" : ""}">${escapeHtml(confirmText || "OK")}</button>` +
          `</div>` +
        `</div>`;
      document.body.appendChild(overlay);

      function close(result) {
        document.removeEventListener("keydown", onKey);
        overlay.remove();
        resolve(result);
      }
      function onKey(e) {
        if (e.key === "Escape") close(false);
        else if (e.key === "Enter") close(true);
      }
      overlay.querySelector(".app-modal-confirm").addEventListener("click", () => close(true));
      const cancelBtn = overlay.querySelector(".app-modal-cancel");
      if (cancelBtn) cancelBtn.addEventListener("click", () => close(false));
      overlay.querySelector(".app-modal-backdrop").addEventListener("click", () => close(false));
      document.addEventListener("keydown", onKey);
      overlay.querySelector(".app-modal-confirm").focus();
    });
  }

  function confirmDialog(message, opts = {}) {
    return showModal({
      message,
      title: opts.title,
      confirmText: opts.confirmText || "OK",
      cancelText: opts.cancelText || "Cancel",
      danger: opts.danger,
    });
  }

  function alertDialog(message, opts = {}) {
    return showModal({ message, title: opts.title, confirmText: opts.confirmText || "OK" });
  }

  function titleCase(s) {
    return String(s || "").replace(/\b\w/g, c => c.toUpperCase());
  }

  function formatDateTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) {
      return String(iso).slice(0, 19).replace("T", " ");
    }
    return d.toLocaleString();
  }

  function pluralize(count, noun) {
    return `${count} ${noun}${count === 1 ? "" : "s"}`;
  }

  function compactText(s, maxLen) {
    const clean = String(s || "").replace(/\s+/g, " ").trim();
    if (clean.length <= maxLen) return clean;
    return clean.slice(0, Math.max(0, maxLen - 3)).trimEnd() + "...";
  }

  function practiceSetName(set) {
    return String((set && set.name) || "").trim() || "Untitled practice set";
  }

  function practiceSetSeriesName(set) {
    return String((set && set.series_name) || "").trim();
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
    const inPracticeSet = activePracticeProblemIds.has(p.id);
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

    const practiceButton = CAN_UPLOAD && activePracticeSet
      ? `<button type="button" class="${inPracticeSet ? "practice-remove-btn" : "practice-add-btn"}" ` +
        `title="${inPracticeSet ? "Remove from active practice set" : "Add to active practice set"}" ` +
        `aria-label="${inPracticeSet ? "Remove from active practice set" : "Add to active practice set"}">` +
        `${inPracticeSet ? "Set -" : "Set +"}</button>`
      : "";
    const actionButtons = CAN_UPLOAD
      ? practiceButton +
        `<button type="button" class="refine-btn" title="${p.solution ? 'Refine solution with a hint' : 'Generate solution with a hint'}" aria-label="Refine solution">✨</button>` +
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
      renderTagsBlock(p) +
      `<div class="rendered">${escapeHtml(p.problem_text)}</div>`;

    if (p.figure_image) {
      const adjustable = CAN_UPLOAD && p.source_image ? " adjustable" : "";
      const titleAttr = adjustable ? ' title="Double-click to adjust crop"' : "";
      html += `<div class="diagram"><img class="figure-image${adjustable}" src="${URL_PREFIX}/figures/${encodeURIComponent(p.figure_image)}" alt="figure"${titleAttr}></div>`;
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
    if (inPracticeSet) div.classList.add("in-practice-set");
    return div;
  }

  function tagComment(name) {
    return tagCommentMap[name] || "";
  }

  function renderTagsBlock(p) {
    const tags = p.tags || [];
    if (!tags.length && !CAN_UPLOAD) return "";
    let html = `<div class="tags">`;
    tags.forEach(t => {
      const comment = tagComment(t);
      const titleAttr = comment ? ` data-title="${escapeHtml(comment)}"` : "";
      html += `<span class="tag-chip" data-tag="${escapeHtml(t)}"${titleAttr}>` +
        `<span class="tag-name">${escapeHtml(t)}</span>` +
        (CAN_UPLOAD ? `<button type="button" class="tag-remove" aria-label="Remove tag" title="Remove tag">×</button>` : "") +
        `</span>`;
    });
    if (CAN_UPLOAD) {
      html += `<button type="button" class="tag-add" title="Add a tag">+ tag</button>`;
    }
    html += `</div>`;
    return html;
  }

  function currentProblemTags(problemEl) {
    return Array.from(problemEl.querySelectorAll(".tags > .tag-chip[data-tag]"))
      .map(el => el.dataset.tag);
  }

  function recordKnownTag(name, comment) {
    if (!name) return;
    const existing = knownTags.find(t => t.name === name);
    if (existing) {
      if (comment) existing.comment = comment;
    } else {
      knownTags.push({ name, comment: comment || "", count: 0 });
    }
    if (comment || !(name in tagCommentMap)) tagCommentMap[name] = comment || "";
    refreshTagDatalist();
    tagMenuFilter.build();
  }

  async function saveProblemTags(problemEl, tags, statusEl) {
    const id = problemEl.dataset.id;
    let data;
    try {
      const resp = await fetch(`${URL_PREFIX}/api/problems/${encodeURIComponent(id)}/tags`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tags }),
      });
      if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try { const err = await resp.json(); if (err && err.error) msg = err.error; } catch (_) {}
        throw new Error(msg);
      }
      data = await resp.json();
    } catch (e) {
      if (statusEl) statusEl.textContent = `Failed: ${e.message}`;
      return false;
    }
    if (data.problem) {
      (data.problem.tags || []).forEach(t => recordKnownTag(t, tagComment(t)));
      syncActivePracticeSetProblem(data.problem);
      const fresh = renderProblem(data.problem);
      problemEl.replaceWith(fresh);
      renderMath(fresh);
    }
    return true;
  }

  function openTagAddForm(tagsEl) {
    if (tagsEl.querySelector(".tag-add-form")) return;
    const addBtn = tagsEl.querySelector(".tag-add");
    if (addBtn) addBtn.hidden = true;
    const form = document.createElement("span");
    form.className = "tag-add-form";
    form.innerHTML =
      `<input type="text" class="tag-add-input" list="all-tags" placeholder="tag name" autocomplete="off">` +
      `<input type="text" class="tag-add-comment" placeholder="describe this new tag (optional)" autocomplete="off" hidden>` +
      `<button type="button" class="tag-add-save">Add</button>` +
      `<button type="button" class="tag-add-cancel" aria-label="Cancel">×</button>` +
      `<span class="tag-add-status cat-editor-status"></span>`;
    tagsEl.appendChild(form);
    const input = form.querySelector(".tag-add-input");
    input.focus();
  }

  function closeTagAddForm(form) {
    const tagsEl = form.closest(".tags");
    form.remove();
    const addBtn = tagsEl && tagsEl.querySelector(".tag-add");
    if (addBtn) addBtn.hidden = false;
  }

  async function commitTagAddForm(form) {
    const problemEl = form.closest(".problem");
    if (!problemEl) return;
    const input = form.querySelector(".tag-add-input");
    const commentInput = form.querySelector(".tag-add-comment");
    const statusEl = form.querySelector(".tag-add-status");
    const name = (input.value || "").trim().toLowerCase().replace(/\s+/g, " ");
    if (!name) { closeTagAddForm(form); return; }
    const current = currentProblemTags(problemEl);
    if (current.indexOf(name) !== -1) { closeTagAddForm(form); return; }
    const comment = commentInput ? (commentInput.value || "").trim() : "";

    form.querySelectorAll("input,button").forEach(el => { el.disabled = true; });
    statusEl.textContent = "Adding…";

    // Register/update the tag (with its comment) before attaching it to the
    // problem. This persists a brand-new tag's description and lets an existing
    // tag's comment be edited from here. A blank comment leaves the stored one
    // untouched (see upsert_tag), so it never clobbers an existing description.
    try {
      const resp = await fetch(`${URL_PREFIX}/api/tags`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, comment }),
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data.tag) recordKnownTag(data.tag.name, data.tag.comment || "");
      }
    } catch (_) { /* non-fatal: the tag still gets auto-registered on save */ }
    recordKnownTag(name, comment);
    const ok = await saveProblemTags(problemEl, current.concat([name]), statusEl);
    if (!ok) {
      form.querySelectorAll("input,button").forEach(el => { el.disabled = false; });
    }
  }

  function refreshTagDatalist() {
    const dl = document.getElementById("all-tags");
    if (!dl) return;
    dl.innerHTML = "";
    knownTags.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.name;
      if (t.comment) opt.textContent = t.comment;
      dl.appendChild(opt);
    });
  }

  async function loadTags() {
    try {
      const resp = await fetch(`${URL_PREFIX}/api/tags`);
      const data = await resp.json();
      knownTags = data.tags || [];
    } catch (e) {
      knownTags = [];
    }
    tagCommentMap = {};
    knownTags.forEach(t => { tagCommentMap[t.name] = t.comment || ""; });
    refreshTagDatalist();
    tagMenuFilter.build();
  }

  // Fetch a single tag's registry entry ({name, comment, count}) or null —
  // cheaper than loadTags() when we only need to re-check one tag's usage.
  async function fetchTag(name) {
    try {
      const resp = await fetch(`${URL_PREFIX}/api/tags?name=${encodeURIComponent(name)}`);
      const data = await resp.json();
      return (data.tags || [])[0] || null;
    } catch {
      return null;
    }
  }

  // After a tag is removed from a problem, offer to delete the tag entirely if
  // it was the last problem using it. Deletion is orphan-only on the server, so
  // declining simply leaves the tag registered (an orphan, reusable later).
  async function offerOrphanTagDeletion(name) {
    const tag = await fetchTag(name);  // re-check just this tag's usage count
    if (!tag || tag.count > 0) return;  // unknown or still in use → nothing to offer
    const ok = await confirmDialog(
      `"${name}" is no longer used by any problem. Delete the tag completely? Choosing Keep leaves it available for later use.`,
      { title: "Delete unused tag?", confirmText: "Delete", cancelText: "Keep", danger: true }
    );
    if (!ok) return;
    try {
      const resp = await fetch(`${URL_PREFIX}/api/tags/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
    } catch (e) {
      alertDialog(`Could not delete tag: ${e.message}`, { title: "Delete failed" });
      return;
    }
    if (tagMenuFilter.has(name)) tagMenuFilter.remove(name);
    await loadTags();
  }

  // A reusable OR filter: a dropdown menu feeding a removable chip list. The
  // menu shell (trigger, panel, chips, open/close, outside-click, params) is
  // shared; `opts.populate(panelEl, makeLeaf)` builds the panel contents, so the
  // same widget serves both the two-level pickers (category→subcategory,
  // exam→subexam) and the flat one (year). Each selection is { value, label }:
  // `value` is the query-param value, `label` the chip text. Returns
  // { build, isActive, appendParams }.
  function createMenuFilter(opts) {
    const menuEl = document.getElementById(opts.menuId);
    const triggerEl = document.getElementById(opts.triggerId);
    const panelEl = document.getElementById(opts.panelId);
    const chipsEl = document.getElementById(opts.chipsId);
    const selected = [];  // [{ value, label }], deduped by value

    function renderChips() {
      if (!chipsEl) return;
      chipsEl.innerHTML = "";
      selected.forEach(p => {
        const chip = document.createElement("span");
        chip.className = "filter-chip";
        chip.dataset.value = p.value;
        if (p.title) chip.title = p.title;
        chip.innerHTML = `<span>${escapeHtml(p.label)}</span>` +
          `<button type="button" class="filter-chip-remove" aria-label="Remove filter">×</button>`;
        chipsEl.appendChild(chip);
      });
    }

    function add(value, label, title) {
      if (!value || selected.some(p => p.value === value)) return;
      selected.push({ value, label, title: title || "" });
      renderChips();
      onFilterChange();
    }

    function remove(value) {
      const i = selected.findIndex(p => p.value === value);
      if (i === -1) return;
      selected.splice(i, 1);
      renderChips();
      onFilterChange();
    }

    // Build a clickable leaf carrying its param value + chip label. `menuText`
    // is what shows inside the menu (e.g. "All", "Function", "2023"); `chipLabel`
    // is the chip text once selected (e.g. "Algebra — Function").
    function makeLeaf(value, menuText, chipLabel, extraClass, title) {
      const leaf = document.createElement("div");
      leaf.className = "pair-menu-sub" + (extraClass ? " " + extraClass : "");
      leaf.setAttribute("role", "menuitem");
      leaf.setAttribute("tabindex", "0");
      leaf.dataset.value = value;
      leaf.dataset.label = chipLabel;
      if (title) leaf.dataset.title = title;  // shown via the CSS hover balloon
      leaf.textContent = menuText;
      return leaf;
    }

    function build() {
      if (!panelEl) return;
      panelEl.innerHTML = "";
      opts.populate(panelEl, makeLeaf);
      if (!panelEl.children.length) {
        const empty = document.createElement("div");
        empty.className = "pair-menu-item";
        empty.textContent = "Nothing to filter yet";
        panelEl.appendChild(empty);
      }
    }

    function open() {
      if (!panelEl || !triggerEl) return;
      panelEl.hidden = false;
      triggerEl.setAttribute("aria-expanded", "true");
    }
    function close() {
      if (!panelEl || !triggerEl) return;
      panelEl.hidden = true;
      triggerEl.setAttribute("aria-expanded", "false");
    }
    function commit(leaf) {
      add(leaf.dataset.value, leaf.dataset.label, leaf.dataset.title);
      close();
    }

    if (triggerEl) {
      triggerEl.addEventListener("click", (e) => {
        e.stopPropagation();
        if (panelEl.hidden) open(); else close();
      });
    }
    if (panelEl) {
      panelEl.addEventListener("click", (e) => {
        const leaf = e.target.closest(".pair-menu-sub");
        if (leaf) commit(leaf);
      });
      panelEl.addEventListener("keydown", (e) => {
        const leaf = e.target.closest(".pair-menu-sub");
        if (leaf && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); commit(leaf); }
      });
    }
    if (chipsEl) {
      chipsEl.addEventListener("click", (e) => {
        const rm = e.target.closest(".filter-chip-remove");
        if (!rm) return;
        const chip = rm.closest(".filter-chip");
        if (chip) remove(chip.dataset.value);
      });
    }
    document.addEventListener("click", (e) => {
      if (menuEl && !menuEl.contains(e.target)) close();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && panelEl && !panelEl.hidden) close();
    });

    return {
      build,
      isActive: () => selected.length > 0,
      has: (value) => selected.some(p => p.value === value),
      remove,
      appendParams: (params) => {
        selected.forEach(p => params.append(opts.paramName, p.value));
      },
    };
  }

  // Panel builder for a two-level picker: each group is a row with a hover
  // flyout listing "All" plus its items. encode(group, item) → param value.
  function pairPopulate({ getGroups, getItems, formatGroup, formatItem }) {
    const encode = (group, item) => (item ? `${group}:${item}` : group);
    return (panelEl, makeLeaf) => {
      getGroups().forEach(group => {
        const row = document.createElement("div");
        row.className = "pair-menu-item";
        row.setAttribute("tabindex", "0");
        row.innerHTML = `<span>${escapeHtml(formatGroup(group))}</span>` +
          `<span class="pair-menu-caret" aria-hidden="true">▸</span>`;
        const submenu = document.createElement("div");
        submenu.className = "pair-submenu";
        submenu.setAttribute("role", "menu");
        submenu.appendChild(
          makeLeaf(encode(group, ""), "All", `${formatGroup(group)} — All`, "pair-menu-all"));
        getItems(group).forEach(item => {
          submenu.appendChild(
            makeLeaf(encode(group, item), formatItem(item),
              `${formatGroup(group)} — ${formatItem(item)}`));
        });
        row.appendChild(submenu);
        panelEl.appendChild(row);
      });
    };
  }

  // Panel builder for a flat picker: each value is a directly clickable leaf.
  // Optional `titleFor(value)` supplies a hover tooltip (e.g. a tag's comment),
  // carried onto the chip once selected.
  function listPopulate({ getValues, formatValue, titleFor }) {
    return (panelEl, makeLeaf) => {
      getValues().forEach(v => {
        const label = formatValue(v);
        panelEl.appendChild(makeLeaf(v, label, label, null, titleFor ? titleFor(v) : ""));
      });
    };
  }

  const catFilter = createMenuFilter({
    menuId: "cat-menu", triggerId: "cat-menu-trigger", panelId: "cat-menu-panel",
    chipsId: "cat-filter-chips",
    paramName: "cat_subcat",
    populate: pairPopulate({
      getGroups: () => knownCategories.slice().sort(),
      getItems: (g) => (subcategoryMap[g] || []).slice().sort(),
      formatGroup: titleCase,
      formatItem: titleCase,
    }),
  });

  const examFilter = createMenuFilter({
    menuId: "exam-menu", triggerId: "exam-menu-trigger", panelId: "exam-menu-panel",
    chipsId: "exam-filter-chips",
    paramName: "exam_subexam",
    populate: pairPopulate({
      getGroups: () => examList.slice(),  // exams are stored case-sensitively
      getItems: (g) => (subexamMap[g] || []).slice().sort(),
      formatGroup: (s) => s,
      formatItem: titleCase,
    }),
  });

  const yearFilter = createMenuFilter({
    menuId: "year-menu", triggerId: "year-menu-trigger", panelId: "year-menu-panel",
    chipsId: "year-filter-chips",
    paramName: "year",
    populate: listPopulate({
      getValues: () => yearList.slice(),
      formatValue: (s) => s,
    }),
  });

  // Figure is a fixed two-value picker; selecting both is the same as no filter.
  const figureFilter = createMenuFilter({
    menuId: "figure-menu", triggerId: "figure-menu-trigger", panelId: "figure-menu-panel",
    chipsId: "figure-filter-chips",
    paramName: "has_figure",
    populate: listPopulate({
      getValues: () => ["1", "0"],
      formatValue: (v) => (v === "1" ? "With figure" : "Without figure"),
    }),
  });

  const tagMenuFilter = createMenuFilter({
    menuId: "tag-menu", triggerId: "tag-menu-trigger", panelId: "tag-menu-panel",
    chipsId: "tag-filter-chips",
    paramName: "tags",
    populate: listPopulate({
      getValues: () => knownTags.map(t => t.name),
      formatValue: (v) => v,
      titleFor: (v) => tagComment(v),
    }),
  });

  function rangeActive() {
    if (!minInput || !maxInput) return false;
    const lo = parseFloat(minInput.value);
    const hi = parseFloat(maxInput.value);
    return lo > 0 || hi < sliderMax;
  }

  function currentFilterParams() {
    const params = new URLSearchParams();
    catFilter.appendParams(params);
    examFilter.appendParams(params);
    yearFilter.appendParams(params);
    figureFilter.appendParams(params);
    tagMenuFilter.appendParams(params);
    if (minInput) params.set("min_time", minInput.value);
    if (maxInput) params.set("max_time", maxInput.value);
    params.set("range_max", String(sliderMax));
    return params;
  }

  function practiceSetSummary(detail) {
    return detail
      ? {
          id: detail.id,
          name: detail.name || "",
          series_name: detail.series_name || "",
          requested_count: detail.requested_count || 0,
          problem_count: detail.problem_count || 0,
          created_at: detail.created_at || "",
          updated_at: detail.updated_at || "",
        }
      : null;
  }

  function practiceSetLabel(set) {
    const stamp = formatDateTime(set.updated_at || set.created_at);
    const series = practiceSetSeriesName(set);
    const seriesPart = series ? ` · series ${series}` : "";
    return `${practiceSetName(set)}${seriesPart} · ${pluralize(set.problem_count || 0, "problem")} · ${stamp}`;
  }

  function populatePracticeSeriesOptions() {
    if (!practiceSeriesNames) return;
    const names = [];
    practiceSets.forEach(set => {
      const name = practiceSetSeriesName(set);
      if (name && !names.includes(name)) names.push(name);
    });
    practiceSeriesNames.innerHTML = "";
    names.sort((a, b) => a.localeCompare(b));
    names.forEach(name => {
      const opt = document.createElement("option");
      opt.value = name;
      practiceSeriesNames.appendChild(opt);
    });
  }

  function populatePracticeSetOptions() {
    if (!practiceSetSelect) return;
    const selectedId = activePracticeSet ? activePracticeSet.id : "";
    practiceSetSelect.innerHTML = `<option value="">Select a practice set...</option>`;
    practiceSets.forEach(set => {
      const opt = document.createElement("option");
      opt.value = set.id;
      opt.textContent = practiceSetLabel(set);
      if (set.id === selectedId) opt.selected = true;
      practiceSetSelect.appendChild(opt);
    });
    practiceSetSelect.insertAdjacentHTML(
      "beforeend",
      `<option value="${PRACTICE_CREATE_SENTINEL}">Create a new set...</option>`
    );
    populatePracticeSeriesOptions();
  }

  function upsertPracticeSetSummary(detail) {
    const summary = practiceSetSummary(detail);
    if (!summary) return;
    const idx = practiceSets.findIndex(set => set.id === summary.id);
    if (idx >= 0) {
      practiceSets[idx] = summary;
    } else {
      practiceSets.push(summary);
    }
    practiceSets.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    populatePracticeSetOptions();
  }

  function removePracticeSetSummary(practiceSetId) {
    practiceSets = practiceSets.filter(set => set.id !== practiceSetId);
    populatePracticeSetOptions();
  }

  function renderPracticeSetPanel() {
    if (!practiceSetPanel || !practiceSetList || !practiceSetTitle || !practiceSetMeta) return;
    if (!activePracticeSet) {
      practiceSetPanel.hidden = true;
      practiceSetList.innerHTML = "";
      if (practicePrintBtn) practicePrintBtn.disabled = true;
      if (practiceDeleteBtn) practiceDeleteBtn.disabled = true;
      if (printHeaderTitle) printHeaderTitle.textContent = "Math Practice Set";
      return;
    }

    practiceSetPanel.hidden = false;
    if (practicePrintBtn) {
      practicePrintBtn.disabled = !(activePracticeSet.problems || []).length;
    }
    if (practiceDeleteBtn) practiceDeleteBtn.disabled = false;
    practiceSetTitle.textContent = practiceSetName(activePracticeSet);
    practiceSetMeta.textContent =
      `${practiceSetSeriesName(activePracticeSet) ? `series ${practiceSetSeriesName(activePracticeSet)} · ` : ""}` +
      `${pluralize(activePracticeSet.problem_count || 0, "problem")} · ` +
      `created ${formatDateTime(activePracticeSet.created_at)}`;
    if (printHeaderTitle) {
      const series = practiceSetSeriesName(activePracticeSet);
      const label = series
        ? `${series}: ${practiceSetName(activePracticeSet)}`
        : practiceSetName(activePracticeSet);
      printHeaderTitle.textContent = `${label} (${activePracticeSet.problem_count || 0})`;
    }

    if (!(activePracticeSet.problems || []).length) {
      practiceSetList.innerHTML = `<p><em>This practice set is empty. Add problems from the list below.</em></p>`;
      return;
    }

    practiceSetList.innerHTML = "";
    const frag = document.createDocumentFragment();
    activePracticeSet.problems.forEach((problem, idx) => {
      const row = document.createElement("div");
      row.className = "practice-set-item";
      row.dataset.problemId = problem.id;
      const metaBits = [];
      if (problem.category) metaBits.push(titleCase(problem.category));
      if (problem.subcategory) metaBits.push(titleCase(problem.subcategory));
      const sourceBits = [problem.year, problem.source_exam, problem.subexam]
        .filter(v => v && v !== "Unknown");
      if (sourceBits.length) metaBits.push(sourceBits.join(" · "));
      row.innerHTML =
        `<div class="practice-set-item-main">` +
          `<div class="practice-set-item-heading">${idx + 1}. ${escapeHtml(metaBits.join(" - ")) || "Problem"}</div>` +
          `<div class="practice-set-item-text">${escapeHtml(compactText(problem.problem_text, 180))}</div>` +
        `</div>` +
        `<button type="button" class="practice-set-remove" aria-label="Remove problem from practice set">Remove</button>`;
      frag.appendChild(row);
    });
    practiceSetList.appendChild(frag);
    renderMath(practiceSetList, PREVIEW_KATEX_OPTS);
  }

  function setActivePracticeSet(detail) {
    activePracticeSet = detail;
    activePracticeProblemIds = new Set((detail && detail.problem_ids) || []);
    populatePracticeSetOptions();
    renderPracticeSetPanel();
  }

  function clearActivePracticeSet() {
    setActivePracticeSet(null);
    if (practiceSetSelect) practiceSetSelect.value = "";
  }

  function restorePracticeSetSelection() {
    if (!practiceSetSelect) return;
    practiceSetSelect.value = activePracticeSet ? activePracticeSet.id : "";
  }

  function syncActivePracticeSetProblem(problem) {
    if (!activePracticeSet || !problem || !activePracticeProblemIds.has(problem.id)) return;
    const nextProblems = (activePracticeSet.problems || []).map(p => p.id === problem.id ? problem : p);
    activePracticeSet = { ...activePracticeSet, problems: nextProblems };
    renderPracticeSetPanel();
  }

  async function fetchPracticeSet(practiceSetId) {
    const resp = await fetch(`${URL_PREFIX}/api/practice_sets/${encodeURIComponent(practiceSetId)}`);
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try {
        const err = await resp.json();
        if (err && err.error) msg = err.error;
      } catch (_) {}
      throw new Error(msg);
    }
    const data = await resp.json();
    return data.practice_set;
  }

  async function loadPracticeSets() {
    if (!CAN_UPLOAD || !practiceSetSelect) return;
    const resp = await fetch(`${URL_PREFIX}/api/practice_sets`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    practiceSets = data.practice_sets || [];
    populatePracticeSetOptions();
  }

  async function selectPracticeSet(practiceSetId, opts = {}) {
    const reloadPage = opts.reloadPage !== false;
    if (!practiceSetId) {
      clearActivePracticeSet();
      if (reloadPage) await loadPage();
      return;
    }
    const detail = await fetchPracticeSet(practiceSetId);
    upsertPracticeSetSummary(detail);
    setActivePracticeSet(detail);
    if (reloadPage) await loadPage();
  }

  async function refreshActivePracticeSet(opts = {}) {
    if (!activePracticeSet || !activePracticeSet.id) return;
    await selectPracticeSet(activePracticeSet.id, opts);
  }

  async function mutatePracticeSet(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try {
        const err = await resp.json();
        if (err && err.error) msg = err.error;
      } catch (_) {}
      throw new Error(msg);
    }
    return resp.status === 204 ? {} : await resp.json();
  }

  async function createPracticeSetFromFilters(name, count, seriesName) {
    let n = parseInt(count, 10);
    if (isNaN(n) || n < 1) n = 1;
    if (practiceStatus) practiceStatus.textContent = "Creating practice set...";
    try {
      const params = currentFilterParams();
      const data = await mutatePracticeSet(
        `${URL_PREFIX}/api/practice_sets?${params.toString()}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ n, name, series_name: seriesName }),
        }
      );
      upsertPracticeSetSummary(data.practice_set);
      setActivePracticeSet(data.practice_set);
      await loadPage();
      if (practiceStatus) {
        practiceStatus.textContent = `Created ${pluralize(data.practice_set.problem_count || 0, "problem")}.`;
      }
    } catch (e) {
      if (practiceStatus) practiceStatus.textContent = `Create failed: ${e.message}`;
      throw e;
    }
  }

  function closePracticeCreateModal(force) {
    if (!practiceCreateModal) return;
    if (!force && practiceCreateSubmit && practiceCreateSubmit.disabled) return;
    practiceCreateModal.hidden = true;
    if (practiceCreateError) practiceCreateError.textContent = "";
    restorePracticeSetSelection();
  }

  function openPracticeCreateModal() {
    if (!practiceCreateModal) return;
    practiceCreateModal.hidden = false;
    restorePracticeSetSelection();
    if (practiceCreateError) practiceCreateError.textContent = "";
    if (practiceCreateSeriesInput) {
      practiceCreateSeriesInput.value = activePracticeSet ? practiceSetSeriesName(activePracticeSet) : "";
    }
    if (practiceCreateNameInput) practiceCreateNameInput.value = "";
    if (practiceCreateCountInput && !practiceCreateCountInput.value) {
      practiceCreateCountInput.value = "5";
    }
    if (practiceCreateSeriesInput && !practiceCreateSeriesInput.value) {
      practiceCreateSeriesInput.focus();
    } else if (practiceCreateNameInput) {
      practiceCreateNameInput.focus();
    }
  }

  async function submitPracticeCreateModal() {
    if (!practiceCreateNameInput || !practiceCreateCountInput || !practiceCreateSubmit) return;
    const seriesName = practiceCreateSeriesInput
      ? (practiceCreateSeriesInput.value || "").trim().replace(/\s+/g, " ")
      : "";
    const name = (practiceCreateNameInput.value || "").trim().replace(/\s+/g, " ");
    let count = parseInt(practiceCreateCountInput.value, 10);
    if (!name) {
      if (practiceCreateError) practiceCreateError.textContent = "Set name is required.";
      practiceCreateNameInput.focus();
      return;
    }
    if (isNaN(count) || count < 1) {
      count = 1;
      practiceCreateCountInput.value = "1";
    }

    practiceCreateSubmit.disabled = true;
    if (practiceCreateCancel) practiceCreateCancel.disabled = true;
    if (practiceCreateSeriesInput) practiceCreateSeriesInput.disabled = true;
    practiceCreateNameInput.disabled = true;
    practiceCreateCountInput.disabled = true;
    if (practiceCreateError) practiceCreateError.textContent = "";
    try {
      await createPracticeSetFromFilters(name, count, seriesName);
      closePracticeCreateModal(true);
    } catch (e) {
      if (practiceCreateError) practiceCreateError.textContent = e.message;
    } finally {
      practiceCreateSubmit.disabled = false;
      if (practiceCreateCancel) practiceCreateCancel.disabled = false;
      if (practiceCreateSeriesInput) practiceCreateSeriesInput.disabled = false;
      practiceCreateNameInput.disabled = false;
      practiceCreateCountInput.disabled = false;
    }
  }

  async function updatePracticeSetMembership(problemId, shouldAdd) {
    if (!activePracticeSet) {
      await alertDialog("Create or select a practice set first.", { title: "No active practice set" });
      return;
    }
    if (practiceStatus) {
      practiceStatus.textContent = shouldAdd ? "Adding problem..." : "Removing problem...";
    }
    try {
      const data = shouldAdd
        ? await mutatePracticeSet(
            `${URL_PREFIX}/api/practice_sets/${encodeURIComponent(activePracticeSet.id)}/problems`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ problem_id: problemId }),
            }
          )
        : await mutatePracticeSet(
            `${URL_PREFIX}/api/practice_sets/${encodeURIComponent(activePracticeSet.id)}/problems/${encodeURIComponent(problemId)}`,
            { method: "DELETE" }
          );
      upsertPracticeSetSummary(data.practice_set);
      setActivePracticeSet(data.practice_set);
      await loadPage();
      if (practiceStatus) {
        practiceStatus.textContent = shouldAdd ? "Problem added." : "Problem removed.";
      }
    } catch (e) {
      if (practiceStatus) {
        practiceStatus.textContent = `${shouldAdd ? "Add" : "Remove"} failed: ${e.message}`;
      }
    }
  }

  async function deleteActivePracticeSet() {
    if (!activePracticeSet) return;
    const ok = await confirmDialog("Delete this practice set? Its saved selection will be lost.", {
      title: "Delete practice set?",
      confirmText: "Delete",
      cancelText: "Cancel",
      danger: true,
    });
    if (!ok) return;
    if (practiceDeleteBtn) practiceDeleteBtn.disabled = true;
    try {
      await mutatePracticeSet(
        `${URL_PREFIX}/api/practice_sets/${encodeURIComponent(activePracticeSet.id)}`,
        { method: "DELETE" }
      );
      const deletedId = activePracticeSet.id;
      clearActivePracticeSet();
      removePracticeSetSummary(deletedId);
      await loadPage();
      if (practiceStatus) practiceStatus.textContent = "Practice set deleted.";
    } catch (e) {
      if (practiceStatus) practiceStatus.textContent = `Delete failed: ${e.message}`;
      if (practiceDeleteBtn) practiceDeleteBtn.disabled = false;
    }
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
      catFilter.isActive() ||
      examFilter.isActive() ||
      yearFilter.isActive() ||
      figureFilter.isActive() ||
      tagMenuFilter.isActive() ||
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
    const ok = await confirmDialog("Delete this problem? This cannot be undone.", {
      title: "Delete problem?", confirmText: "Delete", cancelText: "Cancel", danger: true,
    });
    if (!ok) return;
    const btn = problemEl.querySelector(".delete-btn");
    if (btn) btn.disabled = true;
    try {
      const resp = await fetch(`${URL_PREFIX}/api/problems/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (e) {
      if (btn) btn.disabled = false;
      alertDialog(`Delete failed: ${e.message}`, { title: "Delete failed" });
      return;
    }
    if (activePracticeSet && activePracticeProblemIds.has(id)) {
      try {
        await refreshActivePracticeSet({ reloadPage: false });
      } catch (_) {
        clearActivePracticeSet();
      }
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
    const practiceAddBtn = ev.target.closest(".practice-add-btn");
    if (practiceAddBtn) {
      const problemEl = practiceAddBtn.closest(".problem");
      if (problemEl && problemEl.dataset.id) {
        updatePracticeSetMembership(problemEl.dataset.id, true);
      }
      return;
    }
    const practiceRemoveBtn = ev.target.closest(".practice-remove-btn");
    if (practiceRemoveBtn) {
      const problemEl = practiceRemoveBtn.closest(".problem");
      if (problemEl && problemEl.dataset.id) {
        updatePracticeSetMembership(problemEl.dataset.id, false);
      }
      return;
    }
    if (!CAN_UPLOAD) return;
    const tagRemoveBtn = ev.target.closest(".tag-remove");
    if (tagRemoveBtn) {
      const problemEl = tagRemoveBtn.closest(".problem");
      const chip = tagRemoveBtn.closest(".tag-chip");
      if (problemEl && chip) {
        const removed = chip.dataset.tag;
        const remaining = currentProblemTags(problemEl).filter(t => t !== removed);
        saveProblemTags(problemEl, remaining, null).then(ok => {
          if (ok) offerOrphanTagDeletion(removed);
        });
      }
      return;
    }
    const tagAddBtn = ev.target.closest(".tag-add");
    if (tagAddBtn) {
      openTagAddForm(tagAddBtn.closest(".tags"));
      return;
    }
    const tagAddSave = ev.target.closest(".tag-add-save");
    if (tagAddSave) {
      commitTagAddForm(tagAddSave.closest(".tag-add-form"));
      return;
    }
    const tagAddCancel = ev.target.closest(".tag-add-cancel");
    if (tagAddCancel) {
      closeTagAddForm(tagAddCancel.closest(".tag-add-form"));
      return;
    }
  });

  // Reveal the optional comment field for any tag. For an existing tag, prefill
  // its current comment so it can be edited; for a brand-new tag, start blank.
  listEl.addEventListener("input", function (ev) {
    const input = ev.target.closest(".tag-add-input");
    if (!input) return;
    const form = input.closest(".tag-add-form");
    const commentInput = form && form.querySelector(".tag-add-comment");
    if (!commentInput) return;
    const name = (input.value || "").trim().toLowerCase().replace(/\s+/g, " ");
    commentInput.hidden = !name;
    if (!name) { commentInput.dataset.forTag = ""; return; }
    // Only sync the comment box when the resolved tag changes, so typing in the
    // tag-name field doesn't clobber an edit the user already made to the comment.
    if (commentInput.dataset.forTag !== name) {
      commentInput.dataset.forTag = name;
      const existing = knownTags.find(t => t.name === name);
      commentInput.value = existing ? (existing.comment || "") : "";
      commentInput.placeholder = existing
        ? "edit comment (optional)"
        : "describe this new tag (optional)";
    }
  });

  listEl.addEventListener("keydown", function (ev) {
    const form = ev.target.closest(".tag-add-form");
    if (!form) return;
    if (ev.key === "Enter") { ev.preventDefault(); commitTagAddForm(form); }
    else if (ev.key === "Escape") { ev.preventDefault(); closeTagAddForm(form); }
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
          syncActivePracticeSetProblem(data.problem);
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
          catFilter.build();
          syncActivePracticeSetProblem(data.problem);
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
      syncActivePracticeSetProblem(data.problem);
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
      if (practiceBar) practiceBar.hidden = true;
      clearActivePracticeSet();
      return;
    }

    if (summary.total === 0) {
      filtersEl.hidden = true;
      if (practiceBar) practiceBar.hidden = true;
      clearActivePracticeSet();
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

    examList = (summary.exams || []).slice();
    subexamMap = summary.subexams || {};
    examFilter.build();
    yearList = (summary.years || []).slice();
    yearFilter.build();
    knownCategories = (summary.categories || []).slice();
    subcategoryMap = summary.subcategories || {};
    const subSet = new Set();
    Object.values(subcategoryMap).forEach(list => list.forEach(s => subSet.add(s)));
    knownSubcategories = Array.from(subSet).sort();
    refreshCategoryDatalist();
    refreshSubcategoryDatalist();
    catFilter.build();
    figureFilter.build();

    filtersEl.hidden = false;
    if (practiceBar) practiceBar.hidden = false;
    syncSlider();
  }

  if (filtersToggle) {
    filtersToggle.addEventListener("click", () => {
      const collapsed = filtersEl.classList.toggle("collapsed");
      filtersToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    });
  }

  // The category, exam, year, figure, and tag menus (trigger, flyouts, chips,
  // outside-click close) are fully wired inside createMenuFilter — see
  // catFilter / examFilter / yearFilter / figureFilter / tagMenuFilter.
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

  // Practice sets are persisted on the backend. We render the active one into
  // the hidden print container and let the browser handle print-to-PDF.
  const printContainer = document.getElementById("print-container");

  function clearPrintSelection() {
    document.body.classList.remove("printing");
    printContainer.innerHTML = "";
  }

  function waitForImages(root, timeoutMs = 5000) {
    const images = Array.from((root || document).querySelectorAll("img"));
    return Promise.all(images.map(img => new Promise(resolve => {
      if (!img.currentSrc && !img.src) {
        resolve();
        return;
      }
      if (img.complete) {
        resolve();
        return;
      }
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        img.removeEventListener("load", done);
        img.removeEventListener("error", done);
        resolve();
      };
      const timer = setTimeout(done, timeoutMs);
      img.addEventListener("load", done, { once: true });
      img.addEventListener("error", done, { once: true });
    })));
  }

  function nextFrame() {
    return new Promise(resolve => requestAnimationFrame(() => resolve()));
  }

  if (practiceSetSelect) {
    practiceSetSelect.addEventListener("change", async function () {
      if (practiceStatus) practiceStatus.textContent = "";
      if (practiceSetSelect.value === PRACTICE_CREATE_SENTINEL) {
        openPracticeCreateModal();
        return;
      }
      try {
        await selectPracticeSet(practiceSetSelect.value);
      } catch (e) {
        if (practiceStatus) practiceStatus.textContent = `Load failed: ${e.message}`;
      }
    });
  }

  if (practiceCreateCancel) {
    practiceCreateCancel.addEventListener("click", closePracticeCreateModal);
  }

  if (practiceCreateSubmit) {
    practiceCreateSubmit.addEventListener("click", submitPracticeCreateModal);
  }

  if (practiceCreateModal) {
    const backdrop = practiceCreateModal.querySelector(".app-modal-backdrop");
    if (backdrop) backdrop.addEventListener("click", closePracticeCreateModal);
    practiceCreateModal.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        closePracticeCreateModal();
      } else if (ev.key === "Enter" && ev.target.tagName !== "TEXTAREA") {
        ev.preventDefault();
        submitPracticeCreateModal();
      }
    });
  }

  if (practiceSetList) {
    practiceSetList.addEventListener("click", function (ev) {
      const removeBtn = ev.target.closest(".practice-set-remove");
      const item = removeBtn && removeBtn.closest(".practice-set-item");
      if (removeBtn && item && item.dataset.problemId) {
        updatePracticeSetMembership(item.dataset.problemId, false);
      }
    });
  }

  if (practiceDeleteBtn) {
    practiceDeleteBtn.addEventListener("click", deleteActivePracticeSet);
  }

  if (practicePrintBtn) {
    practicePrintBtn.addEventListener("click", async function () {
      if (!activePracticeSet || !activePracticeSet.problems || !activePracticeSet.problems.length) {
        if (practiceStatus) practiceStatus.textContent = "No problems in the active practice set.";
        return;
      }
      practicePrintBtn.disabled = true;
      try {
        if (practiceStatus) practiceStatus.textContent = "Preparing practice set for print...";
        const freshPracticeSet = activePracticeSet.id
          ? await fetchPracticeSet(activePracticeSet.id)
          : activePracticeSet;
        upsertPracticeSetSummary(freshPracticeSet);
        setActivePracticeSet(freshPracticeSet);
        practicePrintBtn.disabled = true;

        const problems = freshPracticeSet.problems || [];
        if (!problems.length) {
          if (practiceStatus) practiceStatus.textContent = "No problems in the active practice set.";
          practicePrintBtn.disabled = false;
          return;
        }

        printContainer.innerHTML = "";
        problems.forEach(p => {
          const el = renderProblem(p);
          el.classList.add("print-selected");
          printContainer.appendChild(el);
        });
        renderMath(printContainer);
        await waitForImages(printContainer);
        await nextFrame();
        await nextFrame();

        if (practiceStatus) {
          practiceStatus.textContent = `Printing ${pluralize(problems.length, "problem")}...`;
        }
        document.body.classList.add("printing");
        const cleanup = () => {
          clearPrintSelection();
          if (practiceStatus) practiceStatus.textContent = "";
          practicePrintBtn.disabled = !activePracticeSet || !(activePracticeSet.problems || []).length;
          window.removeEventListener("afterprint", cleanup);
        };
        window.addEventListener("afterprint", cleanup);
        setTimeout(() => window.print(), 0);
      } catch (e) {
        clearPrintSelection();
        if (practiceStatus) practiceStatus.textContent = `Print failed: ${e.message}`;
        practicePrintBtn.disabled = !activePracticeSet || !(activePracticeSet.problems || []).length;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", async function () {
    await Promise.all([loadSummary(), loadTags()]);
    if (CAN_UPLOAD) {
      try {
        await loadPracticeSets();
      } catch (e) {
        if (practiceStatus) practiceStatus.textContent = `Practice sets unavailable: ${e.message}`;
      }
    }
    await loadPage();
  });
})();
