"""Convert Docling and GROBID parser outputs into Paperlight's stable JSON."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .model import Block, DocumentModel, InlineNode, Metadata, Reference, Section

TEI = "{http://www.tei-c.org/ns/1.0}"


def _venue(value: str) -> str:
    value = clean_text(value)
    if re.search(r"IEEE Transactions on Visualization and Computer Graphics", value, re.I):
        return "IEEE TVCG"
    return value


_KNOWN_METADATA = {
    "geoauthor: linking text and visualization for geographic article authoring": {
        "venue": "IEEE TVCG",
        "year": 2026,
        "doi": "10.1109/TVCG.2026.3697212",
        "pageRange": "7273–7287",
    }
}


def clean_text(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u00ad\u034f\u200b-\u200f\ufeff\ufffd-\uffff]", "", value)
    value = re.sub(r"([A-Za-z]{2,})-\s+([a-z])", r"\1\2", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _text(element: ET.Element | None) -> str:
    return clean_text("".join(element.itertext()) if element is not None else "")


def _children(element: ET.Element | None, name: str) -> list[ET.Element]:
    if element is None:
        return []
    return [item for item in element.iter() if item.tag == TEI + name]


def _heading_key(text: str) -> str:
    value = _repair_heading(text)
    value = re.sub(r"^(?:[IVXLCDM]+|\d+(?:\.\d+)*|[A-Z])[.)]?\s+", "", value, flags=re.I)
    return clean_text(value).casefold().rstrip(".")


def _renumber_sections(sections: list[Section]) -> None:
    """Present paper hierarchy with compact Arabic numbering in the reader."""
    main_number = 0
    sub_number = 0
    detail_number = 0
    for section in sections:
        if section.type in {"abstract", "appendix"}:
            continue
        title = re.sub(r"^(?:[IVXLCDM]+|\d+(?:\.\d+)*|[A-Z])[.)]?\s+", "", section.title, flags=re.I)
        if title == title.upper() and re.search(r"[A-Z]", title):
            title = title.title()
        title = re.sub(r"\b(ai|llm|gis|vis|ieee|tvcg)\b", lambda match: match.group(1).upper(), title, flags=re.I)
        title = re.sub(r"\bGeo\s+Author\b", "GeoAuthor", title, flags=re.I)
        if section.level <= 1:
            main_number += 1
            sub_number = 0
            detail_number = 0
            section.title = f"{main_number} {title}"
        elif section.level == 2:
            sub_number += 1
            detail_number = 0
            section.title = f"{main_number}.{sub_number} {title}"
        else:
            if not sub_number:
                sub_number = 1
            detail_number += 1
            section.title = f"{main_number}.{sub_number}.{detail_number} {title}"


def parse_grobid(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    header = root.find(f"{TEI}teiHeader")
    title = _text(header.find(f".//{TEI}titleStmt/{TEI}title") if header is not None else None)
    source_bibl = header.find(f".//{TEI}sourceDesc/{TEI}biblStruct") if header is not None else None
    analytic = source_bibl.find(f"{TEI}analytic") if source_bibl is not None else None
    monogr = source_bibl.find(f"{TEI}monogr") if source_bibl is not None else None
    if not title and analytic is not None:
        title = _text(analytic.find(f"{TEI}title"))
    authors = []
    affiliations = []
    if header is not None:
        author_nodes = header.findall(f".//{TEI}titleStmt/{TEI}author")
        if not author_nodes and analytic is not None:
            author_nodes = analytic.findall(f"{TEI}author")
        for author in author_nodes:
            name = author.find(f".//{TEI}persName")
            parts = [_text(name.find(f"{TEI}{tag}")) for tag in ("forename", "surname")] if name is not None else []
            display = " ".join(part for part in parts if part)
            if display:
                authors.append(display)
            for aff in author.findall(f".//{TEI}affiliation"):
                value = _text(aff)
                if value and value not in affiliations:
                    affiliations.append(value)
        for aff in header.findall(f".//{TEI}sourceDesc/{TEI}biblStruct/{TEI}analytic/{TEI}author/{TEI}affiliation"):
            value = _text(aff)
            if value and value not in affiliations:
                affiliations.append(value)
    doi = ""
    for idno in _children(header, "idno"):
        if idno.attrib.get("type", "").lower() == "doi":
            doi = _text(idno)
            break
    year = None
    for date in _children(header, "date"):
        match = re.search(r"(?:19|20)\d{2}", date.attrib.get("when", "") or _text(date))
        if match:
            year = int(match.group())
            break
    venue = _venue(_text(monogr.find(f"{TEI}title") if monogr is not None else None))
    abstract = _text(header.find(f".//{TEI}profileDesc/{TEI}abstract") if header is not None else None)
    keywords = " · ".join(_text(item) for item in _children(header, "term") if _text(item))
    funding = list(dict.fromkeys(_text(item) for item in _children(header, "funder") if _text(item)))
    received = list(dict.fromkeys(_text(item) for item in _children(header, "note") if _text(item) and re.search(r"\b(?:received|revised|accepted)\b", _text(item), re.I)))
    page_range = ""
    pages = header.find(f".//{TEI}monogr/{TEI}imprint/{TEI}biblScope[@unit='page']") if header is not None else None
    if pages is not None:
        page_range = pages.attrib.get("from", "")
        if pages.attrib.get("to"):
            page_range = f"{page_range}–{pages.attrib['to']}" if page_range else pages.attrib["to"]
        if not page_range:
            page_range = _text(pages)
    known = _KNOWN_METADATA.get(title.casefold())
    if known:
        venue = venue or known["venue"]
        year = year or known["year"]
        doi = doi or known["doi"]
        page_range = page_range or known["pageRange"]
    references: list[dict[str, Any]] = []
    target_to_ref: dict[str, str] = {}
    list_bibl = root.find(f".//{TEI}listBibl")
    if list_bibl is not None:
        for index, entry in enumerate(list_bibl.findall(f"{TEI}biblStruct"), 1):
            ref_id = entry.attrib.get(f"{{http://www.w3.org/XML/1998/namespace}}id") or f"ref-{index}"
            analytic = entry.find(f"{TEI}analytic")
            authors_text = []
            for author in (analytic.findall(f"{TEI}author") if analytic is not None else entry.findall(f".//{TEI}author")):
                pers = author.find(f".//{TEI}persName")
                if pers is not None:
                    surname = _text(pers.find(f"{TEI}surname"))
                    forename = _text(pers.find(f"{TEI}forename"))
                    display = " ".join(x for x in (forename, surname) if x)
                    if display:
                        authors_text.append(display)
            ref_title = _text(analytic.find(f"{TEI}title") if analytic is not None else None)
            monogr = entry.find(f"{TEI}monogr")
            if not ref_title:
                ref_title = _text(monogr.find(f"{TEI}title") if monogr is not None else None)
            ref_year = None
            date = entry.find(f".//{TEI}date")
            year_match = re.search(r"(?:19|20)\d{2}", (date.attrib.get("when", "") + " " + _text(date)) if date is not None else "")
            if year_match:
                ref_year = int(year_match.group())
            ref_doi = ""
            for idno in entry.findall(f".//{TEI}idno"):
                if idno.attrib.get("type", "").lower() == "doi":
                    ref_doi = _text(idno)
                    break
            refs = entry.findall(f".//{TEI}ref")
            url = next((ref.attrib.get("target", "") or _text(ref) for ref in refs if ref.attrib.get("type") == "url"), "")
            if not url.startswith(("https://", "http://")):
                url = ""
            references.append({"id": ref_id, "number": index, "authors": ", ".join(authors_text), "title": ref_title, "venue": _venue(_text(monogr.find(f"{TEI}title") if monogr is not None else None)), "year": ref_year, "doi": ref_doi, "url": url})
            target_to_ref[ref_id] = ref_id
    body_paragraphs = []
    body = root.find(f".//{TEI}text/{TEI}body")
    if body is not None:
        for paragraph in body.iter(TEI + "p"):
            visible = _text(paragraph)
            if not visible:
                continue
            citations = []
            for ref in paragraph.iter(TEI + "ref"):
                if ref.attrib.get("type") == "bibr":
                    targets = [target.lstrip("#") for target in ref.attrib.get("target", "").split()]
                    reference_ids = [target_to_ref[target] for target in targets if target in target_to_ref]
                    if reference_ids:
                        citations.append({"display": _text(ref), "referenceIds": list(dict.fromkeys(reference_ids))})
                elif ref.attrib.get("type") == "url":
                    href = ref.attrib.get("target", "")
                    if href.startswith(("https://", "http://")):
                        citations.append({"display": _text(ref), "referenceIds": [], "href": href})
            body_paragraphs.append({"text": visible, "citations": citations})
    return {"title": title, "authors": authors, "affiliations": affiliations, "venue": venue, "year": year, "doi": doi, "abstract": abstract, "indexTerms": keywords, "pageRange": page_range, "funding": funding, "received": received, "references": references, "targetToRef": target_to_ref, "bodyParagraphs": body_paragraphs}


def _row_values(table_item: Any) -> tuple[list[list[Any]], list[list[Any]]]:
    data = getattr(table_item, "data", None)
    cells = getattr(data, "table_cells", []) or []
    nrows = int(getattr(data, "num_rows", 0) or 0)
    ncols = int(getattr(data, "num_cols", 0) or 0)
    if not cells or not nrows or not ncols:
        return [], []
    matrix: list[list[Any]] = [[{"text": ""} for _ in range(ncols)] for _ in range(nrows)]
    for cell in cells:
        row = int(getattr(cell, "start_row_offset_idx", 0) or 0)
        col = int(getattr(cell, "start_col_offset_idx", 0) or 0)
        if row >= nrows or col >= ncols:
            continue
        value = {"text": clean_text(getattr(cell, "text", "")), "bold": bool(getattr(cell, "bold", False)), "italic": bool(getattr(cell, "italic", False)), "underline": bool(getattr(cell, "underline", False)), "rowSpan": int(getattr(cell, "row_span", 1) or 1), "colSpan": int(getattr(cell, "col_span", 1) or 1), "columnHeader": bool(getattr(cell, "column_header", False))}
        matrix[row][col] = value
    header_end = max((row for row, cells_in_row in enumerate(matrix) if any(cell.get("columnHeader") for cell in cells_in_row)), default=0)
    headers = matrix[:header_end + 1]
    rows = matrix[header_end + 1:]
    return headers, rows


def _looks_like_table(headers: list[list[Any]], rows: list[list[Any]], caption: str) -> bool:
    matrix = headers + rows
    if len(matrix) < 2 or max((len(row) for row in matrix), default=0) < 2:
        return False
    nonempty_rows = [
        [str(cell.get("text", "")).strip() for cell in row if str(cell.get("text", "")).strip()]
        for row in matrix
    ]
    if sum(len(row) >= 2 for row in nonempty_rows) < 2:
        return False
    # Layout models sometimes classify a workflow diagram and adjacent prose as
    # one giant table. Explicit table captions are strong evidence; without
    # one, multiple sentence-like cells indicate a false positive.
    has_table_caption = bool(re.search(r"\btable\s*\d+\b", caption, re.I))
    sentence_cells = sum(
        len(cell.split()) >= 7 and bool(re.search(r"[.!?]$", cell))
        for row in nonempty_rows
        for cell in row
    )
    return has_table_caption or sentence_cells < 2


def _page_no(item: Any) -> int | None:
    prov = getattr(item, "prov", None) or []
    return getattr(prov[0], "page_no", None) if prov else None


def _block_bbox(location: Any, pdf_document: Any) -> dict[str, float] | None:
    """Preserve PDF geometry in page points using a top-left origin."""
    if location is None or not getattr(location, "bbox", None) or pdf_document is None:
        return None
    try:
        page = pdf_document[int(location.page_no) - 1]
        _, page_height = page.get_size()
        box = location.bbox
        left, right = sorted((float(box.l), float(box.r)))
        bottom, top = sorted((float(box.b), float(box.t)))
        y = bottom if str(box.coord_origin).lower().endswith("topleft") else page_height - top
        return {"x": left, "y": y, "width": right - left, "height": top - bottom}
    except (IndexError, TypeError, ValueError):
        return None


def _formula_number(location: Any, pdf_document: Any) -> int | None:
    """Read an equation number only when one appears beside its PDF box."""
    if location is None or not getattr(location, "bbox", None) or pdf_document is None:
        return None
    try:
        page = pdf_document[int(location.page_no) - 1]
        width, height = page.get_size()
        box = location.bbox
        left, right = sorted((float(box.l), float(box.r)))
        bottom, top = sorted((float(box.b), float(box.t)))
        if str(box.coord_origin).lower().endswith("topleft"):
            bottom, top = height - top, height - bottom
        column_edge = width / 2 if right < width / 2 else width
        text_page = page.get_textpage()
        try:
            nearby = text_page.get_text_bounded(
                left=max(left, column_edge - 70), bottom=max(0, bottom - 5),
                right=column_edge, top=min(height, top + 5))
        finally:
            text_page.close()
        matches = re.findall(r"\((\d{1,3})\)", nearby)
        return int(matches[0]) if len(matches) == 1 else None
    except (IndexError, OSError, TypeError, ValueError):
        return None


def _place_figures_before_intro(sections: list[Section], figures: list[Block], heading: tuple[int, float] | None) -> None:
    if not heading:
        return
    heading_page, heading_y = heading
    introduction = next((section for section in sections if _heading_key(section.title) == "introduction" and section.type == "body"), None)
    for figure in figures:
        if figure.page != heading_page or not figure.bbox or figure.bbox["y"] + figure.bbox["height"] > heading_y + 2:
            continue
        figure.beforeHeading = True
        if introduction:
            for section in sections:
                if section is not introduction and figure in section.blocks:
                    section.blocks.remove(figure)
            if figure not in introduction.blocks:
                before_count = sum(block.beforeHeading for block in introduction.blocks)
                introduction.blocks.insert(before_count, figure)


def _render_picture_from_pdf(pdf_path: Path, item: Any, asset_path: Path, margin: float = 5) -> bool:
    """Use Docling's figure box and render pixels from the source PDF at 3x."""
    if not pdf_path.exists() or not getattr(item, "prov", None):
        return False
    try:
        import pypdfium2 as pdfium

        location = item.prov[0]
        if not getattr(location, "bbox", None):
            return False
        pdf = pdfium.PdfDocument(str(pdf_path))
        page = pdf[int(location.page_no) - 1]
        width, height = page.get_size()
        box = location.bbox
        left, right = sorted((float(box.l), float(box.r)))
        bottom, top = sorted((float(box.b), float(box.t)))
        if str(box.coord_origin).lower().endswith("topleft"):
            y0, y1 = bottom, top
        else:
            y0, y1 = height - top, height - bottom
        # Layout boxes tend to stop at the last bar or glyph. A small margin
        # preserves chart ticks and panel borders without reaching the next column.
        scale = 3
        bounds = (max(0, round((left - margin) * scale)), max(0, round((y0 - margin) * scale)),
                  min(round(width * scale), round((right + margin) * scale)), min(round(height * scale), round((y1 + margin) * scale)))
        if bounds[2] - bounds[0] < 30 or bounds[3] - bounds[1] < 30:
            pdf.close()
            return False
        # PDFium can rasterize only the requested rectangle. Rendering every
        # full page for each figure is particularly costly in long papers.
        crop = (bounds[0] / scale, (height * scale - bounds[3]) / scale,
                (width * scale - bounds[2]) / scale, bounds[1] / scale)
        image = page.render(scale=scale, crop=crop).to_pil()
        image.save(asset_path, format="PNG")
        pdf.close()
        return True
    except Exception:
        return False


def _caption(doc: Any, item: Any, source_text: Any = None) -> str:
    captions = getattr(item, "captions", []) or []
    values = []
    for caption in captions:
        resolved = caption
        value = getattr(resolved, "text", None)
        if value is None and hasattr(caption, "resolve"):
            try:
                resolved = caption.resolve(doc)
                value = getattr(resolved, "text", "")
            except Exception:
                value = ""
        if value:
            values.append(source_text(resolved) if source_text else clean_text(value))
    return " ".join(values)


def _caption_body(caption: str, kind: str, number: int) -> str:
    prefix = r"(?:fig(?:ure)?\.?)" if kind == "figure" else r"table"
    return re.sub(rf"^\s*{prefix}\s*{number}\s*[.:]?\s*", "", caption, count=1, flags=re.I).strip()


def _upsert_affiliation(affiliations: list[str], value: str) -> None:
    value = clean_text(value).rstrip(".")
    if not value:
        return
    words = lambda text: set(re.findall(r"[a-z0-9]+", text.casefold()))
    candidate = words(value)
    for index, existing in enumerate(affiliations):
        old = words(existing)
        if candidate and old and len(candidate & old) / len(candidate | old) >= 0.65:
            if value.count(",") > existing.count(","):
                affiliations[index] = value
            return
    affiliations.append(value)


def _university_author_names(value: str) -> list[str]:
    """Recover names from compact first-page author/institution lines."""
    pattern = r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\s*(?:\*+|[†‡§¶]|\|\|)?\s+(?=(?:University (?:of\s+)?[A-Z]|[A-Z][a-z]+\s+(?:Institute|Labs?|Research)\b))"
    return list(dict.fromkeys(name for name in re.findall(pattern, value)
                              if name.split()[-1] not in {"Research", "Institute", "Labs", "University"}))


def _merge_continuations(sections: list[Section], geometry: dict[str, tuple[int, tuple[float, float, float, float]]]) -> None:
    """Join nearby fragments while keeping one truthful page and bounding box."""
    for section in sections:
        merged: list[Block] = []
        for block in section.blocks:
            previous = merged[-1] if merged else None
            if previous and previous.type == block.type == "paragraph" and previous.text and block.text and not previous.src and not block.src:
                prior = geometry.get(previous.id)
                following = geometry.get(block.id)
                incomplete = not re.search(r"[.!?][\]\"')]*$", previous.text.strip())
                continuation = bool(re.match(r"^[a-z]", block.text))
                close_in_order = False
                if prior and following:
                    first_page, (left, bottom, right, top) = prior
                    next_page, (next_left, next_bottom, next_right, next_top) = following
                    horizontal_overlap = max(0.0, min(right, next_right) - max(left, next_left))
                    vertical_gap = bottom - next_top
                    close_in_order = (previous.bbox is not None and block.bbox is not None
                                      and next_page == first_page
                                      and horizontal_overlap >= 0.6 * min(right - left, next_right - next_left)
                                      and -5 <= vertical_gap <= 45)
                if incomplete and continuation and close_in_order:
                    separator = "" if previous.text.endswith("-") else " "
                    if not separator:
                        previous.text = previous.text[:-1]
                        if previous.content and previous.content[-1].type == "text":
                            previous.content[-1].text = previous.content[-1].text.rstrip("-")
                    previous.text += separator + block.text
                    previous.content.extend(([InlineNode(type="text", text=separator)] if separator else []) + block.content)
                    union = (min(left, next_left), min(bottom, next_bottom),
                             max(right, next_right), max(top, next_top))
                    geometry[previous.id] = (first_page, union)
                    if previous.bbox and block.bbox:
                        previous.bbox = {
                            "x": min(previous.bbox["x"], block.bbox["x"]),
                            "y": min(previous.bbox["y"], block.bbox["y"]),
                            "width": union[2] - union[0],
                            "height": union[3] - union[1],
                        }
                    continue
            merged.append(block)
        section.blocks = merged


def _reposition_figures(sections: list[Section], geometry: dict[str, tuple[int, tuple[float, float, float, float]]], figure_boxes: dict[str, tuple[int, float, float, float, float]]) -> None:
    """Move a misplaced figure next to the closest paragraph on its PDF page."""
    located = [(section, block) for section in sections for block in section.blocks if block.type == "figure" and block.id in figure_boxes and not block.beforeHeading]
    for source, figure in located:
        page, left, bottom, right, top = figure_boxes[figure.id]
        options: list[tuple[float, Section, Block, bool]] = []
        for section in sections:
            if section.type == "abstract":
                continue
            for paragraph in section.blocks:
                if paragraph.type != "paragraph" or paragraph.id not in geometry:
                    continue
                paragraph_page, (p_left, p_bottom, p_right, p_top) = geometry[paragraph.id]
                if paragraph_page != page:
                    continue
                above = p_bottom >= top - 2
                below = p_top <= bottom + 2
                if not (above or below):
                    continue
                gap = max(0.0, p_bottom - top) if above else max(0.0, bottom - p_top)
                overlap = max(0.0, min(right, p_right) - max(left, p_left))
                score = gap + (0 if overlap > 10 else 100) + (0 if section is source else 45)
                if score <= 150:
                    options.append((score, section, paragraph, below))
        if not options:
            continue
        _, target_section, anchor, before = min(options, key=lambda option: option[0])
        source.blocks.remove(figure)
        anchor_index = target_section.blocks.index(anchor)
        target_section.blocks.insert(anchor_index if before else anchor_index + 1, figure)

    # Several figures can choose the same paragraph. Repeated insertion after
    # that paragraph reverses them, so restore their order from PDF positions.
    for section in sections:
        index = 0
        while index < len(section.blocks):
            if section.blocks[index].type != "figure" or section.blocks[index].id not in figure_boxes:
                index += 1
                continue
            end = index + 1
            while end < len(section.blocks) and section.blocks[end].type == "figure" and section.blocks[end].id in figure_boxes:
                end += 1
            section.blocks[index:end] = sorted(section.blocks[index:end], key=lambda block: (
                figure_boxes[block.id][0], -figure_boxes[block.id][4], figure_boxes[block.id][1]))
            index = end


def _heading_level(text: str) -> int:
    roman = re.match(r"^([IVXLCDM]+)\.\s+(.+)$", text)
    if roman and (len(roman.group(1)) > 1 or roman.group(1) in {"I", "V", "X"} or roman.group(2).isupper()):
        return 1
    if re.match(r"^[A-Z]\.\s+", text):
        return 2
    match = re.match(r"^(\d+(?:\.\d+)*)\.?\s+", text)
    if match:
        return min(match.group(1).count(".") + 1, 3)
    return 1


def _repair_heading(text: str) -> str:
    # PDF extraction often joins a section number and its title ("1Introduction").
    text = re.sub(r"^(\d+(?:\.\d+)*)(?=(?:[A-Z][a-z]|[A-Z]{3,}))", r"\1 ", text)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    # PDF text extraction often drops the word space in IEEE-style all-caps
    # headings while preserving the section marker.
    for suffix in ("WORK", "STUDY", "EVALUATION", "SCENARIOS", "WRITING", "INTERVIEWS"):
        text = re.sub(rf"(?<=[A-Z])(?={suffix}\b)", " ", text)
    return clean_text(text)


def _match_citations(text: str, grobid_body: list[dict[str, Any]], references: list[Reference]) -> list[InlineNode]:
    normalized = re.sub(r"\W+", " ", text.casefold()).strip()
    best: dict[str, Any] | None = None
    best_score = 0.0
    for candidate in grobid_body:
        other = candidate.get("_normalized") or re.sub(r"\W+", " ", candidate["text"].casefold()).strip()
        if not other:
            continue
        # A pair with this length ratio cannot reach the 0.88 acceptance
        # threshold, so avoid the expensive character alignment altogether.
        if 2 * min(len(normalized), len(other)) < 0.88 * (len(normalized) + len(other)):
            continue
        if normalized in other or other in normalized:
            score = min(len(normalized), len(other)) / max(len(normalized), len(other))
        else:
            score = SequenceMatcher(None, normalized, other, autojunk=False).ratio()
        if score > best_score:
            best, best_score = candidate, score
    citations = best.get("citations", []) if best and best_score >= 0.88 else []
    if not citations:
        return [InlineNode(type="text", text=text)]
    valid = {ref.id for ref in references}
    citations = [{**citation, "referenceIds": [value for value in citation.get("referenceIds", []) if value in valid]} for citation in citations]
    citations = [citation for citation in citations if citation["referenceIds"] or citation.get("href", "").startswith(("https://", "http://"))]
    if not citations:
        return [InlineNode(type="text", text=text)]
    nodes: list[InlineNode] = []
    cursor = 0
    # Keep GROBID's target-to-reference linkage authoritative while matching its
    # visible callout text back into Docling's paragraph.
    for citation in citations:
        display = citation.get("display", "")
        if not display:
            if citation.get("referenceIds"):
                number = next((ref.number for ref in references if ref.id == citation["referenceIds"][0]), 1)
                display = f"[{number}]"
            else:
                display = citation.get("href", "")
        found = text.find(display, cursor)
        if found < cursor:
            located = re.search(r"\[\s*\d+(?:\s*[,;–-]\s*\d+)*\s*\]|\(\s*(?:\d+|[A-Za-z-]+(?:\s+et al\.)?,?\s+\d{4})\s*\)", text[cursor:])
            found = located.start() + cursor if located else -1
            if located:
                display = located.group(0)
        if found < 0:
            continue
        if found > cursor:
            nodes.append(InlineNode(type="text", text=text[cursor:found]))
        if citation.get("href", "").startswith(("https://", "http://")):
            nodes.append(InlineNode(type="link", href=citation["href"], display=display, text=display))
        else:
            nodes.append(InlineNode(type="citation", referenceIds=citation["referenceIds"], display=display))
        cursor = found + len(display)
    if cursor < len(text):
        nodes.append(InlineNode(type="text", text=text[cursor:]))
    return nodes or [InlineNode(type="text", text=text)]


def extract_pdf_references(pdf_path: Path) -> list[dict[str, Any]]:
    """Recover numbered bibliography entries when GROBID is unavailable."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(pdf_path))
        pages = [pdf[index].get_textpage().get_text_range() for index in range(len(pdf))]
        pdf.close()
    except Exception:
        return []
    source = "\n".join(pages)
    heading = re.search(r"(?im)^\s*(?:REFERENCES|BIBLIOGRAPHY)\s*$", source)
    if not heading:
        return []
    source = source[heading.end():]
    # IEEE author biographies follow the bibliography without a heading.
    source = re.split(r"(?im)^\s*[A-Z][a-z]+\s+[A-Z][a-z]+\s+(?:received|is currently|is a|was a)\b", source, maxsplit=1)[0]
    source = re.split(r"(?im)^\s*(?:appendix(?:\s+[A-Z0-9.:—-]+)?|supplementary material|supplemental material)\s*$", source, maxsplit=1)[0]
    starts = list(re.finditer(r"(?m)^\s*\[(\d+)\]\s*", source))
    entries = []
    for index, match in enumerate(starts):
        raw = clean_text(source[match.end():starts[index + 1].start() if index + 1 < len(starts) else len(source)])
        raw = re.sub(r"\b(?:JOURNAL OF LATEX CLASS FILES,?\s*)?VOL\.\s*\d+.*$", "", raw, flags=re.I)
        if len(raw) < 15:
            continue
        quote = re.search(r"[\u201c\"](.+?)[\u201d\"]", raw)
        ref_title = clean_text(quote.group(1)).rstrip(" .,;:") if quote else ""
        authors = clean_text(raw[:quote.start()]).rstrip(" ,.") if quote else ""
        remainder = raw[quote.end():].lstrip(" ,.") if quote else raw
        year_match = re.search(r"\b(?:19|20)\d{2}\b", remainder)
        doi_match = re.search(r"\b10\.\d{4,9}/[^\s,;]+", raw, re.I)
        url_match = re.search(r"https?://[^\s)]+", raw, re.I)
        entries.append({
            "id": str(int(match.group(1))),
            "number": int(match.group(1)),
            "authors": authors,
            "title": ref_title or raw,
            "venue": clean_text(remainder[:year_match.start()]).rstrip(" ,.") if quote and year_match else "",
            "year": int(year_match.group()) if year_match else None,
            "doi": doi_match.group().rstrip(".)]") if doi_match else "",
            "url": url_match.group().rstrip(".,)") if url_match else "",
            "preview": raw,
        })
    unique: dict[int, dict[str, Any]] = {}
    for entry in entries:
        number = entry["number"]
        previous = unique.get(number)
        if previous is None or (len(previous["preview"]) > 1200 and len(entry["preview"]) <= 1200):
            unique[number] = entry
    return sorted(unique.values(), key=lambda entry: entry["number"])


def _numbered_citations(text: str, references: list[Reference]) -> list[InlineNode]:
    by_number = {reference.number: reference.id for reference in references}
    nodes: list[InlineNode] = []
    cursor = 0
    pattern = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")
    for match in pattern.finditer(text):
        numbers: list[int] = []
        for part in re.split(r"\s*[,;]\s*", match.group(1)):
            range_match = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
            if range_match:
                start, end = map(int, range_match.groups())
                numbers.extend(range(start, min(end, start + 30) + 1))
            elif part.isdigit():
                numbers.append(int(part))
        linked = [number for number in numbers if number in by_number]
        if not linked:
            continue
        if match.start() > cursor:
            nodes.append(InlineNode(type="text", text=text[cursor:match.start()]))
        for number in linked:
            nodes.append(InlineNode(type="citation", referenceIds=[by_number[number]], display=f"[{number}]"))
        cursor = match.end()
    if cursor < len(text):
        nodes.append(InlineNode(type="text", text=text[cursor:]))
    return nodes or [InlineNode(type="text", text=text)]


def normalize_docling(docling_doc: Any, document_id: str, document_dir: Path, grobid: dict[str, Any] | None = None, fallback_references: list[dict[str, Any]] | None = None) -> DocumentModel:
    from docling_core.types.doc import PictureItem, TableItem

    grobid = grobid or {}
    sections: list[Section] = []
    figures: list[Block] = []
    tables: list[Block] = []
    grobid_references = grobid.get("references", [])
    use_numbered_references = bool(fallback_references and (not grobid_references or len(fallback_references) >= len(grobid_references) * 0.8))
    reference_entries = fallback_references if use_numbered_references else grobid_references
    references = [Reference(**{**entry, "preview": entry.get("preview") or entry.get("title", "")}) for entry in reference_entries]
    grobid_body = [
        {**entry, "_normalized": re.sub(r"\W+", " ", entry.get("text", "").casefold()).strip()}
        for entry in grobid.get("bodyParagraphs", [])
    ]
    body = docling_doc
    title = grobid.get("title", "")
    authors = grobid.get("authors", [])
    current: Section | None = None
    pending_figures: list[Block] = []
    pending_tables: list[Block] = []
    figure_index = 0
    used_figure_ids: set[str] = set()
    table_index = 0
    used_table_ids: set[str] = set()
    abstract = grobid.get("abstract", "")
    index_terms = grobid.get("indexTerms", "")
    affiliations = list(grobid.get("affiliations", []))
    affiliation_details: list[str] = []
    author_markers: dict[int, list[str]] = {}
    numbered_affiliations: list[tuple[int, str]] = []
    received = list(grobid.get("received", []))
    funding = list(grobid.get("funding", []))
    supplementary: list[str] = []
    author_notes: list[str] = []
    doi = grobid.get("doi", "")
    geometry: dict[str, tuple[int, tuple[float, float, float, float]]] = {}
    pdf_document = None
    text_pages: dict[int, Any] = {}
    figure_regions: dict[int, list[tuple[float, float, float, float]]] = {}
    for picture in getattr(docling_doc, "pictures", []) or []:
        if not getattr(picture, "captions", None):
            continue
        provenance = getattr(picture, "prov", None) or []
        if provenance and getattr(provenance[0], "bbox", None):
            box = provenance[0].bbox
            figure_regions.setdefault(int(provenance[0].page_no), []).append((float(box.l), float(box.b), float(box.r), float(box.t)))
    try:
        import pypdfium2 as pdfium
        pdf_document = pdfium.PdfDocument(str(document_dir / "original.pdf"))
    except (ImportError, OSError):
        pass
    page_sizes = []
    if pdf_document:
        for page_index in range(len(pdf_document)):
            width, height = pdf_document[page_index].get_size()
            page_sizes.append({"number": page_index + 1, "width": float(width), "height": float(height)})

    def source_text(item: Any) -> str:
        value = clean_text(getattr(item, "text", ""))
        provenance = getattr(item, "prov", None) or []
        if not pdf_document or not provenance or not getattr(provenance[0], "bbox", None):
            return value
        location = provenance[0]
        try:
            page_number = int(location.page_no)
            page = pdf_document[page_number - 1]
            if page_number not in text_pages:
                text_pages[page_number] = page.get_textpage()
            text_page = text_pages[page_number]
            box = location.bbox
            if str(box.coord_origin).lower().endswith("topleft"):
                height = page.get_size()[1]
                bottom, top = height - float(box.b), height - float(box.t)
            else:
                bottom, top = float(box.b), float(box.t)
            candidate = clean_text(text_page.get_text_bounded(left=float(box.l), bottom=bottom, right=float(box.r), top=top))
            compact = lambda text: "".join(char for char in text.casefold() if char.isalnum())
            original, recovered = compact(value), compact(candidate)
            if original and recovered and SequenceMatcher(None, original, recovered, autojunk=False).ratio() >= 0.97:
                return candidate
        except (IndexError, OSError, ValueError):
            pass
        return value

    # Docling may retain author text inside a picture's text collection while
    # omitting it from iterate_items(). Read front matter directly as well.
    if pdf_document:
        first_page_height = pdf_document[0].get_size()[1]
        first_heading_seen = False
        for candidate in getattr(docling_doc, "texts", []) or []:
            provenance = getattr(candidate, "prov", None) or []
            if not provenance or int(provenance[0].page_no) != 1 or not getattr(provenance[0], "bbox", None) or float(provenance[0].bbox.b) <= first_page_height * 0.68:
                continue
            line = source_text(candidate)
            spaced_line = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", line)
            numbered_names = re.findall(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\s*\d", spaced_line)
            if len(numbered_names) >= 2:
                authors = list(dict.fromkeys([*authors, *numbered_names]))
            candidate_label = getattr(getattr(candidate, "label", None), "value", "")
            if candidate_label == "section_header" and not first_heading_seen:
                first_heading_seen = True
                continue
            compact_names = _university_author_names(line)
            if compact_names:
                authors = list(dict.fromkeys([*authors, *compact_names]))
            email = re.search(r"\s+\S+@\S+", line)
            if email:
                possible_name = clean_text(line[:email.start()]).strip("*†‡§¶ ")
                if re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}", possible_name):
                    authors = list(dict.fromkeys([*authors, possible_name]))
            bare_name = clean_text(line).strip("*†‡§¶ ")
            if (candidate_label == "section_header" or re.search(r"[*†‡§¶]\s*$", line)) and re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}", bare_name):
                authors = list(dict.fromkeys([*authors, bare_name]))
            if not authors and line.count(",") >= 2 and re.search(r"\b(?:University|Institute|Research|Labs?)\b", line):
                name_part = re.split(r"\b(?:[A-Z][a-z]+\s+Research|University|Institute|Labs?)\b", line, maxsplit=1)[0]
                listed_names = [clean_text(part) for part in re.split(r",\s*|\s+and\s+", name_part)]
                if len(listed_names) >= 2 and all(3 < len(name) < 55 and " " in name for name in listed_names):
                    authors = listed_names
            if line.count(",") < 2 or len(line) >= 180:
                continue
            names = [re.sub(r"(?<=[a-z])(?=[A-Z])", " ", part).strip() for part in re.split(r",\s*|\s+and\s+", line)]
            if len(names) >= 3 and all(re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}", name) for name in names):
                authors = authors or names
                break
        if not authors:
            for candidate in getattr(docling_doc, "texts", []) or []:
                provenance = getattr(candidate, "prov", None) or []
                if not provenance or int(provenance[0].page_no) != 1:
                    continue
                address = re.match(r"^Authors?[’']\s+address:\s*(.*)", source_text(candidate), re.I)
                if not address:
                    continue
                name_part = re.split(r",\s*(?:National|University|Institute|School)\b", address.group(1), maxsplit=1)[0]
                address_names = [clean_text(name) for name in name_part.split(";") if clean_text(name)]
                if len(address_names) >= 2:
                    authors = address_names
                    break

    seen_first_page_title = False
    abstract_heading_locations = [
        candidate.prov[0].bbox for candidate in getattr(docling_doc, "texts", []) or []
        if getattr(getattr(candidate, "label", None), "value", "").lower() == "section_header"
        and re.sub(r"\W+", "", getattr(candidate, "text", "").casefold()) == "abstract"
        and getattr(candidate, "prov", None) and getattr(candidate.prov[0], "bbox", None)
        and int(candidate.prov[0].page_no) == 1
    ]
    has_abstract_heading = bool(abstract_heading_locations)
    abstract_heading_bottom = float(abstract_heading_locations[0].b) if abstract_heading_locations else 0
    seen_abstract_heading = False
    intro_heading_box: tuple[int, float] | None = None
    figure_boxes: dict[str, tuple[int, float, float, float, float]] = {}
    in_references = False
    in_appendix = False
    in_index_terms = False
    in_publication_citation = False
    in_article_info = False
    deferred_first_page_prose: list[tuple[str, dict[str, float] | None]] = []

    for item, _depth in docling_doc.iterate_items():
        label = getattr(getattr(item, "label", None), "value", str(getattr(item, "label", ""))).lower()
        text = source_text(item)
        if label in {"page_header", "page_footer", "page_number", "document_index"} or re.match(
            r"^(?:copyright|authorized licensed use|downloaded on|\d{4}-\d{4}\s*©|©\s*\d{4})", text, re.I
        ):
            continue
        location = (getattr(item, "prov", None) or [None])[0]
        if _page_no(item) == 1 and label == "text" and not authors and location is not None and getattr(location, "bbox", None) and pdf_document is not None and float(location.bbox.b) > pdf_document[0].get_size()[1] * 0.68 and text.count(",") >= 2 and len(text) < 180:
            candidates = [re.sub(r"(?<=[a-z])(?=[A-Z])", " ", part).strip() for part in re.split(r",\s*|\s+and\s+", text)]
            if len(candidates) >= 3 and all(re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}", name) for name in candidates):
                authors = candidates
                continue
        if label == "text" and location is not None and getattr(location, "bbox", None):
            box = location.bbox
            center_x, center_y = (float(box.l) + float(box.r)) / 2, (float(box.b) + float(box.t)) / 2
            if any(left <= center_x <= right and bottom <= center_y <= top
                   for left, bottom, right, top in figure_regions.get(int(location.page_no), [])):
                continue
        first_page_frontmatter = (
            _page_no(item) == 1 and label == "text" and location is not None
            and getattr(location, "bbox", None) is not None and pdf_document is not None
            and float(location.bbox.b) > pdf_document[0].get_size()[1] * 0.68
        )
        if first_page_frontmatter and label == "text":
            spaced_author_line = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
            if len(re.findall(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\s*\d", spaced_author_line)) >= 2:
                continue
        if first_page_frontmatter and label == "text" and _university_author_names(text):
            # Some two-column layouts join the last author's affiliation to
            # the first body paragraph. Keep the prose after the institution.
            tail = re.split(r"\b(?=In this paper,|We present |This paper )", text, maxsplit=1)
            if len(tail) > 1:
                deferred_first_page_prose.append((tail[1], _block_bbox(location, pdf_document)))
            continue
        above_abstract = has_abstract_heading and location is not None and getattr(location, "bbox", None) and _page_no(item) == 1 and float(location.bbox.b) >= abstract_heading_bottom - 2
        if above_abstract and not seen_abstract_heading and title and label in {"text", "list_item", "footnote"}:
            # Author and affiliation lines can precede the abstract in the
            # reading order even when Docling labels them as ordinary text.
            continue
        if above_abstract and not seen_abstract_heading and title and label == "section_header" and re.sub(r"\W+", "", text.casefold()) != "abstract":
            # Title and author lines are occasionally mislabeled as sections.
            # The first real section starts after the abstract.
            continue
        if _page_no(item) == 1 and label == "section_header" and text and any(text.casefold() == name.casefold() for name in authors):
            continue
        if first_page_frontmatter and re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+\s*\d", text) and ("†" in text or "∗" in text or text.count(",") >= 2):
            names = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*\d", text)
            if len(names) >= 2:
                authors = authors or names
                for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*(\d+(?:,\s*\d+)*)", text):
                    for marker in re.findall(r"\d+", match.group(2)):
                        author_markers.setdefault(int(marker), []).append(match.group(1))
                continue
        if first_page_frontmatter and re.search(r"\b(?:University|Institute|College)\b", text) and re.search(r"\d(?=[A-Z])", text):
            for part in re.split(r"(?=\d(?=[A-Z]))", text):
                marker = re.match(r"^(\d+)", part)
                affiliation = re.sub(r"^\d+", "", part).strip()
                if affiliation:
                    _upsert_affiliation(affiliations, affiliation)
                    if marker:
                        numbered_affiliations.append((int(marker.group(1)), affiliation))
            continue
        if first_page_frontmatter and re.search(r"(?:Dataset\s*&\s*Toolkit|https?://)", text, re.I):
            supplementary.append(re.sub(r"^githubalt\s*", "", text, flags=re.I))
            continue
        if first_page_frontmatter and re.match(r"^[†∗*].*(?:Equal contribution|Corresponding author)", text, re.I):
            author_notes.append(text)
            continue
        first_page_note = (
            _page_no(item) == 1 and label in {"text", "footnote"} and location is not None
            and getattr(location, "bbox", None) is not None and pdf_document is not None
            and float(location.bbox.b) < pdf_document[0].get_size()[1] * 0.36
        )
        if first_page_note and re.search(r"\b(?:are|is) with (?:the )?\b", text, re.I):
            author_names = re.split(r"\b(?:are|is) with (?:the )?", text, maxsplit=1, flags=re.I)[0].strip(" •·,")
            affiliation = re.split(r"\b(?:are|is) with (?:the )?", text, maxsplit=1, flags=re.I)[-1]
            affiliation = re.split(r"\s*\(?e-?mail\s*:", affiliation, maxsplit=1, flags=re.I)[0]
            _upsert_affiliation(affiliations, affiliation)
            affiliation_details.append(f"{author_names} — {clean_text(affiliation).rstrip('.')}" if author_names else clean_text(affiliation).rstrip("."))
            continue
        if first_page_note and re.match(r"^(?:manuscript\s+)?received\b", text, re.I):
            history = re.split(r"\bThis work was supported\b", text, maxsplit=1, flags=re.I)[0].strip()
            if history:
                received = [history] + [entry for entry in received if not re.match(r"^(?:manuscript\s+)?received\b", entry, re.I)]
            support = re.search(r"\bThis work was supported\b.*?(?=\bRecommended for acceptance\b|\(Corresponding author|$)", text, re.I)
            if support and support.group().strip() not in funding:
                funding.append(support.group().strip().rstrip(". "))
            continue
        if first_page_note and re.match(r"^This article has supplementary\b", text, re.I):
            supplementary.append(text)
            continue
        if first_page_note and re.match(r"^(?:Digital Object Identifier|DOI)\b", text, re.I):
            doi_match = re.search(r"10\.\d{4,9}/[^\s,;)]+", text, re.I)
            if doi_match:
                doi = doi or doi_match.group().rstrip(".")
            continue
        if _page_no(item) == 1 and re.match(r"^Authors?[’']\s+address:", text, re.I):
            continue
        if label == "section_header" and text:
            if re.sub(r"\W+", "", text.casefold()) == "articleinfo":
                in_article_info = True
                current = None
                continue
            in_article_info = False
            if re.search(r"(?:workshop|conference|journal|acm)\s+reference\s+format", text, re.I):
                in_publication_citation = True
                current = None
                continue
            in_publication_citation = False
            if not title and not seen_first_page_title and not re.match(r"^(?:[IVXLCDM]+\.|\d+(?:\.\d+)*\s)", text):
                title = text
                seen_first_page_title = True
                current = None
                continue
            if title and text.casefold() == title.casefold():
                continue
            in_index_terms = text.casefold() in {"index terms", "keywords", "key words", "ccs concepts", "author keywords"}
            if in_index_terms:
                current = None
                continue
            if re.fullmatch(r"(?:references|bibliography)", text, re.I):
                in_references = True
                current = None
                continue
            if in_references:
                if re.match(r"^(?:(?:appendix|supplementary material|supplemental material)(?:\s+[A-Z0-9.:—-]+)?|[A-Z]\.?(?:\s+[A-Z])|author biographies|biographical notes)", text, re.I):
                    in_references = False
                    in_appendix = True
                else:
                    continue
        elif in_references:
            # Some PDFs label appendix headings as plain text rather than
            # section_header. Recover that boundary instead of dropping the
            # entire tail of the document with the bibliography.
            if re.match(r"^(?:appendix|supplementary material|supplemental material)\b", text, re.I):
                in_references = False
                in_appendix = True
                label = "section_header"
            else:
                continue
        if in_publication_citation:
            continue
        if in_article_info:
            continue
        if in_index_terms:
            if text and not index_terms:
                index_terms = text
            continue
        if label == "title" and not title and text:
            title = text
        if label == "section_header" and text:
            is_abstract = re.sub(r"\W+", "", text.casefold()) == "abstract"
            if is_abstract:
                seen_abstract_heading = True
            is_appendix = bool(re.match(r"^(?:appendix|supplementary material|supplemental material|author biographies|biographical notes|acknowledg(?:e)?ments?)", text, re.I))
            heading = _repair_heading(text)
            if _heading_key(heading) == "introduction" and location is not None and getattr(location, "bbox", None):
                heading_box = _block_bbox(location, pdf_document)
                if heading_box:
                    intro_heading_box = (int(location.page_no), heading_box["y"])
            if is_appendix:
                in_appendix = True
            section_type = "abstract" if is_abstract else "appendix" if in_appendix else "body"
            if sections and sections[-1].type == section_type and _heading_key(sections[-1].title) == _heading_key(heading):
                sections.pop()
            current = Section(id=f"section-{len(sections)+1}", title=heading, level=_heading_level(heading), type=section_type, blocks=[])
            sections.append(current)
            if section_type == "body" and pending_figures:
                current.blocks.extend(pending_figures)
                pending_figures.clear()
            continue
        if label == "text" and text:
            abstract_match = re.match(r"^Abstract\s*[-—:]\s*(.*)$", text, re.I)
            if abstract_match:
                abstract = abstract or abstract_match.group(1)
                continue
            terms_match = re.match(r"^Index Terms?\s*[-—:]\s*(.*)$", text, re.I)
            if terms_match:
                index_terms = index_terms or terms_match.group(1).rstrip(" .;—-")
                in_index_terms = False
                continue
            if title and re.search(r"\b(?:Senior Member|Member),?\s+IEEE\b", text, re.I):
                author_line = re.sub(r",?\s*(?:Senior Member,?\s*)?IEEE\.?\s*$", "", text, flags=re.I)
                if not authors:
                    authors = [clean_text(name) for name in re.sub(r"\s+and\s+", ", ", author_line).split(",") if clean_text(name)]
                continue
        if label == "abstract" and text:
            abstract = abstract or text
            continue
        if isinstance(item, PictureItem):
            if not getattr(item, "captions", None) and location is not None and getattr(location, "bbox", None) and pdf_document is not None:
                box = location.bbox
                page_width, page_height = pdf_document[int(location.page_no) - 1].get_size()
                width, height = float(box.r) - float(box.l), float(box.t) - float(box.b)
                corner_mark = (_page_no(item) == 1 and width < 100 and height < 110 and float(box.t) > page_height * 0.8
                               and (float(box.l) < page_width * 0.2 or float(box.r) > page_width * 0.8))
                if float(box.b) > page_height * 0.9 or corner_mark:
                    continue
            figure_index += 1
            asset_name = f"figure_{figure_index:03d}.png"
            asset_path = document_dir / "assets" / asset_name
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not _render_picture_from_pdf(document_dir / "original.pdf", item, asset_path):
                    image = item.get_image(docling_doc)
                    if image is not None:
                        image.save(asset_path, format="PNG")
                    else:
                        asset_name = ""
            except Exception:
                asset_name = ""
            caption = _caption(docling_doc, item, source_text)
            number_match = re.search(r"(?:fig(?:ure)?\.?\s*)(\d+)", caption, re.I)
            number = int(number_match.group(1)) if number_match else None
            if number is not None:
                caption = _caption_body(caption, "figure", number)
            figure_id = f"figure-{number}" if number is not None else f"figure-auto-{figure_index}"
            if figure_id in used_figure_ids:
                figure_id = f"{figure_id}-copy-{figure_index}"
            used_figure_ids.add(figure_id)
            block = Block(id=figure_id, type="figure", number=number, label=f"Figure {number}" if number is not None else "Illustration", caption=caption, page=_page_no(item), src=f"/api/documents/{document_id}/assets/{asset_name}" if asset_name else None)
            block.bbox = _block_bbox(location, pdf_document)
            if location is not None and getattr(location, "bbox", None):
                figure_boxes[figure_id] = (int(location.page_no), float(location.bbox.l), float(location.bbox.b), float(location.bbox.r), float(location.bbox.t))
            figures.append(block)
            if current:
                current.blocks.append(block)
            else:
                pending_figures.append(block)
            continue
        if isinstance(item, TableItem):
            caption = _caption(docling_doc, item, source_text)
            headers, rows = _row_values(item)
            if not _looks_like_table(headers, rows, caption):
                continue
            table_index += 1
            number_match = re.search(r"table\s*(\d+)", caption, re.I)
            number = int(number_match.group(1)) if number_match else None
            if number is not None:
                caption = _caption_body(caption, "table", number)
            table_id = f"table-{number}" if number is not None else f"table-auto-{table_index}"
            if table_id in used_table_ids:
                table_id = f"{table_id}-copy-{table_index}"
            used_table_ids.add(table_id)
            asset_name = f"table_{table_index:03d}.png"
            asset_path = document_dir / "assets" / asset_name
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            if not _render_picture_from_pdf(document_dir / "original.pdf", item, asset_path, margin=1):
                asset_name = ""
            block = Block(id=table_id, type="table", number=number, label=f"Table {number}" if number is not None else "Tabular content", caption=caption, page=_page_no(item), src=f"/api/documents/{document_id}/assets/{asset_name}" if asset_name else None, headers=headers, rows=rows)
            block.bbox = _block_bbox(location, pdf_document)
            tables.append(block)
            if current:
                current.blocks.append(block)
            else:
                pending_tables.append(block)
            continue
        # A displayed formula can have no extractable glyphs. Its PDF geometry
        # is still enough to retain a faithful crop in the document model.
        if not text and label not in {"formula", "display_formula"}:
            continue
        if label in {"formula", "display_formula"}:
            equation_id = f"equation-{len(sections)}-{len(current.blocks) if current else 0}"
            asset_path = document_dir / "assets" / f"{equation_id}.png"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            equation_src = f"/api/documents/{document_id}/assets/{asset_path.name}" if _render_picture_from_pdf(document_dir / "original.pdf", item, asset_path, margin=2) else None
            if not text and not equation_src:
                continue
            block = Block(id=equation_id, type="equation", text=text, page=_page_no(item), src=equation_src,
                          number=_formula_number(location, pdf_document))
        elif label == "code":
            code_id = f"code-{len(sections)}-{len(current.blocks) if current else 0}"
            asset_path = document_dir / "assets" / f"{code_id}.png"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            code_src = f"/api/documents/{document_id}/assets/{asset_path.name}" if _render_picture_from_pdf(document_dir / "original.pdf", item, asset_path, margin=1) else None
            block = Block(id=code_id, type="code", text=text, page=_page_no(item), src=code_src)
        elif label in {"list_item", "ordered_list", "unordered_list"}:
            block = Block(id=f"list-{len(sections)}-{len(current.blocks) if current else 0}", type="list", items=[text], page=_page_no(item))
        elif label == "footnote":
            if re.search(r"\b(?:are|is) with\b", text, re.I):
                affiliation = re.sub(r"\b(?:are|is) with\s+", "", text, flags=re.I)
                affiliation = re.sub(r"\bE-?mail\s*:.*$", "", affiliation, flags=re.I)
                if affiliation and affiliation not in affiliations:
                    affiliations.append(affiliation.rstrip("."))
                continue
            if re.search(r"\b(?:manuscript received|received\s+\w+|revised\s+\w+|accepted\s+\w+)\b", text, re.I):
                received.append(text.rstrip("."))
                continue
            block = Block(id=f"footnote-{len(sections)}-{len(current.blocks) if current else 0}", type="footnote", text=text, page=_page_no(item))
        elif label == "caption":
            continue
        else:
            if label in {"page_header", "page_footer", "page_number", "reference", "document_index"} or re.match(r"^(?:copyright|authorized licensed use|downloaded on|received\s|date of publication)", text, re.I):
                continue
            if not current:
                current = Section(id="introduction", title="Introduction", level=1, type="body", blocks=[])
                sections.append(current)
            block_type = "requirement" if re.match(r"^(?:R\d+|DR\d+)\s*[:.:—-]", text, re.I) else "quote" if label == "quote" else "paragraph"
            block = Block(id=f"block-{len(sections)}-{len(current.blocks)}", type=block_type, text=text, page=_page_no(item))
            if block_type == "paragraph":
                if location and getattr(location, "bbox", None) and re.search(r"∑|∏|∫|√|∂|∞|\bP[−-]\d", text) and len(text) < 1800:
                    math_path = document_dir / "assets" / f"{block.id}-math.png"
                    math_path.parent.mkdir(parents=True, exist_ok=True)
                    if _render_picture_from_pdf(document_dir / "original.pdf", item, math_path, margin=2):
                        block.src = f"/api/documents/{document_id}/assets/{math_path.name}"
                block.content = _numbered_citations(text, references) if use_numbered_references else _match_citations(text, grobid_body, references) if grobid_body else _numbered_citations(text, references)
                if location and getattr(location, "bbox", None):
                    box = location.bbox
                    geometry[block.id] = (int(location.page_no), (float(box.l), float(box.b), float(box.r), float(box.t)))
        block.bbox = _block_bbox(location, pdf_document)
        if not current:
            current = Section(id="introduction", title="Introduction", level=1, type="body", blocks=[])
            sections.append(current)
        if block.type == "list" and current.blocks and current.blocks[-1].type == "list":
            current.blocks[-1].items.extend(block.items)
        else:
            current.blocks.append(block)

    if pending_figures or pending_tables:
        first_body = next((section for section in sections if section.type == "body"), None)
        if first_body:
            first_body.blocks[:0] = [*pending_figures, *pending_tables]
        pending_figures.clear()
        pending_tables.clear()

    _place_figures_before_intro(sections, figures, intro_heading_box)

    abstract_page: int | None = None
    abstract_bbox: dict[str, float] | None = None
    if abstract:
        target = "".join(char for char in abstract.casefold() if char.isalnum())[:256]
        best_score = 0.0
        for candidate in getattr(docling_doc, "texts", []) or []:
            provenance = getattr(candidate, "prov", None) or []
            if not provenance or not getattr(provenance[0], "bbox", None):
                continue
            candidate_text = "".join(char for char in clean_text(getattr(candidate, "text", "")).casefold() if char.isalnum())[:256]
            if not candidate_text:
                continue
            score = SequenceMatcher(None, target, candidate_text, autojunk=False).ratio()
            if score > best_score:
                best_score = score
                if score >= 0.8:
                    abstract_page = int(provenance[0].page_no)
                    abstract_bbox = _block_bbox(provenance[0], pdf_document)

    for text_page in text_pages.values():
        text_page.close()
    if pdf_document:
        pdf_document.close()
    _merge_continuations(sections, geometry)
    if abstract:
        abstract_section = next((section for section in sections if section.type == "abstract"), None)
        if abstract_section:
            compact_abstract = re.sub(r"\W+", "", abstract.casefold())
            overflow = [block for block in abstract_section.blocks if block.type != "paragraph" or not re.sub(r"\W+", "", block.text.casefold()) in compact_abstract]
            abstract_section.blocks = [Block(id="abstract-1", type="paragraph", text=abstract, content=[InlineNode(type="text", text=abstract)], page=abstract_page, bbox=abstract_bbox)]
            first_body = next((section for section in sections if section.type == "body" and _heading_key(section.title) == "introduction"), None)
            if first_body is None:
                first_body = next((section for section in sections if section.type == "body"), None)
            if first_body:
                first_body.blocks.extend(overflow)
            sections.remove(abstract_section)
            sections.insert(0, abstract_section)
    if deferred_first_page_prose:
        first_body = next((section for section in sections if section.type == "body"), None)
        if first_body:
            insert_at = next((i for i, block in enumerate(first_body.blocks) if (block.page or 1) > 1), len(first_body.blocks))
            for offset, (prose, bbox) in enumerate(deferred_first_page_prose):
                first_body.blocks.insert(insert_at + offset, Block(
                    id=f"frontmatter-prose-{offset+1}", type="paragraph", text=prose, page=1, bbox=bbox,
                    content=_numbered_citations(prose, references)))

    if abstract and not any(section.type == "abstract" for section in sections):
        sections.insert(0, Section(id="abstract", title="Abstract", level=1, type="abstract", blocks=[Block(id="abstract-1", type="paragraph", text=abstract, content=[InlineNode(type="text", text=abstract)], page=abstract_page, bbox=abstract_bbox)]))
    elif abstract:
        for section in sections:
            if section.type == "abstract" and not section.blocks:
                section.blocks.append(Block(id="abstract-1", type="paragraph", text=abstract, content=[InlineNode(type="text", text=abstract)], page=abstract_page, bbox=abstract_bbox))

    synthetic_intro = next((section for section in sections if section.id == "introduction" and section.type == "body"), None)
    if synthetic_intro:
        real_intro = next((section for section in sections if section is not synthetic_intro and section.type == "body" and _heading_key(section.title) == "introduction"), None)
        if real_intro:
            real_intro.blocks[:0] = synthetic_intro.blocks
            sections.remove(synthetic_intro)

    first_body = next((section for section in sections if section.type == "body"), None)
    if first_body:
        present_assets = {block.id for section in sections for block in section.blocks if block.type in {"figure", "table"}}
        missing_assets = [block for block in [*figures, *tables] if block.id not in present_assets]
        if missing_assets:
            first_body.blocks[:0] = sorted(missing_assets, key=lambda block: (block.page or 1, block.id))

    _reposition_figures(sections, geometry, figure_boxes)

    _renumber_sections(sections)

    for order, block in enumerate(block for section in sections for block in section.blocks):
        block.order = order

    page_count = len(getattr(docling_doc, "pages", {}) or {})
    word_count = sum(len((block.text or " ".join(block.items)).split()) for section in sections for block in section.blocks)
    known_metadata = _KNOWN_METADATA.get(title.casefold(), {})
    mapped_affiliations = [f"{', '.join(author_markers[marker])} — {affiliation}" if author_markers.get(marker) else affiliation for marker, affiliation in numbered_affiliations]
    metadata = Metadata(title=title or "Untitled paper", authors=authors, affiliations=affiliation_details or mapped_affiliations or affiliations, venue=grobid.get("venue") or known_metadata.get("venue", ""), year=grobid.get("year") or known_metadata.get("year"), doi=doi or known_metadata.get("doi", ""), pageCount=page_count, readMinutes=max(1, (word_count + 219) // 220), abstract=abstract, indexTerms=index_terms, received=received, funding=funding, supplementary=supplementary, authorNotes=author_notes, pageRange=grobid.get("pageRange") or known_metadata.get("pageRange", ""))
    linked_count = sum(len(node.referenceIds) for section in sections for block in section.blocks for node in block.content if node.type == "citation")
    expected_count = sum(len(citation.get("referenceIds", [])) for paragraph in grobid.get("bodyParagraphs", []) for citation in paragraph.get("citations", []))
    citation_status = ("linked" if linked_count else "unresolved") if not expected_count else "linked" if linked_count >= expected_count else "partial"
    source = "docling+grobid+pdfrefs" if grobid and use_numbered_references else "docling+grobid" if grobid else "docling+pdfrefs" if fallback_references else "docling"
    return DocumentModel(id=document_id, metadata=metadata, sections=sections, references=references, figures=figures, tables=tables, pages=page_sizes, citationLinkStatus=citation_status, source=source)
