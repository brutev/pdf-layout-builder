#!/usr/bin/env python3
"""Score evidence ranking against manually reviewed policy field evidence."""

import argparse
import json
from pathlib import Path


def correct(candidate, annotation):
    blocks = set(candidate["evidence_block_ids"])
    lines = set(candidate["source_ocr_line_ids"])
    return any(candidate["page"] == alt["page"] and
               set(alt["block_ids"]) <= blocks and set(alt["ocr_line_ids"]) <= lines
               for alt in annotation["evidence_alternatives"])


def evaluate(truth, manifests):
    cases = []
    for name, document in truth["documents"].items():
        got = {field["field"]: field for field in manifests[name]["fields"]}
        for annotation in document["fields"]:
            field = got[annotation["field"]]
            hits = [correct(candidate, annotation) for candidate in field["candidates"]]
            first = next((index + 1 for index, hit in enumerate(hits) if hit), None)
            cases.append({"document": name, "field": annotation["field"],
                          "document_present": annotation["document_present"],
                          "retrievable": bool(annotation["evidence_alternatives"]),
                          "first_correct_rank": first, "candidate_count": len(hits),
                          "correct_candidates": sum(hits), "false_candidates": len(hits) - sum(hits),
                          "abstained": field["status"] == "not_found",
                          "conflict_expected": annotation["conflicting_values_present"],
                          "conflict_predicted": field["conflicting_values"]})
    retrievable = [case for case in cases if case["retrievable"]]
    present = [case for case in cases if case["document_present"]]
    absent = [case for case in cases if not case["document_present"]]
    total_candidates = sum(case["candidate_count"] for case in cases)
    correct_candidates = sum(case["correct_candidates"] for case in cases)
    return {"field_annotations": len(cases), "retrievable_fields": len(retrievable),
            "document_present_fields": len(present), "document_absent_fields": len(absent),
            "evidence_recall_at_1": sum(case["first_correct_rank"] == 1 for case in retrievable) / len(retrievable) if retrievable else None,
            "evidence_recall_at_3": sum(case["first_correct_rank"] is not None and case["first_correct_rank"] <= 3 for case in retrievable) / len(retrievable) if retrievable else None,
            "mean_reciprocal_rank": sum(1 / case["first_correct_rank"] for case in retrievable if case["first_correct_rank"]) / len(retrievable) if retrievable else None,
            "evidence_precision": correct_candidates / total_candidates if total_candidates else None,
            "false_candidate_rate": (total_candidates - correct_candidates) / total_candidates if total_candidates else None,
            "field_retrieval_coverage": sum(not case["abstained"] for case in present) / len(present) if present else None,
            "abstention_accuracy": sum(case["abstained"] for case in absent) / len(absent) if absent else None,
            "average_evidence_bundle_size": total_candidates / len(cases) if cases else 0,
            "total_candidates": total_candidates,
            "latency_seconds": {name: manifest["statistics"]["retrieval_seconds"] for name, manifest in manifests.items()},
            "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, default=Path(__file__).parent / "tests/fixtures/evidence_ground_truth.json")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--small", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(json.loads(args.truth.read_text()),
                      {name: json.loads(path.read_text()) for name, path in (("policy", args.policy), ("small", args.small))})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{result['field_annotations']} field annotations; Recall@1 {result['evidence_recall_at_1']:.3f}, "
          f"Recall@3 {result['evidence_recall_at_3']:.3f}, precision {result['evidence_precision']:.3f}")


if __name__ == "__main__":
    main()
