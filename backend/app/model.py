"""Normalized document model shared by parser adapters and the reader API."""

from typing import Any, Literal

from pydantic import BaseModel, Field
from .publication import PublicationInfo

DOCUMENT_MODEL_VERSION = 3


class InlineNode(BaseModel):
    type: Literal["text", "citation", "link", "inlineEquation", "superscript", "subscript", "figureLink", "tableLink"]
    text: str = ""
    bold: bool = False
    italic: bool = False
    display: str = ""
    referenceIds: list[str] = Field(default_factory=list)
    href: str | None = None
    figureId: str | None = None
    tableId: str | None = None
    unresolved: bool = False
    src: str | None = None
    latex: str = ""
    mathml: str = ""
    source: str = ""
    number: int | None = None


class TableCell(BaseModel):
    text: str = ""
    content: list[InlineNode] = Field(default_factory=list)
    header: bool = False
    colSpan: int = 1
    rowSpan: int = 1
    align: Literal["left", "center", "right"] = "left"
    topRule: Literal["none", "single", "double"] = "none"
    bottomRule: Literal["none", "single", "double"] = "none"


class TableRow(BaseModel):
    group: Literal["head", "body", "foot"] = "body"
    cells: list[TableCell] = Field(default_factory=list)


class Block(BaseModel):
    id: str
    type: Literal["paragraph", "heading", "figure", "table", "equation", "list", "quote", "footnote", "requirement", "code"]
    text: str = ""
    latex: str = ""
    mathml: str = ""
    source: str = ""
    sourceUrl: str = ""
    recognition: dict[str, Any] = Field(default_factory=dict)
    content: list[InlineNode] = Field(default_factory=list)
    level: int | None = None
    page: int | None = None
    bbox: dict[str, float] | None = None
    order: int | None = None
    items: list[str] = Field(default_factory=list)
    listContent: list[list[InlineNode]] = Field(default_factory=list)
    listOrdered: bool = False
    number: int | None = None
    label: str = ""
    caption: str = ""
    captionContent: list[InlineNode] = Field(default_factory=list)
    src: str | None = None
    beforeHeading: bool = False
    continuesPrevious: bool = False
    headers: list[Any] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    tableRows: list[TableRow] = Field(default_factory=list)


class Section(BaseModel):
    id: str
    title: str
    level: int = 1
    type: Literal["abstract", "body", "appendix"] = "body"
    presentation: Literal["article", "prompt"] = "article"
    tocHidden: bool = False
    blocks: list[Block] = Field(default_factory=list)


class Reference(BaseModel):
    id: str
    number: int
    authors: str = ""
    title: str = ""
    venue: str = ""
    year: int | None = None
    doi: str = ""
    url: str = ""
    preview: str = ""


class Metadata(BaseModel):
    publication: PublicationInfo | None = None
    title: str = "Untitled paper"
    authors: list[str] = Field(default_factory=list)
    affiliations: list[str] = Field(default_factory=list)
    venue: str = ""
    year: int | None = None
    doi: str = ""
    pageCount: int = 0
    readMinutes: int = 1
    pageRange: str = ""
    abstract: str = ""
    indexTerms: str = ""
    received: list[str] = Field(default_factory=list)
    funding: list[str] = Field(default_factory=list)
    supplementary: list[str] = Field(default_factory=list)
    authorNotes: list[str] = Field(default_factory=list)
    notice: str = ""


class DocumentModel(BaseModel):
    id: str
    modelVersion: int = DOCUMENT_MODEL_VERSION
    fingerprint: str = ""
    status: Literal["ready"] = "ready"
    metadata: Metadata
    sections: list[Section]
    references: list[Reference] = Field(default_factory=list)
    figures: list[Block] = Field(default_factory=list)
    tables: list[Block] = Field(default_factory=list)
    pages: list[dict[str, float]] = Field(default_factory=list)
    citationLinkStatus: Literal["linked", "partial", "unresolved"] = "unresolved"
    source: str = "docling"
    sourceUrl: str = ""
    arxivId: str = ""
    arxivVersion: int | None = None
    fallbackReason: str = ""


class ProcessingStatus(BaseModel):
    documentId: str
    ownerId: str | None = None
    ownerUsername: str | None = None
    status: Literal["processing", "ready", "failed"]
    stage: str = "queued"
    progress: float = 0
    document: DocumentModel | None = None
    error: str | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    formulaStatus: Literal["processing", "ready", "failed"] | None = None
    sourceUrl: str = ""
    arxivId: str = ""
    arxivVersion: int | None = None
    parseSource: str = ""
    fallbackReason: str = ""
