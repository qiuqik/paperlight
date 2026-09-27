"""Legacy model upgrades must preserve annotation anchors."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.scripts.upgrade_legacy_models import _check_compatibility, _merge_geometry


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


if __name__ == "__main__":
    unittest.main()
