"""Legacy model upgrades must preserve annotation anchors."""

import unittest

from backend.scripts.upgrade_legacy_models import _check_compatibility


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


if __name__ == "__main__":
    unittest.main()
