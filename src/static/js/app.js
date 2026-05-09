document.addEventListener("DOMContentLoaded", function () {
  renderMathInElement(document.body, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "$", right: "$", display: false },
      { left: "\\(", right: "\\)", display: false }
    ],
    throwOnError: false,
    ignoredTags: ["script", "noscript", "style", "textarea", "code"]
  });
});

(function () {
  const form = document.getElementById("upload-form");
  const btn = document.getElementById("process-btn");
  const progress = document.getElementById("progress-wrap");
  if (form) {
    form.addEventListener("submit", function () {
      btn.disabled = true;
      btn.textContent = "Processing…";
      progress.classList.add("active");
    });
  }

  const catSel = document.getElementById("filter-category");
  const diffSel = document.getElementById("filter-difficulty");
  const countEl = document.getElementById("filter-count");
  const items = document.querySelectorAll("#problem-list .problem");
  function applyFilters() {
    const c = catSel.value;
    const d = diffSel.value;
    let visible = 0;
    items.forEach(el => {
      const matchC = !c || el.dataset.category === c;
      const matchD = !d || el.dataset.difficulty === d;
      const show = matchC && matchD;
      el.classList.toggle("hidden", !show);
      if (show) visible++;
    });
    if (countEl) {
      countEl.textContent = (c || d) ? `Showing ${visible} of ${items.length}` : "";
    }
  }
  if (catSel && diffSel) {
    catSel.addEventListener("change", applyFilters);
    diffSel.addEventListener("change", applyFilters);
  }

  const printBtn = document.getElementById("print-btn");
  const printCount = document.getElementById("print-count");
  const printStatus = document.getElementById("print-status");
  function clearSelection() {
    document.querySelectorAll(".problem.print-selected")
      .forEach(el => el.classList.remove("print-selected"));
  }
  if (printBtn) {
    printBtn.addEventListener("click", function () {
      const visible = Array.from(items).filter(el => !el.classList.contains("hidden"));
      if (visible.length === 0) {
        printStatus.textContent = "No problems match the current filters.";
        return;
      }
      let n = parseInt(printCount.value, 10);
      if (isNaN(n) || n < 1) n = 1;
      n = Math.min(n, visible.length);
      const pool = visible.slice();
      for (let i = pool.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }
      clearSelection();
      pool.slice(0, n).forEach(el => el.classList.add("print-selected"));
      printStatus.textContent = `Printing ${n} problem(s)…`;
      const cleanup = () => {
        clearSelection();
        printStatus.textContent = "";
        window.removeEventListener("afterprint", cleanup);
      };
      window.addEventListener("afterprint", cleanup);
      window.print();
    });
  }
})();
