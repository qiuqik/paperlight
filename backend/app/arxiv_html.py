"""Convert official arXiv LaTeXML HTML into Paperlight's own document model."""

from __future__ import annotations

import re
from collections.abc import Callable
from html import escape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from .arxiv_source import ArxivSource, official_get
from .model import Block, DocumentModel, InlineNode, Metadata, Reference, Section, TableCell, TableRow


MATH_TAGS = {"math", "semantics", "mrow", "mi", "mn", "mo", "mtext", "ms", "mspace", "msub", "msup", "msubsup",
             "mfrac", "msqrt", "mroot", "mover", "munder", "munderover", "mtable", "mtr", "mtd", "menclose", "mpadded",
             "mphantom", "mmultiscripts", "mprescripts", "none"}
MATH_ATTRIBUTES = {"display", "mathvariant", "stretchy", "accent", "accentunder", "lspace", "rspace", "linethickness",
                   "rowspan", "columnspan", "columnalign", "rowalign", "notation", "width", "height", "depth"}


def _classes(tag: Tag) -> set[str]:
    return set(tag.get("class") or [])


def _text(tag: Tag | None) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip() if tag else ""


def _safe_id(value: str | None, prefix: str) -> str:
    return prefix + re.sub(r"[^A-Za-z0-9_.-]", "-", value or "unknown")


def _mathml(math: Tag) -> str:
    """Serialize only inert MathML tags and attributes; no source HTML survives."""
    def visit(node: Tag | NavigableString) -> str:
        if isinstance(node, NavigableString):
            return escape(str(node))
        if node.name not in MATH_TAGS:
            return ""
        attrs = "".join(f' {name}="{escape(str(value), quote=True)}"' for name, value in node.attrs.items()
                        if name in MATH_ATTRIBUTES and isinstance(value, str) and len(value) < 80)
        return f"<{node.name}{attrs}>" + "".join(visit(child) for child in node.children) + f"</{node.name}>"
    return visit(math)


def _formula(math: Tag) -> tuple[str, str, str]:
    annotation = math.find("annotation", attrs={"encoding": "application/x-tex"})
    latex = annotation.get_text(strip=True) if annotation else str(math.get("alttext") or "")
    return _mathml(math), latex, str(math.get("alttext") or latex or _text(math))


def _inline(element: Tag, references: dict[str, str]) -> list[InlineNode]:
    nodes: list[InlineNode] = []

    def add_text(value: str, *, bold: bool = False, italic: bool = False) -> None:
        value = re.sub(r"\s+", " ", value)
        if value:
            if nodes and nodes[-1].type == "text" and nodes[-1].bold == bold and nodes[-1].italic == italic:
                nodes[-1].text += value
            else:
                nodes.append(InlineNode(type="text", text=value, bold=bold, italic=italic))

    def visit(node: Tag | NavigableString, *, bold: bool = False, italic: bool = False) -> None:
        if isinstance(node, NavigableString):
            add_text(str(node), bold=bold, italic=italic)
            return
        if node.name == "math":
            mathml, latex, text = _formula(node)
            nodes.append(InlineNode(type="inlineEquation", text=text, latex=latex, mathml=mathml, source="arxiv_html"))
            return
        if node.name == "cite":
            for child in node.children:
                visit(child, bold=bold, italic=italic)
            return
        if node.name == "a":
            href = str(node.get("href") or "")
            label = _text(node)
            if href.startswith("#"):
                target = href[1:]
                if target in references:
                    nodes.append(InlineNode(type="citation", text=label, referenceIds=[references[target]]))
                else:
                    nodes.append(InlineNode(type="link", text=label, href=f"#{_safe_id(target, 'h-')}"))
            elif href.startswith("https://") and urlparse(href).hostname in {"arxiv.org", "doi.org"}:
                nodes.append(InlineNode(type="link", text=label, href=href))
            else:
                add_text(label, bold=bold, italic=italic)
            return
        if node.name in {"script", "style", "iframe", "object", "form"}:
            return
        if "ltx_tag" in _classes(node):
            return
        if node.name in {"sup", "sub"}:
            nodes.append(InlineNode(type="superscript" if node.name == "sup" else "subscript", text=_text(node)))
            return
        next_bold = bold or node.name in {"b", "strong"} or "ltx_font_bold" in _classes(node)
        next_italic = italic or node.name in {"i", "em"} or "ltx_font_italic" in _classes(node)
        for child in node.children:
            visit(child, bold=next_bold, italic=next_italic)

    for child in element.children:
        visit(child)
    return nodes


def _references(article: Tag) -> tuple[list[Reference], dict[str, str]]:
    entries: list[Reference] = []
    mapping: dict[str, str] = {}
    for item in article.select(".ltx_bibitem[id]"):
        marker = _text(item.select_one(".ltx_bib_key"))
        match = re.search(r"\d+", marker)
        number = int(match.group()) if match else len(entries) + 1
        identifier = str(item["id"])
        mapping[identifier] = identifier
        entries.append(Reference(id=identifier, number=number, authors=_text(item.select_one(".ltx_bib_author")),
                                 title=_text(item.select_one(".ltx_bib_title")) or _text(item),
                                 year=int(year.group()) if (year := re.search(r"(?:19|20)\d{2}", _text(item))) else None,
                                 preview=_text(item)))
    return entries, mapping


def _prompt_section(title: str) -> bool:
    return bool(re.fullmatch(r"(?:\d+(?:\.\d+)*\.?\s*)?Prompts?", title.strip(), re.I))


def _list_content(tag: Tag, references: dict[str, str]) -> tuple[list[str], list[list[InlineNode]]]:
    items: list[str] = []
    content: list[list[InlineNode]] = []
    for item in tag.find_all("li", recursive=False):
        value = _text(item)
        marker = _text(item.select_one(".ltx_tag_item"))
        if marker and value.startswith(marker):
            value = value[len(marker):].lstrip()
        items.append(value)
        content.append(_inline(item, references))
    return items, content


def _table_rows(figure: Tag, references: dict[str, str]) -> list[TableRow]:
    table = figure.select_one("table.ltx_tabular")
    if not table:
        return []
    def span(value: object) -> int:
        try:
            return min(20, max(1, int(str(value))))
        except (TypeError, ValueError):
            return 1

    rows: list[TableRow] = []
    for row in table.find_all("tr"):
        parent = row.find_parent(["thead", "tbody", "tfoot"])
        group = {"thead": "head", "tfoot": "foot"}.get(parent.name if parent else "", "body")
        cells: list[TableCell] = []
        for cell in row.find_all(["th", "td"], recursive=False):
            classes = _classes(cell)
            align = "right" if "ltx_align_right" in classes else "center" if "ltx_align_center" in classes else "left"
            border = {value.removeprefix("ltx_border_") for value in classes if value.startswith("ltx_border_")}
            top = "double" if any(value.startswith("tt") for value in border) else "single" if any(value.startswith("t") for value in border) else "none"
            bottom = "double" if any(value.endswith("bb") for value in border) else "single" if any(value.endswith("b") for value in border) else "none"
            cells.append(TableCell(text=_text(cell), content=_inline(cell, references), header=cell.name == "th",
                                   colSpan=span(cell.get("colspan", 1)),
                                   rowSpan=span(cell.get("rowspan", 1)),
                                   align=align, topRule=top, bottomRule=bottom))
        if cells:
            rows.append(TableRow(group=group, cells=cells))
    return rows


def _image(tag: Tag, source: ArxivSource, assets: Path, client: object) -> str | None:
    image = tag.find("img", src=True)
    if not image:
        preceding = tag.find_previous_sibling()
        if preceding and preceding.name == "div" and "ltx_para" in _classes(preceding):
            candidates = preceding.find_all("img", src=True)
            # LaTeXML sometimes emits an image-only paragraph immediately before
            # the separate figure caption. Never attach ordinary paragraph images.
            if len(candidates) == 1 and not preceding.get_text(" ", strip=True):
                image = candidates[0]
    if not image:
        return None
    url = urljoin(source.html_url, image["src"])
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "arxiv.org" or not parsed.path.startswith(f"/html/{source.versioned_id}/"):
        return None
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return None
    try:
        data = official_get(client, url, max_bytes=15_000_000)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 406:
            return None
        try:
            data = official_get(client, url.replace("https://arxiv.org/", "https://export.arxiv.org/", 1), max_bytes=15_000_000)
        except (httpx.HTTPError, ValueError):
            return None
    except (httpx.HTTPError, ValueError):
        return None
    filename = f"html_{len(list(assets.glob('html_*'))):04d}{suffix}"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / filename).write_bytes(data)
    return filename


def parse_arxiv_html(html: bytes, source: ArxivSource, document_id: str, folder: Path, client: object) -> DocumentModel:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.ltx_document")
    if not article:
        raise ValueError("arXiv HTML does not contain a paper body.")
    title = _text(article.select_one("h1.ltx_title_document"))
    authors = [_text(person) for person in article.select(".ltx_authors .ltx_personname") if _text(person)]
    references, reference_ids = _references(article)
    sections: list[Section] = []
    figures: list[Block] = []
    tables: list[Block] = []
    broken_images = 0

    def add_content(parent: Tag, section: Section) -> None:
        nonlocal broken_images
        for child in parent.find_all(recursive=False):
            classes = _classes(child)
            if child.name == "section" and "ltx_bibliography" not in classes:
                add_section(child, section.level + 1)
            elif child.name == "figure":
                caption = child.find("figcaption")
                prompt_lines = child.select(".ltx_foreignobject_content .ltx_p")
                if child.find("svg") and prompt_lines and not child.find("img"):
                    for index, paragraph in enumerate(prompt_lines):
                        value = _text(paragraph)
                        if not value:
                            continue
                        section.blocks.append(Block(id=_safe_id(str(paragraph.get("id") or f"{child.get('id')}-{index}"), "h-"),
                                                    type="heading" if index == 0 else "paragraph", text=value,
                                                    content=_inline(paragraph, reference_ids)))
                    continue
                number_match = re.search(r"\d+", _text(caption.select_one(".ltx_tag")) if caption else "")
                number = int(number_match.group()) if number_match else None
                kind = "table" if "ltx_table" in classes else "figure"
                identifier = _safe_id(str(child.get("id") or f"{kind}-{len(figures) + len(tables)}"), "h-")
                image_name = _image(child, source, folder / "assets", client)
                if kind == "figure" and not image_name:
                    broken_images += 1
                caption_text = re.sub(r"^(?:Figure|Table)\s+\d+\s*:\s*", "", _text(caption), flags=re.I)
                block = Block(id=identifier, type=kind, number=number, label=("Table" if kind == "table" else "Figure") + (f" {number}" if number else ""),
                              caption=caption_text, captionContent=_inline(caption, reference_ids) if caption else [],
                              src=f"/api/documents/{document_id}/assets/{image_name}" if image_name else None,
                              source="arxiv_html" if image_name or kind == "table" else "arxiv_html_missing_visual",
                              sourceUrl=f"https://export.arxiv.org/pdf/{source.versioned_id}" if not image_name else "")
                if kind == "table":
                    block.tableRows = _table_rows(child, reference_ids)
                    rows = [[_text(cell) for cell in row.find_all(["td", "th"], recursive=False)] for row in child.select("table.ltx_tabular tr")]
                    if rows:
                        block.headers = rows[0]
                        block.rows = rows[1:]
                    tables.append(block)
                else:
                    figures.append(block)
                section.blocks.append(block)
            elif "ltx_equation" in classes or (child.name == "math" and child.get("display") == "block"):
                math = child if child.name == "math" else child.find("math")
                if math:
                    mathml, latex, text = _formula(math)
                    number_match = re.search(r"\d+", _text(child.select_one(".ltx_tag")))
                    section.blocks.append(Block(id=_safe_id(str(child.get("id") or math.get("id")), "h-"), type="equation",
                                                text=text, latex=latex, mathml=mathml, source="arxiv_html",
                                                number=int(number_match.group()) if number_match else None))
            elif child.name == "div" and not ({"ltx_abstract", "ltx_bibliography", "ltx_authors"} & classes):
                add_content(child, section)
            elif child.name == "p" and "ltx_p" in classes:
                content = _inline(child, reference_ids)
                value = _text(child)
                if value:
                    section.blocks.append(Block(id=_safe_id(str(child.get("id")), "h-"),
                                                type="paragraph", text=value, content=content))
            elif child.name in {"ul", "ol"}:
                items, list_content = _list_content(child, reference_ids)
                if items:
                    section.blocks.append(Block(id=_safe_id(str(child.get("id")), "h-list-"), type="list", items=items,
                                                listContent=list_content, listOrdered=child.name == "ol"))

    def add_section(tag: Tag, level: int) -> None:
        title_tag = tag.find(["h2", "h3", "h4", "h5"], recursive=False)
        heading = _text(title_tag) or "Section"
        section = Section(id=_safe_id(str(tag.get("id")), "h-"), title=heading, level=min(level, 3),
                          type="appendix" if "ltx_appendix" in _classes(tag) else "body",
                          presentation="prompt" if _prompt_section(heading) else "article")
        sections.append(section)
        add_content(tag, section)

    abstract = article.select_one(".ltx_abstract")
    if abstract:
        section = Section(id="abstract", title="Abstract", type="abstract")
        sections.append(section)
        add_content(abstract, section)
    preamble = Section(id="preamble", title="Introduction")
    add_content(article, preamble)
    if preamble.blocks:
        sections.insert(1 if abstract else 0, preamble)
    body_sections = [section for section in sections if section.type == "body" and section.id != "preamble"]
    body_chars = sum(len(block.text) for section in body_sections for block in section.blocks if block.type == "paragraph")
    if not title or len(body_sections) < 2 or body_chars < 500 or broken_images > max(2, len(figures) // 2):
        raise ValueError("arXiv HTML conversion is incomplete.")
    return DocumentModel(id=document_id, metadata=Metadata(title=title, authors=authors, pageCount=0,
                         readMinutes=max(1, round(body_chars / 1200))), sections=sections,
                         references=references, figures=figures, tables=tables, citationLinkStatus="linked" if references else "unresolved",
                         source="arxiv_html")


def repair_missing_figure_assets(model: DocumentModel, html: bytes, source: ArxivSource,
                                 folder: Path, client: object) -> int:
    """Upgrade old HTML models without changing text blocks or annotation anchors."""
    soup = BeautifulSoup(html, "html.parser")
    recovered = 0
    for figure in model.figures:
        if figure.source != "arxiv_html_missing_visual" or figure.src or not figure.id.startswith("h-"):
            continue
        tag = soup.find(id=figure.id[2:])
        if not isinstance(tag, Tag) or tag.name != "figure":
            continue
        filename = _image(tag, source, folder / "assets", client)
        if not filename:
            continue
        figure.src = f"/api/documents/{model.id}/assets/{filename}"
        figure.source = "arxiv_html"
        figure.sourceUrl = ""
        for section in model.sections:
            for block in section.blocks:
                if block.id == figure.id:
                    block.src, block.source, block.sourceUrl = figure.src, figure.source, ""
        recovered += 1
    return recovered


def upgrade_html_layout(model: DocumentModel, html: bytes) -> int:
    """Restore list and table structure in saved HTML models without changing block IDs."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.ltx_document")
    if not article:
        return 0
    _, reference_ids = _references(article)
    changed = 0
    for section in model.sections:
        presentation = "prompt" if _prompt_section(section.title) else "article"
        if section.presentation != presentation:
            section.presentation = presentation
            changed += 1
        for block in section.blocks:
            if block.type == "list" and block.id.startswith("h-list-"):
                tag = article.find(id=block.id.removeprefix("h-list-"))
                if isinstance(tag, Tag) and tag.name in {"ul", "ol"}:
                    _, content = _list_content(tag, reference_ids)
                    if len(content) == len(block.items) and (block.listContent != content or block.listOrdered != (tag.name == "ol")):
                        block.listContent = content
                        block.listOrdered = tag.name == "ol"
                        changed += 1
            elif block.type == "table" and block.id.startswith("h-"):
                tag = article.find(id=block.id[2:])
                if isinstance(tag, Tag) and tag.name == "figure":
                    rows = _table_rows(tag, reference_ids)
                    if rows and block.tableRows != rows:
                        block.tableRows = rows
                        changed += 1
    for table in model.tables:
        matching = next((block for section in model.sections for block in section.blocks if block.id == table.id), None)
        if matching and table.tableRows != matching.tableRows:
            table.tableRows = matching.tableRows
    return changed
