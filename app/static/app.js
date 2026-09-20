"use strict";

/* 编织行指令审校前端：编辑 -> 防抖调用 /api/analyze -> 渲染逐行结果 */

const STORAGE_KEY = "knit-review-plan-v1";
const DEBOUNCE_MS = 250;

const EXAMPLE = {
  name: "示例方案",
  start_stitches: 20,
  rows: [
    "[k2 inc1]*4 k8",
    "[k3 dec2]*4",
    "k16",
    "[k3 inc2]*4",
  ],
};

const els = {
  start: document.getElementById("start-stitches"),
  editor: document.getElementById("rows-editor"),
  add: document.getElementById("btn-add-row"),
  results: document.getElementById("results"),
  summary: document.getElementById("summary"),
  sync: document.getElementById("sync-state"),
  btnExample: document.getElementById("btn-example"),
  btnImport: document.getElementById("btn-import"),
  btnExport: document.getElementById("btn-export"),
  file: document.getElementById("file-import"),
};

let state = load() || { start_stitches: 20, rows: [...EXAMPLE.rows] };
let debounceTimer = null;
let reqSeq = 0;

// ---------------------------------------------------------------- 初始化
function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (typeof obj.start_stitches !== "number" || !Array.isArray(obj.rows)) return null;
    return { start_stitches: obj.start_stitches, rows: obj.rows.map(String) };
  } catch {
    return null;
  }
}

function persist() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

// ---------------------------------------------------------------- 编辑器
function renderEditor(focusIndex = -1) {
  els.start.value = state.start_stitches;
  els.editor.innerHTML = "";
  state.rows.forEach((text, i) => {
    const wrap = document.createElement("div");
    wrap.className = "row-edit";

    const no = document.createElement("span");
    no.className = "row-no";
    no.textContent = `第 ${i + 1} 行`;

    const input = document.createElement("input");
    input.type = "text";
    input.value = text;
    input.placeholder = "如 k4 [k2 inc1]*3 dec2";
    input.setAttribute("aria-label", `第 ${i + 1} 行指令`);
    input.addEventListener("input", () => {
      state.rows[i] = input.value;
      scheduleAnalyze();
    });

    const ops = document.createElement("div");
    ops.className = "row-ops";
    const up = miniBtn("↑", "上移", () => moveRow(i, -1));
    const down = miniBtn("↓", "下移", () => moveRow(i, 1));
    const del = miniBtn("删除", "删除本行", () => removeRow(i));
    del.classList.add("danger");
    ops.append(up, down, del);

    wrap.append(no, input, ops);
    els.editor.appendChild(wrap);
    if (i === focusIndex) input.focus();
  });
}

function miniBtn(text, title, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-mini";
  b.textContent = text;
  b.title = title;
  b.addEventListener("click", onClick);
  return b;
}

function moveRow(i, delta) {
  const j = i + delta;
  if (j < 0 || j >= state.rows.length) return;
  [state.rows[i], state.rows[j]] = [state.rows[j], state.rows[i]];
  renderEditor(j);
  persist();
  scheduleAnalyze();
}

function removeRow(i) {
  state.rows.splice(i, 1);
  renderEditor();
  persist();
  scheduleAnalyze();
}

els.start.addEventListener("input", () => {
  const v = Number(els.start.value);
  state.start_stitches = Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;
  scheduleAnalyze();
});

els.add.addEventListener("click", () => {
  state.rows.push("");
  renderEditor(state.rows.length - 1);
  persist();
});

// ---------------------------------------------------------------- 分析
function scheduleAnalyze() {
  persist();
  clearTimeout(debounceTimer);
  els.sync.textContent = "计算中…";
  els.sync.className = "sync-state pending";
  debounceTimer = setTimeout(analyze, DEBOUNCE_MS);
}

async function analyze() {
  const seq = ++reqSeq;
  try {
    const resp = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_stitches: state.start_stitches, rows: state.rows }),
    });
    if (!resp.ok) throw new Error(`服务返回 ${resp.status}`);
    const data = await resp.json();
    if (seq === reqSeq) {
      renderResults(data.rows);
      els.sync.textContent = "已同步";
      els.sync.className = "sync-state";
    }
  } catch (err) {
    if (seq === reqSeq) {
      els.sync.textContent = "分析失败：" + err.message;
      els.sync.className = "sync-state error";
    }
  }
}

// ---------------------------------------------------------------- 结果渲染
const STATUS_TEXT = {
  ok: "通过",
  unconsumed: "未全部消耗",
  shortage: "针数短缺",
  error: "语法错误",
  blocked: "已阻断",
};

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderSource(row) {
  const src = row.source;
  if (row.status === "shortage" && row.failing_start != null) {
    const [a, b] = [row.failing_start, row.failing_end];
    return (
      escapeHtml(src.slice(0, a)) +
      `<span class="fail-hl" title="第一个无法执行的指令">${escapeHtml(src.slice(a, b))}</span>` +
      escapeHtml(src.slice(b))
    );
  }
  if (row.status === "error" && row.error_pos != null) {
    const p = Math.max(0, Math.min(row.error_pos, src.length));
    return escapeHtml(src.slice(0, p)) +
      `<span class="caret" title="出错位置">▲</span>` + escapeHtml(src.slice(p));
  }
  return escapeHtml(src);
}

function renderResults(rows) {
  els.results.innerHTML = "";
  renderSummary(rows);
  if (rows.length === 0) {
    const d = document.createElement("div");
    d.className = "empty-hint";
    d.textContent = "还没有任何行，点击“添加一行”或“载入示例”开始。";
    els.results.appendChild(d);
    return;
  }

  rows.forEach((row, i) => {
    if (i > 0) {
      const arrow = document.createElement("div");
      arrow.className = "flow-arrow";
      const prev = rows[i - 1];
      const carried = (prev.status === "ok" || prev.status === "unconsumed")
        ? prev.remaining : "—";
      arrow.innerHTML = `↓ 上一行产出 <b>${carried}</b> 针作为本行可用针数`;
      els.results.appendChild(arrow);
    }
    els.results.appendChild(renderCard(row, i));
  });
}

function renderSummary(rows) {
  const counts = { ok: 0, unconsumed: 0, shortage: 0, error: 0, blocked: 0 };
  rows.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
  const last = rows[rows.length - 1];
  const finalStitches = last && (last.status === "ok" || last.status === "unconsumed")
    ? last.remaining : null;

  const badges = [];
  badges.push(`行数 <b>${rows.length}</b>`);
  badges.push(`起始 <b>${state.start_stitches}</b> 针`);
  badges.push(`✅ 通过 <b>${counts.ok}</b>`);
  if (counts.unconsumed) badges.push(`⚠️ 未耗尽 <b>${counts.unconsumed}</b>`);
  if (counts.shortage) badges.push(`❌ 短缺 <b>${counts.shortage}</b>`);
  if (counts.error) badges.push(`❌ 语法错误 <b>${counts.error}</b>`);
  if (counts.blocked) badges.push(`🚫 阻断 <b>${counts.blocked}</b>`);
  badges.push(`最终针数 <b>${finalStitches == null ? "—" : finalStitches}</b>`);

  els.summary.innerHTML = badges.map((b) => `<span class="badge">${b}</span>`).join("");
}

function renderCard(row, i) {
  const card = document.createElement("div");
  card.className = `row-card ${row.status}`;

  const head = document.createElement("div");
  head.className = "row-card-head";
  head.innerHTML =
    `<span class="idx">第 ${i + 1} 行</span>` +
    `<span class="status-tag">${STATUS_TEXT[row.status] || row.status}</span>`;
  card.appendChild(head);

  const source = document.createElement("div");
  source.className = "row-source";
  source.innerHTML = renderSource(row) || "<span style='opacity:.5'>（空行）</span>";
  card.appendChild(source);

  if (row.status !== "blocked") {
    const m = document.createElement("div");
    m.className = "metrics";
    const parts = ["<span>可用 <b>" + row.available + "</b></span>"];
    if (row.status === "shortage") {
      parts.push(`<span class="metric-consumed">已消耗 <b>${row.consumed}</b></span>`);
      parts.push(`<span class="metric-short">短缺 <b>${row.shortage}</b></span>`);
    } else if (row.status !== "error") {
      parts.push(`<span class="metric-consumed">消耗 <b>${row.consumed}</b></span>`);
      parts.push(`<span class="metric-produced">产出 <b>${row.produced}</b></span>`);
      parts.push(`<span class="metric-remaining">剩余 <b>${row.remaining}</b></span>`);
      if (row.unconsumed) {
        parts.push(`<span class="metric-unused">未消耗 <b>${row.unconsumed}</b></span>`);
      }
    }
    m.innerHTML = parts.join("");
    card.appendChild(m);
  }

  if (row.error) {
    const msg = document.createElement("div");
    msg.className = "msg";
    msg.textContent = row.error;
    card.appendChild(msg);
  }

  if (row.expanded_count > 0) {
    const det = document.createElement("details");
    det.className = "expanded";
    det.innerHTML = `<summary>展开指令（共 ${row.expanded_count} 条）</summary>`;
    const list = document.createElement("div");
    list.className = "token-list";
    list.innerHTML = row.expanded_preview
      .map((t) => `<span class="tok">${escapeHtml(t)}</span>`).join("");
    det.appendChild(list);
    if (row.expanded_truncated) {
      const note = document.createElement("div");
      note.className = "trunc-note";
      note.textContent = `仅预览前 ${row.expanded_preview.length} 条，共 ${row.expanded_count} 条。`;
      det.appendChild(note);
    }
    card.appendChild(det);
  }

  return card;
}

// ---------------------------------------------------------------- 导入导出
els.btnExample.addEventListener("click", () => {
  state = { start_stitches: EXAMPLE.start_stitches, rows: [...EXAMPLE.rows] };
  renderEditor();
  persist();
  scheduleAnalyze();
});

els.btnExport.addEventListener("click", () => {
  const plan = {
    format: "knit-row-review/v1",
    name: "编织方案",
    start_stitches: state.start_stitches,
    rows: state.rows,
    exported_at: new Date().toISOString(),
  };
  const blob = new Blob([JSON.stringify(plan, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `knit-plan-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

els.btnImport.addEventListener("click", () => els.file.click());

els.file.addEventListener("change", () => {
  const file = els.file.files && els.file.files[0];
  els.file.value = "";
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const obj = JSON.parse(String(reader.result));
      if (typeof obj.start_stitches !== "number" || !Number.isInteger(obj.start_stitches)
          || obj.start_stitches < 0 || !Array.isArray(obj.rows)
          || !obj.rows.every((r) => typeof r === "string")) {
        throw new Error("缺少 start_stitches（非负整数）或 rows（字符串数组）");
      }
      state = { start_stitches: obj.start_stitches, rows: obj.rows };
      renderEditor();
      persist();
      scheduleAnalyze();
    } catch (err) {
      alert("导入失败：" + err.message);
    }
  };
  reader.readAsText(file);
});

// ---------------------------------------------------------------- 启动
renderEditor();
scheduleAnalyze();
