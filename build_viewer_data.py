"""Create a directly openable review UI snapshot from saved extraction results."""

import argparse
import json
from pathlib import Path

import pymupdf
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=Path("policy_roi.pdf"))
    parser.add_argument("--layout", type=Path, default=Path("policy_roi.page-001.json"))
    parser.add_argument("--ocr", type=Path, default=Path("policy_data_enhanced.json"))
    parser.add_argument("--ocr-source", type=Path, help="JSON linked for review when --ocr is a classified copy")
    parser.add_argument("--roi-output", type=Path, help="Structured layout ROI OCR JSON linked in the viewer")
    parser.add_argument("--association", type=Path, help="OCR-to-layout association JSON for visual debugging")
    parser.add_argument("--sections", type=Path, help="Section-classified blocks for review")
    parser.add_argument("--evidence", type=Path, help="Question-directed evidence manifest for review")
    parser.add_argument("--crop-dir", type=Path, help="Directory containing the ROI crop gallery")
    parser.add_argument("--image", type=Path, help="Original JPEG image; otherwise extract it from the PDF")
    parser.add_argument("--title", default="Policy schedule")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()

    layout = json.loads(args.layout.read_text())
    extraction = json.loads(args.ocr.read_text())
    page = extraction["rapidocr_layers"]["pages"][0]
    association = json.loads(args.association.read_text(encoding="utf-8"))["pages"][0] if args.association else None
    section_archive = json.loads(args.sections.read_text(encoding="utf-8")) if args.sections else None
    section_page = section_archive["pages"][0] if section_archive else None
    evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else None
    if evidence and section_archive and (evidence.get("source_sha256") != section_archive.get("source_sha256") or
                                         evidence.get("source_classification_schema") != section_archive.get("schema")):
        raise ValueError("evidence manifest does not match section classifications")
    if association and (association["width_px"], association["height_px"]) != (page["width_px"], page["height_px"]):
        raise ValueError("association and OCR page dimensions differ")
    if section_page and (section_page["width_px"], section_page["height_px"]) != (page["width_px"], page["height_px"]):
        raise ValueError("section classifications and OCR page dimensions differ")
    crop_gallery_file = None
    if args.crop_dir:
        try:
            relative_crop_dir = args.crop_dir.resolve().relative_to(args.output_dir.resolve())
        except ValueError as error:
            raise ValueError("--crop-dir must be inside --output-dir for browser links") from error
        if not (args.crop_dir / "index.html").is_file():
            raise ValueError("--crop-dir has no index.html gallery")
        crop_gallery_file = f"{relative_crop_dir.as_posix()}/index.html"
    data = {
        "title": args.title,
        "pdf": args.pdf.name,
        "layoutFile": args.layout.name,
        "ocrFile": (args.ocr_source or args.ocr).name,
        "roiOcrFile": args.roi_output.name if args.roi_output else None,
        "associationFile": args.association.name if args.association else None,
        "sectionFile": args.sections.name if args.sections else None,
        "evidenceFile": args.evidence.name if args.evidence else None,
        "cropGalleryFile": crop_gallery_file,
        "classification": extraction.get("classification"),
        "layout": {
            "width": layout["width_px"],
            "height": layout["height_px"],
            "regions": [
                {"id": r["id"], "type": r["type"], "box": r["bbox_pixel"]}
                for r in layout["regions"]
            ],
        },
        "ocr": {
            "width_px": page["width_px"],
            "height_px": page["height_px"],
            "full_page_lines": page["full_page_lines"],
        },
        "association": association,
        "sectionBlocks": section_page["blocks"] if section_page else None,
        "evidenceFields": evidence["fields"] if evidence else None,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "viewer-data.js").write_text(
        "window.VIEWER_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n"
    )

    if args.image:
        with Image.open(args.image) as source:
            if source.size == (page["width_px"], page["height_px"]):
                source.convert("RGB").save(args.output_dir / "viewer-page.jpg", quality=95)
            else:
                # Layout-driven OCR renders images through the same PDF conversion
                # as draw_layers.py. Use that page image for aligned overlays.
                source_doc = pymupdf.open(args.image)
                try:
                    converted = source_doc.convert_to_pdf()
                finally:
                    source_doc.close()
                with pymupdf.open(stream=converted, filetype="pdf") as pdf:
                    pix = pdf[0].get_pixmap(
                        matrix=pymupdf.Matrix(
                            extraction["rapidocr_layers"]["dpi"] / 72,
                            extraction["rapidocr_layers"]["dpi"] / 72,
                        ),
                        colorspace=pymupdf.csRGB, alpha=False,
                    )
                    if (pix.width, pix.height) != (page["width_px"], page["height_px"]):
                        raise ValueError("Rendered image dimensions do not match the OCR page")
                    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(
                        args.output_dir / "viewer-page.jpg", quality=95)
    else:
        with pymupdf.open(args.pdf) as pdf:
            images = pdf[0].get_images(full=True)
            if not images:
                raise ValueError("The first PDF page has no embedded source image")
            image = pdf.extract_image(images[0][0])
            if image["ext"] != "jpeg":
                raise ValueError(f"Expected an embedded JPEG, found {image['ext']}")
            if (image["width"], image["height"]) != (
                page["width_px"], page["height_px"]
            ):
                raise ValueError("The PDF image dimensions do not match the OCR page")
            (args.output_dir / "viewer-page.jpg").write_bytes(image["image"])

    print(f"Wrote viewer-data.js and viewer-page.jpg to {args.output_dir}")


if __name__ == "__main__":
    main()
