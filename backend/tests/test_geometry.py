"""Reading order must follow the PDF position of a figure above Introduction."""

import unittest
from types import SimpleNamespace

from backend.app.model import Block, Section
from backend.app.normalizer import _block_bbox, _place_figures_before_intro


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


if __name__ == "__main__":
    unittest.main()
