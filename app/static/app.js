const state = {
  mode: "text",
  entities: [],
  selectedEntities: new Set(),
  analysis: null,
  accepted: new Set(),
  file: null,
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  textTab: $("#text-tab"),
  documentTab: $("#document-tab"),
  textPanel: $("#text-panel"),
  documentPanel: $("#document-panel"),
  sourceText: $("#source-text"),
  charCount: $("#char-count"),
  fileInput: $("#document-file"),
  fileSummary: $("#file-summary"),
  dropZone: $("#drop-zone"),
  entityOptions: $("#entity-options"),
  toggleAll: $("#toggle-all"),
  analyzeButton: $("#analyze-button"),
  clearButton: $("#clear-button"),
  reviewSection: $("#review-section"),
  reviewSummary: $("#review-summary"),
  preview: $("#highlight-preview"),
  findingList: $("#finding-list"),
  acceptedCount: $("#accepted-count"),
  exportButton: $("#export-button"),
  resultBox: $("#result-box"),
  maskedResult: $("#masked-result"),
  copyButton: $("#copy-button"),
  dictionaryForm: $("#dictionary-form"),
  dictionaryTerm: $("#dictionary-term"),
  dictionaryType: $("#dictionary-type"),
  dictionaryNote: $("#dictionary-note"),
  dictionaryList: $("#dictionary-list"),
  toast: $("#toast"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = "処理に失敗しました。";
    try {
      const payload = await response.json();
      message = typeof payload.detail === "string" ? payload.detail : message;
    } catch (_) { /* response did not contain JSON */ }
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function showToast(message, isError = false) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", isError);
  elements.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { elements.toast.hidden = true; }, 3500);
}

function setBusy(busy, label = "PII候補を検出") {
  elements.analyzeButton.disabled = busy;
  elements.exportButton.disabled = busy;
  elements.analyzeButton.lastChild.textContent = busy ? " 解析中…" : ` ${label}`;
}

function setMode(mode) {
  state.mode = mode;
  const isText = mode === "text";
  elements.textPanel.hidden = !isText;
  elements.documentPanel.hidden = isText;
  elements.textTab.classList.toggle("active", isText);
  elements.documentTab.classList.toggle("active", !isText);
  elements.textTab.setAttribute("aria-selected", String(isText));
  elements.documentTab.setAttribute("aria-selected", String(!isText));
  clearAnalysis();
}

function renderEntities() {
  const groups = new Map();
  state.entities.forEach((entity) => {
    if (!groups.has(entity.group)) groups.set(entity.group, []);
    groups.get(entity.group).push(entity);
  });
  elements.entityOptions.innerHTML = [...groups.entries()].map(([group, entities]) => `
    <div class="entity-group">
      <h4>${escapeHtml(group)}</h4>
      ${entities.map((entity) => `
        <label class="entity-option">
          <input type="checkbox" value="${escapeHtml(entity.id)}" ${state.selectedEntities.has(entity.id) ? "checked" : ""}>
          <span>${escapeHtml(entity.label)}</span>
        </label>`).join("")}
    </div>`).join("");
  elements.entityOptions.setAttribute("aria-busy", "false");
  elements.entityOptions.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) state.selectedEntities.add(input.value);
      else state.selectedEntities.delete(input.value);
      updateToggleAll();
    });
  });
  elements.dictionaryType.innerHTML = state.entities.map((entity) =>
    `<option value="${escapeHtml(entity.id)}">${escapeHtml(entity.label)}</option>`
  ).join("");
  elements.dictionaryType.value = "CUSTOM";
  updateToggleAll();
}

function updateToggleAll() {
  elements.toggleAll.textContent = state.selectedEntities.size ? "すべて解除" : "すべて選択";
}

function entityLabel(id) {
  return state.entities.find((item) => item.id === id)?.label || id;
}

async function analyze() {
  if (!state.selectedEntities.size) {
    showToast("検出するPIIを1つ以上選択してください。", true);
    return;
  }
  setBusy(true);
  try {
    let analysis;
    if (state.mode === "text") {
      const text = elements.sourceText.value;
      if (!text.trim()) throw new Error("解析するテキストを入力してください。");
      const result = await api("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, entities: [...state.selectedEntities] }),
      });
      analysis = { ...result, blocks: [{ id: "text", text: result.text }], session_id: null };
    } else {
      if (!state.file) throw new Error("解析する文書を選択してください。");
      const form = new FormData();
      form.append("file", state.file);
      form.append("entities", [...state.selectedEntities].join(","));
      analysis = await api("/api/documents/analyze", { method: "POST", body: form });
    }
    state.analysis = analysis;
    state.accepted = new Set(analysis.findings.map((finding) => finding.id));
    renderReview();
    elements.reviewSection.hidden = false;
    elements.reviewSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function renderReview() {
  const findings = state.analysis?.findings || [];
  elements.reviewSummary.textContent = findings.length
    ? `${findings.length}件の候補が見つかりました。チェックを外すとマスク対象から除外されます。`
    : "候補は見つかりませんでした。PII辞書への追加も確認してください。";
  elements.findingList.innerHTML = findings.length ? findings.map((finding) => `
    <label class="finding-card" title="${escapeHtml(finding.text)}">
      <span class="finding-toggle"><input type="checkbox" value="${finding.id}" ${state.accepted.has(finding.id) ? "checked" : ""}></span>
      <span>
        <strong>${escapeHtml(finding.text)}</strong>
        <span class="finding-meta">
          <span class="entity-chip">${escapeHtml(entityLabel(finding.entity_type))}</span>
          <span>信頼度 ${Math.round(finding.score * 100)}%</span>
          <span>${escapeHtml(finding.source)}</span>
        </span>
      </span>
    </label>`).join("") : '<div class="empty-state">検出候補はありません</div>';
  elements.findingList.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) state.accepted.add(input.value);
      else state.accepted.delete(input.value);
      renderPreview();
      updateAcceptedCount();
    });
  });
  renderPreview();
  updateAcceptedCount();
  elements.resultBox.hidden = true;
}

function renderPreview() {
  const blocks = state.analysis?.blocks || [];
  const findings = state.analysis?.findings || [];
  elements.preview.innerHTML = blocks.map((block, index) => {
    const blockFindings = findings
      .filter((finding) => finding.block_id === block.id)
      .sort((a, b) => a.start - b.start);
    let cursor = 0;
    let html = "";
    blockFindings.forEach((finding) => {
      html += escapeHtml(block.text.slice(cursor, finding.start));
      const accepted = state.accepted.has(finding.id);
      html += `<mark class="${accepted ? "" : "rejected"}" title="${escapeHtml(entityLabel(finding.entity_type))}">${escapeHtml(block.text.slice(finding.start, finding.end))}</mark>`;
      cursor = finding.end;
    });
    html += escapeHtml(block.text.slice(cursor));
    if (blocks.length > 1) {
      return `<span class="block-separator">BLOCK ${index + 1}</span>${html}`;
    }
    return html;
  }).join("\n");
}

function updateAcceptedCount() {
  elements.acceptedCount.textContent = `${state.accepted.size}件をマスク`;
  elements.exportButton.disabled = !state.analysis;
}

async function exportMasked() {
  if (!state.analysis) return;
  setBusy(true);
  try {
    if (state.mode === "text") {
      const result = await api("/api/mask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: state.analysis.text,
          findings: state.analysis.findings,
          accepted_ids: [...state.accepted],
          mask_character: "█",
        }),
      });
      elements.maskedResult.textContent = result.masked_text;
      elements.resultBox.hidden = false;
      elements.resultBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else {
      const result = await api(`/api/documents/${state.analysis.session_id}/mask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted_ids: [...state.accepted], mask_character: "█" }),
      });
      const link = document.createElement("a");
      link.href = result.download_url;
      link.download = result.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      showToast("マスク済み文書を作成しました。");
    }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function clearAnalysis() {
  state.analysis = null;
  state.accepted.clear();
  elements.reviewSection.hidden = true;
  elements.resultBox.hidden = true;
}

function setFile(file) {
  const allowed = ["docx", "pptx", "pdf"];
  const extension = file?.name.split(".").pop().toLowerCase();
  if (!file || !allowed.includes(extension)) {
    state.file = null;
    elements.fileSummary.hidden = true;
    if (file) showToast("DOCX、PPTX、PDFファイルを選択してください。", true);
    return;
  }
  if (file.size > 25 * 1024 * 1024) {
    state.file = null;
    elements.fileSummary.hidden = true;
    showToast("ファイルサイズは25MB以下にしてください。", true);
    return;
  }
  state.file = file;
  elements.fileSummary.textContent = `${file.name} — ${(file.size / 1024 / 1024).toFixed(2)} MB`;
  elements.fileSummary.hidden = false;
  clearAnalysis();
}

async function loadDictionary() {
  try {
    const entries = await api("/api/dictionary");
    elements.dictionaryList.innerHTML = entries.length ? entries.map((entry) => `
      <div class="dictionary-entry">
        <strong>${escapeHtml(entry.term)}</strong>
        <span>${escapeHtml(entityLabel(entry.entity_type))}</span>
        <button type="button" data-entry-id="${entry.id}" aria-label="${escapeHtml(entry.term)}を削除">×</button>
      </div>`).join("") : '<div class="empty-state">登録語はまだありません</div>';
    elements.dictionaryList.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/dictionary/${button.dataset.entryId}`, { method: "DELETE" });
          await loadDictionary();
          showToast("辞書から削除しました。");
        } catch (error) { showToast(error.message, true); }
      });
    });
  } catch (error) { showToast(error.message, true); }
}

async function initialize() {
  try {
    const [entities, health] = await Promise.all([api("/api/entities"), api("/api/health")]);
    state.entities = entities;
    state.selectedEntities = new Set(entities.map((entity) => entity.id));
    renderEntities();
    const banner = $("#status-banner");
    const status = $("#status-text");
    if (health.nlp_available) {
      status.textContent = "GiNZA日本語NERとローカル検出ルールを利用できます";
    } else {
      banner.classList.add("warning");
      status.textContent = "GiNZAモデルを読み込めないため、現在はルールとPII辞書のみで検出します";
    }
    await loadDictionary();
  } catch (error) {
    showToast("アプリケーションを初期化できませんでした。", true);
  }
}

elements.textTab.addEventListener("click", () => setMode("text"));
elements.documentTab.addEventListener("click", () => setMode("document"));
elements.sourceText.addEventListener("input", () => {
  elements.charCount.textContent = `${elements.sourceText.value.length.toLocaleString()} / 500,000`;
  clearAnalysis();
});
elements.fileInput.addEventListener("change", () => setFile(elements.fileInput.files[0]));
["dragenter", "dragover"].forEach((eventName) => elements.dropZone.addEventListener(eventName, (event) => {
  event.preventDefault();
  elements.dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((eventName) => elements.dropZone.addEventListener(eventName, (event) => {
  event.preventDefault();
  elements.dropZone.classList.remove("dragging");
}));
elements.dropZone.addEventListener("drop", (event) => setFile(event.dataTransfer.files[0]));
elements.toggleAll.addEventListener("click", () => {
  const select = state.selectedEntities.size === 0;
  state.selectedEntities = new Set(select ? state.entities.map((entity) => entity.id) : []);
  renderEntities();
});
elements.analyzeButton.addEventListener("click", analyze);
elements.clearButton.addEventListener("click", () => {
  elements.sourceText.value = "";
  elements.charCount.textContent = "0 / 500,000";
  elements.fileInput.value = "";
  state.file = null;
  elements.fileSummary.hidden = true;
  clearAnalysis();
});
$("#accept-all").addEventListener("click", () => {
  state.accepted = new Set(state.analysis?.findings.map((finding) => finding.id) || []);
  renderReview();
});
$("#reject-all").addEventListener("click", () => { state.accepted.clear(); renderReview(); });
elements.exportButton.addEventListener("click", exportMasked);
elements.copyButton.addEventListener("click", async () => {
  await navigator.clipboard.writeText(elements.maskedResult.textContent);
  showToast("クリップボードにコピーしました。");
});
elements.dictionaryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/dictionary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        term: elements.dictionaryTerm.value,
        entity_type: elements.dictionaryType.value,
        note: elements.dictionaryNote.value,
      }),
    });
    elements.dictionaryForm.reset();
    elements.dictionaryType.value = "CUSTOM";
    await loadDictionary();
    showToast("PII辞書に追加しました。");
  } catch (error) { showToast(error.message, true); }
});

initialize();

