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
    parser.add_argument("--image", type=Path, help="Original JPEG image; otherwise extract it from the PDF")
    parser.add_argument("--title", default="Policy schedule")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()

    layout = json.loads(args.layout.read_text())
    extraction = json.loads(args.ocr.read_text())
    page = extraction["rapidocr_layers"]["pages"][0]
    data = {
        "title": args.title,
        "pdf": args.pdf.name,
        "layoutFile": args.layout.name,
        "ocrFile": args.ocr.name,
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
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "viewer-data.js").write_text(
        "window.VIEWER_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n"
    )

    if args.image:
        with Image.open(args.image) as source:
            if source.size != (page["width_px"], page["height_px"]):
                raise ValueError("The image dimensions do not match the OCR page")
            source.convert("RGB").save(args.output_dir / "viewer-page.jpg", quality=95)
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
