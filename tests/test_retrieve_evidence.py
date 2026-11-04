"""Question-directed retrieval on reconstructed blocks, without OCR inference."""

import tempfile
import unittest
from pathlib import Path

import yaml

from retrieve_evidence import alias_match, load_fields, retrieve, retrieve_field


def block(number, text, box, section="Vehicle", page=1, table=None, cell=None, confidence=.95):
    return {"block_id": f"block_{number:04d}", "text": text, "page": page,
            "bbox_pixel": box, "confidence": confidence,
            "section_classification": {"primary_section": section, "status": "classified" if section else "unknown"},
            "parent_table_id": table, "parent_cell_id": cell,
            "layout_region_ids": [f"{page}-{number}"], "ocr_line_ids": [f"p{page:03d}_ocr_{number:04d}"],
            "docling_refs": [f"#/texts/{number}"], "native_pdf_refs": []}


def page(number, blocks):
    return {"page": number, "width_px": 800, "height_px": 1100, "blocks": blocks}


def field(**changes):
    return {"field": "engine_number", "question": "What is the engine number?", "aliases": ["Engine No", "Engine Number"],
            "data_type": "identifier", "preferred_sections": ["Vehicle"],
            "fallback_sections": ["Unknown", "*"], "max_candidates": 3,
            "normalization": ["trim", "collapse_whitespace", "uppercase", "remove_spaces"],
            "required": True, **changes}


class RetrievalTests(unittest.TestCase):
    def test_alias_and_inline_value(self):
        self.assertEqual(alias_match("Engine No: G4FG123456", ["Engine No"])[2], "exact")
        got = retrieve_field(field(), [page(1, [block(1, "Engine No: G4FG123456", [20, 20, 300, 45])])])
        self.assertEqual(got["candidates"][0]["normalized_value"], "G4FG123456")
        self.assertEqual(got["candidates"][0]["relationship"], "same_block")

    def test_label_value_same_row_and_provenance(self):
        blocks = [block(1, "Engine Number", [20, 100, 160, 120], table="1-1", cell="1-2"),
                  block(2, "G4FG 123456", [180, 100, 310, 120], table="1-1", cell="1-3")]
        got = retrieve_field(field(), [page(1, blocks)])
        c = got["candidates"][0]
        self.assertEqual(c["relationship"], "same_row")
        self.assertEqual(c["source_ocr_line_ids"], ["p001_ocr_0001", "p001_ocr_0002"])
        self.assertEqual(c["source_region_ids"], ["1-1", "1-2"])
        self.assertEqual(c["parent_table_ids"], ["1-1"])

    def test_same_column_and_soft_global_fallback(self):
        blocks = [block(1, "Engine No", [20, 100, 160, 120], section="Policy"),
                  block(2, "G4FG123456", [20, 135, 180, 155], section=None)]
        got = retrieve_field(field(), [page(1, blocks)])
        self.assertEqual(got["candidates"][0]["relationship"], "same_column")
        self.assertTrue(got["fallback_used"])
        self.assertEqual(got["candidates"][0]["section"], "Policy")

    def test_preferred_section_ranks_above_equivalent_fallback(self):
        blocks = [block(1, "Engine No: G4FG123456", [20, 20, 200, 40], "Vehicle"),
                  block(2, "Engine No: G4FG999999", [20, 80, 200, 100], "Policy")]
        got = retrieve_field(field(), [page(1, blocks)])
        self.assertEqual(got["candidates"][0]["section"], "Vehicle")
        self.assertEqual(len(got["candidates"]), 2)
        self.assertTrue(got["conflicting_values"])

    def test_previous_value_is_preserved_but_ranked_lower(self):
        blocks = [block(1, "Engine No", [20, 20, 140, 40]),
                  block(2, "G4FG123456", [160, 20, 290, 40]),
                  block(3, "Previous Engine No", [20, 80, 140, 100]),
                  block(4, "G4FG999999", [160, 80, 290, 100])]
        got = retrieve_field(field(), [page(1, blocks)])
        self.assertEqual(got["candidates"][0]["associated_value"], "G4FG123456")
        self.assertTrue(any(c["temporal_role"] == "previous" for c in got["candidates"]))

    def test_missing_and_low_confidence(self):
        got = retrieve_field(field(), [page(1, [block(1, "Engine No: G4FG123456", [0, 0, 200, 20], confidence=.2)])])
        self.assertEqual(got["status"], "not_found")

    def test_multi_page_and_duplicate_prevention(self):
        blocks = [block(1, "Engine No", [20, 100, 150, 120], page=2),
                  block(2, "G4FG123456", [180, 100, 300, 120], page=2)]
        got = retrieve_field(field(), [page(1, []), page(2, blocks)])
        self.assertEqual(got["candidates"][0]["page"], 2)
        self.assertEqual(len({(c["page"], tuple(c["evidence_block_ids"])) for c in got["candidates"]}), len(got["candidates"]))

    def test_config_drives_arbitrary_field(self):
        config = {"sections": [{"name": "Custom", "fields": [{**field(), "field": "custom_id",
                    "preferred_sections": ["Custom"]}]}]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "questions.yaml"
            path.write_text(yaml.safe_dump(config))
            self.assertEqual(load_fields(path)[0]["field"], "custom_id")
            result = retrieve({"pages": [page(1, [block(1, "Engine No: G4FG123456", [0, 0, 200, 20], "Custom")])]}, path)
            self.assertEqual(result["fields"][0]["candidates"][0]["section"], "Custom")

    def test_optional_semantic_signal_is_recorded(self):
        got = retrieve_field(field(), [page(1, [block(1, "Engine No: G4FG123456", [0, 0, 200, 20])])],
                             semantic_scorer=lambda question, passage: .8)
        self.assertEqual(got["candidates"][0]["score_components"]["semantic_similarity"], .8)

    def test_rejected_candidates_keep_provenance(self):
        config = field()
        config["max_candidates"] = 1
        blocks = [block(1, "Engine No: G4FG123456", [10, 10, 200, 30]),
                  block(2, "Engine No: G4FG999999", [10, 60, 200, 80])]
        got = retrieve_field(config, [page(1, blocks)])
        self.assertEqual(len(got["candidates"]), 1)
        self.assertEqual(got["rejected_candidates"][0]["source_ocr_line_ids"], ["p001_ocr_0002"])
        self.assertEqual(got["rejected_candidates"][0]["reason"], "outside_candidate_limit")


if __name__ == "__main__":
    unittest.main()
