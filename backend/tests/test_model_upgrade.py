"""Legacy model upgrades must preserve annotation anchors."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.scripts.upgrade_legacy_models import _add_anchored_equations, _append_missing_appendix, _check_compatibility, _embed_equation, _geometry_equation_anchor, _joined_raw_bbox, _merge_geometry, _text_equation_anchor


def model(*blocks):
    return {"sections": [{"blocks": list(blocks)}]}


class ModelUpgradeTests(unittest.TestCase):
    def test_rejects_changed_block_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "old block IDs disappeared"):
            _check_compatibility(model({"id": "old", "type": "paragraph"}),
                                 model({"id": "new", "type": "paragraph"}), [])

    def test_preserves_annotated_text_and_cross_block_order(self) -> None:
        old = model({"id": "a", "type": "paragraph", "text": "Alpha"},
                    {"id": "b", "type": "paragraph", "text": "Beta"})
        note = {"anchor": {"start": {"blockId": "a"}, "end": {"blockId": "b"}}}
        _check_compatibility(old, old, [note])
        with self.assertRaisesRegex(ValueError, "Existing block text changed"):
            _check_compatibility(old, model({"id": "a", "type": "paragraph", "text": "Changed"},
                                            {"id": "b", "type": "paragraph", "text": "Beta"}), [note])
        with self.assertRaisesRegex(ValueError, "Existing text block order changed"):
            _check_compatibility(old, model({"id": "b", "type": "paragraph", "text": "Beta"},
                                            {"id": "a", "type": "paragraph", "text": "Alpha"}), [note])

    def test_allows_new_blocks_when_old_blocks_are_unchanged(self) -> None:
        old = model({"id": "a", "type": "paragraph", "text": "Alpha"})
        new = model({"id": "a", "type": "paragraph", "text": "Alpha"},
                    {"id": "new", "type": "figure", "caption": "New figure"})
        _check_compatibility(old, new, [])

    def test_geometry_merge_keeps_legacy_text_and_ids(self) -> None:
        old = model({"id": "old-1", "type": "paragraph", "text": "Alpha", "page": 2},
                    {"id": "old-2", "type": "paragraph", "text": "Beta", "page": 2})
        new = model({"id": "shifted-1", "type": "paragraph", "text": "Alpha", "page": 2,
                     "bbox": {"x": 10, "y": 20, "width": 100, "height": 30}},
                    {"id": "shifted-2", "type": "paragraph", "text": "Changed", "page": 2,
                     "bbox": {"x": 10, "y": 60, "width": 100, "height": 30}})
        report = _merge_geometry(old, new)
        self.assertEqual(report["matched"], 1)
        self.assertEqual([block["id"] for block in old["sections"][0]["blocks"]], ["old-1", "old-2"])
        self.assertEqual([block["text"] for block in old["sections"][0]["blocks"]], ["Alpha", "Beta"])
        self.assertIsNotNone(old["sections"][0]["blocks"][0]["bbox"])
        self.assertNotIn("bbox", old["sections"][0]["blocks"][1])
        self.assertEqual([block["order"] for block in old["sections"][0]["blocks"]], [0, 1])

    def test_raw_docling_geometry_requires_unique_exact_text(self) -> None:
        old = model({"id": "p1", "type": "paragraph", "text": "A unique sentence.", "page": 2},
                    {"id": "p2", "type": "paragraph", "text": "Repeated", "page": 2})
        location = SimpleNamespace(page_no=2, bbox=object())
        items = [SimpleNamespace(text=text, prov=[location]) for text in
                 ("A unique sentence.", "Repeated", "Repeated")]
        doc = SimpleNamespace(iterate_items=lambda: ((item, 0) for item in items))
        with patch("backend.scripts.upgrade_legacy_models._block_bbox", return_value={"x": 1, "y": 2, "width": 3, "height": 4}):
            report = _merge_geometry(old, model(), doc, object())
        self.assertEqual(report["rawMatched"], 1)
        self.assertIsNotNone(old["sections"][0]["blocks"][0]["bbox"])
        self.assertNotIn("bbox", old["sections"][0]["blocks"][1])

    def test_geometry_merge_skips_duplicate_legacy_text(self) -> None:
        old = model({"id": "a", "type": "paragraph", "text": "Repeated", "page": 1},
                    {"id": "b", "type": "paragraph", "text": "Repeated", "page": 1})
        new = model({"id": "c", "type": "paragraph", "text": "Repeated", "page": 1,
                     "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}})
        report = _merge_geometry(old, new)
        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["missingBbox"], 2)

    def test_joined_raw_geometry_requires_unique_same_column_text(self) -> None:
        first = {"x": 320, "y": 100, "width": 210, "height": 15}
        second = {"x": 318, "y": 140, "width": 215, "height": 35}
        before = "The paragraph before the formula begins here"
        after = "The paragraph continues after the formula ends"
        parts = [(before, 2, first), (after, 2, second)]
        self.assertEqual(_joined_raw_bbox(f"{before} {after}", 2, parts),
                         {"x": 318, "y": 100, "width": 215, "height": 75})
        repeated = [*parts, (after, 2, {"x": 320, "y": 145, "width": 210, "height": 30})]
        self.assertIsNone(_joined_raw_bbox(f"{before} {after}", 2, repeated))
        other_column = [(parts[0][0], 2, first), (parts[1][0], 2, {"x": 20, "y": 140, "width": 215, "height": 35})]
        self.assertIsNone(_joined_raw_bbox(f"{before} {after}", 2, other_column))

    def test_equation_is_inserted_between_exact_neighbors(self) -> None:
        old = {"sections": [{"id": "method", "title": "Method", "type": "body", "blocks": [
            {"id": "p1", "type": "paragraph", "text": "Before", "page": 2},
            {"id": "p2", "type": "paragraph", "text": "After", "page": 2}]}]}
        equation = {"id": "equation-1", "type": "equation", "text": "", "page": 2,
                    "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}, "src": "/api/documents/abc/assets/equation-1.png"}
        new = {"sections": [{"id": "method", "title": "Method", "type": "body", "blocks": [
            {"id": "shifted-1", "type": "paragraph", "text": "Before", "page": 2},
            equation,
            {"id": "shifted-2", "type": "paragraph", "text": "After", "page": 2}]}]}
        assets, unplaced, reasons = _add_anchored_equations(old, new)
        self.assertEqual(assets, ["equation-1.png"])
        self.assertEqual(unplaced, 0)
        self.assertEqual(reasons, {})
        self.assertEqual([block["id"] for block in old["sections"][0]["blocks"]],
                         ["p1", "equation-1", "p2"])

    def test_equation_without_both_neighbors_is_skipped(self) -> None:
        old = model({"id": "p1", "type": "paragraph", "text": "After", "page": 2})
        new = model({"id": "eq", "type": "equation", "page": 2,
                     "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                     "src": "/api/documents/abc/assets/eq.png"},
                    {"id": "p2", "type": "paragraph", "text": "After", "page": 2})
        assets, unplaced, reasons = _add_anchored_equations(old, new)
        self.assertEqual(assets, [])
        self.assertEqual(unplaced, 1)
        self.assertEqual(reasons, {"incomplete-anchors": 1})

    def test_formula_crop_is_embedded_without_changing_merged_paragraph_text(self) -> None:
        before, after = "The method before the formula", "The explanation after the formula"
        paragraph = {"id": "old", "type": "paragraph", "page": 2, "text": f"{before} {after}",
                     "content": [{"type": "text", "text": before}, {"type": "text", "text": " "},
                                 {"type": "text", "text": after}]}
        old = model(paragraph)
        equation = {"id": "eq", "type": "equation", "page": 2,
                    "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                    "src": "/api/documents/abc/assets/eq.png", "number": 4}
        new = model({"type": "paragraph", "text": before, "page": 2}, equation,
                    {"type": "paragraph", "text": after, "page": 2})
        assets, unplaced, reasons = _add_anchored_equations(old, new)
        self.assertEqual((assets, unplaced, reasons), (["eq.png"], 0, {}))
        self.assertEqual(paragraph["text"], f"{before} {after}")
        self.assertEqual("".join(item.get("text", "") for item in paragraph["content"]), paragraph["text"])
        self.assertEqual(paragraph["content"][1]["src"], equation["src"])

    def test_ambiguous_merged_paragraph_does_not_embed_formula(self) -> None:
        before, after = "The method before the formula", "The explanation after the formula"
        paragraph = {"type": "paragraph", "page": 2, "text": f"{before} {after}"}
        old = model(dict(paragraph), dict(paragraph))
        equation = {"type": "equation", "page": 2, "src": "equation.png"}
        self.assertFalse(_embed_equation(old, [dict(paragraph, text=before), equation,
                                               dict(paragraph, text=after)], 1, equation))

    def test_geometry_anchor_finds_only_gap_between_adjacent_paragraphs(self) -> None:
        before = {"type": "paragraph", "page": 5,
                  "bbox": {"x": 108, "y": 380, "width": 396, "height": 54}}
        after = {"type": "paragraph", "page": 5,
                 "bbox": {"x": 108, "y": 520, "width": 228, "height": 107}}
        old = {"sections": [{"title": "3.3 Theoretical Analysis", "blocks": [before, after]}]}
        equation = {"type": "equation", "page": 5,
                    "bbox": {"x": 110, "y": 440, "width": 360, "height": 12}}
        self.assertEqual(_geometry_equation_anchor(old, equation, "3.3 Theoretical Analysis"), (0, 1))
        self.assertIsNone(_geometry_equation_anchor(old, equation, "Another section"))
        old["sections"][0]["blocks"].insert(1, {"type": "figure", "page": 5})
        self.assertIsNone(_geometry_equation_anchor(old, equation, "3.3 Theoretical Analysis"))

    def test_geometry_anchor_uses_same_column_figure_gap_and_renumbered_section(self) -> None:
        old = {"sections": [{"title": "5.1 Baseline Optimization", "blocks": [
            {"type": "figure", "page": 4, "bbox": {"x": 292, "y": 38, "width": 253, "height": 52}},
            {"type": "paragraph", "page": 4, "bbox": {"x": 293, "y": 175, "width": 252, "height": 69}},
        ]}]}
        equation = {"type": "equation", "page": 4,
                    "bbox": {"x": 382, "y": 149, "width": 72, "height": 23}}
        self.assertEqual(_geometry_equation_anchor(old, equation, "4.1 Baseline Optimization"), (0, 1))
        equation["bbox"]["x"] = 50
        self.assertIsNone(_geometry_equation_anchor(old, equation, "4.1 Baseline Optimization"))

    def test_geometry_anchor_can_append_before_next_section_in_same_column(self) -> None:
        old = {"sections": [
            {"title": "4.2 From Wiggles to the Sine Illusion", "blocks": [
                {"type": "paragraph", "page": 3, "bbox": {"x": 284, "y": 377, "width": 252, "height": 38}}]},
            {"title": "5 SineStream", "blocks": [
                {"type": "paragraph", "page": 3, "bbox": {"x": 283, "y": 509, "width": 252, "height": 50}}]},
        ]}
        equation = {"type": "equation", "page": 3,
                    "bbox": {"x": 330, "y": 420, "width": 204, "height": 55}}
        self.assertEqual(_geometry_equation_anchor(old, equation, "3.2 From Wiggles to the Sine Illusion"), (0, 1))

    def test_text_anchor_places_formula_before_merged_following_paragraph(self) -> None:
        before_text = "The explanation before the displayed formula"
        after_text = "The next paragraph starts with this explanation"
        old = {"sections": [{"title": "5.4 Matching", "blocks": [
            {"type": "paragraph", "page": 9, "text": before_text},
            {"type": "paragraph", "page": 9, "text": after_text + " and continues after another formula"},
        ]}]}
        new_blocks = [
            {"type": "paragraph", "page": 9, "text": before_text,
             "bbox": {"x": 80, "y": 167, "width": 450, "height": 57}},
            {"type": "equation", "page": 9,
             "bbox": {"x": 210, "y": 229, "width": 320, "height": 54}},
            {"type": "paragraph", "page": 9, "text": after_text,
             "bbox": {"x": 80, "y": 288, "width": 450, "height": 21}},
        ]
        self.assertEqual(_text_equation_anchor(old, new_blocks, 1, "5.4 Matching", []), (0, 1))
        new_blocks[1]["bbox"]["x"] = 540
        self.assertIsNone(_text_equation_anchor(old, new_blocks, 1, "5.4 Matching", []))

    def test_text_anchor_requires_page_boundary_for_cross_page_formula(self) -> None:
        before_text = "The final paragraph on the PDF page"
        after_text = "The opening paragraph on the next PDF page"
        old = {"sections": [{"title": "2 Training", "blocks": [
            {"type": "paragraph", "page": 2, "text": "Intro. " + before_text},
            {"type": "paragraph", "page": 3, "text": after_text + " More detail."},
        ]}]}
        new_blocks = [
            {"type": "paragraph", "page": 2, "text": before_text,
             "bbox": {"x": 307, "y": 576, "width": 236, "height": 118}},
            {"type": "equation", "page": 2,
             "bbox": {"x": 346, "y": 708, "width": 196, "height": 10}},
            {"type": "paragraph", "page": 3, "text": after_text,
             "bbox": {"x": 55, "y": 70, "width": 216, "height": 10}},
        ]
        pages = [{"number": 2, "height": 792}, {"number": 3, "height": 792}]
        self.assertEqual(_text_equation_anchor(old, new_blocks, 1, "2 Training", pages), (0, 1))
        new_blocks[1]["bbox"]["y"] = 400
        self.assertIsNone(_text_equation_anchor(old, new_blocks, 1, "2 Training", pages))

    def test_text_anchor_appends_formula_before_next_section(self) -> None:
        before_text = "from which we obtain the density"
        after_text = "For text-conditional sampling of images"
        old = {"sections": [
            {"title": "3.1 Tailored SNR Samplers", "blocks": [
                {"type": "paragraph", "page": 4, "text": "CosMap. " + before_text}]},
            {"title": "4 Text-to-Image Architecture", "blocks": [
                {"type": "paragraph", "page": 4, "text": after_text + " and further details."}]},
        ]}
        new_blocks = [
            {"type": "paragraph", "page": 4, "text": before_text,
             "bbox": {"x": 307, "y": 277, "width": 134, "height": 9}},
            {"type": "equation", "page": 4,
             "bbox": {"x": 325, "y": 297, "width": 217, "height": 24}},
            {"type": "paragraph", "page": 4, "text": after_text,
             "bbox": {"x": 307, "y": 356, "width": 234, "height": 56}},
        ]
        self.assertEqual(_text_equation_anchor(old, new_blocks, 1, "3.1 Tailored SNR Samplers", []), (0, 1))

    def test_disjoint_appendix_is_added_after_legacy_content(self) -> None:
        old = {"sections": [{"id": "body", "type": "body", "blocks": [
            {"id": "p1", "type": "paragraph", "text": "Main", "page": 2}]}]}
        appendix = {"id": "appendix", "type": "appendix", "blocks": [
            {"id": "eq1", "type": "equation", "page": 3,
             "src": "/api/documents/abc/assets/eq1.png"}]}
        new = {"sections": [appendix]}
        assets, sections, blocks = _append_missing_appendix(old, new)
        self.assertEqual((assets, sections, blocks), (["eq1.png"], 1, 1))
        self.assertEqual([section["id"] for section in old["sections"]], ["body", "appendix"])
        self.assertEqual(old["sections"][1]["blocks"][0]["order"], 1)

    def test_overlapping_appendix_is_rejected(self) -> None:
        old = model({"id": "p1", "type": "paragraph", "page": 3})
        new = {"sections": [{"id": "appendix", "type": "appendix", "blocks": [
            {"id": "p2", "type": "paragraph", "page": 3}]}]}
        with self.assertRaisesRegex(ValueError, "overlap"):
            _append_missing_appendix(old, new)

    def test_acknowledgments_do_not_hide_missing_appendix(self):
        old = {"sections": [{"id": "ack", "title": "ACKNOWLEDGMENTS", "type": "appendix", "blocks": [
            {"id": "ack-text", "type": "paragraph", "page": 10}]}]}
        new = {"sections": [old["sections"][0],
            {"id": "a", "title": "A Registry", "type": "appendix", "blocks": [
                {"id": "a-text", "type": "paragraph", "page": 12}]}]}
        self.assertEqual(_append_missing_appendix(old, new), ([], 1, 1))


if __name__ == "__main__":
    unittest.main()
