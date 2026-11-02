#!/usr/bin/env python3
"""Add layered RapidOCR evidence to an existing Docling JSON extraction."""

import argparse
import json
import re
from importlib.metadata import version
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from rapidocr import RapidOCR
from rapidocr.utils.typings import EngineType, LangRec


def render_pages(path, dpi):
    if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot read image: {path}")
        yield image
        return
    with pymupdf.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
                                  colorspace=pymupdf.csRGB, alpha=False)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
            yield cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def rect_from_docling(bbox, page_size, image_size, pad=6):
    width, height = image_size
    sx = width / page_size["width"]
    sy = height / page_size["height"]
    x0, x1 = bbox["l"] * sx, bbox["r"] * sx
    if bbox.get("coord_origin") == "BOTTOMLEFT":
        y0 = (page_size["height"] - bbox["t"]) * sy
        y1 = (page_size["height"] - bbox["b"]) * sy
    else:
        y0, y1 = bbox["t"] * sy, bbox["b"] * sy
    return clip_rect((x0 - pad, min(y0, y1) - pad,
                      x1 + pad, max(y0, y1) + pad), width, height)


def clip_rect(box, width, height):
    x0, y0, x1, y1 = box
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(width, int(x1)), min(height, int(y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return [x0, y0, x1, y1]


def ocr_lines(ocr, image, offset=(0, 0), scale=1):
    result = ocr(image)
    if result.txts is None:
        return []
    lines = []
    for text, score, box in zip(result.txts, result.scores, result.boxes):
        points = np.asarray(box, dtype=float)
        x0 = float(points[:, 0].min()) / scale + offset[0]
        y0 = float(points[:, 1].min()) / scale + offset[1]
        x1 = float(points[:, 0].max()) / scale + offset[0]
        y1 = float(points[:, 1].max()) / scale + offset[1]
        lines.append({"text": str(text), "confidence": round(float(score), 4),
                      "bbox_pixel": [round(v, 2) for v in (x0, y0, x1, y1)]})
    return lines


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def baseline_text(document):
    values = [item.get("text", "") for item in document.get("texts", [])]
    for table in document.get("tables", []):
        values.extend(cell.get("text", "") for cell in
                      table.get("data", {}).get("table_cells", []))
    return {normalize(value) for value in values if value}


def grid_geometry(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 31, 12)
    height, width = gray.shape
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, width // 5), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, height // 25))))
    ys = np.where(np.count_nonzero(horizontal, axis=1) > width * .28)[0]
    breaks = np.where(np.diff(ys) > 1)[0] + 1
    y_lines = [int(np.median(group)) for group in np.split(ys, breaks) if len(group)]
    return y_lines, vertical


def refine_to_grid_cell(box, y_lines, vertical):
    if box is None:
        return None
    height, width = vertical.shape
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    above = [y for y in y_lines if y < cy]
    below = [y for y in y_lines if y > cy]
    if not above or not below:
        return box
    top, bottom = above[-1], below[0]
    if not 10 <= bottom - top <= height * .1:
        return box
    y0, y1 = top + 1, bottom - 1
    profile = np.count_nonzero(vertical[y0:y1], axis=0)
    xs = np.where(profile > (y1 - y0) * .7)[0]
    breaks = np.where(np.diff(xs) > 1)[0] + 1
    x_lines = [int(np.median(group)) for group in np.split(xs, breaks) if len(group)]
    left = [x for x in x_lines if x < cx]
    right = [x for x in x_lines if x > cx]
    if not left or not right:
        return box
    return clip_rect((left[-1] + 1, y0, right[0] - 1, y1), width, height) or box


def table_cell_regions(document, page_no, page_size, image_size, grid):
    regions = []
    for table_index, table in enumerate(document.get("tables", [])):
        provenance = table.get("prov", [])
        if not provenance or provenance[0].get("page_no") != page_no:
            continue
        for cell_index, cell in enumerate(table.get("data", {}).get("table_cells", [])):
            bbox = cell.get("bbox")
            if not bbox:
                continue
            box = rect_from_docling(bbox, page_size, image_size)
            if box:
                box = refine_to_grid_cell(box, *grid)
                regions.append({"kind": "table_cell", "bbox_pixel": box,
                                "table_index": table_index, "cell_index": cell_index,
                                "docling_text": cell.get("text", ""),
                                "row": cell.get("start_row_offset_idx"),
                                "column": cell.get("start_col_offset_idx")})
    return regions


def process_region(ocr, image, region):
    x0, y0, x1, y1 = region["bbox_pixel"]
    crop = image[y0:y1, x0:x1]
    upscaled = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return {**region, "upscaled_2x_lines": ocr_lines(ocr, upscaled, (x0, y0), 2)}


def enhance(source, baseline, output, dpi, backend, extra_rois, table_cells):
    data = json.loads(baseline.read_text(encoding="utf-8"))
    if "document" not in data:
        raise ValueError("input JSON has no Docling document")
    engine = EngineType.TORCH if backend == "torch" else EngineType.ONNXRUNTIME
    ocr = RapidOCR(params={
        "Det.engine_type": engine,
        "Rec.engine_type": engine,
        "Cls.engine_type": engine,
        "Rec.lang_type": LangRec.EN,
        "Global.use_cls": False,
        "Global.log_level": "error",
    })
    known = baseline_text(data["document"])
    pages = []
    for page_no, image in enumerate(render_pages(source, dpi), 1):
        height, width = image.shape[:2]
        page_size = data["document"].get("pages", {}).get(str(page_no), {}).get(
            "size", {"width": width, "height": height})
        full_page = ocr_lines(ocr, image)
        regions = []
        footer = clip_rect((0, height * .94, width, height), width, height)
        if footer:
            regions.append({"kind": "footer", "bbox_pixel": footer})
        if table_cells:
            grid = grid_geometry(image)
            regions.extend(table_cell_regions(data["document"], page_no,
                                              page_size, (width, height), grid))
        for spec in extra_rois:
            target_page, coords = spec
            if target_page == page_no:
                box = clip_rect(coords, width, height)
                if box:
                    regions.append({"kind": "custom", "bbox_pixel": box})
        processed = [process_region(ocr, image, region) for region in regions]
        new_lines = [line for line in full_page
                     if line["confidence"] >= .8 and len(normalize(line["text"])) >= 3
                     and normalize(line["text"]) not in known]
        pages.append({"page": page_no, "width_px": width, "height_px": height,
                      "full_page_lines": full_page, "new_full_page_lines": new_lines,
                      "regions": processed})
        print(f"Page {page_no}: {len(full_page)} OCR lines, "
              f"{len(new_lines)} unmatched, {len(processed)} region passes")
    data["rapidocr_layers"] = {"version": version("rapidocr"), "backend": backend,
                               "dpi": dpi, "pages": pages,
                               "note": "Supplemental OCR evidence; review disagreements before replacing Docling text."}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Original PDF or image")
    parser.add_argument("--json", required=True, type=Path, help="Existing Docling JSON")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--backend", choices=("torch", "onnxruntime"), default="torch")
    parser.add_argument("--no-table-cells", action="store_true")
    parser.add_argument("--roi", action="append", default=[],
                        help="Repeatable page:x0,y0,x1,y1 rectangle in rendered pixels")
    args = parser.parse_args()
    if args.dpi < 72:
        parser.error("--dpi must be at least 72")
    for path in (args.input, args.json):
        if not path.is_file():
            parser.error(f"file does not exist: {path}")
    if args.output.suffix.lower() != ".json":
        parser.error("output must be a .json file")
    if args.output.resolve() == args.json.resolve():
        parser.error("output must differ from the Docling JSON so it stays available for comparison")
    rois = []
    for spec in args.roi:
        try:
            page, coords = spec.split(":", 1)
            numbers = [float(part) for part in coords.split(",")]
            if len(numbers) != 4:
                raise ValueError
            if int(page) < 1:
                raise ValueError
            rois.append((int(page), numbers))
        except ValueError:
            parser.error(f"invalid --roi: {spec}")
    enhance(args.input, args.json, args.output, args.dpi, args.backend,
            rois, not args.no_table_cells)
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
