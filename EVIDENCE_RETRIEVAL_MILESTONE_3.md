# Milestone 3: question-directed evidence retrieval

## Architecture and files

`section_classified.json` → `retrieve_evidence.py` → `evidence_manifest.json` → review UI. The retrieval engine reads all 21 field definitions from `insurance_policy_questions.yaml`; it contains no individual field IDs. YAML now supplies aliases, type, preferred/fallback sections, maximum candidates, normalization, and required status. It also supports optional value strategy, exclusion phrases, and value cues.

Each field searches all pages and sections. Section classification changes rank but never prevents a match. Candidate evidence combines an alias anchor and its own or nearby value block. The rank uses exact/fuzzy aliases, row/column geometry, table/cell relationships, OCR confidence, value type, question word overlap, section preference, and a penalty for previous-policy context. A stacked-label rule handles engine and chassis values sharing one narrow column. Duplicate evidence is removed; selected and rejected candidates retain block, OCR, layout, Docling, and native PDF references. The output is evidence for later answer generation; it is not an extracted final answer.

The optional `--semantic-rerank` uses the existing cached NLI RoBERTa model as a small shortlist relevance signal. It is off in the normal upload flow because it did not improve the measured ranking on these two scans. No vector database, Qwen, or new OCR was added.

Files added: `retrieve_evidence.py`, `evaluate_evidence.py`, `tests/test_retrieve_evidence.py`, `tests/fixtures/evidence_ground_truth.json`, `audits/evidence_m3_results.json`, `audits/evidence_m3_sample.json`. Files updated: `insurance_policy_questions.yaml`, `app.py`, `build_viewer_data.py`, `index.html`, `viewer.js`, `viewer.css`, and `README.md`. No commit was made.

## Manifest example

The complete actual records are in `audits/evidence_m3_sample.json`. One candidate from the ICICI Lombard policy:

```json
{
  "field": "engine_number",
  "question": "What is the engine number of the vehicle?",
  "preferred_section": "Vehicle",
  "status": "candidates_found",
  "candidates": [{
    "rank": 1,
    "block_id": "block_0060",
    "label_block_id": "block_0050",
    "associated_value": "17670",
    "page": 1,
    "source_ocr_line_ids": ["p001_ocr_0057", "p001_ocr_0067"],
    "relationship": "same_column"
  }]
}
```

The sample above is shortened for readability. The full candidate includes its original text, box, region IDs, score components, section, parent table/cell IDs, normalization, and fallback status.

## Ground-truth evaluation

`tests/fixtures/evidence_ground_truth.json` records all 21 fields on each of two visually reviewed real policies (42 field annotations). For each field it records document presence, expected normalized value, acceptable evidence block and OCR line IDs, page, and whether a conflicting value appears. The ICICI Lombard scan and low-resolution Oriental scan have different layouts. Three printed fields lacked reliable OCR evidence; retrieval recall uses the **25 fields with identifiable OCR evidence** as its denominator. Extraction accuracy is not measured.

| Metric | Result |
| --- | ---: |
| Evidence Recall@1 | 0.920 (23/25) |
| Evidence Recall@3 | 0.920 (23/25) |
| Mean reciprocal rank | 0.920 |
| Evidence precision | 0.828 (24/29 selected candidates) |
| False candidate rate | 0.172 (5/29) |
| Field retrieval coverage | 0.821 (23/28 present fields have a candidate) |
| Abstention accuracy | 1.000 (14/14 absent fields returned not_found) |
| Average selected evidence bundle | 0.690 candidates per configured field |

The two missed retrievable fields were the issue date and insured address on the low-resolution Oriental scan. The date has no nearby recognized label, and the address label/value relationship was fragmented. This is a small selected evaluation set and does not establish production accuracy. Full per-field outcomes are in `audits/evidence_m3_results.json`.

## Latency and review

The default retriever took **0.642 s** for the 129-block ICICI policy and **0.570 s** for the 105-block Oriental policy. The outputs found candidates for 14/21 and 9/21 fields respectively. The optional semantic reranker preserved the same evaluation metrics and added roughly **3.9 s model initialization** plus retrieval times of **1.363 s** and **1.174 s**; it remains off by default.

The upload pipeline now writes `evidence_manifest.json` after `section_classified.json`. The Evidence review panel shows question, preferred section, candidate value and score, alternatives, rejected candidates, conflict and fallback indicators. Clicking evidence highlights its source OCR lines on the page. A real policy viewer snapshot was built with all 21 evidence fields. The existing canvas displays page 1; later-page candidate coordinates remain in the manifest and are explained in the detail pane.

Verification: `.venv/bin/python -m unittest discover -s tests -q` **46 passed**; `node --check viewer.js`, Python compilation, and `git diff --check` passed.

## Limitations

Some OCR text is incomplete or corrupted, especially on the smaller scan. The retriever deliberately returns `not_found` when it cannot anchor a field label and value reliably. Five of 29 selected candidates in the manual set were false alternatives, often nearby values in dense tables. No policy in this set contained a nominee section. The evaluation covers two policy layouts and should be extended before changing rank weights. The optional NLI signal is a relevance cue, not a calibrated probability. The review canvas needs multi-page navigation to highlight later-page evidence directly.
