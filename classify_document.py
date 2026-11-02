#!/usr/bin/env python3
"""Classify extracted document text with a local RoBERTa NLI model."""

import argparse
import json
import re
from pathlib import Path


DEFAULT_MODEL = "cross-encoder/nli-distilroberta-base"
CANDIDATE_LABELS = ("tax invoice", "insurance policy", "other document")
MIN_SCORE = 0.70
MIN_MARGIN = 0.20


def document_text(archive: dict, max_words: int = 100) -> tuple[str, list[str]]:
    """Use the first-page heading and opening fields to identify document type."""
    lines = []
    sources = []
    ocr_pages = archive.get("rapidocr_layers", {}).get("pages", [])
    if ocr_pages:
        lines.extend(
            line.get("text", "")
            for line in ocr_pages[0].get("full_page_lines", [])
            if line.get("confidence", 0) >= 0.35
        )
        sources.append("rapidocr")

    document = archive.get("document", {})
    docling_lines = []
    for item in document.get("texts", []):
        provenance = item.get("prov", [])
        if provenance and provenance[0].get("page_no") != 1:
            continue
        docling_lines.append(item.get("text", ""))
    for table in document.get("tables", []):
        provenance = table.get("prov", [])
        if provenance and provenance[0].get("page_no") != 1:
            continue
        docling_lines.extend(
            cell.get("text", "")
            for cell in table.get("data", {}).get("table_cells", [])
        )
    if docling_lines:
        lines.extend(docling_lines)
        sources.append("docling")

    if not lines:
        native = archive.get("native_pdf_text", [])
        if native:
            lines.extend(native[0].get("text", "").splitlines())
            sources.append("native_pdf_text")

    unique_lines = []
    seen = set()
    for line in lines:
        normalized = re.sub(r"\W+", "", line.casefold())
        if len(normalized) < 3 or normalized in seen:
            continue
        seen.add(normalized)
        unique_lines.append(line.strip())

    words = "\n".join(unique_lines).split()
    return " ".join(words[:max_words]), sources


def classify_text(text: str, classifier, model_id: str, sources: list[str]) -> dict:
    if len(text.split()) < 12:
        return {
            "status": "insufficient_text",
            "decision": "needs_review",
            "model": model_id,
            "text_sources": sources,
            "words_used": len(text.split()),
            "note": "Too little extracted text for a model decision.",
        }

    result = classifier(
        text,
        candidate_labels=list(CANDIDATE_LABELS),
        hypothesis_template="This document is a {}.",
        multi_label=False,
    )
    scores = {label: float(score) for label, score in zip(result["labels"], result["scores"])}
    if set(scores) != set(CANDIDATE_LABELS):
        raise ValueError("Model did not return all document type labels")
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    label, score = ranked[0]
    margin = score - ranked[1][1]
    decision = label.replace(" ", "_") if score >= MIN_SCORE and margin >= MIN_MARGIN else "needs_review"
    return {
        "status": "classified",
        "decision": decision,
        "model": model_id,
        "model_score": round(score, 4),
        "top_label": label,
        "scores": {name: round(scores[name], 4) for name in CANDIDATE_LABELS},
        "hypothesis_template": "This document is a {}.",
        "text_sources": sources,
        "words_used": len(text.split()),
        "review_thresholds": {"minimum_top_score": MIN_SCORE, "minimum_margin": MIN_MARGIN},
        "note": "Relative zero-shot model scores across three document types, not calibrated probabilities. Review critical decisions.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Docling or enhanced OCR JSON")
    parser.add_argument("--output", required=True, type=Path, help="New JSON containing classification")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"input does not exist: {args.input}")
    if args.input.resolve() == args.output.resolve():
        parser.error("output must differ from input to preserve the original extraction")

    archive = json.loads(args.input.read_text(encoding="utf-8"))
    text, sources = document_text(archive)
    if len(text.split()) >= 12:
        try:
            from transformers import pipeline

            classifier = pipeline("zero-shot-classification", model=args.model, device="cpu")
        except (OSError, ValueError) as error:
            parser.exit(2, f"Could not load {args.model}: {error}\nDownload the model on a network-enabled machine, then rerun this command.\n")
    else:
        classifier = None

    archive["classification"] = classify_text(text, classifier, args.model, sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    result = archive["classification"]
    print(f"Saved {args.output} ({result['decision']})")


if __name__ == "__main__":
    main()
