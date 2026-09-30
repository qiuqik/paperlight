"""Version pinning, safe HTML mapping, and private arXiv API access."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.app.arxiv_html import parse_arxiv_html, repair_missing_figure_assets, upgrade_html_layout
from backend.app.arxiv_source import ArxivSource, resolve_arxiv_url


class ArxivImportTests(unittest.TestCase):
    def test_unversioned_link_is_pinned_from_official_history(self) -> None:
        page = b'<div class="submission-history">[v1] Jan 1 [v2] Feb 2</div>'
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=page)))
        with client:
            source = resolve_arxiv_url("https://arxiv.org/abs/2603.17965", client)
        self.assertEqual(source.versioned_id, "2603.17965v2")
        self.assertEqual(source.html_url, "https://arxiv.org/html/2603.17965v2")

    def test_explicit_version_and_host_validation(self) -> None:
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok")))
        with client:
            self.assertEqual(resolve_arxiv_url("https://arxiv.org/pdf/2603.17965v1", client).version, 1)
            with self.assertRaises(ValueError):
                resolve_arxiv_url("https://arxiv.org.evil.test/abs/2603.17965", client)
            with self.assertRaises(ValueError):
                resolve_arxiv_url("http://arxiv.org/abs/2603.17965", client)

    def test_mathml_and_assets_are_mapped_without_source_html_injection(self) -> None:
        html = b'''<article class="ltx_document"><h1 class="ltx_title_document">A study</h1>
        <div class="ltx_authors"><span class="ltx_personname">Alice</span></div>
        <section class="ltx_section" id="S1"><h2>1 Introduction</h2><div class="ltx_para"><p class="ltx_p" id="p1">''' + b"A " * 300 + b'''<math display="inline" onclick="evil()"><semantics><msub><mi>x</mi><mn>1</mn></msub><annotation encoding="application/x-tex">x_1</annotation></semantics></math></p></div>
        <figure class="ltx_figure" id="S1.F1"><img src="2603.17965v1/figure.png" onerror="evil()"><figcaption>Figure 1</figcaption></figure>
        <div class="ltx_logical-block"><div class="ltx_para"><img src="2603.17965v1/teaser.png"></div>
        <figure class="ltx_figure" id="S1.F2"><figcaption>Figure 2: Teaser</figcaption></figure></div></section>
        <section class="ltx_section" id="S2"><h2>2 Results</h2><div class="ltx_para"><p class="ltx_p" id="p2">''' + b"B " * 300 + b'''</p></div></section></article>'''
        source = ArxivSource("2603.17965", 1, "https://arxiv.org/abs/2603.17965v1")
        with tempfile.TemporaryDirectory() as directory, patch("backend.app.arxiv_html.official_get", return_value=b"PNG"):
            model = parse_arxiv_html(html, source, "a" * 32, Path(directory), object())
            node = model.sections[0].blocks[0].content[-1]
            self.assertEqual(node.latex, "x_1")
            self.assertIn("<msub>", node.mathml)
            self.assertNotIn("onclick", node.mathml)
            self.assertEqual(len(model.figures), 2)
            self.assertTrue((Path(directory) / "assets" / Path(model.figures[0].src).name).is_file())
            self.assertTrue((Path(directory) / "assets" / Path(model.figures[1].src).name).is_file())
            self.assertEqual(model.figures[1].source, "arxiv_html")
            model.figures[1].src = None
            model.figures[1].source = "arxiv_html_missing_visual"
            section_figure = next(block for section in model.sections for block in section.blocks if block.id == model.figures[1].id)
            section_figure.src = None
            section_figure.source = "arxiv_html_missing_visual"
            self.assertEqual(repair_missing_figure_assets(model, html, source, Path(directory), object()), 1)
            self.assertEqual(section_figure.src, model.figures[1].src)

    def test_incomplete_html_requires_pdf_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                parse_arxiv_html(b"<html><h1>Error</h1></html>", ArxivSource("2603.17965", 1, ""),
                                  "a" * 32, Path(directory), object())

    def test_prompt_svg_is_readable_text_and_missing_visual_has_pdf_link(self) -> None:
        body = "A substantial paragraph about the result. " * 20
        html = f'''<article class="ltx_document"><h1 class="ltx_title_document">Prompts paper</h1>
        <section class="ltx_section" id="S1"><h2>1 Background</h2><div class="ltx_para"><p class="ltx_p">{body}</p></div>
        <div class="ltx_logical-block"><figure class="ltx_figure" id="S1.F1"><figcaption>Figure 1: Illustration unavailable</figcaption></figure></div></section>
        <section class="ltx_section" id="S2"><h2>2 Prompts</h2><div class="ltx_para"><p class="ltx_p">{body}</p></div>
        <figure class="ltx_figure" id="S2.F2"><svg><foreignobject><span class="ltx_foreignobject_content">
        <span class="ltx_p">Grading prompt</span><span class="ltx_p">Check every layer.</span>
        </span></foreignobject></svg></figure></section></article>'''
        with tempfile.TemporaryDirectory() as directory:
            model = parse_arxiv_html(html.encode(), ArxivSource("2603.17965", 1, ""),
                                      "a" * 32, Path(directory), object())
        self.assertEqual(len(model.figures), 1)
        self.assertEqual(model.figures[0].source, "arxiv_html_missing_visual")
        self.assertEqual(model.figures[0].sourceUrl, "https://export.arxiv.org/pdf/2603.17965v1")
        self.assertEqual(model.sections[1].presentation, "prompt")
        self.assertIn("Grading prompt", [block.text for block in model.sections[1].blocks])

    def test_list_markers_prompt_sections_and_table_spans_keep_source_structure(self) -> None:
        body = "A substantial paragraph about the result. " * 20
        html = f'''<article class="ltx_document"><h1 class="ltx_title_document">A study</h1>
        <section class="ltx_section" id="S1"><h2>3.3 Prompt Processing</h2>
        <div class="ltx_para"><p class="ltx_p">{body}</p></div>
        <ul id="S1.I1"><li class="ltx_item"><span class="ltx_tag ltx_tag_item">•</span><div class="ltx_para"><p class="ltx_p">We introduce LaDe.</p></div></li></ul>
        <figure class="ltx_table" id="S1.T1"><figcaption>Table 1: Results</figcaption>
        <table class="ltx_tabular"><thead><tr><th class="ltx_border_tt" colspan="2">Grouped <math display="inline"><semantics><mo>↑</mo><annotation encoding="application/x-tex">\\uparrow</annotation></semantics></math></th></tr></thead>
        <tbody><tr><th class="ltx_align_left">Method</th><td class="ltx_align_center ltx_border_bb"><span class="ltx_font_bold">3.58</span></td></tr></tbody></table></figure></section>
        <section class="ltx_section" id="S2"><h2>8 Prompts</h2><div class="ltx_para"><p class="ltx_p">{body}</p></div></section></article>'''
        with tempfile.TemporaryDirectory() as directory:
            model = parse_arxiv_html(html.encode(), ArxivSource("2603.17965", 1, ""),
                                      "a" * 32, Path(directory), object())
        self.assertEqual(model.sections[0].presentation, "article")
        self.assertEqual(model.sections[1].presentation, "prompt")
        listed = next(block for block in model.sections[0].blocks if block.type == "list")
        self.assertEqual(listed.items, ["We introduce LaDe."])
        self.assertNotIn("•", "".join(node.text for node in listed.listContent[0]))
        table = model.tables[0]
        self.assertEqual(table.tableRows[0].cells[0].colSpan, 2)
        self.assertEqual(table.tableRows[0].cells[0].content[-1].latex, "\\uparrow")
        self.assertTrue(table.tableRows[1].cells[1].content[0].bold)
        model.sections[0].presentation = "prompt"
        listed.listContent = []
        for block in model.sections[0].blocks:
            if block.type == "table":
                block.tableRows = []
        self.assertGreaterEqual(upgrade_html_layout(model, html.encode()), 3)
        self.assertEqual(model.sections[0].presentation, "article")
        self.assertTrue(listed.listContent)
        self.assertTrue(next(block for block in model.sections[0].blocks if block.type == "table").tableRows)


if __name__ == "__main__":
    unittest.main()
