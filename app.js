const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const analyze = document.getElementById("analyze");
const selection = document.getElementById("selection");
const statusBox = document.getElementById("status");
const reviewLink = document.getElementById("review-link");
const stages = ["layout", "extract", "classify", "review"];
let selectedFile = null;
let previewUrl = null;
let pollTimer = null;

function showStatus(message, kind = "") {
  statusBox.textContent = message;
  statusBox.className = `status ${kind}`;
}
function setStage(stage) {
  const names = { "Detecting layout": "layout", "Extracting text": "extract", "Reading OCR lines": "extract", "Classifying document": "classify", "Preparing review": "review", "Review ready": "review" };
  const active = names[stage];
  const index = stages.indexOf(active);
  for (const [i, name] of stages.entries()) {
    const item = document.querySelector(`[data-step="${name}"]`);
    item.className = index < 0 ? "" : i < index ? "complete" : i === index ? "active" : "";
  }
}
function selectFile(file) {
  if (!file) return;
  if (!["image/jpeg", "image/png"].includes(file.type)) { showStatus("Choose a JPEG or PNG image.", "error"); return; }
  if (file.size > 20 * 1024 * 1024) { showStatus("Image must be under 20 MB.", "error"); return; }
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  selectedFile = file;
  document.getElementById("thumbnail").src = previewUrl;
  document.getElementById("file-name").textContent = file.name;
  document.getElementById("file-size").textContent = `${(file.size / 1024).toFixed(1)} KB`;
  selection.hidden = false;
  analyze.disabled = false;
  showStatus("Ready to analyze this image.");
}
dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));
for (const eventName of ["dragenter", "dragover"]) dropzone.addEventListener(eventName, event => { event.preventDefault(); dropzone.classList.add("dragover"); });
for (const eventName of ["dragleave", "drop"]) dropzone.addEventListener(eventName, event => { event.preventDefault(); dropzone.classList.remove("dragover"); });
dropzone.addEventListener("drop", event => selectFile(event.dataTransfer.files[0]));
document.getElementById("remove-file").addEventListener("click", () => { selectedFile = null; fileInput.value = ""; selection.hidden = true; analyze.disabled = true; if (previewUrl) URL.revokeObjectURL(previewUrl); showStatus("Choose an image to get started."); });

async function pollJob(id) {
  try {
    const response = await fetch(`/api/jobs/${id}`, { cache: "no-store" });
    if (!response.ok) throw Error("Could not read processing status.");
    const job = await response.json();
    setStage(job.stage);
    document.getElementById("progress-summary").textContent = job.stage;
    if (job.status === "complete") {
      clearInterval(pollTimer);
      for (const name of stages) document.querySelector(`[data-step="${name}"]`).className = "complete";
      showStatus("Analysis finished. Open the review to inspect the result.", "success");
      reviewLink.href = job.review_url;
      reviewLink.hidden = false;
      analyze.disabled = false;
      return false;
    } else if (job.status === "error") {
      clearInterval(pollTimer);
      showStatus(job.error || "Processing failed.", "error");
      analyze.disabled = false;
      return false;
    } else {
      showStatus(job.stage + "…");
    }
  } catch (error) {
    clearInterval(pollTimer);
    showStatus(error.message, "error");
    analyze.disabled = false;
    return false;
  }
  return true;
}
analyze.addEventListener("click", async () => {
  if (!selectedFile) return;
  analyze.disabled = true;
  reviewLink.hidden = true;
  setStage("Detecting layout");
  showStatus("Uploading image to the local processor…");
  try {
    const response = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": selectedFile.type }, body: selectedFile });
    const job = await response.json();
    if (!response.ok) throw Error(job.error || "Could not start processing.");
    const running = await pollJob(job.id);
    if (running) {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(() => pollJob(job.id), 1500);
    }
  } catch (error) {
    showStatus(error.message, "error");
    analyze.disabled = false;
  }
});
