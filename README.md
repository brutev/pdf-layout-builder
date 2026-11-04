# PDF layout builder

Draw detected layout regions from a PDF or image on a toggleable PDF layer and export their coordinates as JSON. Layout detection itself uses no OCR or AI model; the review app also offers OCR and optional RoBERTa classification.

## Run the local app

```sh
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open `http://127.0.0.1:8765` in a browser. Upload a JPEG or PNG (up to 20 MB). The app processes it locally, shows each stage, and opens a review page with layout overlays, OCR text, and a RoBERTa document type result. The first classification run downloads the model from Hugging Face. If the model is unavailable, the review page still opens with OCR and an **Unavailable** classification card. Generated files are saved under `runs/<job-id>/`.

On macOS, you can also double-click `run-review.command` to start the server in Terminal.

## Review the sample in a UI

Open `index.html` in a browser. The review screen currently displays the newly supplied `images.jpeg` policy image with toggleable table, cell, text, and RapidOCR overlays. Search the OCR lines, click a result or region, and inspect its coordinates in the right panel. The layered PDF and source JSON files remain available from the screen.

After updating the sample PDF or JSON files, rebuild the browser snapshot:

```sh
.venv/bin/python build_viewer_data.py \
  --image /path/to/image.jpeg \
  --pdf new_policy_roi.pdf \
  --layout new_policy_roi.page-001.json \
  --ocr new_policy_classified.json \
  --ocr-source new_policy_data_enhanced.json \
  --title "Uploaded policy image"
```

The current review screen is a snapshot of page 1. The image dimensions must match the OCR page. When `--image` is omitted, the builder extracts an embedded JPEG from the PDF instead.

## Classify the document type with RoBERTa

The optional classifier reads the first 100 words of first-page extracted text from the saved JSON and uses the NLI-trained `cross-encoder/nli-distilroberta-base` model to compare **Tax invoice**, **Insurance policy**, and **Other document**. The model is downloaded from Hugging Face on first use; inference runs locally and does not upload the document. Run it after OCR:

```sh
.venv/bin/python classify_document.py \
  --input new_policy_data_enhanced.json \
  --output new_policy_classified.json
.venv/bin/python build_viewer_data.py \
  --image /path/to/image.jpeg \
  --pdf new_policy_roi.pdf \
  --layout new_policy_roi.page-001.json \
  --ocr new_policy_classified.json \
  --ocr-source new_policy_data_enhanced.json \
  --title "Uploaded policy image"
```

Reload `index.html` to see the result in the document type card. The card shows **Not classified** until the model has actually run. A label is shown when its relative score is at least 0.70 and leads the next label by at least 0.20; otherwise the result is **Needs review**. These are uncalibrated zero-shot model scores, not verified probabilities; OCR errors and documents containing policy language for another purpose can affect the result. Validate with a labeled set of your own documents before automating document routing.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```sh
.venv/bin/python draw_layers.py --input policy.pdf --output policy_roi.pdf
```

You can also use a JPEG, PNG, or TIFF directly:

```sh
.venv/bin/python draw_layers.py --input /Users/vigneshv/Downloads/1.jpeg --output policy_roi.pdf --debug
```

Options: `--dpi` (default 200), `--min-cell-area` (minimum scanned cell area in pixels, default 400), and `--debug` (save intermediate scanned-page images beside the output).

Each page gets a JSON file named like `policy_roi.page-001.json`. Every region has a unique `id`, a `type` (`text`, `table`, `cell`, or `drawing`), and `bbox_pdf`, `bbox_pixel`, and `bbox_normalized` coordinates. The sidecar also records the actual rendered pixel dimensions, DPI, page rotation, and source SHA-256 for coordinate validation. PDF coordinates are points in PyMuPDF's unrotated page space; pixel and normalized coordinates follow the displayed page orientation at the requested DPI. `bbox` is an alias for `bbox_pixel`.

Pages with selectable words use PyMuPDF text blocks, table detection, and vector drawing bounds. Other pages use OpenCV morphology to find ruled grids and nearby text. Scanned pages are deskewed for detection, then boxes are mapped back onto the original page. Detection is approximate for borderless tables, photos, glare, and watermarks. The PDF content is preserved, and the `Layout ROIs` layer can be toggled in a compatible viewer.

## Extract data with Docling

Install the updated requirements, then run:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python extract_data.py \
  --input /Users/vigneshv/Downloads/1.jpeg \
  --output policy_data.json
```

For a PDF with a misleading text layer, add `--force-ocr`. Docling uses OCR plus layout and table models, and may download model weights on its first run.

The JSON contains Docling's extracted text, tables, reading order, coordinates and provenance, plus conversion errors and a page-count audit. For digital PDFs it also contains native text and word coordinates from PyMuPDF. Image assets and source-file bytes are excluded. OCR cannot guarantee every character, so check critical fields against the source, particularly where a watermark covers text. The command exits with status 2 if Docling reports an incomplete conversion or its page count differs from the source.

## Layered RapidOCR review

RapidOCR can make a second, independent pass on the whole page, the footer and each Docling table cell. Each pass is saved as text, confidence and pixel coordinates alongside the original Docling result. It does not silently replace fields that disagree.

```sh
.venv/bin/python enhance_rapidocr.py \
  --input /Users/vigneshv/Downloads/1.jpeg \
  --json policy_data.json \
  --output policy_data_enhanced.json
```

Use `--roi 1:190,338,460,363` to add a close-up of the obscured address (coordinates are pixels at the selected DPI for PDFs, and image pixels for JPEGs). Use `--no-table-cells` for a faster page and footer pass. The default Torch backend uses RapidOCR's bundled models; `--backend onnxruntime` is available if that runtime is installed. OCR can recover text missed by Docling, but watermark overlap may still require a person to verify the original.

## Layout-driven ROI OCR

Generate fresh sidecars and pass the first page JSON (or its directory) to RapidOCR:

```sh
.venv/bin/python draw_layers.py --input document.jpeg --output layout.pdf --dpi 200
.venv/bin/python extract_data.py --input document.jpeg --output extracted.json
.venv/bin/python enhance_rapidocr.py \
  --input document.jpeg --json extracted.json --output enhanced.json \
  --layout layout.page-001.json --dpi 200 --no-table-cells \
  --roi-output roi_ocr.json --crop-dir roi-crops
```

The ROI path renders the source through PyMuPDF exactly as the detector does. It rejects sidecars with mismatched source hash, DPI, page count, rotation, or pixel dimensions. Older sidecars without this metadata must be regenerated. Nearby text fragments are grouped into line and short column blocks; tables use nonoverlapping cell crops when cells are usable, otherwise one table crop. Very small crops are retained as skipped records. The `layout_roi_ocr` object is also kept in `enhanced.json`. Each result records the group ID, original detector IDs, crop box and dimensions, mapped page-coordinate OCR lines, status, and page statistics. `roi-crops/index.html` provides a visual gallery of every attempted crop.

Full-page RapidOCR remains enabled by default. The local app uses `--no-layout-roi-ocr` so crop OCR does not run during normal review. Run the command above without that flag when a difficult area needs recovery. Existing footer, custom `--roi`, and Docling table-cell passes still work.

## Associate full-page OCR with layout

Use the same source and DPI for layout detection and full-page OCR:

```sh
.venv/bin/python enhance_rapidocr.py \
  --input document.jpeg --json extracted.json --output enhanced.json \
  --layout layout.page-001.json --dpi 200 --no-table-cells --no-layout-roi-ocr
.venv/bin/python associate_ocr_layout.py \
  --ocr enhanced.json --layout layout.page-001.json \
  --source document.jpeg --output associated.json
.venv/bin/python build_viewer_data.py \
  --image document.jpeg --pdf layout.pdf --layout layout.page-001.json \
  --ocr enhanced.json --association associated.json
```

`associated.json` contains one record for each original full-page OCR line, scored region candidates, a primary region or an unassigned reason, matched text fragments, table/cell ancestors, and structured text blocks. Docling text, Docling table cells, and native PDF words are linked as separate source evidence. Each OCR line appears once in reconstructed block text. The association stage uses saved boxes and OCR output; it does not invoke RapidOCR. In the viewer, click a region to highlight its OCR lines, or click an OCR line to inspect candidate scores and its region hierarchy. The normal upload flow generates this file automatically.

### Section classification

The upload flow also reads `insurance_policy_questions.yaml` and writes `section_classified.json` from the structured blocks in `associated.json`. It combines heading and field aliases, nearby layout context, and the cached NLI RoBERTa model. Unclear blocks remain unknown or ambiguous. NLI scores are evidence, not calibrated probabilities. The review sidebar lists classified blocks and highlights their source OCR lines when selected.

Run this stage separately with:

```sh
.venv/bin/python classify_sections.py --input associated.json --output section_classified.json
```

Use `--no-model` for a deterministic structural and keyword fallback. The model is loaded from the local cache unless `--allow-model-download` is explicitly supplied. To evaluate manually reviewed examples from two policy layouts, run `evaluate_sections.py --policy POLICY_SECTION_JSON --small SMALL_SECTION_JSON --output evaluation.json`.

### Question-directed evidence retrieval

The upload flow now reads the 21 field definitions in `insurance_policy_questions.yaml` and writes `evidence_manifest.json` after section classification. Each field has aliases, a value type, preferred and fallback sections, a candidate limit, normalization rules, and required status. The retriever searches all classified blocks so an incorrect section label cannot hide evidence. It ranks exact/fuzzy labels, same-row and same-column values, table relationships, OCR confidence, and section context, then retains competing values and source IDs. It does not answer the questions or invoke Qwen.

```sh
.venv/bin/python retrieve_evidence.py --input section_classified.json --output evidence_manifest.json
```

The default is lightweight and does not load a model. `--semantic-rerank` optionally adds a small, separately reported NLI RoBERTa relevance signal to the shortlist when the cached model is available. The review sidebar displays each question, candidate and rejected evidence, score, and fallback status; selecting a candidate highlights its source OCR lines on page 1. Evaluate saved manifests with `evaluate_evidence.py --policy POLICY_MANIFEST --small SMALL_MANIFEST --output evaluation.json`.
