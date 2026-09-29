"""Conservative PDF geometry checks for inline math and original-view fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .model import DocumentModel


MATH_FONT = re.compile(r"(?:math|cmmi|cmsy|msbm|symbol|stix|mt2|euler)", re.I)
MATH_GLYPH = re.compile(r"[α-ωΑ-Ω∑∫∂∇≈≤≥≠∈⊂⊕⊗∀∃√∞₀-₉⁰-⁹]")


def _overlaps(first: dict[str, float], second: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = second
    return min(first["x"] + first["width"], right) > max(first["x"], left) and min(first["y"] + first["height"], bottom) > max(first["y"], top)


def _crop(pdf: Any, page_number: int, box: dict[str, float], target: Path) -> bool:
    try:
        page = pdf[page_number - 1]
        width, height = page.get_size()
        scale = 2.5
        left = max(0, box["x"] - 2)
        top = max(0, box["y"] - 2)
        right = min(width, box["x"] + box["width"] + 2)
        bottom = min(height, box["y"] + box["height"] + 2)
        if right <= left or bottom <= top:
            return False
        image = page.render(scale=scale, crop=(left, height - bottom, width - right, top)).to_pil()
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        return True
    except (IndexError, KeyError, OSError, ValueError):
        return False


def preserve_uncertain_inline_math(model: DocumentModel, pdf_path: Path, folder: Path) -> int:
    """Use whole-paragraph PDF crops only when math-font glyphs lie inside its box.

    Character positions identify a risky paragraph, not a purported exact formula span.
    This avoids replacing an uncertain subscript or a single symbol with invented TeX.
    """
    try:
        import pymupdf
        import pypdfium2 as pdfium
    except ImportError:
        return 0
    count = 0
    with pymupdf.open(pdf_path) as geometry:
        raster = pdfium.PdfDocument(str(pdf_path))
        try:
            pages: dict[int, dict[str, Any]] = {}
            for section in model.sections:
                for block in section.blocks:
                    if block.type != "paragraph" or not block.page or not block.bbox:
                        continue
                    if block.page < 1 or block.page > len(geometry):
                        continue
                    if block.page not in pages:
                        pages[block.page] = geometry[block.page - 1].get_text("rawdict")
                    raw = pages[block.page]
                    risky = False
                    for region in raw.get("blocks", []):
                        for line in region.get("lines", []):
                            if not _overlaps(block.bbox, tuple(line.get("bbox", (0, 0, 0, 0)))):
                                continue
                            for span in line.get("spans", []):
                                if not _overlaps(block.bbox, tuple(span.get("bbox", (0, 0, 0, 0)))):
                                    continue
                                chars = "".join(char.get("c", "") for char in span.get("chars", []))
                                if MATH_GLYPH.search(chars) or (MATH_FONT.search(span.get("font", "")) and re.search(r"[=+*/^_{}|]", chars)):
                                    risky = True
                                    break
                            if risky:
                                break
                        if risky:
                            break
                    if not risky:
                        continue
                    filename = f"inline_original_{block.id}.png"
                    if _crop(raster, block.page, block.bbox, folder / "assets" / filename):
                        block.src = f"/api/documents/{model.id}/assets/{filename}"
                        block.source = "pdf_original_paragraph"
                        count += 1
        finally:
            raster.close()
    return count
