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
  --ocr new_policy_data_enhanced.json \
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

Each page gets a JSON file named like `policy_roi.page-001.json`. Every region has a unique `id`, a `type` (`text`, `table`, `cell`, or `drawing`), and `bbox_pdf`, `bbox_pixel`, and `bbox_normalized` coordinates. PDF coordinates are points in PyMuPDF's unrotated page space; pixel and normalized coordinates follow the displayed page orientation at the requested DPI. `bbox` is an alias for `bbox_pixel`.

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
