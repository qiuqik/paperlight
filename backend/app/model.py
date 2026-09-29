"""Normalized document model shared by parser adapters and the reader API."""

from typing import Any, Literal

from pydantic import BaseModel, Field

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
    number: int | None = None


class Block(BaseModel):
    id: str
    type: Literal["paragraph", "heading", "figure", "table", "equation", "list", "quote", "footnote", "requirement", "code"]
    text: str = ""
    content: list[InlineNode] = Field(default_factory=list)
    level: int | None = None
    page: int | None = None
    bbox: dict[str, float] | None = None
    order: int | None = None
    items: list[str] = Field(default_factory=list)
    number: int | None = None
    label: str = ""
    caption: str = ""
    captionContent: list[InlineNode] = Field(default_factory=list)
    src: str | None = None
    beforeHeading: bool = False
    headers: list[Any] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)


class Section(BaseModel):
    id: str
    title: str
    level: int = 1
    type: Literal["abstract", "body", "appendix"] = "body"
    presentation: Literal["article", "prompt"] = "article"
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
