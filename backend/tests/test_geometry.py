"""Reading order must follow the PDF position of a figure above Introduction."""

import unittest
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import patch

from backend.app.model import Block, Section
from backend.app.normalizer import _block_bbox, _place_figures_before_intro, _reposition_figures, normalize_docling


class GeometryTests(unittest.TestCase):
    def test_bbox_uses_top_left_origin(self) -> None:
        pdf = [SimpleNamespace(get_size=lambda: (600, 800))]
        location = SimpleNamespace(page_no=1, bbox=SimpleNamespace(l=10, b=650, r=200, t=750, coord_origin="BOTTOMLEFT"))
        self.assertEqual(_block_bbox(location, pdf), {"x": 10, "y": 50, "width": 190, "height": 100})

    def test_figure_above_intro_moves_before_heading(self) -> None:
        first = Block(id="figure-1", type="figure", page=2, bbox={"x": 20, "y": 30, "width": 500, "height": 100})
        second = Block(id="figure-2", type="figure", page=2, bbox={"x": 20, "y": 140, "width": 500, "height": 80})
        abstract = Section(id="abstract", title="Abstract", type="abstract", blocks=[first, second])
        intro = Section(id="introduction", title="1 Introduction", type="body", blocks=[Block(id="p1", type="paragraph", page=2)])
        _place_figures_before_intro([abstract, intro], [first, second], (2, 250))
        self.assertEqual([block.id for block in intro.blocks], ["figure-1", "figure-2", "p1"])
        self.assertEqual(abstract.blocks, [])
        self.assertTrue(all(block.beforeHeading for block in [first, second]))

    def test_figure_position_follows_pdf_geometry_instead_of_citation(self) -> None:
        first = Block(id="p1", type="paragraph", page=2, text="Figure 1 shows the result.")
        second = Block(id="p2", type="paragraph", page=2, text="Discussion continues.")
        figure = Block(id="figure-1", type="figure", page=2)
        section = Section(id="method", title="Method", blocks=[first, second, figure])
        geometry = {"p1": (2, (10, 600, 250, 700)), "p2": (2, (10, 300, 250, 400))}
        figure_boxes = {"figure-1": (2, 10, 440, 250, 560)}

        _reposition_figures([section], geometry, figure_boxes)

        self.assertEqual([block.id for block in section.blocks], ["p1", "figure-1", "p2"])

    def test_figures_sharing_anchor_keep_pdf_order(self) -> None:
        paragraph = Block(id="p1", type="paragraph", page=2)
        upper = Block(id="upper", type="figure", page=2)
        lower = Block(id="lower", type="figure", page=2)
        section = Section(id="results", title="Results", blocks=[paragraph, upper, lower])
        geometry = {"p1": (2, (10, 600, 250, 700))}
        figure_boxes = {"upper": (2, 10, 480, 250, 560), "lower": (2, 10, 380, 250, 460)}

        _reposition_figures([section], geometry, figure_boxes)

        self.assertEqual([block.id for block in section.blocks], ["p1", "upper", "lower"])

    def test_appendix_after_references_and_unreadable_formula_are_retained(self) -> None:
        def item(label: str, text: str, page: int):
            return SimpleNamespace(label=SimpleNamespace(value=label), text=text,
                                   prov=[SimpleNamespace(page_no=page, bbox=None)])

        items = [item("title", "A paper", 1), item("section_header", "Introduction", 1),
                 item("text", "Main findings.", 1), item("section_header", "References", 2),
                 item("text", "[1] A reference.", 2), item("section_header", "Appendix A", 3),
                 item("text", "Additional results.", 3), item("display_formula", "", 3)]
        doc = SimpleNamespace(iterate_items=lambda: ((entry, 0) for entry in items),
                              texts=[], pictures=[], pages={1: None, 2: None, 3: None})
        core = ModuleType("docling_core")
        core.__path__ = []
        types = ModuleType("docling_core.types")
        types.__path__ = []
        doc_types = ModuleType("docling_core.types.doc")
        doc_types.PictureItem = type("PictureItem", (), {})
        doc_types.TableItem = type("TableItem", (), {})
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(sys.modules, {
            "docling_core": core, "docling_core.types": types, "docling_core.types.doc": doc_types,
        }), patch("backend.app.normalizer._render_picture_from_pdf", return_value=True):
            model = normalize_docling(doc, "abc", Path(temp_dir))

        appendix = next(section for section in model.sections if section.type == "appendix")
        self.assertEqual([block.type for block in appendix.blocks], ["paragraph", "equation"])
        self.assertEqual(appendix.blocks[0].text, "Additional results.")
        self.assertTrue(appendix.blocks[1].src.endswith(".png"))


if __name__ == "__main__":
    unittest.main()
