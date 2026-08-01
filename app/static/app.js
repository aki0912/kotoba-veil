const state = {
  mode: "text",
  entities: [],
  selectedEntities: new Set(),
  analysis: null,
  accepted: new Set(),
  file: null,
  settingsSelectionSnapshot: "",
  pendingSelection: null,
  selectionTimer: null,
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
  reviewEmpty: $("#review-empty"),
  reviewContent: $("#review-content"),
  reviewSummary: $("#review-summary"),
  preview: $("#highlight-preview"),
  manualFindingForm: $("#manual-finding-form"),
  manualFindingHeading: $("#manual-finding-heading"),
  manualEntityType: $("#manual-entity-type"),
  manualSaveDictionary: $("#manual-save-dictionary"),
  manualFindingError: $("#manual-finding-error"),
  cancelManualFinding: $("#cancel-manual-finding"),
  entityLegend: $("#entity-legend"),
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
  settingsDialog: $("#settings-dialog"),
  openSettings: $("#open-settings"),
  closeSettings: $("#close-settings"),
  applySettings: $("#apply-settings"),
  selectedEntityCount: $("#selected-entity-count"),
  dialogSelectionSummary: $("#dialog-selection-summary"),
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
  elements.manualEntityType.innerHTML = `<option value="">分類を選択</option>${state.entities.map((entity) =>
    `<option value="${escapeHtml(entity.id)}">${escapeHtml(entity.label)}</option>`
  ).join("")}`;
  updateToggleAll();
}

function updateToggleAll() {
  elements.toggleAll.textContent = state.selectedEntities.size ? "すべて解除" : "すべて選択";
  elements.selectedEntityCount.textContent = state.selectedEntities.size;
  elements.dialogSelectionSummary.textContent = `${state.selectedEntities.size} / ${state.entities.length}種類を検出`;
}

function entityLabel(id) {
  return state.entities.find((item) => item.id === id)?.label || id;
}

function entityClass(id) {
  return `entity-${String(id).toLowerCase().replaceAll("_", "-")}`;
}

function sourceLabel(source) {
  return source === "manual-selection" ? "手動追加" : source;
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
    if (window.matchMedia("(max-width: 980px)").matches) {
      elements.reviewSection.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function renderReview() {
  const findings = state.analysis?.findings || [];
  elements.reviewEmpty.hidden = true;
  elements.reviewContent.hidden = false;
  elements.reviewSummary.textContent = findings.length
    ? `${findings.length}件を検出`
    : "候補なし";
  const visibleTypes = [...new Set(findings.map((finding) => finding.entity_type))];
  elements.entityLegend.innerHTML = visibleTypes.map((type) =>
    `<span class="legend-item ${entityClass(type)}">${escapeHtml(entityLabel(type))}</span>`
  ).join("");
  elements.findingList.innerHTML = findings.length ? findings.map((finding) => `
    <div class="finding-card ${entityClass(finding.entity_type)} ${finding.source === "manual-selection" ? "manual-finding-card" : ""}" title="${escapeHtml(finding.text)}">
      <label class="finding-toggle"><input type="checkbox" value="${finding.id}" aria-label="${escapeHtml(finding.text)}をマスク" ${state.accepted.has(finding.id) ? "checked" : ""}></label>
      <span>
        <strong>${escapeHtml(finding.text)}</strong>
        <span class="finding-meta">
          <span class="entity-chip ${entityClass(finding.entity_type)}">${escapeHtml(entityLabel(finding.entity_type))}</span>
          <span>信頼度 ${Math.round(finding.score * 100)}%</span>
          <span class="${finding.source === "manual-selection" ? "manual-source-chip" : ""}">${escapeHtml(sourceLabel(finding.source))}</span>
          <span class="finding-state"><span class="state-masked">マスク対象</span><span class="state-retained">原文を残す</span></span>
        </span>
      </span>
      ${finding.source === "manual-selection" ? `
        <button class="remove-manual-finding" type="button" data-finding-id="${finding.id}" aria-label="${escapeHtml(finding.text)}の手動候補を削除">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 6V4h10v2h4v2h-2v12H5V8H3V6h4Zm2 0h6V5H9v1ZM7 8v10h10V8H7Zm3 2h2v6h-2v-6Zm4 0h2v6h-2v-6Z"/></svg>
        </button>` : ""}
    </div>`).join("") : '<div class="empty-state">検出候補はありません</div>';
  elements.findingList.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) state.accepted.add(input.value);
      else state.accepted.delete(input.value);
      renderPreview();
      updateAcceptedCount();
    });
  });
  elements.findingList.querySelectorAll(".remove-manual-finding").forEach((button) => {
    button.addEventListener("click", () => removeManualFinding(button.dataset.findingId));
  });
  renderPreview();
  updateAcceptedCount();
  elements.resultBox.hidden = true;
}

function renderPreview() {
  const blocks = state.analysis?.blocks || [];
  const findings = state.analysis?.findings || [];
  elements.preview.innerHTML = blocks.map((block, index) => {
    const segments = findings
      .filter((finding) => finding.block_id === block.id)
      .map((finding) => ({ ...finding, kind: "finding" }));
    if (state.pendingSelection?.block_id === block.id) {
      segments.push({ ...state.pendingSelection, kind: "pending" });
    }
    segments.sort((a, b) => a.start - b.start || a.end - b.end);
    const characters = Array.from(block.text);
    let cursor = 0;
    let html = "";
    segments.forEach((segment) => {
      html += escapeHtml(characters.slice(cursor, segment.start).join(""));
      const value = escapeHtml(characters.slice(segment.start, segment.end).join(""));
      if (segment.kind === "pending") {
        html += `<span class="pending-selection">${value}</span>`;
      } else {
        const accepted = state.accepted.has(segment.id);
        const label = entityLabel(segment.entity_type);
        const statusLabel = accepted ? label : "原文を残す";
        const title = accepted ? `${label}：マスク対象` : `${label}：マスクしない`;
        html += `<mark class="${entityClass(segment.entity_type)} ${accepted ? "" : "rejected"}" data-label="${escapeHtml(statusLabel)}" aria-label="${escapeHtml(title)}：${value}">${value}</mark>`;
      }
      cursor = segment.end;
    });
    html += escapeHtml(characters.slice(cursor).join(""));
    const separator = blocks.length > 1
      ? `<span class="block-separator">BLOCK ${index + 1}</span>`
      : "";
    return `${separator}<div class="preview-block" data-block-id="${escapeHtml(block.id)}">${html}</div>`;
  }).join("");
  renderManualFindingBar();
}

function renderManualFindingBar() {
  const pending = state.pendingSelection;
  elements.manualFindingForm.hidden = !pending;
  if (!pending) {
    elements.manualFindingError.hidden = true;
    return;
  }
  elements.manualFindingHeading.textContent = `「${pending.text}」`;
}

function capturePreviewSelection() {
  if (!state.analysis) return;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return;
  const range = selection.getRangeAt(0);
  const startBlock = previewBlockForNode(range.startContainer);
  const endBlock = previewBlockForNode(range.endContainer);
  if (!startBlock || !endBlock) return;
  if (startBlock !== endBlock) {
    showToast("複数の文書ブロックをまたぐ範囲は追加できません。", true);
    return;
  }

  const blockId = startBlock.dataset.blockId;
  const block = state.analysis.blocks.find((item) => item.id === blockId);
  if (!block) return;
  const start = codePointOffsetWithin(startBlock, range.startContainer, range.startOffset);
  const end = codePointOffsetWithin(startBlock, range.endContainer, range.endOffset);
  const characters = Array.from(block.text);
  const rawText = characters.slice(start, end).join("");
  const leadingText = rawText.match(/^\s*/u)?.[0] || "";
  const trailingText = rawText.match(/\s*$/u)?.[0] || "";
  const trimmedStart = start + Array.from(leadingText).length;
  const trimmedEnd = end - Array.from(trailingText).length;
  const text = characters.slice(trimmedStart, trimmedEnd).join("");

  if (!text) {
    showToast("空白だけの範囲は追加できません。", true);
    return;
  }
  if (Array.from(text).length > 200) {
    showToast("選択範囲は200文字以内にしてください。", true);
    return;
  }
  const overlaps = state.analysis.findings.some((finding) =>
    finding.block_id === blockId && trimmedStart < finding.end && trimmedEnd > finding.start
  );
  if (overlaps) {
    showToast("すでに検出候補です。候補のチェックをONにしてください。", true);
    return;
  }

  state.pendingSelection = {
    block_id: blockId,
    start: trimmedStart,
    end: trimmedEnd,
    text,
  };
  selection.removeAllRanges();
  elements.manualFindingForm.reset();
  elements.manualEntityType.value = "";
  elements.manualFindingError.hidden = true;
  renderPreview();
}

function previewBlockForNode(node) {
  const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  return element?.closest(".preview-block") || null;
}

function codePointOffsetWithin(block, container, offset) {
  const prefix = document.createRange();
  prefix.selectNodeContents(block);
  prefix.setEnd(container, offset);
  return Array.from(prefix.toString()).length;
}

function clearPendingSelection() {
  if (!state.pendingSelection) return;
  state.pendingSelection = null;
  window.getSelection()?.removeAllRanges();
  renderPreview();
}

function showManualFindingError(message) {
  elements.manualFindingError.textContent = message;
  elements.manualFindingError.hidden = false;
}

function updateAcceptedCount() {
  elements.acceptedCount.textContent = `${state.accepted.size}件をマスク`;
  elements.exportButton.disabled = !state.analysis;
}

async function addManualFinding(event) {
  event.preventDefault();
  const pending = state.pendingSelection;
  if (!pending || !state.analysis) return;
  if (!elements.manualEntityType.value) {
    showManualFindingError("PII分類を選択してください。");
    elements.manualEntityType.focus();
    return;
  }

  const submitButton = elements.manualFindingForm.querySelector('button[type="submit"]');
  const options = {
    block_id: pending.block_id,
    start: pending.start,
    end: pending.end,
    entity_type: elements.manualEntityType.value,
    scope: elements.manualFindingForm.elements.namedItem("manual-scope").value,
    save_to_dictionary: elements.manualSaveDictionary.checked,
  };
  const path = state.mode === "text"
    ? "/api/findings/manual"
    : `/api/documents/${state.analysis.session_id}/findings/manual`;
  const body = state.mode === "text"
    ? { ...options, text: state.analysis.text, findings: state.analysis.findings }
    : options;

  submitButton.disabled = true;
  submitButton.textContent = "追加中…";
  elements.manualFindingError.hidden = true;
  try {
    const result = await api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.analysis.findings = [...state.analysis.findings, ...result.added_findings];
    result.added_findings.forEach((finding) => state.accepted.add(finding.id));
    state.pendingSelection = null;
    renderReview();
    if (result.dictionary_status !== "not_requested") await loadDictionary();
    const skipped = result.skipped_count
      ? `（既存候補と重なる${result.skipped_count}件を除外）`
      : "";
    const dictionary = result.dictionary_status === "created"
      ? " PII辞書にも保存しました。"
      : result.dictionary_status === "already_exists"
        ? " PII辞書には登録済みです。"
        : "";
    showToast(`${result.added_count}件をマスク候補に追加しました${skipped}。${dictionary}`.trim());
  } catch (error) {
    showManualFindingError(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "マスク候補に追加";
  }
}

async function removeManualFinding(findingId) {
  const finding = state.analysis?.findings.find((item) => item.id === findingId);
  if (!finding || finding.source !== "manual-selection") return;
  try {
    if (state.mode === "document") {
      await api(`/api/documents/${state.analysis.session_id}/findings/${findingId}`, {
        method: "DELETE",
      });
    }
    state.analysis.findings = state.analysis.findings.filter((item) => item.id !== findingId);
    state.accepted.delete(findingId);
    renderReview();
    showToast("手動追加した候補を削除しました。");
  } catch (error) {
    showToast(error.message, true);
  }
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
          replacement_mode: "entity_label",
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
  state.pendingSelection = null;
  window.clearTimeout(state.selectionTimer);
  elements.reviewEmpty.hidden = false;
  elements.reviewContent.hidden = true;
  elements.reviewSummary.textContent = "未解析";
  elements.entityLegend.innerHTML = "";
  elements.manualFindingForm.hidden = true;
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
document.addEventListener("selectionchange", () => {
  window.clearTimeout(state.selectionTimer);
  const selection = window.getSelection();
  if (
    !selection
    || selection.isCollapsed
    || !selection.anchorNode
    || !elements.preview.contains(selection.anchorNode)
  ) return;
  state.selectionTimer = window.setTimeout(capturePreviewSelection, 180);
});
elements.toggleAll.addEventListener("click", () => {
  const select = state.selectedEntities.size === 0;
  state.selectedEntities = new Set(select ? state.entities.map((entity) => entity.id) : []);
  renderEntities();
});
elements.openSettings.addEventListener("click", () => {
  state.settingsSelectionSnapshot = [...state.selectedEntities].sort().join(",");
  elements.settingsDialog.showModal();
});
elements.closeSettings.addEventListener("click", () => elements.settingsDialog.close());
elements.applySettings.addEventListener("click", () => elements.settingsDialog.close());
elements.settingsDialog.addEventListener("click", (event) => {
  if (event.target === elements.settingsDialog) elements.settingsDialog.close();
});
elements.settingsDialog.addEventListener("close", () => {
  const currentSelection = [...state.selectedEntities].sort().join(",");
  if (currentSelection !== state.settingsSelectionSnapshot && state.analysis) {
    clearAnalysis();
    showToast("検出設定を変更しました。もう一度解析してください。");
  }
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
elements.manualFindingForm.addEventListener("submit", addManualFinding);
elements.cancelManualFinding.addEventListener("click", clearPendingSelection);
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
