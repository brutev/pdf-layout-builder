const PAGE_WIDTH = window.VIEWER_DATA?.ocr.width_px || 768;
const PAGE_HEIGHT = window.VIEWER_DATA?.ocr.height_px || 1024;
const state = { items: [], selected: null, selectedBlock: null, selectedEvidence: null, sectionBlocks: [], zoom: 1, query: "", unassignedOnly: false };
const byId = id => document.getElementById(id);
const viewport = byId("viewport");
const page = byId("page");
const overlays = byId("overlays");

function setZoom(value) {
  state.zoom = Math.max(0.35, Math.min(2.5, value));
  page.style.width = `${PAGE_WIDTH * state.zoom}px`;
  page.style.height = `${PAGE_HEIGHT * state.zoom}px`;
  byId("zoom-label").textContent = `${Math.round(state.zoom * 100)}%`;
}
function fitPage() {
  setZoom(Math.min(2.5, (viewport.clientWidth - 56) / PAGE_WIDTH, (viewport.clientHeight - 56) / PAGE_HEIGHT));
}
function boxStyle(box, width, height) {
  const [x0, y0, x1, y1] = box;
  return { left: `${x0 / width * 100}%`, top: `${y0 / height * 100}%`, width: `${Math.max(1, x1 - x0) / width * 100}%`, height: `${Math.max(1, y1 - y0) / height * 100}%` };
}
function related(item) {
  if (state.selectedEvidence && item.kind === "ocr" && state.selectedEvidence.source_ocr_line_ids.includes(item.id)) return true;
  if (state.selectedBlock && item.kind === "ocr" && state.selectedBlock.ocr_line_ids.includes(item.id)) return true;
  if (!state.selected) return false;
  const selected = state.selected;
  if (selected.kind === "ocr") return selected.association?.matched_region_ids?.includes(item.id) || selected.association?.ancestor_region_ids?.includes(item.id);
  return item.kind === "ocr" && (item.association?.matched_region_ids?.includes(selected.id) || item.association?.ancestor_region_ids?.includes(selected.id));
}
function visible(item) {
  if (related(item)) return true;
  if (item.kind === "ocr" && state.unassignedOnly && item.association) return false;
  return byId(`layer-${item.kind}`).checked || (item.kind === "ocr" && state.query && item.text.toLowerCase().includes(state.query));
}
function render() {
  overlays.replaceChildren();
  let count = 0;
  for (const item of state.items) {
    if (!visible(item)) continue;
    count++;
    const el = document.createElement("button");
    el.type = "button";
    el.className = `overlay ${item.kind}${state.selected === item ? " selected" : ""}${related(item) ? " related" : ""}${item.kind === "ocr" && item.association === null ? " unassigned" : ""}${item.kind === "ocr" && item.candidates?.length > 1 ? " ambiguous" : ""}${state.query && item.kind === "ocr" && item.text.toLowerCase().includes(state.query) ? " search-hit" : ""}`;
    Object.assign(el.style, boxStyle(item.box, item.width, item.height));
    el.title = item.text || `${item.kind} ${item.id}`;
    el.setAttribute("aria-label", el.title);
    el.addEventListener("click", () => select(item));
    item.element = el;
    overlays.append(el);
  }
  byId("visible-count").textContent = `${count} overlays visible`;
}
function select(item, scroll = false) {
  state.selectedBlock = null;
  state.selectedEvidence = null;
  state.selected = item;
  render();
  byId("detail-title").textContent = item.kind === "ocr" ? "RapidOCR line" : `${item.kind[0].toUpperCase()}${item.kind.slice(1)} region`;
  byId("detail-subtitle").textContent = item.kind === "ocr" ? "Independent OCR result from this page" : `Layout region ${item.id}`;
  const detail = byId("detail-body");
  detail.replaceChildren();
  const section = (label, node) => {
    const heading = document.createElement("p"); heading.className = "detail-label"; heading.textContent = label;
    detail.append(heading, node);
  };
  const links = (ids, label) => {
    if (!ids?.length) return;
    const list = document.createElement("div"); list.className = "association-list";
    for (const id of ids) {
      const target = state.items.find(entry => entry.id === id);
      const button = document.createElement("button"); button.type = "button";
      button.textContent = target?.text ? `${id}: ${target.text}` : id;
      button.addEventListener("click", () => target && select(target, true));
      list.append(button);
    }
    section(label, list);
  };
  if (item.text) {
    const value = document.createElement("div"); value.className = "detail-value"; value.textContent = item.text;
    section("Recognized text", value);
    const confidence = document.createElement("div"); confidence.className = "confidence";
    const pct = Math.round(item.confidence * 100);
    confidence.innerHTML = `<span>${pct}%</span><span class="confidence-track"><span class="confidence-fill" style="display:block;width:${pct}%"></span></span>`;
    section("OCR confidence", confidence);
  }
  if (item.kind === "ocr") {
    const block = state.sectionBlocks.find(entry => entry.ocr_line_ids.includes(item.id));
    if (block) {
      const label = document.createElement("div"); label.className = "detail-value";
      const decision = block.section_classification;
      label.textContent = `${block.block_id}: ${decision.primary_section || decision.status} · ${decision.classification_method}${decision.model_score == null ? "" : ` · NLI score ${decision.model_score.toFixed(2)}`}`;
      section("Policy section", label);
    }
  }
  if (item.kind === "ocr" && item.hasAssociationData) {
    if (item.association) {
      const match = document.createElement("div"); match.className = "detail-value";
      match.textContent = `${item.association.primary_region_id} · ${item.association.method} · score ${item.association.score.toFixed(2)}`;
      section("Layout match", match);
      links([...(item.association.matched_region_ids || []), ...(item.association.ancestor_region_ids || [])], "Matched regions / hierarchy");
    } else {
      const reason = document.createElement("div"); reason.className = "detail-value";
      reason.textContent = `Unassigned: ${item.unassigned_reason || "no reliable match"}`;
      section("Layout match", reason);
    }
    if (item.candidates?.length) {
      const list = document.createElement("div"); list.className = "association-list";
      for (const candidate of item.candidates) {
        const button = document.createElement("button"); button.type = "button";
        button.textContent = `${candidate.region_id} · ${candidate.region_type} · ${candidate.score.toFixed(2)} · ${candidate.method}`;
        button.addEventListener("click", () => { const target = state.items.find(entry => entry.id === candidate.region_id); if (target) select(target, true); });
        list.append(button);
      }
      section("Candidate matches", list);
    }
  } else if (item.kind !== "ocr" && item.hasAssociationData) {
    links(state.items.filter(entry => entry.kind === "ocr" && (entry.association?.matched_region_ids?.includes(item.id) || entry.association?.ancestor_region_ids?.includes(item.id))).map(entry => entry.id), "Associated OCR lines");
    links([item.parent_region_id].filter(Boolean), "Parent region");
    links(item.child_region_ids, "Child regions");
  }
  const code = document.createElement("div"); code.className = "detail-code";
  const scaleX = PAGE_WIDTH / item.width, scaleY = PAGE_HEIGHT / item.height;
  code.textContent = `x  ${Math.round(item.box[0] * scaleX)} → ${Math.round(item.box[2] * scaleX)} px\ny  ${Math.round(item.box[1] * scaleY)} → ${Math.round(item.box[3] * scaleY)} px\nsource  ${item.kind === "ocr" ? "RapidOCR full page" : "Layout detector"}`;
  code.style.whiteSpace = "pre-wrap";
  section("Page coordinates", code);
  if (scroll) {
    const y = (item.box[1] / item.height) * PAGE_HEIGHT * state.zoom;
    viewport.scrollTop = Math.max(0, y - viewport.clientHeight / 2);
    const x = (item.box[0] / item.width) * PAGE_WIDTH * state.zoom;
    viewport.scrollLeft = Math.max(0, x - viewport.clientWidth / 2);
  }
}
function selectBlock(block) {
  state.selected = null;
  state.selectedBlock = block;
  state.selectedEvidence = null;
  render();
  const decision = block.section_classification;
  byId("detail-title").textContent = block.block_id;
  byId("detail-subtitle").textContent = `${decision.primary_section || decision.status} · ${decision.classification_method}`;
  const detail = byId("detail-body"); detail.replaceChildren();
  const value = document.createElement("div"); value.className = "detail-value"; value.textContent = block.text;
  const evidence = document.createElement("div"); evidence.className = "detail-code";
  evidence.textContent = `OCR lines: ${block.ocr_line_ids.join(", ")}\nLayout regions: ${block.layout_region_ids.join(", ")}\nOCR confidence: ${Math.round(block.confidence * 100)}%${decision.model_score == null ? "" : `\nNLI score: ${decision.model_score.toFixed(2)} (not calibrated)`}`;
  detail.append(value, evidence);
  const item = state.items.find(entry => entry.id === block.ocr_line_ids[0]);
  if (item) {
    viewport.scrollTop = Math.max(0, item.box[1] / item.height * PAGE_HEIGHT * state.zoom - viewport.clientHeight / 2);
    viewport.scrollLeft = Math.max(0, item.box[0] / item.width * PAGE_WIDTH * state.zoom - viewport.clientWidth / 2);
  }
}
function selectEvidence(field, candidate) {
  state.selected = null;
  state.selectedBlock = null;
  state.selectedEvidence = candidate;
  render();
  byId("detail-title").textContent = field.field.replaceAll("_", " ");
  byId("detail-subtitle").textContent = `Evidence rank ${candidate.rank} · page ${candidate.page} · score ${candidate.score.toFixed(2)}`;
  const detail = byId("detail-body"); detail.replaceChildren();
  const add = (label, value) => {
    const heading = document.createElement("p"); heading.className = "detail-label"; heading.textContent = label;
    const node = document.createElement("div"); node.className = "detail-value"; node.textContent = value;
    detail.append(heading, node);
  };
  add("Question", field.question);
  add("Candidate value", candidate.associated_value);
  add("Source text", candidate.text);
  add("Match", `${candidate.matched_alias} · ${candidate.relationship} · ${candidate.section} · ${candidate.alias_method}`);
  add("Provenance", `Blocks: ${candidate.evidence_block_ids.join(", ")}\nOCR: ${candidate.source_ocr_line_ids.join(", ")}\nRegions: ${candidate.source_region_ids.join(", ")}`);
  const first = state.items.find(item => item.id === candidate.source_ocr_line_ids[0]);
  if (first && candidate.page === 1) {
    viewport.scrollTop = Math.max(0, first.box[1] / first.height * PAGE_HEIGHT * state.zoom - viewport.clientHeight / 2);
    viewport.scrollLeft = Math.max(0, first.box[0] / first.width * PAGE_WIDTH * state.zoom - viewport.clientWidth / 2);
  } else if (candidate.page !== 1) {
    add("Page view", "This review canvas shows page 1. The manifest preserves the coordinates for this later page.");
  }
}
function renderEvidence(fields) {
  if (!fields?.length) return;
  byId("evidence-panel").hidden = false;
  byId("evidence-count").textContent = `${fields.filter(f => f.candidates.length).length}/${fields.length} found`;
  const list = byId("evidence-list"); list.replaceChildren();
  for (const field of fields) {
    const group = document.createElement("details"); group.className = "evidence-group";
    const summary = document.createElement("summary"); summary.textContent = `${field.field.replaceAll("_", " ")} · ${field.status === "not_found" ? "not found" : `${field.candidates.length} candidates`}`;
    group.append(summary);
    const question = document.createElement("p"); question.textContent = field.question; group.append(question);
    const meta = document.createElement("p"); meta.className = "evidence-meta";
    meta.textContent = `Preferred: ${field.preferred_section}${field.fallback_used ? " · fallback used" : ""}${field.conflicting_values ? " · conflicting values" : ""}`;
    group.append(meta);
    for (const candidate of field.candidates) {
      const button = document.createElement("button"); button.type = "button";
      button.textContent = `${candidate.rank === 1 ? "Selected" : "Alternative"} · ${candidate.score.toFixed(2)} · ${candidate.associated_value}`;
      button.title = `${candidate.relationship} · ${candidate.evidence_block_ids.join(", ")}`;
      button.addEventListener("click", () => selectEvidence(field, candidate)); group.append(button);
    }
    for (const rejected of field.rejected_candidates || []) {
      const note = document.createElement("p"); note.className = "evidence-rejected";
      note.textContent = `Rejected: ${rejected.block_id} · ${rejected.reason} · ${rejected.score.toFixed(2)}`; group.append(note);
    }
    list.append(group);
  }
}
function renderSections(blocks) {
  if (!blocks?.length) return;
  state.sectionBlocks = blocks;
  byId("sections-panel").hidden = false;
  const counts = new Map();
  for (const block of blocks) {
    const label = block.section_classification.primary_section || block.section_classification.status;
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  byId("sections-count").textContent = `${blocks.length} blocks`;
  const list = byId("sections-list"); list.replaceChildren();
  for (const [label, count] of counts) {
    const group = document.createElement("details"); group.className = "section-group";
    const summary = document.createElement("summary"); summary.textContent = `${label} (${count})`; group.append(summary);
    for (const block of blocks.filter(entry => (entry.section_classification.primary_section || entry.section_classification.status) === label)) {
      const button = document.createElement("button"); button.type = "button"; button.textContent = block.text || block.block_id;
      button.title = block.block_id; button.addEventListener("click", () => selectBlock(block)); group.append(button);
    }
    list.append(group);
  }
}
function renderSearch() {
  const root = byId("search-results"); root.replaceChildren();
  if (!state.query) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "Search the OCR text to jump to a location on the page."; root.append(p); return; }
  const hits = state.items.filter(item => item.kind === "ocr" && item.text.toLowerCase().includes(state.query));
  for (const item of hits.slice(0, 40)) {
    const button = document.createElement("button"); button.className = "result"; button.type = "button";
    const t = document.createElement("span"); t.className = "result-text"; t.textContent = item.text;
    const m = document.createElement("span"); m.className = "result-meta"; m.textContent = `${Math.round(item.confidence * 100)}% confidence · y ${Math.round(item.box[1])} px`;
    button.append(t, m); button.addEventListener("click", () => select(item, true)); root.append(button);
  }
  if (!hits.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "No OCR lines match this search."; root.append(p); }
  if (hits.length > 40) { const p = document.createElement("p"); p.className = "muted"; p.textContent = `Showing 40 of ${hits.length} matches. Refine your search.`; root.append(p); }
}
async function init() {
  const data = window.VIEWER_DATA;
  if (!data) throw Error("Viewer data unavailable");
  byId("document-name").textContent = data.title;
  byId("toolbar-name").textContent = data.title;
  byId("source-pdf").href = data.pdf;
  byId("source-layout").href = data.layoutFile;
  byId("source-ocr").href = data.ocrFile;
  if (data.roiOcrFile) { byId("source-roi-ocr").href = data.roiOcrFile; byId("source-roi-ocr").hidden = false; }
  if (data.associationFile) { byId("source-association").href = data.associationFile; byId("source-association").hidden = false; }
  if (data.sectionFile) { byId("source-sections").href = data.sectionFile; byId("source-sections").hidden = false; }
  if (data.evidenceFile) { byId("source-evidence").href = data.evidenceFile; byId("source-evidence").hidden = false; }
  if (data.cropGalleryFile) { byId("crop-gallery").href = data.cropGalleryFile; byId("crop-gallery").hidden = false; }
  renderClassification(data.classification);
  const layout = data.layout;
  const association = data.association;
  const regionMap = new Map((association?.regions || []).map(region => [region.id, region]));
  state.items = layout.regions.map(r => ({ kind: r.type, id: r.id, box: r.box, width: layout.width, height: layout.height, hasAssociationData: !!association, ...(regionMap.get(r.id) || {}) }));
  const ocrPage = data.ocr;
  state.items.push(...ocrPage.full_page_lines.map((line, index) => { const match = association?.ocr_lines?.[index]; return { kind: "ocr", id: match?.ocr_line_id || `ocr-${index + 1}`, box: line.bbox_pixel, width: ocrPage.width_px, height: ocrPage.height_px, text: line.text, confidence: line.confidence, hasAssociationData: !!association, association: match?.association, candidates: match?.candidates, unassigned_reason: match?.unassigned_reason }; }));
  renderSections(data.sectionBlocks);
  renderEvidence(data.evidenceFields);
  for (const kind of ["table", "cell", "text", "drawing", "ocr"]) byId(`count-${kind}`).textContent = state.items.filter(item => item.kind === kind).length;
  fitPage(); render();
}
function renderClassification(result) {
  if (!result) return;
  const labels = { insurance_policy: "Insurance policy", tax_invoice: "Tax invoice", other_document: "Other document", needs_review: "Needs review" };
  const badge = byId("classification-badge");
  const title = byId("classification-title");
  const score = byId("classification-score");
  if (result.status === "model_unavailable") {
    badge.className = "classification-badge pending";
    badge.textContent = "UNAVAILABLE";
    title.textContent = "Could not classify";
    score.textContent = "OCR review is ready";
    byId("classification-note").textContent = result.note;
    return;
  }
  badge.className = `classification-badge ${result.decision}`;
  badge.textContent = result.status === "insufficient_text" ? "LOW TEXT" : "ROBERTA";
  title.textContent = labels[result.decision] || "Needs review";
  if (result.model_score == null) {
    score.textContent = "Too little text for a model decision";
  } else if (result.scores) {
    const top = labels[result.top_label.replaceAll(" ", "_")] || result.top_label;
    score.textContent = `${top} match score: ${Math.round(result.model_score * 100)}%`;
  } else {
    score.textContent = `Insurance policy match score: ${Math.round(result.model_score * 100)}%`;
  }
  const options = byId("classification-options");
  options.replaceChildren();
  if (result.scores) {
    for (const [name, value] of Object.entries(result.scores).sort((a, b) => b[1] - a[1])) {
      const option = document.createElement("span");
      option.className = "classification-option";
      option.textContent = `${labels[name.replaceAll(" ", "_")] || name}: ${Math.round(value * 100)}%`;
      options.append(option);
    }
  }
  byId("classification-note").textContent = result.note || "Model output requires review.";
}
for (const kind of ["table", "cell", "text", "drawing", "ocr"]) byId(`layer-${kind}`).addEventListener("change", render);
byId("show-unassigned").addEventListener("change", event => { state.unassignedOnly = event.target.checked; render(); });
byId("search").addEventListener("input", event => { state.query = event.target.value.trim().toLowerCase(); renderSearch(); render(); });
byId("zoom-in").addEventListener("click", () => setZoom(state.zoom + .15));
byId("zoom-out").addEventListener("click", () => setZoom(state.zoom - .15));
byId("fit").addEventListener("click", fitPage);
window.addEventListener("resize", () => { if (state.zoom < 1) fitPage(); });
init().catch(error => { byId("visible-count").textContent = "Could not load data"; byId("search-results").textContent = `${error.message}. Rebuild viewer data using the command in README.md.`; });
