const URL_PREFIX = document.body.dataset.urlPrefix || "";

const STATUS_LABELS = {
  processing: "Classifying",
  pending: "Pending",
  failed: "Failed",
  done: "Done",
};

const STATUS_ORDER = ["processing", "pending", "failed", "done"];

const SECTION_LIMITS = {
  processing: 50,
  pending: 200,
  failed: 50,
  done: 50,
};

const STATUS_PILL_CLASS = {
  processing: "processing",
  pending: "pending",
  failed: "failed",
  done: "done",
};

let timer = null;

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function renderSummary(counts) {
  const wrap = document.getElementById("summary");
  const parts = STATUS_ORDER.map((s) => {
    const n = counts[s] || 0;
    const cls = STATUS_PILL_CLASS[s] || "";
    return `<span class="pill ${cls}"><strong>${n}</strong>${STATUS_LABELS[s]}</span>`;
  });
  wrap.innerHTML = parts.join("");
}

function renderSection(status, items) {
  const wrap = document.getElementById(`${status}-wrap`);
  if (!wrap) return;
  const rows = items.filter((it) => it.status === status).slice(0, SECTION_LIMITS[status]);
  if (rows.length === 0) {
    wrap.innerHTML = `<p class="queue-empty">None.</p>`;
    return;
  }
  const showStarted = status === "processing";
  const showFinished = status === "done" || status === "failed";
  const showCategory = status === "done";
  const showError = status === "failed";
  const showRetry = status === "failed";
  const header = `
    <tr>
      <th>Problem</th>
      <th>Queued</th>
      ${showStarted ? "<th>Started</th>" : ""}
      ${showFinished ? "<th>Finished</th>" : ""}
      <th>Attempts</th>
      ${showCategory ? "<th>Category</th>" : ""}
      ${showError ? "<th>Error</th>" : ""}
      ${showRetry ? "<th></th>" : ""}
    </tr>`;
  const body = rows
    .map((it) => {
      const err = it.last_error
        ? `<div class="err">${escapeHtml(it.last_error)}</div>`
        : "";
      const retryCell = showRetry
        ? `<td><button type="button" class="retry-btn" data-problem-id="${escapeHtml(
            it.problem_id,
          )}">Retry</button></td>`
        : "";
      const cat = [it.category, it.subcategory].filter(Boolean).join(" / ");
      return `
        <tr>
          <td class="snippet">${escapeHtml(it.problem_text)}${
        !showError && it.last_error ? err : ""
      }</td>
          <td>${fmtTime(it.queued_at)}</td>
          ${showStarted ? `<td>${fmtTime(it.started_at)}</td>` : ""}
          ${showFinished ? `<td>${fmtTime(it.finished_at)}</td>` : ""}
          <td>${it.attempts}</td>
          ${showCategory ? `<td class="cat">${escapeHtml(cat)}</td>` : ""}
          ${showError ? `<td>${err || ""}</td>` : ""}
          ${retryCell}
        </tr>`;
    })
    .join("");
  wrap.innerHTML = `<table class="queue-table"><thead>${header}</thead><tbody>${body}</tbody></table>`;
}

async function retryFailed(problemId, button) {
  if (!problemId) return;
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "Retrying…";
  try {
    const resp = await fetch(`${URL_PREFIX}/api/classify/retry`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ problem_id: problemId }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    await refresh();
  } catch (e) {
    button.disabled = false;
    button.textContent = original;
    alert(`Retry failed: ${e.message}`);
  }
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function refresh() {
  try {
    const resp = await fetch(`${URL_PREFIX}/api/classify`, { credentials: "same-origin" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderSummary(data.counts || {});
    const items = data.items || [];
    STATUS_ORDER.forEach((s) => renderSection(s, items));
    document.getElementById("last-updated").textContent =
      `Updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    document.getElementById("last-updated").textContent = `Error: ${e.message}`;
  }
}

function scheduleAuto() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  if (document.getElementById("autorefresh").checked) {
    timer = setInterval(refresh, 5000);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("refresh-btn").addEventListener("click", refresh);
  document.getElementById("autorefresh").addEventListener("change", scheduleAuto);
  document.getElementById("failed-wrap").addEventListener("click", (e) => {
    const btn = e.target.closest(".retry-btn");
    if (!btn) return;
    retryFailed(btn.dataset.problemId, btn);
  });
  refresh();
  scheduleAuto();
});
