#!/usr/bin/env python3
"""Rank compact, traceable evidence bundles for YAML-configured questions."""

import argparse
from difflib import SequenceMatcher
import json
import re
import time
from pathlib import Path

import yaml


SCHEMA = "pdf-layout-builder/evidence-manifest/v1"
TYPES = {"text", "name", "address", "identifier", "date", "amount"}
NORMALIZERS = {"trim", "collapse_whitespace", "uppercase", "remove_spaces"}
STOPWORDS = {"what", "is", "the", "of", "a", "an", "to", "be", "with", "person", "vehicle", "insurance", "policy"}
DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b")
AMOUNT = re.compile(r"(?<!\w)(?:₹|rs\.?\s*)?\d[\d,]*(?:\.\d{1,2})?(?!\w)", re.I)


def normalized(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def load_fields(path):
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("sections"), list):
        raise ValueError("question config needs sections")
    names = {item["name"] for item in config["sections"]}
    seen = set()
    fields = []
    for section in config["sections"]:
        for field in section["fields"]:
            key = field["field"]
            if key in seen:
                raise ValueError(f"duplicate field: {key}")
            seen.add(key)
            if not field.get("question") or not field.get("aliases"):
                raise ValueError(f"{key} needs a question and aliases")
            if not isinstance(field["aliases"], list) or any(not isinstance(alias, str) or not alias.strip() for alias in field["aliases"]):
                raise ValueError(f"{key} has invalid aliases")
            if not isinstance(field.get("required"), bool):
                raise ValueError(f"{key} needs a boolean required status")
            if field.get("value_strategy", "after_alias") not in {"after_alias", "full_block_if_no_colon"}:
                raise ValueError(f"{key} has invalid value_strategy")
            if field.get("data_type") not in TYPES:
                raise ValueError(f"{key} has invalid data_type")
            if not set(field.get("normalization", [])) <= NORMALIZERS:
                raise ValueError(f"{key} has invalid normalization")
            if not isinstance(field.get("max_candidates"), int) or field["max_candidates"] < 1:
                raise ValueError(f"{key} needs positive max_candidates")
            preferred = field.get("preferred_sections", [section["name"]])
            fallback = field.get("fallback_sections", ["Unknown", "*"])
            if not preferred or not set(preferred) <= names or not set(fallback) <= names | {"Unknown", "*"}:
                raise ValueError(f"{key} has invalid sections")
            fields.append({**field, "preferred_sections": preferred, "fallback_sections": fallback,
                           "section": section["name"]})
    return fields


def alias_match(text, aliases):
    """Return best exact or OCR-tolerant alias with original-text span."""
    best = None
    canonical = normalized(text)
    words = canonical.split()
    for alias in aliases:
        parts = normalized(alias).split()
        if not parts:
            continue
        pattern = r"(?<!\w)" + r"[\W_]*".join(map(re.escape, parts)) + r"(?!\w)"
        match = re.search(pattern, text, re.I)
        if match:
            candidate = (1.0, alias, "exact", match.span())
        elif len(parts) >= 2 and len(words) <= 8:
            best_ratio = 0.0
            for width in range(max(1, len(parts) - 1), min(len(words), len(parts) + 1) + 1):
                for start in range(len(words) - width + 1):
                    ratio = SequenceMatcher(None, " ".join(parts), " ".join(words[start:start + width])).ratio()
                    best_ratio = max(best_ratio, ratio)
            candidate = (round(best_ratio, 3), alias, "fuzzy", None) if best_ratio >= .88 else None
        else:
            candidate = None
        if candidate and (best is None or (candidate[0], len(alias)) > (best[0], len(best[1]))):
            best = candidate
    return best


def normalize_value(value, rules):
    result = value
    for rule in rules:
        if rule == "trim":
            result = result.strip(" :-.,\t\n")
        elif rule == "collapse_whitespace":
            result = " ".join(result.split())
        elif rule == "uppercase":
            result = result.upper()
        elif rule == "remove_spaces":
            result = result.replace(" ", "")
    return result


def plausible(value, data_type):
    value = value.strip()
    if not value or len(value) > (240 if data_type == "address" else 110):
        return False
    if data_type == "date":
        return bool(DATE.search(value))
    if data_type == "amount":
        return bool(AMOUNT.search(value)) and "%" not in value and len(value.split()) <= 8
    if data_type == "identifier":
        return bool(re.search(r"\d", value) and re.search(r"[A-Za-z0-9]{4,}", value)) and len(value.split()) <= 5
    return len(re.findall(r"[A-Za-z]", value)) >= 3


def inline_value(block, match, field):
    text = block["text"].strip()
    if match[3] is None:
        return None
    start, end = match[3]
    if field.get("value_strategy") == "full_block_if_no_colon" and ":" not in text:
        return text if plausible(text, field["data_type"]) else None
    tail = text[end:].strip(" :-.,\t\n")
    if not tail:
        return None
    if plausible(tail, field["data_type"]) and (":" in text[end - 1:end + 3] or ":" in text[:end] or
                                                 field["data_type"] in {"identifier", "date", "amount"}):
        return tail
    return None


def box_relation(anchor, value, width, height):
    a, b = anchor["bbox_pixel"], value["bbox_pixel"]
    if anchor["page"] != value["page"]:
        return 0.0, "different_page"
    ay, by = (a[1] + a[3]) / 2, (b[1] + b[3]) / 2
    ah, bh = max(1, a[3] - a[1]), max(1, b[3] - b[1])
    yoverlap = max(0, min(a[3], b[3]) - max(a[1], b[1])) / min(ah, bh)
    xoverlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) / max(1, min(a[2] - a[0], b[2] - b[0]))
    same_table = bool(anchor.get("parent_table_id") and anchor.get("parent_table_id") == value.get("parent_table_id"))
    if yoverlap >= .45 and max(ah, bh) / min(ah, bh) <= 2.2 and b[0] >= a[2] - 5:
        gap = max(0, b[0] - a[2])
        if gap <= width * (.85 if same_table else .34):
            return max(.68 if same_table else .2, 1 - gap / (width * (.95 if same_table else .42))), "same_row"
    gap = b[1] - a[3]
    if -3 <= gap <= min(65, max(35, 2 * max(ah, bh))) and xoverlap >= .25:
        return max(.2, .85 - max(0, gap) / max(70, height * .10)), "same_column"
    return 0.0, "unrelated"


def source_ids(blocks, key):
    return list(dict.fromkeys(item for block in blocks for item in block.get(key, [])))


def union_box(blocks):
    boxes = [b["bbox_pixel"] for b in blocks]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def section_score(block, field):
    decision = block.get("section_classification", {})
    section = decision.get("primary_section") or "Unknown"
    if section in field["preferred_sections"]:
        return 1.0, section
    if section == "Unknown":
        return .5, section
    if section in field["fallback_sections"] or "*" in field["fallback_sections"]:
        return .25, section
    return .0, section


def relevance(question, text):
    q = set(normalized(question).split()) - STOPWORDS
    t = set(normalized(text).split())
    return len(q & t) / len(q) if q else 0.0


def build_candidate(anchor, value_block, value, field, match, geometry, relation, previous_context=False):
    blocks = [anchor] if value_block is anchor else [anchor, value_block]
    section_strength, section = section_score(anchor, field)
    confidence = min(b.get("confidence", 0) for b in blocks)
    type_strength = 1.0 if plausible(value, field["data_type"]) else 0.0
    previous = previous_context or bool(re.search(r"\b(?:previous|prior|old|pre\.?policy|pree pulicy|renewal)\b", anchor["text"], re.I))
    components = {"alias_match": match[0], "geometric_match": round(geometry, 3),
                  "section_match": section_strength, "value_type": type_strength,
                  "ocr_confidence": round(confidence, 3),
                  "question_overlap": round(relevance(field["question"], anchor["text"]), 3),
                  "previous_policy_penalty": .35 if previous else 0.0}
    score = (.36 * components["alias_match"] + .25 * geometry + .13 * section_strength +
             .16 * type_strength + .06 * confidence + .04 * components["question_overlap"] -
             components["previous_policy_penalty"])
    return {"block_id": value_block["block_id"], "label_block_id": anchor["block_id"],
            "evidence_block_ids": [b["block_id"] for b in blocks], "text": "\n".join(b["text"] for b in blocks),
            "section": section, "matched_alias": match[1], "alias_method": match[2],
            "associated_value": value, "normalized_value": normalize_value(value, field["normalization"]),
            "page": anchor["page"], "bbox_pixel": union_box(blocks),
            "source_region_ids": source_ids(blocks, "layout_region_ids"),
            "source_ocr_line_ids": source_ids(blocks, "ocr_line_ids"),
            "parent_cell_ids": list(dict.fromkeys(
                b["parent_cell_id"] for b in blocks if b.get("parent_cell_id"))),
            "parent_table_ids": list(dict.fromkeys(b["parent_table_id"] for b in blocks if b.get("parent_table_id"))),
            "docling_refs": source_ids(blocks, "docling_refs"),
            "native_pdf_refs": source_ids(blocks, "native_pdf_refs"),
            "relationship": relation, "temporal_role": "previous" if previous else "current_or_unspecified",
            "score": round(max(0, min(1, score)), 4), "score_components": components,
            "fallback": section not in field["preferred_sections"]}


def retrieve_field(field, pages, label_ids=None, semantic_scorer=None):
    blocks = [b for page in pages for b in page["blocks"]]
    page_sizes = {page["page"]: (page["width_px"], page["height_px"]) for page in pages}
    candidates = []
    matches = {id(block): alias_match(block.get("text", ""), field["aliases"]) for block in blocks}
    def previous_near(value_block, anchor):
        box = value_block["bbox_pixel"]
        for other in blocks:
            if other is anchor or other is value_block or other["page"] != value_block["page"]:
                continue
            if not re.search(r"\b(?:previous|prior|pre\.?policy|pree pulicy)\b", other["text"], re.I):
                continue
            other_box = other["bbox_pixel"]
            if other_box[0] < box[0] and 0 <= box[0] - other_box[2] <= 220 and abs(
                    (box[1] + box[3]) / 2 - (other_box[1] + other_box[3]) / 2) <= 22:
                return True
        return False
    for anchor in blocks:
        if anchor.get("confidence", 0) < .45 or not anchor.get("text") or len(anchor["text"].split()) > 16:
            continue
        match = matches[id(anchor)]
        if not match:
            continue
        if any(term in normalized(anchor["text"]) for term in field.get("exclude_aliases", [])):
            continue
        if field.get("required_context_terms") and not any(
                normalized(term) in normalized(anchor["text"]) for term in field["required_context_terms"]):
            continue
        own = inline_value(anchor, match, field)
        if own and field.get("value_cues") and not any(cue in normalized(own) for cue in field["value_cues"]):
            own = None
        if own:
            candidates.append(build_candidate(anchor, anchor, own, field, match, 1.0, "same_block"))
            continue
        # Preserve near alternatives, including conflicting values in parallel table columns.
        width, height = page_sizes[anchor["page"]]
        nearby = []
        for value_block in blocks:
            if value_block is anchor or value_block["page"] != anchor["page"] or value_block.get("confidence", 0) < .45:
                continue
            if matches[id(value_block)] or (label_ids and id(value_block) in label_ids):
                continue
            value_text = value_block["text"]
            if len(value_text.split()) <= 6 and (
                re.search(r"\b(?:no|number|name|address|code|category|contact|mfg|model|make|type|acc|idv|value|premium|tax|details|capacity|capaily|cap|seat|year|igst)\b", value_text, re.I)
                or value_text.rstrip().endswith(":")
            ):
                continue
            geometry, relation = box_relation(anchor, value_block, width, height)
            if geometry < .4:
                continue
            if not plausible(value_block["text"], field["data_type"]):
                continue
            if field.get("value_cues") and not any(cue in normalized(value_text) for cue in field["value_cues"]):
                continue
            same_cell = bool(anchor.get("parent_cell_id") and anchor.get("parent_cell_id") == value_block.get("parent_cell_id"))
            same_table = bool(anchor.get("parent_table_id") and anchor.get("parent_table_id") == value_block.get("parent_table_id"))
            if same_cell:
                geometry = min(1, geometry + .12)
            elif same_table:
                geometry = min(1, geometry + .06)
            nearby.append((geometry, value_block, relation))
        nearby.sort(key=lambda item: (-item[0], item[1]["block_id"]))
        stacked_labels = sorted((b for b in blocks if label_ids and id(b) in label_ids and
                                 b["page"] == anchor["page"] and abs(b["bbox_pixel"][1] - anchor["bbox_pixel"][1]) <= 25 and
                                 max(0, min(b["bbox_pixel"][2], anchor["bbox_pixel"][2]) -
                                     max(b["bbox_pixel"][0], anchor["bbox_pixel"][0])) >=
                                 .5 * min(b["bbox_pixel"][2] - b["bbox_pixel"][0],
                                          anchor["bbox_pixel"][2] - anchor["bbox_pixel"][0])),
                                key=lambda b: b["bbox_pixel"][1])
        stacked_values = sorted((b for _, b, relation in nearby if relation == "same_column"),
                                key=lambda b: b["bbox_pixel"][1])
        if len(stacked_labels) >= 2 and len(stacked_values) >= 2:
            label_rank = stacked_labels.index(anchor)
            adjusted = []
            for geometry, value_block, relation in nearby:
                if relation == "same_column" and value_block in stacked_values:
                    value_rank = stacked_values.index(value_block)
                    geometry = min(1, geometry + .12) if label_rank == value_rank else max(.4, geometry - .22)
                adjusted.append((geometry, value_block, relation))
            nearby = sorted(adjusted, key=lambda item: (-item[0], item[1]["block_id"]))
        for geometry, value_block, relation in nearby[:5]:
            candidates.append(build_candidate(anchor, value_block, value_block["text"], field, match,
                                              geometry, relation, previous_near(value_block, anchor)))
    unique = {}
    for candidate in candidates:
        key = (candidate["page"], tuple(candidate["evidence_block_ids"]), candidate["normalized_value"])
        if key not in unique or candidate["score"] > unique[key]["score"]:
            unique[key] = candidate
    ranked = sorted(unique.values(), key=lambda item: (-item["score"], item["page"], item["block_id"]))
    if semantic_scorer:
        for candidate in ranked[:field["max_candidates"] + 2]:
            semantic = max(0.0, min(1.0, float(semantic_scorer(field["question"], candidate["text"]))))
            candidate["score_components"]["semantic_similarity"] = round(semantic, 4)
            candidate["score"] = round(max(0, min(1, candidate["score"] + .08 * (semantic - .5))), 4)
        ranked.sort(key=lambda item: (-item["score"], item["page"], item["block_id"]))
    # A preferred hit is sufficient only with a plausible value and a reliable score.
    preferred_sufficient = any(c["section"] in field["preferred_sections"] and c["score"] >= .70 for c in ranked)
    selected = ranked[:field["max_candidates"]]
    for rank, candidate in enumerate(selected, 1):
        candidate["rank"] = rank
    rejected = [{**c, "reason": "outside_candidate_limit"} for c in ranked[field["max_candidates"]:]]
    values = {c["normalized_value"] for c in selected if c["normalized_value"]}
    return {"field": field["field"], "question": field["question"],
            "data_type": field["data_type"], "required": field["required"],
            "preferred_section": field["preferred_sections"][0],
            "preferred_sections": field["preferred_sections"], "fallback_sections": field["fallback_sections"],
            "status": "candidates_found" if selected else "not_found", "candidates": selected,
            "rejected_candidates": rejected, "fallback_used": bool(selected and (not preferred_sufficient or selected[0]["fallback"])),
            "conflicting_values": len(values) > 1,
            "candidate_count_before_limit": len(ranked)}


def retrieve(archive, config_path, semantic_scorer=None):
    started = time.perf_counter()
    fields = load_fields(config_path)
    pages = archive["pages"]
    all_aliases = [field["aliases"] for field in fields]
    label_ids = set()
    for page in pages:
        for block in page["blocks"]:
            text = block.get("text", "")
            if 1 <= len(text.split()) <= 6 and any(character.isalpha() for character in text):
                if any(alias_match(text, aliases) for aliases in all_aliases):
                    label_ids.add(id(block))
    results = [retrieve_field(field, pages, label_ids, semantic_scorer) for field in fields]
    bundles = [len(item["candidates"]) for item in results]
    return {"schema": SCHEMA, "source_sha256": archive.get("source_sha256"),
            "source_classification_schema": archive.get("schema"), "fields": results,
            "statistics": {"fields": len(results), "found": sum(bool(n) for n in bundles),
                           "not_found": sum(not n for n in bundles),
                           "fallback_used": sum(item["fallback_used"] for item in results),
                           "conflicting": sum(item["conflicting_values"] for item in results),
                           "average_evidence_bundle_size": round(sum(bundles) / len(bundles), 3) if bundles else 0,
                           "retrieval_seconds": round(time.perf_counter() - started, 4)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="section_classified.json")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("insurance_policy_questions.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic-rerank", action="store_true", help="Optionally rerank shortlists using cached NLI RoBERTa")
    args = parser.parse_args()
    scorer = None
    semantic_status = "disabled"
    load_started = time.perf_counter()
    if args.semantic_rerank:
        try:
            from transformers import pipeline
            from classify_document import DEFAULT_MODEL
            classifier = pipeline("zero-shot-classification", model=DEFAULT_MODEL, device="cpu", local_files_only=True)
            def scorer(question, passage):
                result = classifier(passage, candidate_labels=[f"an answer to: {question}"],
                                    hypothesis_template="This document passage contains {}.", multi_label=True)
                return result["scores"][0]
            semantic_status = "available"
        except (ImportError, OSError, ValueError, TypeError) as error:
            semantic_status = "unavailable"
            print(f"Semantic reranker unavailable; using lexical and geometric evidence: {error}")
    load_seconds = time.perf_counter() - load_started
    result = retrieve(json.loads(args.input.read_text(encoding="utf-8")), args.config, scorer)
    result["statistics"]["semantic_model_status"] = semantic_status
    result["statistics"]["semantic_model_initialization_seconds"] = round(load_seconds, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Evidence retrieval: {result['statistics']['found']}/{result['statistics']['fields']} fields with candidates; "
          f"{result['statistics']['fallback_used']} used fallback; "
          f"{result['statistics']['retrieval_seconds']}s; saved {args.output}")


if __name__ == "__main__":
    main()
