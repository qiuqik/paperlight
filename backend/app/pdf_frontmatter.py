"""Recover author names from the original PDF and discard misplaced contact notes."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .model import DocumentModel, Section


def _repair_publisher_cover(model, pdf, protected):
    """Skip a clearly labeled publisher download cover, not an article title page."""
    if len(pdf) < 2:
        return False
    cover = pdf[0].get_text()
    if 'To cite this article:' not in cover or 'To link to this article:' not in cover:
        return False
    page = pdf[1]
    candidates = []
    for entry in page.get_text('dict')['blocks']:
        spans = [span for line in entry.get('lines',[]) for span in line['spans']]
        if spans and entry['bbox'][1] < page.rect.height * .25:
            size = max(span['size'] for span in spans)
            text = ' '.join(span['text'].strip() for span in spans).strip()
            if size >= 16 and len(text) >= 30:
                candidates.append((size, text))
    if not candidates:
        return False
    title = max(candidates)[1]
    model.metadata.title = title
    key = lambda value: re.sub(r'\W+', '', value.casefold())
    title_section = next((s for s in model.sections if
                          key(re.sub(r'^\d+(?:\.\d+)*\s+', '',s.title)) == key(title)),None)
    for section in list(model.sections):
        had_cover = any(b.page == 1 for b in section.blocks)
        section.blocks = [b for b in section.blocks if b.page != 1 or b.id in protected]
        if had_cover and not section.blocks and section is not title_section:
            model.sections.remove(section)
        elif section.blocks and all(b.page == 1 for b in section.blocks):
            section.tocHidden = True
    if title_section:
        # A publisher cover caused the actual article title to become a section.
        # Move its existing abstract anchor rather than synthesizing new text.
        abstract_source = next((b for b in page.get_text('blocks')
                                if 'Key Words:' in b[4] and len(b[4]) > 300), None)
        if abstract_source:
            prefix = key(abstract_source[4])[:80]
            abstract_block = next((b for b in title_section.blocks if key(b.text).startswith(prefix)),None)
            if abstract_block:
                title_section.blocks.remove(abstract_block)
                model.metadata.abstract = re.split(r'Key Words:',abstract_block.text)[0].strip()
                model.metadata.indexTerms = abstract_block.text.split('Key Words:',1)[-1].strip()
                if not any(s.type == 'abstract' for s in model.sections):
                    model.sections.insert(model.sections.index(title_section),Section(
                        id='publisher-abstract',title='Abstract',type='abstract',blocks=[abstract_block]))
                title_section.blocks = [b for b in title_section.blocks if b.id in protected or
                                        not (b.page == 2 and b.bbox and b.bbox['y'] < abstract_source[1])]
        title_section.title = 'Introduction'
        title_section.level = 1
    return True


_AFFILIATION = re.compile(r"\b(?:(?:Microsoft|Adobe)\s+Research|School of|Department of|University|Institute|College|State Key Lab|Laboratory)\b", re.I)
_ROLE = re.compile(r"\b(?:(?:Graduate|Senior|Student)\s+)*Member\s*,?\s*IEEE\b|\bIEEE\b", re.I)
_NAME = re.compile(r"^(?:[A-Z][a-z]+(?:-[A-Z][a-z]+)*|[A-Z]\.)\s+(?:[A-Z][a-z]+\s+)?[A-Z][a-z]+(?:-[A-Z][a-z]+)*$")
_MARKED_NAME = re.compile(r"((?:[A-Z][a-z]+|[A-Z]\.)\s+(?:[A-Z][a-z]+\s+)?[A-Z][a-z]+)\s*(?:\*|[†‡§¶]|\|\|)")


def _names(text: str) -> list[str]:
    # PDF text often places accent glyphs next to, rather than on, the letter.
    text = "".join(char for char in unicodedata.normalize("NFKD", text.replace("¸", "").replace("˘", ""))
                   if unicodedata.category(char) != "Mn")
    text = " ".join(text.split())
    text = _ROLE.sub("", text)
    text = re.split(r"\b(?:e-?mail|@)\b", text, maxsplit=1, flags=re.I)[0]
    text = _AFFILIATION.split(text, maxsplit=1)[0]
    marked = _MARKED_NAME.findall(text)
    # Superscript affiliation numbers delimit names on dense conference title pages.
    text = re.sub(r"\s*[\*†‡§¶]?\s*\d+(?=\s|$)", ", ", text)
    text = re.sub(r"[\*†‡§¶]", " ", text)
    pieces = [part.strip().strip(" ,.;|").strip() for part in re.split(r",|\band\b", text)]
    names = [part for part in pieces if _NAME.fullmatch(part)]
    if names:
        return list(dict.fromkeys(names))
    if len(marked) >= 2:
        return list(dict.fromkeys(marked))
    # Some title pages print one name per visual column without separators.
    if len(pieces) == 1:
        pairs = re.findall(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", text)
        if len(pairs) >= 2 and re.sub(r"\s+", "", " ".join(pairs)) == re.sub(r"\s+", "", text.strip()):
            return list(dict.fromkeys(pairs))
    return []


def repair_pdf_frontmatter(model: DocumentModel, pdf_path: Path, protected_block_ids: set[str] | None = None) -> int:
    """Use the first-page author band; never infer names from the article body."""
    try:
        import pymupdf
    except ImportError:
        return 0
    protected = protected_block_ids or set()
    with pymupdf.open(pdf_path) as pdf:
        if not pdf.page_count:
            return 0
        covered = _repair_publisher_cover(model,pdf,protected)
        article_page = 2 if covered else 1
        page = pdf[article_page - 1]
        blocks = sorted((entry for entry in page.get_text("blocks") if len(entry) > 4 and entry[6] == 0), key=lambda entry: (entry[1], entry[0]))
        title_key = re.sub(r"\W+", "", model.metadata.title.casefold())[:25]
        title_blocks = [entry for entry in blocks if title_key and re.sub(r"\W+", "", entry[4].casefold()).startswith(title_key)]
        if not title_blocks:
            return int(covered)
        title_end = max(entry[3] for entry in title_blocks)
        # Split titles sometimes occupy two consecutive PDF blocks.
        for entry in blocks:
            if title_end <= entry[1] <= title_end + 5 and len(entry[4]) > 12 and re.sub(r"\W+", "", entry[4].casefold()) in re.sub(r"\W+", "", model.metadata.title.casefold()):
                title_end = entry[3]
        abstract_start = min((entry[1] for entry in blocks if re.match(r"^\s*Abstract\b", entry[4], re.I) and entry[1] > title_end), default=page.rect.height * .36)
        band_end = min(abstract_start, title_end + 80, page.rect.height * .36)
        recovered: list[str] = []
        last_author_end = None
        for entry in blocks:
            if entry[1] < title_end + 2 or entry[3] > band_end + 1:
                continue
            if (last_author_end is not None and entry[1] > last_author_end + 5
                    and not _MARKED_NAME.search(entry[4])):
                continue
            names = _names(entry[4])
            if names:
                last_author_end = max(last_author_end or 0, entry[3])
            for name in names:
                if name not in recovered:
                    recovered.append(name)
        changes = int(covered)
        if recovered and len(recovered) >= len(model.metadata.authors) and recovered != model.metadata.authors:
            model.metadata.authors = recovered
            changes += 1
        for section in model.sections:
            retained = []
            for block in section.blocks:
                contact = (block.page == article_page and block.id not in protected
                           and block.type in {"paragraph", "footnote"}
                           and len(block.text) < 250
                           and re.search(r"(?:^|\s)(?:e-?mail\s*:|[^\s]+@[^\s]+)", block.text, re.I)
                           and (section.type == "abstract" or block.type == "footnote" or block.bbox and block.bbox["y"] < page.rect.height * .48))
                if contact:
                    model.metadata.authorNotes.append(block.text)
                    changes += 1
                else:
                    retained.append(block)
            section.blocks = retained
    return changes
