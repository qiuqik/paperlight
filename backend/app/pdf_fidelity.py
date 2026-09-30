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
                    anonymous = next((figure for figure in model.figures
                                      if figure.number is None and figure.page == page_index + 1
                                      and figure.bbox
                                      and figure.bbox["width"] >= page.rect.width * .55
                                      and 0 <= caption_box[1] - (figure.bbox["y"] + figure.bbox["height"]) <= 35), None)
                    if anonymous:
                        original_id = anonymous.id
                        anonymous.id = f"figure-{number}"
                        anonymous.number = number
                        anonymous.label = f"Figure {number}"
                        anonymous.caption = match.group(2)
                        anonymous.captionContent = _numbered_citations(match.group(2), model.references)
                        for section in model.sections:
                            for block in section.blocks:
                                if block.id == original_id:
                                    block.id = anonymous.id
                                    block.number = anonymous.number
                                    block.label = anonymous.label
                                    block.caption = anonymous.caption
                                    block.captionContent = anonymous.captionContent
                        existing.add(number)
                        recovered += 1
                        continue
                    nearby = [box for box in images if 0 <= caption_box[1] - box[3] <= 35]
                    if nearby:
                        anchor = max(nearby, key=lambda box: box[2] - box[0])
                        group = [box for box in nearby if abs(box[1] - anchor[1]) <= 18]
                        left = min(box[0] for box in group)
                        top = min(box[1] for box in group)
                        right = max(box[2] for box in group)
                        bottom = max(box[3] for box in group)
                        if right - left < page.rect.width * .55 or bottom > caption_box[1] - 2:
                            continue
                    else:
                        # Plots may be PDF vector drawings rather than embedded images.
                        side_left = page.rect.width / 2 if caption_box[0] > page.rect.width / 2 else 0
                        side_right = page.rect.width if side_left else page.rect.width / 2
                        drawings = [tuple(drawing["rect"]) for drawing in page.get_drawings()
                                    if drawing["rect"].x0 >= side_left - 3
                                    and drawing["rect"].x1 <= side_right + 3
                                    and caption_box[1] - 200 <= drawing["rect"].y0
                                    and drawing["rect"].y1 <= caption_box[1] - 2
                                    and drawing["rect"].width > 2 and drawing["rect"].height > 1]
                        if not drawings:
                            continue
                        left = min(box[0] for box in drawings)
                        top = min(box[1] for box in drawings)
                        right = max(box[2] for box in drawings)
                        bottom = max(box[3] for box in drawings)
                        if (right - left < page.rect.width * .35 or bottom - top < 60
                                or caption_box[1] - bottom > 35):
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


def merge_split_pdf_figures(model: DocumentModel, pdf_path: Path, folder: Path) -> int:
    """Join adjacent picture tiles when only the lower tile owns the caption."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return 0
    merged = 0
    raster = pdfium.PdfDocument(str(pdf_path))
    try:
        for figure in list(model.figures):
            if not figure.caption or not figure.bbox or not figure.page:
                continue
            box = figure.bbox
            candidate = next((other for other in model.figures
                              if other is not figure and not other.caption and other.page == figure.page
                              and other.bbox and other.bbox["width"] >= 100
                              and 0 <= box["y"] - (other.bbox["y"] + other.bbox["height"]) <= 18
                              and min(box["x"] + box["width"], other.bbox["x"] + other.bbox["width"])
                              - max(box["x"], other.bbox["x"]) >= min(box["width"], other.bbox["width"]) * .8), None)
            if candidate is None or not candidate.bbox:
                continue
            upper = candidate.bbox
            left = min(box["x"], upper["x"])
            top = min(box["y"], upper["y"])
            right = max(box["x"] + box["width"], upper["x"] + upper["width"])
            bottom = max(box["y"] + box["height"], upper["y"] + upper["height"])
            combined = {"x": left, "y": top, "width": right - left, "height": bottom - top}
            asset = folder / "assets" / f"merged_{figure.id}.png"
            if not _crop(raster, figure.page, combined, asset):
                continue
            figure.bbox = combined
            figure.src = f"/api/documents/{model.id}/assets/{asset.name}"
            figure.source = "pdf_original_crop"
            for section in model.sections:
                section.blocks = [block for block in section.blocks if block is not candidate]
            model.figures.remove(candidate)
            merged += 1
    finally:
        raster.close()
    return merged


def recover_pdf_table_captions(model: DocumentModel, pdf_path: Path) -> int:
    """Attach explicit PDF captions when one unnumbered table is on the page."""
    try:
        import pymupdf
    except ImportError:
        return 0
    from .normalizer import _numbered_citations, clean_text

    known = {table.number for table in model.tables if table.number is not None}
    recovered = 0
    with pymupdf.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf):
            missing: list[tuple[int, str]] = []
            for item in page.get_text("blocks"):
                caption = clean_text(item[4])
                match = re.match(r"^Table\s+(\d+)\s*[:.]\s*(.+)", caption, re.I)
                if match and int(match.group(1)) not in known:
                    missing.append((int(match.group(1)), match.group(2)))
            anonymous = [table for table in model.tables if table.page == page_index + 1
                         and table.number is None and not table.caption]
            if len(missing) != 1 or len(anonymous) != 1:
                continue
            number, caption = missing[0]
            table = anonymous[0]
            original_id = table.id
            table.id = f"table-{number}"
            table.number = number
            table.label = f"Table {number}"
            table.caption = caption
            table.captionContent = _numbered_citations(caption, model.references)
            for section in model.sections:
                for block in section.blocks:
                    if block.id == original_id:
                        block.id = table.id
                        block.number = table.number
                        block.label = table.label
                        block.caption = table.caption
                        block.captionContent = table.captionContent
            known.add(number)
            recovered += 1
    return recovered


def recover_composite_pdf_figures(model: DocumentModel, pdf_path: Path, folder: Path) -> int:
    """Replace a cluster of uncaptioned icon crops with its captioned PDF region."""
    try:
        import pymupdf
        import pypdfium2 as pdfium
    except ImportError:
        return 0
    from .normalizer import _numbered_citations, clean_text

    recovered = 0
    with pymupdf.open(pdf_path) as geometry:
        raster = pdfium.PdfDocument(str(pdf_path))
        try:
            for page_index, page in enumerate(geometry):
                parts = [figure for figure in model.figures
                         if figure.page == page_index + 1 and not figure.caption and figure.bbox]
                if len(parts) < 3:
                    continue
                for caption_box in page.get_text("blocks"):
                    caption = clean_text(caption_box[4])
                    match = re.match(r"^Figure\s+(\d+\.\d+)\s*[:.]\s*(.+)", caption, re.I)
                    if not match:
                        continue
                    token = match.group(1)
                    figure_id = f"figure-{token.replace('.', '-')}"
                    if any(figure.id == figure_id for figure in model.figures):
                        continue
                    selected = [figure for figure in parts if figure.bbox
                                and figure.bbox["y"] + figure.bbox["height"] < caption_box[1]
                                and caption_box[1] - figure.bbox["y"] < 550]
                    if len(selected) < 3:
                        continue
                    top = min(figure.bbox["y"] for figure in selected if figure.bbox)
                    left = min(figure.bbox["x"] for figure in selected if figure.bbox)
                    right = max(figure.bbox["x"] + figure.bbox["width"] for figure in selected if figure.bbox)
                    last_bottom = max(figure.bbox["y"] + figure.bbox["height"] for figure in selected if figure.bbox)
                    container = next((tuple(info["bbox"]) for info in page.get_image_info()
                                      if info["bbox"][0] <= left and info["bbox"][2] >= right
                                      and info["bbox"][1] <= top and info["bbox"][3] >= last_bottom
                                      and info["bbox"][3] <= caption_box[1]
                                      and info["bbox"][2] - info["bbox"][0] >= page.rect.width * .45), None)
                    if container:
                        left, top, right, bottom = container
                    else:
                        for text_box in page.get_text("blocks"):
                            if top - 15 <= text_box[1] and text_box[3] <= caption_box[1] - 3:
                                left = min(left, text_box[0])
                                right = max(right, text_box[2])
                        bottom = caption_box[1] - 3
                    box = {"x": max(0, left - 5), "y": max(0, top - 5),
                           "width": min(page.rect.width, right + 5) - max(0, left - 5),
                           "height": bottom + 5 - max(0, top - 5)}
                    if box["width"] < page.rect.width * .45 or box["height"] < 100:
                        continue
                    asset = folder / "assets" / f"recovered_{figure_id}.png"
                    if not _crop(raster, page_index + 1, box, asset):
                        continue
                    first = next(((section, index) for section in model.sections
                                  for index, block in enumerate(section.blocks) if block is selected[0]), None)
                    if first is None:
                        continue
                    figure = Block(id=figure_id, type="figure", number=int(token.split(".")[0]),
                                   label=f"Figure {token}", caption=match.group(2),
                                   captionContent=_numbered_citations(match.group(2), model.references),
                                   page=page_index + 1, bbox=box, source="pdf_original_crop",
                                   src=f"/api/documents/{model.id}/assets/{asset.name}")
                    for section in model.sections:
                        section.blocks = [block for block in section.blocks if block not in selected]
                    first[0].blocks.insert(min(first[1], len(first[0].blocks)), figure)
                    model.figures = [block for block in model.figures if block not in selected]
                    model.figures.append(figure)
                    recovered += 1
        finally:
            raster.close()
    return recovered


def trim_abstract_figure_labels(model: DocumentModel, pdf_path: Path) -> int:
    """Remove figure words that Docling prepends to a located abstract paragraph."""
    try:
        import pymupdf
    except ImportError:
        return 0
    from .normalizer import _numbered_citations, clean_text

    changed = 0
    with pymupdf.open(pdf_path) as pdf:
        for section in model.sections:
            if section.type != "abstract":
                continue
            for block in section.blocks:
                if block.type != "paragraph" or not block.page or not block.bbox or block.page > len(pdf):
                    continue
                box = block.bbox
                region = pymupdf.Rect(box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"])
                source = clean_text(pdf[block.page - 1].get_text("text", clip=region))
                prefix = " ".join(source.split()[:8])
                if len(prefix) < 30:
                    continue
                offset = block.text.casefold().find(prefix.casefold())
                if not 15 <= offset <= 120 or any(node.type == "inlineEquation" for node in block.content):
                    continue
                block.text = block.text[offset:]
                block.content = _numbered_citations(block.text, model.references)
                changed += 1
    return changed


def repair_overlapping_pdf_paragraphs(model: DocumentModel, pdf_path: Path) -> int:
    """Use the PDF text order when two extracted paragraphs overlap one column."""
    try:
        import pymupdf
    except ImportError:
        return 0
    from .normalizer import _numbered_citations, clean_text

    repaired = 0
    with pymupdf.open(pdf_path) as pdf:
        for section in model.sections:
            index = 0
            while index + 1 < len(section.blocks):
                first, second = section.blocks[index:index + 2]
                if (first.type != second.type or first.type != "paragraph"
                        or not first.page or first.page != second.page or not first.bbox or not second.bbox
                        or first.page > len(pdf)
                        or any(node.type == "inlineEquation" for node in [*first.content, *second.content])):
                    index += 1
                    continue
                a, b = first.bbox, second.bbox
                horizontal = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
                vertical = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
                if horizontal < min(a["width"], b["width"]) * .7 or vertical < 4:
                    index += 1
                    continue
                page = pdf[first.page - 1]
                right_column = a["x"] + a["width"] / 2 > page.rect.width / 2
                source_blocks = [item for item in page.get_text("blocks") if
                                 ((item[0] + item[2]) / 2 > page.rect.width / 2) == right_column]
                source = clean_text(" ".join(item[4] for item in sorted(source_blocks, key=lambda item: item[1])))
                head = " ".join(first.text.split()[:5])
                tail = " ".join(second.text.split()[-5:])
                start = source.casefold().find(head.casefold())
                end = source.casefold().find(tail.casefold(), start + len(head)) if start >= 0 else -1
                recovered = source[start:end + len(tail)] if end > start else ""
                old_length = len(first.text) + len(second.text)
                if not recovered or not .75 <= len(recovered) / max(old_length, 1) <= 1.25:
                    index += 1
                    continue
                first.text = recovered
                first.content = _numbered_citations(recovered, model.references)
                first.bbox = {"x": min(a["x"], b["x"]), "y": min(a["y"], b["y"]),
                              "width": max(a["x"] + a["width"], b["x"] + b["width"]) - min(a["x"], b["x"]),
                              "height": max(a["y"] + a["height"], b["y"] + b["height"]) - min(a["y"], b["y"])}
                section.blocks.pop(index + 1)
                repaired += 1
                index += 1
    return repaired


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
