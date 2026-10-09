"""Conservative PDF geometry checks for inline math and original-view fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .model import Block, DocumentModel, InlineNode


MATH_FONT = re.compile(r"(?:math|cmmi|cmsy|msbm|symbol|stix|mt2|euler)", re.I)
MATH_GLYPH = re.compile(r"[α-ωΑ-Ω∑∫∂∇≈≤≥≠∈⊂⊕⊗∀∃√∞₀-₉⁰-⁹]")


def _inline_candidate(text: str) -> bool:
    text = text.strip()
    if len(text) < 4 or re.search(r"[=+−\-*/∈<{,]$", text):
        return False
    if any(text.count(left) != text.count(right) for left, right in (("(", ")"), ("{", "}"), ("[", "]"))):
        return False
    return bool(re.search(r"[A-Za-zα-ωΑ-Ω]", text) and re.search(r"[=+−*/|∈<>()[\]{}₀-₉⁰-⁹]", text))


def recover_inline_pdf_formulas(model: DocumentModel, pdf_path: Path, folder: Path, limit: int = 8,
                                protected_block_ids: set[str] | None = None) -> int:
    """Crop only math-font runs that align exactly with a paragraph substring.

    Ambiguous symbols and runs crossing a text line are left as extracted text.
    The crop is the reading source; later vision output is a separate transcript.
    """
    try:
        import pymupdf
        import pypdfium2 as pdfium
    except ImportError:
        return 0
    protected = protected_block_ids or set()
    paragraphs = [block for section in model.sections for block in section.blocks
                  if block.type == "paragraph" and block.page and block.bbox and len(block.text) >= 35
                  and block.id not in protected
                  and (not block.content or all(node.type == "text" for node in block.content))]
    count = 0
    with pymupdf.open(pdf_path) as geometry:
        raster = pdfium.PdfDocument(str(pdf_path))
        try:
            for page_number, page in enumerate(geometry, start=1):
                if count >= limit:
                    break
                for pdf_block in page.get_text("dict")["blocks"]:
                    for line in pdf_block.get("lines", []):
                        spans = line.get("spans", [])
                        run: list[dict] = []
                        for index in range(len(spans) + 1):
                            span = spans[index] if index < len(spans) else None
                            font = span.get("font", "") if span else ""
                            math = bool(span and (MATH_FONT.search(font) or re.search(r"\bCMR\d|\bCMBX\d", font, re.I)))
                            if math:
                                run.append(span)
                                continue
                            if run:
                                original = "".join(item["text"] for item in run).strip()
                                strong = any(MATH_FONT.search(item["font"]) for item in run)
                                prose = any(len(item["text"].strip()) >= 8 and not MATH_FONT.search(item["font"])
                                            for item in spans[:index - len(run)] + spans[index:])
                                if (strong and prose and 2 <= len(run) and len(original) <= 65
                                        and _inline_candidate(original) and count < limit):
                                    top = min(item["bbox"][1] for item in run)
                                    bottom = max(item["bbox"][3] for item in run)
                                    prose_spans = [item for item in spans if item not in run and len(item["text"].strip()) >= 8]
                                    baseline = sorted(item["origin"][1] for item in run)[len(run) // 2]
                                    if prose_spans and max(item["origin"][1] for item in run) - baseline < 1:
                                        bottom = min(bottom, min(item["bbox"][3] for item in prose_spans) + .5)
                                    left = min(item["bbox"][0] for item in run)
                                    right = max(item["bbox"][2] for item in run)
                                    owners = [block for block in paragraphs if block.page == page_number and block.bbox
                                              and block.bbox["x"] - 3 <= left and block.bbox["x"] + block.bbox["width"] + 3 >= right
                                              and block.bbox["y"] - 3 <= top and block.bbox["y"] + block.bbox["height"] + 3 >= bottom
                                              and block.text.count(original) == 1 and not any(node.type == "inlineEquation" for node in block.content)]
                                    if len(owners) == 1:
                                        owner = owners[0]
                                        start = owner.text.index(original)
                                        box = {"x": left, "y": top, "width": right - left, "height": bottom - top}
                                        asset = folder / "assets" / f"inline_{owner.id}.png"
                                        if _crop(raster, page_number, box, asset, margin=0):
                                            owner.content = [InlineNode(type="text", text=owner.text[:start]),
                                                             InlineNode(type="inlineEquation", text=original,
                                                                        src=f"/api/documents/{model.id}/assets/{asset.name}",
                                                                        source="pdf_original_crop"),
                                                             InlineNode(type="text", text=owner.text[start + len(original):])]
                                            count += 1
                                run = []
        finally:
            raster.close()
    return count


def _overlaps(first: dict[str, float], second: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = second
    return min(first["x"] + first["width"], right) > max(first["x"], left) and min(first["y"] + first["height"], bottom) > max(first["y"], top)


def _crop(pdf: Any, page_number: int, box: dict[str, float], target: Path, margin: float = 2,
          omit: list[tuple[float, float, float, float]] | None = None) -> bool:
    try:
        page = pdf[page_number - 1]
        width, height = page.get_size()
        scale = 2.5
        left = max(0, box["x"] - margin)
        top = max(0, box["y"] - margin)
        right = min(width, box["x"] + box["width"] + margin)
        bottom = min(height, box["y"] + box["height"] + margin)
        if right <= left or bottom <= top:
            return False
        image = page.render(scale=scale, crop=(left, height - bottom, width - right, top)).to_pil()
        # A panel legend can extend into a column gutter. Omit an adjacent
        # caption in that gutter without truncating the legend above it.
        for rect in omit or []:
            image.paste('white', (max(0,int((rect[0]-left-1)*scale)),max(0,int((rect[1]-top-1)*scale)),
                                 min(image.width,int((rect[2]-left+1)*scale)+1),min(image.height,int((rect[3]-top+1)*scale)+1)))
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        return True
    except (IndexError, KeyError, OSError, ValueError):
        return False


def pdf_figure_caption_blocks(page: Any) -> list:
    """Find captions even when the PDF groups them with the diagram above."""
    entries = page.get_text('blocks')
    found = []
    for entry in entries:
        if re.match(r'^Fig(?:ure)?\.?\s*\d+\s*[:.]',entry[4],re.I):
            found.append(entry)
    for block in page.get_text('dict')['blocks']:
        lines = block.get('lines',[])
        for index,line in enumerate(lines):
            text = ''.join(s['text'] for s in line['spans']).strip()
            if index == 0 or not re.match(r'^Fig(?:ure)?\.?\s*\d+\s*[:.]',text,re.I):
                continue
            tail = lines[index:]
            found.append((min(l['bbox'][0] for l in tail),min(l['bbox'][1] for l in tail),
                          max(l['bbox'][2] for l in tail),max(l['bbox'][3] for l in tail),
                          ' '.join(''.join(s['text'] for s in l['spans']) for l in tail),0,0))
    return found


def recover_missing_pdf_figures(model: DocumentModel, pdf_path: Path, folder: Path,
                               protected_block_ids: set[str] | None = None) -> int:
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
    protected = protected_block_ids or set()
    recovered = 0
    with pymupdf.open(pdf_path) as geometry:
        raster = pdfium.PdfDocument(str(pdf_path))
        try:
            for page_index, page in enumerate(geometry):
                images = [tuple(info["bbox"]) for info in page.get_image_info()
                          if info["bbox"][2] - info["bbox"][0] >= 25
                          and info["bbox"][3] - info["bbox"][1] >= 25]
                for caption_box in pdf_figure_caption_blocks(page):
                    caption = clean_text(caption_box[4])
                    match = re.match(r"^Fig(?:ure)?\.?(?:\s*)(\d+)\s*[:.]\s*(.+)", caption, re.I)
                    if not match:
                        continue
                    number = int(match.group(1))
                    if number in existing:
                        continue
                    # A schema panel can be labeled as code rather than a
                    # picture. Keep its anchor and crop, but expose its caption
                    # and figure number so in-text navigation works.
                    schema = next((b for s in model.sections for b in s.blocks if
                                   b.type=='code' and b.src and b.page==page_index+1 and b.bbox
                                   and b.id not in protected and -3<=caption_box[1]-b.bbox['y']-b.bbox['height']<=35
                                   and min(caption_box[2],b.bbox['x']+b.bbox['width'])-max(caption_box[0],b.bbox['x'])
                                   >.35*min(caption_box[2]-caption_box[0],b.bbox['width'])),None)
                    if schema:
                        schema.type,schema.number,schema.label = 'figure',number,f'Figure {number}'
                        schema.caption = match.group(2)
                        schema.captionContent = _numbered_citations(schema.caption,model.references)
                        model.figures.append(schema)
                        existing.add(number)
                        recovered += 1
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
                    nearby = [box for box in images if 0 <= caption_box[1] - box[3] <= 35
                              and min(box[2], caption_box[2]) - max(box[0], caption_box[0]) > 0]
                    if nearby:
                        anchor = max(nearby, key=lambda box: box[2] - box[0])
                        group = [box for box in nearby if abs(box[1] - anchor[1]) <= 18]
                        left = min(box[0] for box in group)
                        top = min(box[1] for box in group)
                        right = max(box[2] for box in group)
                        bottom = max(box[3] for box in group)
                        if right - left < page.rect.width * .18 or bottom > caption_box[1] - 2:
                            continue
                    else:
                        # Plots may be PDF vector drawings rather than embedded images.
                        side_left = page.rect.width / 2 if caption_box[0] > page.rect.width / 2 else 0
                        side_right = page.rect.width if side_left else page.rect.width / 2
                        # Another figure's caption or a body paragraph is a hard
                        # boundary. A fixed look-back window can mix two stacked plots.
                        boundary = max((entry[3] + 2 for entry in page.get_text("blocks")
                                        if entry[3] < caption_box[1] - 3
                                        and min(entry[2], caption_box[2]) - max(entry[0], caption_box[0]) > 30
                                        and (re.match(r"^Fig(?:ure)?\.?\s*\d+\s*[:.]", clean_text(entry[4]), re.I)
                                             or len(clean_text(entry[4])) > 160)), default=0)
                        floor = max(boundary, caption_box[1] - 450)
                        drawings = [tuple(drawing["rect"]) for drawing in page.get_drawings()
                                    if drawing["rect"].x0 >= side_left - 3
                                    and drawing["rect"].x1 <= side_right + 3
                                    and floor <= drawing["rect"].y0
                                    and drawing["rect"].y1 <= caption_box[1] - 2
                                    and drawing["rect"].width > 2 and drawing["rect"].height > 1]
                        if not drawings:
                            continue
                        left = min(box[0] for box in drawings)
                        top = min(box[1] for box in drawings)
                        right = max(box[2] for box in drawings)
                        bottom = max(box[3] for box in drawings)
                        if (right - left < page.rect.width * .25 or bottom - top < 60
                                or caption_box[1] - bottom > 35):
                            continue
                        for entry in page.get_text("blocks"):
                            if (entry[0] >= side_left and entry[2] <= side_right
                                    and max(floor, top - 20) <= entry[1] and entry[3] < caption_box[1] - 2):
                                left, top = min(left, entry[0]), min(top, entry[1])
                                right, bottom = max(right, entry[2]), max(bottom, entry[3])
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


def attach_pdf_figure_captions(model: DocumentModel, pdf_path: Path) -> int:
    """Pair an existing uncaptioned visual with a numbered caption below it.

    A page can contain several columns and figures, so require horizontal
    overlap and a short vertical gap. Keep an unpaired visual unnumbered.
    """
    try:
        import pymupdf
    except ImportError:
        return 0
    from .normalizer import _numbered_citations, clean_text

    used = {figure.number for figure in model.figures if figure.number is not None}
    attached = 0
    with pymupdf.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf):
            for entry in page.get_text("blocks"):
                caption = clean_text(entry[4])
                match = re.match(r"^Fig(?:ure)?\.?\s*(\d+)\s*[:.]\s*(.+)", caption, re.I)
                if not match:
                    continue
                number = int(match.group(1))
                if number in used:
                    continue
                candidates = []
                for figure in model.figures:
                    box = figure.bbox
                    if figure.page != page_index + 1 or figure.number is not None or not box:
                        continue
                    gap = entry[1] - (box["y"] + box["height"])
                    overlap = max(0, min(entry[2], box["x"] + box["width"]) - max(entry[0], box["x"]))
                    if -3 <= gap <= 35 and overlap >= min(entry[2] - entry[0], box["width"]) * .35:
                        candidates.append((gap, figure))
                if len(candidates) != 1:
                    continue
                figure = candidates[0][1]
                old_id = figure.id
                body = match.group(2)
                for block in [figure, *(block for section in model.sections for block in section.blocks if block.id == old_id)]:
                    block.id = f"figure-{number}"
                    block.number = number
                    block.label = f"Figure {number}"
                    block.caption = body
                    block.captionContent = _numbered_citations(body, model.references)
                used.add(number)
                attached += 1
    return attached


def remove_unlabeled_pdf_decoration(model: DocumentModel, protected_block_ids: set[str] | None = None) -> int:
    """Exclude narrow margin marks and tiny icons from the figure collection."""
    protected = protected_block_ids or set()
    discard = {figure.id for figure in model.figures if figure.id not in protected and not figure.caption
               and figure.bbox and (figure.bbox["width"] < 24 or figure.bbox["height"] < 24)}
    if not discard:
        return 0
    model.figures = [figure for figure in model.figures if figure.id not in discard]
    for section in model.sections:
        section.blocks = [block for block in section.blocks if block.id not in discard]
    return len(discard)


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


def expand_pdf_figure_crops(model: DocumentModel, pdf_path: Path, folder: Path,
                            protected_block_ids: set[str] | None = None) -> int:
    """Restore upper panels and axis labels omitted from a caption's picture box."""
    import pymupdf
    import pypdfium2 as pdfium
    from .normalizer import clean_text
    protected = protected_block_ids or set()
    changed = 0
    with pymupdf.open(pdf_path) as pdf:
        raster = pdfium.PdfDocument(str(pdf_path))
        try:
            for figure in list(model.figures):
                if not figure.number or not figure.page or not figure.bbox or figure.id in protected:
                    continue
                page = pdf[figure.page-1]
                captions = [entry for entry in page.get_text('blocks') if re.match(
                    rf'^Fig(?:ure)?\.?\s*{figure.number}\s*[:.]',clean_text(entry[4]),re.I)]
                if len(captions) != 1:
                    continue
                caption = captions[0]
                box = figure.bbox
                if caption[1] < box['y'] + box['height'] - 5 or caption[1] - box['y'] - box['height'] > 60:
                    continue
                wide = caption[2]-caption[0] > page.rect.width*.55
                left_column = caption[0] < page.rect.width/2
                left = 0 if wide or left_column else page.rect.width/2 - 30
                right = page.rect.width if wide or not left_column else page.rect.width/2 + 30
                boundaries = [entry[3]+2 for entry in page.get_text('blocks')
                              if entry[3] < box['y'] and min(entry[2],caption[2])-max(entry[0],caption[0]) > 30
                              and (re.match(r'^(?:Fig(?:ure)?\.?|Table)\s*\d+\s*[:.]',clean_text(entry[4]),re.I)
                                   or len(clean_text(entry[4])) > 200)]
                for entry in page.get_text('dict')['blocks']:
                    spans = [span for line in entry.get('lines',[]) for span in line['spans']]
                    if (spans and entry['bbox'][3] < box['y'] and max(s['size'] for s in spans)>=9
                            and sum(len(s['text']) for s in spans)>20 and entry['bbox'][2]-entry['bbox'][0]>80
                            and min(entry['bbox'][2],caption[2])-max(entry['bbox'][0],caption[0])>30):
                        boundaries.append(entry['bbox'][3]+2)
                for table in model.tables:
                    if table.page==figure.page and table.bbox and table.bbox['y']+table.bbox['height']<box['y']:
                        boundaries.append(table.bbox['y']+table.bbox['height']+2)
                floor = max([caption[1]-600,0,*boundaries])
                components = [tuple(info['bbox']) for info in page.get_image_info()]
                components += [tuple(d['rect']) for d in page.get_drawings() if d['rect'].width>2 and d['rect'].height>2]
                components = [b for b in components if b[0]>=left-3 and b[2]<=right+3
                              and b[1]>=floor and b[3]<caption[1]-2 and b[2]-b[0]>2 and b[3]-b[1]>2]
                if not components:
                    continue
                # Grow from the detected panel. Remote rules, page backgrounds
                # and headers are not part of the graphic merely because they
                # occur in the same column above its caption.
                extent = (box['x'],box['y'],box['x']+box['width'],box['y']+box['height'])
                connected = []
                remaining = components.copy()
                while remaining:
                    added = []
                    for part in remaining:
                        x_overlap = min(extent[2],part[2])-max(extent[0],part[0])
                        y_overlap = min(extent[3],part[3])-max(extent[1],part[1])
                        if (x_overlap>=10 and y_overlap>=-35) or (y_overlap>=10 and x_overlap>=-18):
                            added.append(part)
                    if not added:
                        break
                    connected.extend(added)
                    remaining = [part for part in remaining if part not in added]
                    extent = (min(extent[0],*(p[0] for p in added)),min(extent[1],*(p[1] for p in added)),
                              max(extent[2],*(p[2] for p in added)),max(extent[3],*(p[3] for p in added)))
                components = connected
                if not components:
                    continue
                x0,y0 = min(b[0] for b in components),min(b[1] for b in components)
                x1,y1 = max(b[2] for b in components),max(b[3] for b in components)
                for entry in page.get_text('blocks'):
                    if (entry[0]>=left and entry[2]<=right and max(floor,y0-15)<=entry[1]
                            and entry[3]<caption[1]-2 and len(clean_text(entry[4]))<160):
                        x0,y0,x1,y1 = min(x0,entry[0]),min(y0,entry[1]),max(x1,entry[2]),max(y1,entry[3])
                overlap = max(0,min(x1,box['x']+box['width'])-max(x0,box['x'])) * max(
                    0,min(y1,box['y']+box['height'])-max(y0,box['y']))
                area = box['width']*box['height']
                if overlap < .85*area or (x1-x0)*(y1-y0) < 1.18*area:
                    continue
                expanded = dict(x=x0,y=y0,width=x1-x0,height=y1-y0)
                omit = [tuple(entry[:4]) for entry in page.get_text('blocks')
                        if entry is not caption and re.match(r'^Fig(?:ure)?\.?\s*\d+\s*[:.]',clean_text(entry[4]),re.I)
                        and entry[1]<y1 and entry[3]>y0 and entry[0]<x1 and entry[2]>x0]
                if any(min(x1,r[2])-max(x0,r[0])>.15*(x1-x0) for r in omit):
                    continue
                headers = [tuple(entry[:4]) for entry in page.get_text('blocks') if entry[3]<box['y']
                           and entry[1]<page.rect.height*.08
                           and re.search(r'IEEE|TRANSACTIONS|arXiv|doi\.org',entry[4])]
                for header in headers:
                    if header[1]<expanded['y']+expanded['height'] and header[3]>expanded['y']:
                        bottom = expanded['y']+expanded['height']
                        expanded['y'] = header[3]+3
                        expanded['height'] = bottom-expanded['y']
                asset = folder/'assets'/f'expanded_{figure.id}.png'
                if not _crop(raster,figure.page,expanded,asset,omit=omit):
                    continue
                figure.bbox,figure.src,figure.source = expanded,f'/api/documents/{model.id}/assets/{asset.name}','pdf_original_crop'
                changed += 1
        finally:
            raster.close()
    return changed


def repair_pdf_word_spacing(model: DocumentModel, pdf_path: Path, protected_block_ids: set[str] | None = None) -> int:
    """Restore spaces and line hyphens only when every source character agrees."""
    import pymupdf
    from .normalizer import clean_text, _numbered_citations
    protected = protected_block_ids or set()
    changed = 0
    compact = lambda value: re.sub(r"[\s-]+", "", clean_text(value))
    with pymupdf.open(pdf_path) as pdf:
        for section in model.sections:
            for block in section.blocks:
                if (block.type != 'paragraph' or not block.page or not block.bbox
                        or block.id in protected or len(block.text) < 60
                        or any(n.type not in {'text','citation'} or n.bold or n.italic for n in block.content)):
                    continue
                box = block.bbox
                source = clean_text(pdf[block.page-1].get_text('text',clip=pymupdf.Rect(
                    box['x']-1,box['y']-1,box['x']+box['width']+1,box['y']+box['height']+1)))
                if source and source != block.text and compact(source) == compact(block.text):
                    block.text = source
                    block.content = _numbered_citations(source,model.references)
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
