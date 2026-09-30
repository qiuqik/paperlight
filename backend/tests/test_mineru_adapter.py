"""Contract checks for MinerU MiddleJson adaptation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.mineru_normalizer import _asset, _inline_equations, normalize_mineru


class _Page:
    def get_size(self):
        return (600, 800)


class _Pdf:
    def __len__(self):
        return 1

    def __getitem__(self, index):
        return _Page()

    def close(self):
        pass


class MineruAdapterTests(unittest.TestCase):
    def test_inline_equations_are_detected_even_inside_links(self):
        spans = [{"type": "text", "content": "let "},
                 {"type": "hyperlink", "content": [{"type": "equation_inline", "content": "x_i"}]}]
        self.assertEqual(_inline_equations(spans), ["x_i"])

    def test_first_figure_equation_and_references_keep_source_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / ("a" * 32)
            (folder / "assets").mkdir(parents=True)
            for name in ("page_0_image_2.jpg", "page_0_image_3.jpg", "page_0_equation_6.jpg", "page_0_table_8.jpg"):
                (folder / "assets" / name).write_bytes(b"image")
            middle = {"schema": "docvortex.middle", "pages": [{"page_idx": 0, "blocks": [
                {"type": "doc_title", "content": [{"type": "text", "content": "A Paper"}]},
                {"type": "text", "content": [{"type": "text", "content": "Alice and Bob"}]},
                {"type": "image", "bbox": [0.1, 0.2, 0.9, 0.4], "content": [
                    {"type": "image_body", "image_path": "images/page_0_image_2.jpg"},
                    {"type": "image_caption", "content": [{"type": "text", "content": "Fig. 1: Overview"}]}]},
                {"type": "image", "bbox": [0.1, 0.4, 0.9, 0.5], "content": [
                    {"type": "image_body", "image_path": "images/page_0_image_3.jpg"},
                    {"type": "image_caption", "content": [{"type": "text", "content": "(b) Second panel"}]},
                    {"type": "image_caption", "content": [{"type": "text", "content": "Fig. 1: Continued figure"}]}]},
                {"type": "paragraph_title", "level": 2, "content": [{"type": "text", "content": "1 INTRODUCTION"}]},
                {"type": "text", "content": [{"type": "text", "content": "See [1] for details."}]},
                {"type": "equation", "image_path": "images/page_0_equation_6.jpg", "content": "x^2", "bbox": [0.3, 0.5, 0.7, 0.6]},
                {"type": "table", "bbox": [0.1, 0.6, 0.9, 0.8], "content": [
                    {"type": "table_body", "image_path": "images/page_0_table_8.jpg", "content": "<table><tr><td>A</td></tr></table>"},
                    {"type": "table_caption", "content": [{"type": "text", "content": "Table 2: Results"}]}]},
                {"type": "ref_text", "content": [{"type": "text", "content": "[1] A. Author, A cited work, 2024."}]},
            ]}]}
            with patch("backend.app.mineru_normalizer.pdfium.PdfDocument", return_value=_Pdf()):
                model = normalize_mineru(middle, folder.name, folder, folder / "original.pdf")
            self.assertEqual(model.metadata.authors, ["Alice", "Bob"])
            self.assertEqual(model.sections[1].title, "1 Introduction")
            self.assertEqual(model.figures[0].id, "figure-1")
            self.assertEqual(model.figures[1].id, "figure-1-part-2")
            self.assertEqual(model.figures[0].bbox["width"], 480)
            self.assertEqual(model.references[0].number, 1)
            self.assertEqual(model.tables[0].id, "table-2")
            self.assertEqual(model.citationLinkStatus, "linked")
            equation = next(block for section in model.sections for block in section.blocks if block.type == "equation")
            self.assertEqual(equation.latex, "x^2")
            self.assertEqual(equation.source, "mineru_basic_pdf_crop")
            self.assertTrue(equation.src.endswith("page_0_equation_6.jpg"))
            self.assertIsNone(_asset("images/../secret.jpg", folder))


if __name__ == "__main__":
    unittest.main()
