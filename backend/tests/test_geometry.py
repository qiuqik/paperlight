"""Reading order must follow the PDF position of a figure above Introduction."""

import unittest
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import patch

from backend.app.model import Block, Section
from backend.app.normalizer import _block_bbox, _formula_number, _heading_key, _merge_continuations, _merge_list_item, _place_figures_before_intro, _repair_heading, _reposition_figures, extract_pdf_references, normalize_docling
from backend.scripts.reorder_wide_figures import repair as repair_wide_figures
from backend.scripts.repair_list_geometry import repair as repair_list_geometry


class GeometryTests(unittest.TestCase):
    def test_list_item_merge_covers_all_items_on_one_page(self) -> None:
        first = Block(id="list", type="list", items=["First"], page=2,
                      bbox={"x": 50, "y": 100, "width": 220, "height": 30})
        second = Block(id="next", type="list", items=["Second"], page=2,
                       bbox={"x": 55, "y": 140, "width": 210, "height": 25})

        self.assertTrue(_merge_list_item(first, second))

        self.assertEqual(first.items, ["First", "Second"])
        self.assertEqual(first.bbox, {"x": 50, "y": 100, "width": 220, "height": 65})

    def test_list_item_merge_rejects_different_page_or_column(self) -> None:
        first = Block(id="list", type="list", items=["First"], page=2,
                      bbox={"x": 50, "y": 100, "width": 220, "height": 30})
        for page, x in ((3, 50), (2, 310)):
            with self.subTest(page=page, x=x):
                second = Block(id="next", type="list", items=["Second"], page=page,
                               bbox={"x": x, "y": 140, "width": 210, "height": 25})
                self.assertFalse(_merge_list_item(first, second))
                self.assertEqual(first.items, ["First"])

    def test_saved_list_geometry_uses_only_unique_same_column_source_items(self) -> None:
        first_text = "The first list item has enough text to match uniquely"
        second_text = "The second list item also has enough unique text"
        original = {"x": 50, "y": 100, "width": 220, "height": 30}
        model = {"modelVersion": 3, "pages": [{"number": 2, "height": 800}],
                 "sections": [{"blocks": [{"id": "list", "type": "list", "page": 2,
                                           "items": [f"• {first_text}", f"• {second_text}"],
                                           "bbox": dict(original)}]}]}
        def item(text: str, left: float, right: float, bottom: float, top: float) -> dict:
            return {"label": "list_item", "text": text, "prov": [{"page_no": 2, "bbox": {
                "l": left, "r": right, "b": bottom, "t": top, "coord_origin": "BOTTOMLEFT"}}]}
        snapshot = {"texts": [item(first_text, 50, 270, 670, 700),
                              item(second_text, 55, 265, 635, 660)]}

        self.assertEqual(repair_list_geometry(model, snapshot), [{"block": "list", "page": 2, "items": 2}])
        self.assertEqual(model["sections"][0]["blocks"][0]["bbox"],
                         {"x": 50, "y": 100, "width": 220, "height": 65})
        model["sections"][0]["blocks"][0]["bbox"] = dict(original)
        snapshot["texts"][1]["prov"][0]["bbox"].update(l=310, r=520)
        self.assertEqual(repair_list_geometry(model, snapshot), [])
        snapshot["texts"][1]["prov"][0]["bbox"].update(l=55, r=265)
        snapshot["texts"].append(item(second_text, 55, 265, 635, 660))
        self.assertEqual(repair_list_geometry(model, snapshot), [])
    def test_continuation_merges_nearby_same_column_fragments_and_unions_bbox(self) -> None:
        first = Block(id="first", type="paragraph", text="A paragraph continues", page=2,
                      bbox={"x": 50, "y": 100, "width": 200, "height": 30})
        second = Block(id="second", type="paragraph", text="on the next line.", page=2,
                       bbox={"x": 52, "y": 140, "width": 190, "height": 20})
        section = Section(id="body", title="Body", blocks=[first, second])
        geometry = {"first": (2, (50, 670, 250, 700)), "second": (2, (52, 640, 242, 660))}

        _merge_continuations([section], geometry)

        self.assertEqual([block.id for block in section.blocks], ["first"])
        self.assertEqual(first.text, "A paragraph continues on the next line.")
        self.assertEqual(first.bbox, {"x": 50, "y": 100, "width": 200, "height": 60})
        self.assertEqual(geometry["first"], (2, (50, 640, 250, 700)))

    def test_continuation_keeps_cross_page_and_cross_column_geometry_separate(self) -> None:
        cases = [
            (3, (50, 670, 250, 700)),
            (2, (300, 640, 500, 660)),
        ]
        for page, second_box in cases:
            with self.subTest(page=page, second_box=second_box):
                first = Block(id="first", type="paragraph", text="A paragraph continues", page=2)
                second = Block(id="second", type="paragraph", text="on the next line.", page=page)
                section = Section(id="body", title="Body", blocks=[first, second])
                geometry = {"first": (2, (50, 670, 250, 700)), "second": (page, second_box)}

                _merge_continuations([section], geometry)

                self.assertEqual([block.id for block in section.blocks], ["first", "second"])

    def test_joined_section_number_matches_normal_heading(self) -> None:
        self.assertEqual(_repair_heading("1Introduction"), "1 Introduction")
        self.assertEqual(_repair_heading("2RELATED WORK"), "2 RELATED WORK")
        self.assertEqual(_heading_key("1Introduction"), _heading_key("Introduction"))

    def test_equation_number_requires_one_nearby_pdf_number(self) -> None:
        location = SimpleNamespace(page_no=3, bbox=SimpleNamespace(
            l=86, b=309, r=271, t=334, coord_origin="BOTTOMLEFT"))
        page = SimpleNamespace(get_size=lambda: (565, 771),
                               get_textpage=lambda: SimpleNamespace(get_text_bounded=lambda **kwargs: "formula (1)", close=lambda: None))
        self.assertEqual(_formula_number(location, [None, None, page]), 1)
        page.get_textpage = lambda: SimpleNamespace(get_text_bounded=lambda **kwargs: "(1) and (2)", close=lambda: None)
        self.assertIsNone(_formula_number(location, [None, None, page]))

    def test_repeated_bibliography_numbers_and_appendix_are_excluded(self) -> None:
        page_text = ("REFERENCES\n[1] A. Author. A useful paper. 2020.\n"
                     "[2] B. Author. Another useful paper. 2021.\n"
                     "[1] A. Author. A useful paper. 2020.\n"
                     "[2] B. Author. Another useful paper. 2021.\n"
                     "APPENDIX A\n[1] This appendix item is not a reference.\n")
        class FakePdf:
            def __len__(self): return 1
            def __getitem__(self, index):
                return SimpleNamespace(get_textpage=lambda: SimpleNamespace(get_text_range=lambda: page_text))
            def close(self): pass
        pdfium = ModuleType("pypdfium2")
        pdfium.PdfDocument = lambda path: FakePdf()
        with patch.dict(sys.modules, {"pypdfium2": pdfium}):
            references = extract_pdf_references(Path("unused.pdf"))
        self.assertEqual([entry["number"] for entry in references], [1, 2])
        self.assertTrue(all("appendix item" not in entry["preview"].lower() for entry in references))

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

    def test_full_width_figure_precedes_both_columns_below_it(self) -> None:
        left = Block(id="left", type="paragraph", page=12)
        right = Block(id="right", type="paragraph", page=12)
        figure = Block(id="figure", type="figure", page=12)
        first = Section(id="earlier", title="Earlier", blocks=[left])
        second = Section(id="later", title="Later", blocks=[figure, right])
        geometry = {
            "left": (12, (49, 147, 290, 286)),
            "right": (12, (311, 343, 553, 504)),
        }
        figure_boxes = {"figure": (12, 48, 579, 552, 711)}

        _reposition_figures([first, second], geometry, figure_boxes)

        self.assertEqual([block.id for block in first.blocks], ["figure", "left"])
        self.assertEqual([block.id for block in second.blocks], ["right"])

    def test_full_width_figure_already_before_columns_stays_in_earlier_section(self) -> None:
        figure = Block(id="figure", type="figure", page=12)
        left = Block(id="left", type="paragraph", page=12)
        right = Block(id="right", type="paragraph", page=12)
        first = Section(id="earlier", title="Earlier", blocks=[figure])
        second = Section(id="later", title="Later", blocks=[left, right])
        geometry = {
            "left": (12, (49, 147, 290, 286)),
            "right": (12, (311, 343, 553, 504)),
        }
        figure_boxes = {"figure": (12, 48, 579, 552, 711)}

        _reposition_figures([first, second], geometry, figure_boxes)

        self.assertEqual([block.id for block in first.blocks], ["figure"])
        self.assertEqual([block.id for block in second.blocks], ["left", "right"])

    def test_saved_wide_figure_repair_preserves_text_and_anchor_ids(self) -> None:
        model = {"modelVersion": 3, "pages": [{"number": 12, "height": 792}],
                 "sections": [
                     {"id": "earlier", "title": "Earlier", "blocks": [
                         {"id": "left", "type": "paragraph", "text": "Left text", "page": 12,
                          "bbox": {"x": 49, "y": 506, "width": 241, "height": 139}, "order": 0}]},
                     {"id": "later", "title": "Later", "blocks": [
                         {"id": "figure", "type": "figure", "page": 12,
                          "bbox": {"x": 48, "y": 81, "width": 504, "height": 133}, "order": 1},
                         {"id": "right", "type": "paragraph", "text": "Right text", "page": 12,
                          "bbox": {"x": 311, "y": 288, "width": 242, "height": 161}, "order": 2}]}],
                 "figures": [{"id": "figure", "type": "figure", "order": 1}]}

        moved = repair_wide_figures(model)

        self.assertEqual(moved, [{"figure": "figure", "page": 12, "before": "left"}])
        self.assertEqual([block["id"] for section in model["sections"] for block in section["blocks"]],
                         ["figure", "left", "right"])
        self.assertEqual([block["text"] for section in model["sections"] for block in section["blocks"]
                          if block["type"] == "paragraph"], ["Left text", "Right text"])
        self.assertEqual(model["figures"][0]["order"], 0)
        self.assertEqual(repair_wide_figures(model), [])

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

    def test_lettered_appendix_after_references_is_retained(self) -> None:
        def item(label: str, text: str, page: int):
            return SimpleNamespace(label=SimpleNamespace(value=label), text=text,
                                   prov=[SimpleNamespace(page_no=page, bbox=None)])

        items = [item("title", "A paper", 1), item("section_header", "Introduction", 1),
                 item("text", "Main findings.", 1), item("section_header", "References", 2),
                 item("text", "[1] A reference.", 2),
                 item("section_header", "A Additional Experimental Results", 3),
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
        self.assertEqual(appendix.blocks[0].text, "Additional results.")
        self.assertEqual(appendix.blocks[1].type, "equation")

    def test_synthetic_abstract_keeps_source_page_and_bbox(self) -> None:
        abstract_text = "This paper explains the result."
        source = SimpleNamespace(label=SimpleNamespace(value="text"), text=abstract_text,
                                 prov=[SimpleNamespace(page_no=1, bbox=object())])
        doc = SimpleNamespace(iterate_items=lambda: iter(()), texts=[source], pictures=[], pages={1: None})
        core = ModuleType("docling_core")
        core.__path__ = []
        types = ModuleType("docling_core.types")
        types.__path__ = []
        doc_types = ModuleType("docling_core.types.doc")
        doc_types.PictureItem = type("PictureItem", (), {})
        doc_types.TableItem = type("TableItem", (), {})
        box = {"x": 20, "y": 40, "width": 200, "height": 30}
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(sys.modules, {
            "docling_core": core, "docling_core.types": types, "docling_core.types.doc": doc_types,
        }), patch("backend.app.normalizer._block_bbox", return_value=box):
            model = normalize_docling(doc, "abc", Path(temp_dir), grobid={"abstract": abstract_text})

        abstract = next(section for section in model.sections if section.type == "abstract")
        self.assertEqual(abstract.blocks[0].page, 1)
        self.assertEqual(abstract.blocks[0].bbox, box)


if __name__ == "__main__":
    unittest.main()
