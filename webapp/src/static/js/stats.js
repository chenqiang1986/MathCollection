(function () {
  const catChartEl = document.getElementById("chart-categories");
  const subChartEl = document.getElementById("chart-subcategories");
  const subTitleEl = document.getElementById("subcategory-title");
  const diffChartEl = document.getElementById("chart-difficulty");
  const diffTitleEl = document.getElementById("difficulty-title");

  let selectedCategory = null;
  let selectedSubcategory = null;
  let categoryItems = [];
  let subcategoryItems = [];

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function titleCase(s) {
    return String(s || "").replace(/\b\w/g, c => c.toUpperCase());
  }

  function renderBars(container, items, options) {
    options = options || {};
    container.innerHTML = "";
    if (!items.length) {
      container.innerHTML = `<p class="chart-empty"><em>No data.</em></p>`;
      return;
    }
    const max = Math.max(1, ...items.map(it => it.value));
    const frag = document.createDocumentFragment();
    items.forEach(it => {
      const row = document.createElement("div");
      row.className = "bar-row";
      if (options.clickable) row.classList.add("clickable");
      if (options.activeKey != null && it.key === options.activeKey) {
        row.classList.add("active");
      }
      row.dataset.key = it.key;
      const pct = (it.value / max) * 100;
      row.innerHTML =
        `<div class="bar-label">${escapeHtml(it.label)}</div>` +
        `<div class="bar-track">` +
          `<div class="bar-fill" style="width:${pct.toFixed(2)}%"></div>` +
          `<span class="bar-value">${it.value}</span>` +
        `</div>`;
      if (options.clickable) {
        row.tabIndex = 0;
        row.setAttribute("role", "button");
        row.addEventListener("click", () => options.onClick(it));
        row.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            options.onClick(it);
          }
        });
      }
      frag.appendChild(row);
    });
    container.appendChild(frag);
  }

  function onCategoryClick(it) {
    if (selectedCategory === it.key) {
      selectedCategory = null;
      selectedSubcategory = null;
    } else {
      selectedCategory = it.key;
      selectedSubcategory = null;
    }
    renderCategoryChart();
    loadSubcategories(selectedCategory);
    loadDifficulty(selectedCategory, null);
  }

  function onSubcategoryClick(it) {
    selectedSubcategory = (selectedSubcategory === it.key) ? null : it.key;
    renderSubcategoryChart();
    loadDifficulty(selectedCategory, selectedSubcategory);
  }

  function renderCategoryChart() {
    renderBars(catChartEl, categoryItems, {
      clickable: true,
      activeKey: selectedCategory,
      onClick: onCategoryClick,
    });
  }

  function renderSubcategoryChart() {
    if (subTitleEl) {
      subTitleEl.textContent = selectedCategory
        ? `Subcategories — ${titleCase(selectedCategory)}`
        : "Subcategories (all categories)";
    }
    renderBars(subChartEl, subcategoryItems, {
      clickable: true,
      activeKey: selectedSubcategory,
      onClick: onSubcategoryClick,
    });
  }

  async function loadCategories() {
    catChartEl.innerHTML = `<p class="chart-empty"><em>Loading…</em></p>`;
    let data;
    try {
      const resp = await fetch("/api/stats/categories");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
    } catch (e) {
      catChartEl.innerHTML = `<p class="chart-empty"><em>Failed to load: ${escapeHtml(e.message)}</em></p>`;
      return;
    }
    categoryItems = (data.categories || []).map(c => ({
      key: c.category,
      label: titleCase(c.category) || "(uncategorized)",
      value: c.count,
    }));
    renderCategoryChart();
  }

  async function loadSubcategories(category) {
    if (!subChartEl) return;
    subChartEl.innerHTML = `<p class="chart-empty"><em>Loading…</em></p>`;
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    let data;
    try {
      const resp = await fetch(`/api/stats/subcategories?${params.toString()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
    } catch (e) {
      subChartEl.innerHTML = `<p class="chart-empty"><em>Failed to load: ${escapeHtml(e.message)}</em></p>`;
      return;
    }
    subcategoryItems = (data.subcategories || []).map(s => {
      const label = s.subcategory
        ? (category ? titleCase(s.subcategory) : `${titleCase(s.category)} / ${titleCase(s.subcategory)}`)
        : `${titleCase(s.category)} (no subcategory)`;
      return {
        key: s.subcategory || "",
        label,
        value: s.count,
      };
    });
    renderSubcategoryChart();
  }

  async function loadDifficulty(category, subcategory) {
    const parts = [];
    if (category) parts.push(titleCase(category));
    if (subcategory) parts.push(titleCase(subcategory));
    diffTitleEl.textContent = parts.length
      ? `Difficulty distribution — ${parts.join(" / ")}`
      : "Difficulty distribution (all problems)";
    diffChartEl.innerHTML = `<p class="chart-empty"><em>Loading…</em></p>`;
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (subcategory) params.set("subcategory", subcategory);
    let data;
    try {
      const resp = await fetch(`/api/stats/difficulty?${params.toString()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
    } catch (e) {
      diffChartEl.innerHTML = `<p class="chart-empty"><em>Failed to load: ${escapeHtml(e.message)}</em></p>`;
      return;
    }
    const items = (data.buckets || []).map(b => ({
      key: b.label,
      label: b.label,
      value: b.count,
    }));
    renderBars(diffChartEl, items, { clickable: false });
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadCategories();
    loadSubcategories(null);
    loadDifficulty(null, null);
  });
})();
