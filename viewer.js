const PAGE_WIDTH = window.VIEWER_DATA?.ocr.width_px || 768;
const PAGE_HEIGHT = window.VIEWER_DATA?.ocr.height_px || 1024;
const state = { items: [], selected: null, zoom: 1, query: "" };
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
function visible(item) { return byId(`layer-${item.kind}`).checked || (item.kind === "ocr" && state.query && item.text.toLowerCase().includes(state.query)); }
function render() {
  overlays.replaceChildren();
  let count = 0;
  for (const item of state.items) {
    if (!visible(item)) continue;
    count++;
    const el = document.createElement("button");
    el.type = "button";
    el.className = `overlay ${item.kind}${state.selected === item ? " selected" : ""}${state.query && item.kind === "ocr" && item.text.toLowerCase().includes(state.query) ? " search-hit" : ""}`;
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
  if (item.text) {
    const value = document.createElement("div"); value.className = "detail-value"; value.textContent = item.text;
    section("Recognized text", value);
    const confidence = document.createElement("div"); confidence.className = "confidence";
    const pct = Math.round(item.confidence * 100);
    confidence.innerHTML = `<span>${pct}%</span><span class="confidence-track"><span class="confidence-fill" style="display:block;width:${pct}%"></span></span>`;
    section("OCR confidence", confidence);
  }
  const code = document.createElement("div"); code.className = "detail-code";
  const scaleX = PAGE_WIDTH / item.width, scaleY = PAGE_HEIGHT / item.height;
  code.textContent = `x  ${Math.round(item.box[0] * scaleX)} → ${Math.round(item.box[2] * scaleX)} px\ny  ${Math.round(item.box[1] * scaleY)} → ${Math.round(item.box[3] * scaleY)} px\nsource  ${item.kind === "ocr" ? "RapidOCR" : "Layout detector"}`;
  code.style.whiteSpace = "pre-wrap";
  section("Page coordinates", code);
  if (scroll) {
    const y = (item.box[1] / item.height) * PAGE_HEIGHT * state.zoom;
    viewport.scrollTop = Math.max(0, y - viewport.clientHeight / 2);
    const x = (item.box[0] / item.width) * PAGE_WIDTH * state.zoom;
    viewport.scrollLeft = Math.max(0, x - viewport.clientWidth / 2);
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
  renderClassification(data.classification);
  const layout = data.layout;
  state.items = layout.regions.filter(r => ["table", "cell", "text"].includes(r.type)).map(r => ({ kind: r.type, id: r.id, box: r.box, width: layout.width, height: layout.height }));
  const ocrPage = data.ocr;
  state.items.push(...ocrPage.full_page_lines.map((line, index) => ({ kind: "ocr", id: `ocr-${index + 1}`, box: line.bbox_pixel, width: ocrPage.width_px, height: ocrPage.height_px, text: line.text, confidence: line.confidence })));
  for (const kind of ["table", "cell", "text", "ocr"]) byId(`count-${kind}`).textContent = state.items.filter(item => item.kind === kind).length;
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
for (const kind of ["table", "cell", "text", "ocr"]) byId(`layer-${kind}`).addEventListener("change", render);
byId("search").addEventListener("input", event => { state.query = event.target.value.trim().toLowerCase(); renderSearch(); render(); });
byId("zoom-in").addEventListener("click", () => setZoom(state.zoom + .15));
byId("zoom-out").addEventListener("click", () => setZoom(state.zoom - .15));
byId("fit").addEventListener("click", fitPage);
window.addEventListener("resize", () => { if (state.zoom < 1) fitPage(); });
init().catch(error => { byId("visible-count").textContent = "Could not load data"; byId("search-results").textContent = `${error.message}. Rebuild viewer data using the command in README.md.`; });
