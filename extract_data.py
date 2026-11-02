#!/usr/bin/env python3
"""Extract text and document structure from a PDF or image into JSON."""

import argparse
import json
from importlib.metadata import version
from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
IMAGE_PAYLOAD_KEYS = {"image", "image_ref", "images", "bytes_base64", "base64"}


def is_embedded_asset(value):
    return isinstance(value, str) and value.startswith("data:") and ";base64," in value[:100]


def strip_image_payloads(value):
    """Keep text, structure, captions and coordinates, but drop image assets."""
    if isinstance(value, dict):
        return {
            key: strip_image_payloads(item)
            for key, item in value.items()
            if key.lower() not in IMAGE_PAYLOAD_KEYS
            and not is_embedded_asset(item)
        }
    if isinstance(value, list):
        return [strip_image_payloads(item) for item in value if not is_embedded_asset(item)]
    return value


def native_pdf_text(path):
    """Keep the PDF's own words alongside Docling's interpretation."""
    import pymupdf

    pages = []
    with pymupdf.open(path) as pdf:
        for index, page in enumerate(pdf):
            words = page.get_text("words")
            pages.append({
                "page": index + 1,
                "width_pt": page.rect.width,
                "height_pt": page.rect.height,
                "text": page.get_text("text"),
                "words": [
                    {
                        "text": word[4],
                        "bbox_pdf": list(word[:4]),
                        "block": word[5],
                        "line": word[6],
                        "word": word[7],
                    }
                    for word in words
                ],
            })
    return pages


def extract(path, force_ocr=False):
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import (
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
    )

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    options.generate_page_images = False
    options.generate_picture_images = False
    options.generate_table_images = False
    options.extract_form_fields = True
    if force_ocr:
        options.ocr_options.force_full_page_ocr = True

    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
        },
    )
    result = converter.convert(path)
    status = result.status.value
    errors = [item.model_dump(mode="json") for item in result.errors]
    document = strip_image_payloads(result.document.export_to_dict())
    return status, errors, document, status == ConversionStatus.SUCCESS.value


def build_archive(path, force_ocr=False):
    import pymupdf

    status, errors, document, complete = extract(path, force_ocr=force_ocr)
    native = native_pdf_text(path) if path.suffix.lower() == ".pdf" else []
    with pymupdf.open(path) as source_doc:
        source_page_count = source_doc.page_count
    extracted_page_count = len(document.get("pages", {}))
    page_count_matches = source_page_count == extracted_page_count
    return {
        "schema": "pdf-layout-builder/docling-extraction/v1",
        "extraction": {
            "engine": "docling",
            "version": version("docling"),
            "status": status,
            "errors": errors,
            "force_full_page_ocr": force_ocr,
            "complete_conversion": complete and page_count_matches,
        },
        "audit": {
            "source_page_count": source_page_count,
            "docling_page_count": extracted_page_count,
            "page_count_matches": page_count_matches,
            "native_pdf_word_count": sum(len(page["words"]) for page in native),
        },
        "document": document,
        "native_pdf_text": native,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force-ocr", action="store_true",
                        help="OCR entire pages even when a PDF has selectable text")
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"input does not exist: {args.input}")
    if args.input.suffix.lower() not in IMAGE_SUFFIXES | {".pdf"}:
        parser.error("input must be a PDF, JPEG, PNG, or TIFF")
    if args.output.suffix.lower() != ".json":
        parser.error("output must have a .json extension")
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must be different files")

    archive = build_archive(args.input, force_ocr=args.force_ocr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(args.output)
    status = archive["extraction"]["status"]
    print(f"Saved {args.output} (Docling status: {status})")
    if not archive["extraction"]["complete_conversion"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
