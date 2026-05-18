const STATUS_LABELS = {
  processing: "Processing",
  pending: "Pending",
  failed: "Failed",
  done: "Done",
};

const SECTION_LIMITS = { processing: 50, pending: 200, failed: 50, done: 50 };

let timer = null;

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function renderSummary(counts) {
  const wrap = document.getElementById("summary");
  const order = ["processing", "pending", "failed", "done"];
  const parts = order.map((s) => {
    const n = counts[s] || 0;
    return `<span class="pill ${s}"><strong>${n}</strong>${STATUS_LABELS[s]}</span>`;
  });
  wrap.innerHTML = parts.join("");
}

function renderSection(status, items) {
  const wrap = document.getElementById(`${status}-wrap`);
  const rows = items.filter((it) => it.status === status).slice(0, SECTION_LIMITS[status]);
  if (rows.length === 0) {
    wrap.innerHTML = `<p class="queue-empty">None.</p>`;
    return;
  }
  const showStarted = status === "processing";
  const showFinished = status === "done" || status === "failed";
  const showError = status === "failed";
  const showSolution = status === "pending" || status === "processing";
  const header = `
    <tr>
      <th>File</th>
      <th>Queued</th>
      ${showStarted ? "<th>Started</th>" : ""}
      ${showFinished ? "<th>Finished</th>" : ""}
      <th>Attempts</th>
      ${showSolution ? "<th>Solution?</th>" : ""}
      ${showError ? "<th>Error</th>" : ""}
    </tr>`;
  const body = rows
    .map((it) => {
      const err = it.last_error
        ? `<div class="err">${escapeHtml(it.last_error)}</div>`
        : "";
      return `
        <tr>
          <td class="file">${escapeHtml(it.filename)}${
        !showError && it.last_error ? err : ""
      }</td>
          <td>${fmtTime(it.queued_at)}</td>
          ${showStarted ? `<td>${fmtTime(it.started_at)}</td>` : ""}
          ${showFinished ? `<td>${fmtTime(it.finished_at)}</td>` : ""}
          <td>${it.attempts}</td>
          ${showSolution ? `<td>${it.with_solution ? "yes" : "no"}</td>` : ""}
          ${showError ? `<td>${err || ""}</td>` : ""}
        </tr>`;
    })
    .join("");
  wrap.innerHTML = `<table class="queue-table"><thead>${header}</thead><tbody>${body}</tbody></table>`;
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
    const resp = await fetch("/api/queue", { credentials: "same-origin" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderSummary(data.counts || {});
    const items = data.items || [];
    ["processing", "pending", "failed", "done"].forEach((s) =>
      renderSection(s, items)
    );
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
  refresh();
  scheduleAuto();
});
