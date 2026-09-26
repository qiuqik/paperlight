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
    value = re.sub(r"[\u00ad\u034f\u200b-\u200f\ufeff\ufffd]", "", value)
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


def _render_picture_from_pdf(pdf_path: Path, item: Any, asset_path: Path) -> bool:
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
        scale = 3
        bounds = (max(0, round(left * scale)), max(0, round(y0 * scale)),
                  min(round(width * scale), round(right * scale)), min(round(height * scale), round(y1 * scale)))
        if bounds[2] - bounds[0] < 30 or bounds[3] - bounds[1] < 30:
            pdf.close()
            return False
        image = page.render(scale=scale).to_pil().crop(bounds)
        image.save(asset_path, format="PNG")
        pdf.close()
        return True
    except Exception:
        return False


def _caption(doc: Any, item: Any) -> str:
    captions = getattr(item, "captions", []) or []
    values = []
    for caption in captions:
        value = getattr(caption, "text", None)
        if value is None and hasattr(caption, "resolve"):
            try:
                value = getattr(caption.resolve(doc), "text", "")
            except Exception:
                value = ""
        if value:
            values.append(clean_text(value))
    return " ".join(values)


def _heading_level(text: str) -> int:
    roman = re.match(r"^([IVXLCDM]+)\.\s+(.+)$", text)
    if roman and (len(roman.group(1)) > 1 or roman.group(1) in {"I", "V", "X"} or roman.group(2).isupper()):
        return 1
    if re.match(r"^[A-Z]\.\s+", text):
        return 2
    match = re.match(r"^(\d+(?:\.\d+)+)\s+", text)
    if match:
        return min(match.group(1).count(".") + 1, 3)
    return 1


def _repair_heading(text: str) -> str:
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
        other = re.sub(r"\W+", " ", candidate["text"].casefold()).strip()
        if not other:
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
            "venue": clean_text(remainder[:year_match.start()]).rstrip(" ,.") if year_match else "",
            "year": int(year_match.group()) if year_match else None,
            "doi": doi_match.group().rstrip(".)]") if doi_match else "",
            "url": url_match.group().rstrip(".,)") if url_match else "",
            "preview": raw,
        })
    return entries


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
    body = docling_doc
    title = grobid.get("title", "")
    authors = grobid.get("authors", [])
    current: Section | None = None
    figure_index = 0
    table_index = 0
    abstract = grobid.get("abstract", "")
    index_terms = grobid.get("indexTerms", "")
    affiliations = list(grobid.get("affiliations", []))
    received = list(grobid.get("received", []))
    seen_first_page_title = False
    in_references = False
    in_index_terms = False

    for item, _depth in docling_doc.iterate_items():
        label = getattr(getattr(item, "label", None), "value", str(getattr(item, "label", ""))).lower()
        text = clean_text(getattr(item, "text", ""))
        if label == "section_header" and text:
            if not title and not seen_first_page_title and not re.match(r"^(?:[IVXLCDM]+\.|\d+(?:\.\d+)*\s)", text):
                title = text
                seen_first_page_title = True
                current = None
                continue
            if title and text.casefold() == title.casefold():
                continue
            in_index_terms = text.casefold() in {"index terms", "keywords", "key words"}
            if in_index_terms:
                current = None
                continue
            if re.fullmatch(r"(?:references|bibliography)", text, re.I):
                in_references = True
                current = None
                continue
            if in_references:
                if re.match(r"^(?:appendix|author biographies|biographical notes)", text, re.I):
                    in_references = False
                else:
                    continue
        elif in_references:
            continue
        if in_index_terms:
            if text and not index_terms:
                index_terms = text
            continue
        if label == "title" and not title and text:
            title = text
        if label == "section_header" and text:
            is_abstract = text.casefold() == "abstract"
            is_appendix = bool(re.match(r"^(?:appendix|author biographies|biographical notes|acknowledg(?:e)?ments?)", text, re.I))
            heading = _repair_heading(text)
            section_type = "abstract" if is_abstract else "appendix" if is_appendix else "body"
            if sections and sections[-1].type == section_type and _heading_key(sections[-1].title) == _heading_key(heading):
                sections.pop()
            current = Section(id=f"section-{len(sections)+1}", title=heading, level=_heading_level(heading), type="abstract" if is_abstract else "appendix" if is_appendix else "body", blocks=[])
            sections.append(current)
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
                    authors = [clean_text(name) for name in author_line.split(",") if clean_text(name)]
                continue
        if label == "abstract" and text:
            abstract = abstract or text
            continue
        if isinstance(item, PictureItem):
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
            caption = _caption(docling_doc, item)
            number_match = re.search(r"(?:fig(?:ure)?\.?\s*)(\d+)", caption, re.I)
            number = int(number_match.group(1)) if number_match else figure_index
            block = Block(id=f"figure-{number}", type="figure", number=number, label=f"Figure {number}", caption=caption, page=_page_no(item), src=f"/api/documents/{document_id}/assets/{asset_name}" if asset_name else None)
            figures.append(block)
            if current:
                current.blocks.append(block)
            continue
        if isinstance(item, TableItem):
            caption = _caption(docling_doc, item)
            headers, rows = _row_values(item)
            if not _looks_like_table(headers, rows, caption):
                continue
            table_index += 1
            number_match = re.search(r"table\s*(\d+)", caption, re.I)
            number = int(number_match.group(1)) if number_match else table_index
            block = Block(id=f"table-{number}", type="table", number=number, label=f"Table {number}", caption=caption, page=_page_no(item), headers=headers, rows=rows)
            tables.append(block)
            if current:
                current.blocks.append(block)
            continue
        if not text:
            continue
        if label in {"formula", "display_formula"}:
            block = Block(id=f"equation-{len(sections)}-{len(current.blocks) if current else 0}", type="equation", text=text, page=_page_no(item))
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
                block.content = _numbered_citations(text, references) if use_numbered_references else _match_citations(text, grobid.get("bodyParagraphs", []), references) if grobid.get("bodyParagraphs") else _numbered_citations(text, references)
        if not current:
            current = Section(id="introduction", title="Introduction", level=1, type="body", blocks=[])
            sections.append(current)
        if block.type == "list" and current.blocks and current.blocks[-1].type == "list":
            current.blocks[-1].items.extend(block.items)
        else:
            current.blocks.append(block)

    if abstract and not any(section.type == "abstract" for section in sections):
        sections.insert(0, Section(id="abstract", title="Abstract", level=1, type="abstract", blocks=[Block(id="abstract-1", type="paragraph", text=abstract, content=[InlineNode(type="text", text=abstract)])]))
    elif abstract:
        for section in sections:
            if section.type == "abstract" and not section.blocks:
                section.blocks.append(Block(id="abstract-1", type="paragraph", text=abstract, content=[InlineNode(type="text", text=abstract)]))

    _renumber_sections(sections)

    page_count = len(getattr(docling_doc, "pages", {}) or {})
    word_count = sum(len((block.text or " ".join(block.items)).split()) for section in sections for block in section.blocks)
    metadata = Metadata(title=title or "Untitled paper", authors=authors, affiliations=affiliations, venue=grobid.get("venue", ""), year=grobid.get("year"), doi=grobid.get("doi", ""), pageCount=page_count, readMinutes=max(1, (word_count + 219) // 220), abstract=abstract, indexTerms=index_terms, received=received, funding=grobid.get("funding", []), pageRange=grobid.get("pageRange", ""))
    linked_count = sum(len(node.referenceIds) for section in sections for block in section.blocks for node in block.content if node.type == "citation")
    expected_count = sum(len(citation.get("referenceIds", [])) for paragraph in grobid.get("bodyParagraphs", []) for citation in paragraph.get("citations", []))
    citation_status = ("linked" if linked_count else "unresolved") if not expected_count else "linked" if linked_count >= expected_count else "partial"
    source = "docling+grobid+pdfrefs" if grobid and use_numbered_references else "docling+grobid" if grobid else "docling+pdfrefs" if fallback_references else "docling"
    return DocumentModel(id=document_id, metadata=metadata, sections=sections, references=references, figures=figures, tables=tables, citationLinkStatus=citation_status, source=source)
