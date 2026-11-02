#!/usr/bin/env python3
"""Draw non-ML layout regions on a PDF or image and export per-page JSON."""

import argparse
import json
from pathlib import Path

import cv2
import fitz
import numpy as np


COLORS = {
    "text": (0.1, 0.35, 1.0),
    "table": (1.0, 0.15, 0.15),
    "cell": (0.0, 0.65, 0.2),
    "drawing": (1.0, 0.55, 0.0),
}


def valid_rect(box, bounds, min_side=0.5):
    """Clip a box to page bounds and reject empty boxes."""
    rect = fitz.Rect(box) & bounds
    return rect if not rect.is_empty and rect.width >= min_side and rect.height >= min_side else None


def digital_regions(page):
    regions = []
    bounds = page.rect * page.derotation_matrix
    for block in page.get_text("blocks"):
        if len(block) > 6 and block[6] != 0:
            continue
        box = valid_rect(block[:4], bounds)
        if box:
            regions.append(("text", box))

    for table in page.find_tables():
        box = valid_rect(table.bbox, bounds)
        if box:
            regions.append(("table", box))
        for cell in table.cells:
            if cell is not None:
                box = valid_rect(cell, bounds)
                if box:
                    regions.append(("cell", box))

    for drawing in page.get_drawings():
        raw = fitz.Rect(drawing["rect"])
        # A vector line has a zero-height or zero-width bounding box.
        if raw.width < .5:
            raw.x0 -= .25
            raw.x1 += .25
        if raw.height < .5:
            raw.y0 -= .25
            raw.y1 += .25
        box = valid_rect(raw, bounds)
        if box:
            regions.append(("drawing", box))
    return regions


def deskew_mask(gray):
    """Return aligned grayscale image and inverse affine transform."""
    foreground = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    points = cv2.findNonZero(foreground)
    if points is None or len(points) < 20:
        return gray, np.eye(2, 3, dtype=np.float32)
    angle = cv2.minAreaRect(points)[-1]
    angle = angle - 90 if angle > 45 else angle
    # A large angle normally means a designed diagonal or a photograph, not skew.
    if abs(angle) > 10:
        angle = 0
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    aligned = cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return aligned, cv2.invertAffineTransform(matrix)


def scan_regions(gray, min_cell_area, debug_prefix=None):
    aligned, inverse = deskew_mask(gray)
    h, w = aligned.shape
    binary = cv2.adaptiveThreshold(aligned, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 31, 12)
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                   cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, w // 40), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, h // 50))))
    grid = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))
    contours, hierarchy = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    hierarchy = hierarchy[0] if hierarchy is not None else []
    candidate = []
    for i, contour in enumerate(contours):
        x, y, cw, ch = cv2.boundingRect(contour)
        area = cw * ch
        if (area < min_cell_area or cw < 8 or ch < 8 or
                (cw > w * .98 and ch > h * .98)):
            continue
        candidate.append((i, (x, y, x + cw, y + ch)))

    # A table is a grid contour containing at least two cell-like child contours.
    tables = []
    cells = []
    candidates_by_id = dict(candidate)
    for i, box in candidate:
        children = [j for j, _ in candidate if hierarchy[j][3] == i]
        if len(children) >= 2:
            tables.append(box)
            cells.extend(candidates_by_id[j] for j in children)
    if not tables:
        # Broken outer borders still permit individual ruled cells.
        cells = [box for i, box in candidate if hierarchy[i][2] < 0]

    text_mask = cv2.subtract(binary, cv2.dilate(grid, np.ones((3, 3), np.uint8)))
    merged = cv2.dilate(text_mask, cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(5, w // 180), max(3, h // 350))))
    text_contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    text_boxes = []
    for contour in text_contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        if cw * ch >= max(30, min_cell_area // 8) and cw >= 5 and ch >= 3:
            text_boxes.append((x, y, x + cw, y + ch))

    if debug_prefix:
        cv2.imwrite(str(debug_prefix) + ".threshold.png", binary)
        cv2.imwrite(str(debug_prefix) + ".grid.png", grid)
        cv2.imwrite(str(debug_prefix) + ".text.png", merged)

    def unwarp(box):
        x0, y0, x1, y1 = box
        corners = np.array([[x0, y0, 1], [x1, y0, 1],
                            [x0, y1, 1], [x1, y1, 1]], dtype=np.float32)
        mapped = corners @ inverse.T
        return (float(mapped[:, 0].min()), float(mapped[:, 1].min()),
                float(mapped[:, 0].max()), float(mapped[:, 1].max()))

    return [(kind, unwarp(box)) for kind, boxes in
            (("table", tables), ("cell", cells), ("text", text_boxes)) for box in boxes]


def pixel_to_pdf(pixel_box, page, dpi):
    scale = 72.0 / dpi
    displayed = fitz.Rect(*(v * scale for v in pixel_box))
    return displayed * page.derotation_matrix


def pdf_to_pixel(pdf_box, page, dpi):
    displayed = pdf_box * page.rotation_matrix
    scale = dpi / 72.0
    return [round(v * scale, 2) for v in displayed]


def process_page(page, layer, dpi, min_cell_area, debug_prefix):
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72),
                          colorspace=fitz.csGRAY, alpha=False)
    pixel_bounds = fitz.Rect(0, 0, pix.width, pix.height)
    pdf_bounds = page.rect * page.derotation_matrix
    if page.get_text("words"):
        source = digital_regions(page)
        mode = "digital"
    else:
        gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
        source = [(kind, pixel_to_pdf(box, page, dpi)) for kind, box in
                  scan_regions(gray, min_cell_area, debug_prefix)]
        mode = "scanned"

    regions = []
    seen = set()
    for kind, box in source:
        pdf_box = valid_rect(box, pdf_bounds)
        if pdf_box is None:
            continue
        pixel_box = valid_rect(pdf_to_pixel(pdf_box, page, dpi), pixel_bounds)
        if pixel_box is None:
            continue
        key = (kind, *(round(v, 1) for v in pdf_box))
        if key in seen:
            continue
        seen.add(key)
        page.draw_rect(pdf_box, color=COLORS[kind], fill=COLORS[kind],
                       fill_opacity=.3, width=max(.35, 72 / dpi), oc=layer,
                       overlay=True)
        pixel_values = [round(v, 2) for v in pixel_box]
        regions.append({
            "id": f"{page.number + 1}-{len(regions) + 1}",
            "type": kind,
            "bbox": pixel_values,
            "bbox_pixel": pixel_values,
            "bbox_pdf": [round(v, 2) for v in pdf_box],
            "bbox_normalized": [round(pixel_values[0] / pix.width, 6),
                                round(pixel_values[1] / pix.height, 6),
                                round(pixel_values[2] / pix.width, 6),
                                round(pixel_values[3] / pix.height, 6)],
        })
    return mode, regions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--min-cell-area", type=int, default=400)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if args.dpi < 72 or args.min_cell_area < 1:
        parser.error("--dpi must be at least 72 and --min-cell-area must be positive")
    if not args.input.is_file():
        parser.error(f"input does not exist: {args.input}")
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must be different files")
    if args.output.suffix.lower() != ".pdf":
        parser.error("output must have a .pdf extension")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = fitz.open(args.input)
    if source.is_pdf:
        doc = source
    elif args.input.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        pdf_bytes = source.convert_to_pdf()
        source.close()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    else:
        source.close()
        parser.error("input must be a PDF, JPEG, PNG, or TIFF")
    with doc:
        layer = doc.add_ocg("Layout ROIs", on=True)
        for page in doc:
            prefix = args.output.with_name(f"{args.output.stem}.page-{page.number + 1:03d}")
            mode, regions = process_page(page, layer, args.dpi, args.min_cell_area,
                                         prefix if args.debug else None)
            payload = {"page": page.number + 1, "mode": mode,
                       "width_px": round(page.rect.width * args.dpi / 72),
                       "height_px": round(page.rect.height * args.dpi / 72),
                       "regions": regions}
            Path(str(prefix) + ".json").write_text(json.dumps(payload, indent=2) + "\n")
            print(f"Page {page.number + 1}: {mode}, {len(regions)} regions")
        doc.save(args.output, garbage=4, deflate=True)
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
