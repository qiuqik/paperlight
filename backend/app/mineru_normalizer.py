"""Adapt MinerU MiddleJson and its PDF crops to Paperlight's document model."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from .model import Block, DocumentModel, Metadata, Reference, Section
from .normalizer import _numbered_citations, clean_text, finalize_document_model
from .pdf_fidelity import _crop


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    if isinstance(value, dict):
        return _text(value.get("content", ""))
    return ""


def _inline_equations(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for part in value for item in _inline_equations(part)]
    if isinstance(value, dict):
        if value.get("type") == "equation_inline":
            return [str(value.get("content", ""))]
        return _inline_equations(value.get("content", []))
    return []


def _asset(value: str, folder: Path) -> str | None:
    relative = Path(value)
    if relative.parts[:1] != ("images",) or not relative.name or ".." in relative.parts:
        return None
    path = (folder / "assets" / relative.name).resolve()
    if not path.is_relative_to((folder / "assets").resolve()) or not path.is_file():
        return None
    return f"/api/documents/{folder.name}/assets/{relative.name}"


def _bbox(block: dict[str, Any], size: tuple[float, float]) -> dict[str, float] | None:
    raw = block.get("bbox")
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        x1, y1, x2, y2 = map(float, raw)
    except (TypeError, ValueError):
        return None
    if not (0 <= x1 < x2 <= 1.01 and 0 <= y1 < y2 <= 1.01):
        return None
    return {"x": x1 * size[0], "y": y1 * size[1],
            "width": (x2 - x1) * size[0], "height": (y2 - y1) * size[1]}


def _reference(raw: str) -> Reference | None:
    match = re.match(r"^\s*\[(\d+)\]\s*(.+)", clean_text(raw))
    if not match:
        return None
    number = int(match.group(1))
    body = match.group(2)
    year = re.search(r"\b(?:19|20)\d{2}\b", body)
    doi = re.search(r"\b10\.\d{4,9}/[^\s,;]+", body, re.I)
    url = re.search(r"https?://[^\s)]+", body, re.I)
    title_match = re.search(r'[“"](.+?)[”"]', body)
    return Reference(id=str(number), number=number,
                     authors=clean_text(body[:title_match.start()]).rstrip(" ,.") if title_match else "",
                     title=clean_text(title_match.group(1)) if title_match else body,
                     year=int(year.group()) if year else None,
                     doi=doi.group().rstrip(".)]") if doi else "",
                     url=url.group().rstrip(".,)") if url else "", preview=body)


def normalize_mineru(middle: dict[str, Any], document_id: str, folder: Path,
                     pdf_path: Path) -> DocumentModel:
    if middle.get("schema") != "docvortex.middle" or not isinstance(middle.get("pages"), list):
        raise ValueError("MinerU did not return a supported MiddleJson document.")
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page_sizes = [tuple(map(float, pdf[index].get_size())) for index in range(len(pdf))]
    finally:
        pdf.close()
    pages = [{"number": index + 1, "width": size[0], "height": size[1]}
             for index, size in enumerate(page_sizes)]
    title = ""
    authors: list[str] = []
    sections: list[Section] = []
    references: dict[int, Reference] = {}
    current = Section(id="front-matter", title="Abstract", type="abstract")
    sections.append(current)
    in_references = False
    figures: list[Block] = []
    tables: list[Block] = []
    figure_count = table_count = equation_count = paragraph_count = 0
    figure_ids: set[str] = set()
    table_ids: set[str] = set()
    abstract = ""
    for page in middle["pages"]:
        page_index = page.get("page_idx")
        if not isinstance(page_index, int) or not 0 <= page_index < len(page_sizes):
            raise ValueError("MinerU page index does not match the PDF.")
        page_no = page_index + 1
        for source in page.get("blocks", []):
            kind = source.get("type")
            raw = _text(source.get("content", ""))
            visible = clean_text(raw)
            box = _bbox(source, page_sizes[page_index])
            if kind in {"header", "footer", "aside_text", "page_number", "page_footnote"}:
                continue
            if kind == "doc_title":
                title = visible
                continue
            if kind == "paragraph_title":
                if not visible:
                    continue
                if visible.casefold().strip(" .") in {"references", "bibliography"}:
                    in_references = True
                    continue
                in_references = False
                level = max(1, min(3, int(source.get("level") or 2) - 1))
                if re.match(r"^abstract\b", visible, re.I):
                    current = sections[0]
                    continue
                section_type = "appendix" if re.match(r"^(?:appendix|[A-Z][.)]\s)", visible, re.I) else "body"
                current = Section(id=f"section-{len(sections)}", title=visible, level=level, type=section_type)
                sections.append(current)
                continue
            if kind == "ref_text":
                ref = _reference(visible)
                if ref and ref.number not in references:
                    references[ref.number] = ref
                continue
            if in_references:
                continue
            if kind in {"image", "table", "chart"}:
                parts = source.get("content", [])
                if not isinstance(parts, list):
                    parts = []
                image_part = next((part for part in parts if isinstance(part, dict) and part.get("type", "").endswith("_body")), {})
                caption = clean_text(" ".join(_text(part) for part in parts
                                              if isinstance(part, dict) and part.get("type", "").endswith("_caption")))
                caption_number = re.search(r"\b(?:Fig\.?|Figure|Table)\s*(\d+)\b", caption, re.I)
                if kind == "table":
                    table_count += 1
                    number = int(caption_number.group(1)) if caption_number else table_count
                    block_type, block_id, label = "table", f"table-{number}", f"Table {number}"
                    if block_id in table_ids:
                        block_id = f"{block_id}-part-{table_count}"
                        label += " (continued)"
                    table_ids.add(block_id)
                else:
                    figure_count += 1
                    number = int(caption_number.group(1)) if caption_number else figure_count
                    block_type, block_id, label = "figure", f"figure-{number}", f"Figure {number}"
                    if block_id in figure_ids:
                        block_id = f"{block_id}-part-{figure_count}"
                        label += " (continued)"
                    figure_ids.add(block_id)
                path = image_part.get("image_path") or source.get("image_path") or ""
                asset = _asset(path, folder)
                asset_source = "mineru_basic_pdf_crop"
                if not asset and box:
                    filename = f"page_{page_index}_{block_type}_{block_id}.png"
                    original = pdfium.PdfDocument(str(pdf_path))
                    try:
                        if _crop(original, page_no, box, folder / "assets" / filename):
                            asset = f"/api/documents/{document_id}/assets/{filename}"
                            asset_source = "pdf_original_crop"
                    finally:
                        original.close()
                if not asset:
                    # A visual with no materialized image is not safe to show as an empty figure.
                    continue
                block = Block(id=block_id, type=block_type, page=page_no, bbox=box, number=number,
                              label=label, caption=re.sub(r"^(?:Fig\.?|Figure|Table)\s*\d+\s*[:.]?\s*", "", caption, flags=re.I),
                              src=asset, source=asset_source)
                current.blocks.append(block)
                (tables if block_type == "table" else figures).append(block)
                continue
            if kind == "equation":
                equation_count += 1
                latex = raw.strip()
                asset = _asset(source.get("image_path") or "", folder)
                asset_source = "mineru_basic_pdf_crop"
                if not asset and box:
                    filename = f"page_{page_index}_equation_{equation_count}.png"
                    original = pdfium.PdfDocument(str(pdf_path))
                    try:
                        if _crop(original, page_no, box, folder / "assets" / filename):
                            asset = f"/api/documents/{document_id}/assets/{filename}"
                            asset_source = "pdf_original_crop"
                    finally:
                        original.close()
                current.blocks.append(Block(id=f"equation-{equation_count}", type="equation", page=page_no,
                                            bbox=box, src=asset, latex=latex, source=asset_source,
                                            recognition={"rawOutput": latex, "provider": "mineru_basic",
                                                         "originalAsset": Path(source.get("image_path") or "").name}))
                continue
            if kind not in {"text", "list", "code", "algorithm"} or not visible:
                continue
            if page_no == 1 and not authors and title and current.type == "abstract" and not current.blocks:
                # The first short line after the title is normally the author roster.
                if len(visible) < 300 and not re.match(r"^(?:abstract|index terms)\b", visible, re.I):
                    authors = [clean_text(item) for item in re.split(r",\s*|\s+and\s+", visible) if clean_text(item)]
                    continue
            if current.type == "abstract" and re.match(r"^\*\*Abstract\*\*\s*[—–-]?\s*", raw, re.I):
                visible = clean_text(re.sub(r"^\*\*Abstract\*\*\s*[—–-]?\s*", "", raw, flags=re.I))
                abstract = visible
            if re.match(r"^\*\*Index Terms\*\*", raw, re.I):
                continue
            paragraph_count += 1
            block_type = "code" if kind in {"code", "algorithm"} else "paragraph"
            block = Block(id=f"paragraph-{paragraph_count}", type=block_type, text=visible,
                          page=page_no, bbox=box, source="mineru_basic")
            inline_latex = _inline_equations(source.get("content", []))
            if inline_latex:
                block.recognition = {"inlineLatex": inline_latex, "provider": "mineru_basic"}
                if box:
                    filename = f"inline_original_{block.id}.png"
                    original = pdfium.PdfDocument(str(pdf_path))
                    try:
                        if _crop(original, page_no, box, folder / "assets" / filename):
                            block.src = f"/api/documents/{document_id}/assets/{filename}"
                            block.source = "pdf_original_paragraph"
                    finally:
                        original.close()
            current.blocks.append(block)
    if not title or not any(section.type == "body" for section in sections):
        raise ValueError("MinerU result lacks a title or body sections.")
    if not any(block.type == "paragraph" for section in sections for block in section.blocks):
        raise ValueError("MinerU result lacks body text.")
    if not abstract:
        abstract = " ".join(block.text for block in sections[0].blocks if block.type == "paragraph")
    references_list = [references[number] for number in sorted(references)]
    for section in sections:
        for block in section.blocks:
            if block.type == "paragraph":
                block.content = _numbered_citations(block.text, references_list)
    linked = any(node.type == "citation" for section in sections for block in section.blocks for node in block.content)
    words = sum(len(block.text.split()) for section in sections for block in section.blocks)
    model = DocumentModel(id=document_id,
                          metadata=Metadata(title=title, authors=authors, abstract=abstract,
                                            pageCount=len(page_sizes), readMinutes=max(1, (words + 219) // 220)),
                          sections=sections, references=references_list, figures=figures, tables=tables,
                          pages=pages, citationLinkStatus="linked" if linked else "unresolved", source="mineru_basic")
    return finalize_document_model(model, document_dir=folder)
