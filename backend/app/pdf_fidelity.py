"""Conservative PDF geometry checks for inline math and original-view fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .model import Block, DocumentModel


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


def recover_missing_pdf_figures(model: DocumentModel, pdf_path: Path, folder: Path) -> int:
    """Recover only wide image groups with an explicit Fig. caption in the PDF.

    PDF publishers sometimes flatten a table-like figure into adjacent image tiles.
    A numbered caption, close image edges, and a wide combined box are all required;
    otherwise we leave the original PDF as the source of truth.
    """
    try:
        import pymupdf
        import pypdfium2 as pdfium
    except ImportError:
        return 0
    from .normalizer import _numbered_citations, clean_text

    existing = {figure.number for figure in model.figures if figure.number is not None}
    recovered = 0
    with pymupdf.open(pdf_path) as geometry:
        raster = pdfium.PdfDocument(str(pdf_path))
        try:
            for page_index, page in enumerate(geometry):
                images = [tuple(info["bbox"]) for info in page.get_image_info()
                          if info["bbox"][2] - info["bbox"][0] >= 25
                          and info["bbox"][3] - info["bbox"][1] >= 25]
                for caption_box in page.get_text("blocks"):
                    caption = clean_text(caption_box[4])
                    match = re.match(r"^Fig(?:ure)?\.?(?:\s*)(\d+)\s*[:.]\s*(.+)", caption, re.I)
                    if not match:
                        continue
                    number = int(match.group(1))
                    if number in existing or caption_box[1] > page.rect.height * .45:
                        continue
                    nearby = [box for box in images if 0 <= caption_box[1] - box[3] <= 35]
                    if not nearby:
                        continue
                    anchor = max(nearby, key=lambda box: box[2] - box[0])
                    group = [box for box in nearby if abs(box[1] - anchor[1]) <= 18]
                    left = min(box[0] for box in group)
                    top = min(box[1] for box in group)
                    right = max(box[2] for box in group)
                    bottom = max(box[3] for box in group)
                    if right - left < page.rect.width * .55 or bottom > caption_box[1] - 2:
                        continue
                    box = {"x": left, "y": top, "width": right - left, "height": bottom - top}
                    asset = folder / "assets" / f"recovered_figure_{number}.png"
                    if not _crop(raster, page_index + 1, box, asset):
                        continue
                    block = Block(id=f"figure-{number}", type="figure", number=number,
                                  label=f"Figure {number}", caption=match.group(2),
                                  captionContent=_numbered_citations(match.group(2), model.references),
                                  page=page_index + 1, bbox=box, source="pdf_original_crop",
                                  src=f"/api/documents/{model.id}/assets/{asset.name}")
                    target = next(((section, index) for section in model.sections
                                   for index, item in enumerate(section.blocks)
                                   if item.page == page_index + 1 and item.bbox
                                   and item.bbox.get("y", 0) >= caption_box[3]), None)
                    if target:
                        target[0].blocks.insert(target[1], block)
                    else:
                        section = next((section for section in model.sections
                                        if any(item.page == page_index + 1 for item in section.blocks)), None)
                        if section is None:
                            continue
                        section.blocks.append(block)
                    model.figures.append(block)
                    existing.add(number)
                    recovered += 1
        finally:
            raster.close()
    return recovered


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
