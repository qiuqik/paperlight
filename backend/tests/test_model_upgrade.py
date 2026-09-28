"""Legacy model upgrades must preserve annotation anchors."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.scripts.upgrade_legacy_models import _add_anchored_equations, _append_missing_appendix, _check_compatibility, _joined_raw_bbox, _merge_geometry


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


if __name__ == "__main__":
    unittest.main()
