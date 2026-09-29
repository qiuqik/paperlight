"""PDF conversion boundary: original PDF -> normalized Paperlight document."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .model import DocumentModel
from .normalizer import extract_pdf_references, normalize_docling, parse_grobid
from .pdf_fidelity import preserve_uncertain_inline_math


@dataclass
class PdfParseResult:
    model: DocumentModel
    timings: dict[str, float]
    used_ocr: bool


def _needs_ocr(docling_doc: Any) -> bool:
    page_text: dict[int, int] = {}
    total_chars = 0
    for item, _depth in docling_doc.iterate_items():
        text = str(getattr(item, "text", "") or "").strip()
        total_chars += len(text)
        provenance = getattr(item, "prov", None) or []
        if provenance:
            page = getattr(provenance[0], "page_no", None)
            if page is not None:
                page_text[page] = page_text.get(page, 0) + len(text)
    count = len(getattr(docling_doc, "pages", {}) or {})
    return total_chars < 120 or bool(count and page_text and sum(page_text.get(page, 0) < 40 for page in range(1, count + 1)) / count >= .3)


def _fetch_grobid(pdf_path: Path, filename: str, folder: Path, grobid_url: str) -> dict[str, Any]:
    try:
        with pdf_path.open("rb") as source:
            response = httpx.post(f"{grobid_url}/api/processFulltextDocument",
                                  files={"input": (filename, source, "application/pdf")},
                                  data={"consolidateHeader": "1", "consolidateCitations": "1", "includeRawCitations": "1",
                                        "teiCoordinates": ["figure", "table"]}, timeout=900)
        response.raise_for_status()
        (folder / "grobid.xml").write_text(response.text, encoding="utf-8")
        return parse_grobid(response.text)
    except (httpx.HTTPError, OSError, ValueError) as error:
        (folder / "grobid-error.txt").write_text(str(error), encoding="utf-8")
        return {}


def parse_pdf(pdf_path: Path, document_id: str, filename: str, folder: Path, grobid_url: str,
              progress: Callable[[str, float], None]) -> PdfParseResult:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    timings: dict[str, float] = {}
    started = time.perf_counter()
    references = extract_pdf_references(pdf_path)
    timings["referenceScanSeconds"] = round(time.perf_counter() - started, 2)
    mode = os.environ.get("PAPERLIGHT_REFERENCE_MODE", "auto").lower()
    use_grobid = mode == "full" or (mode != "fast" and len(references) < 10)
    with ThreadPoolExecutor(max_workers=1) as executor:
        grobid_started = time.perf_counter()
        future = executor.submit(_fetch_grobid, pdf_path, filename, folder, grobid_url) if use_grobid else None
        progress("extracting_structure", .15)
        docling_started = time.perf_counter()

        def convert(ocr: bool):
            options = PdfPipelineOptions(do_ocr=ocr, do_table_structure=True, generate_picture_images=True,
                                         images_scale=2.0, do_formula_enrichment=False)
            return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}).convert(str(pdf_path)).document

        document = convert(False)
        ocr_mode = os.environ.get("PAPERLIGHT_OCR_MODE", "auto").lower()
        used_ocr = ocr_mode == "always" or (ocr_mode == "auto" and _needs_ocr(document))
        if used_ocr:
            progress("recognizing_scanned_pages", .48)
            document = convert(True)
        (folder / "docling.json").write_text(json.dumps(document.export_to_dict(), ensure_ascii=False, default=str), encoding="utf-8")
        timings["doclingSeconds"] = round(time.perf_counter() - docling_started, 2)
        progress("linking_references", .74)
        grobid_data = future.result() if future else {}
        timings["grobidSeconds"] = round(time.perf_counter() - grobid_started, 2) if future else 0.0
    progress("normalizing_document", .9)
    normalizing = time.perf_counter()
    model = normalize_docling(document, document_id, folder, grobid_data, references)
    preserved = preserve_uncertain_inline_math(model, pdf_path, folder)
    timings["originalInlineParagraphs"] = float(preserved)
    if use_grobid and not grobid_data:
        model.metadata.notice = "GROBID 暂不可用；已尝试从 PDF 文本恢复编号参考文献，出版元数据可能不完整。"
    elif not grobid_data.get("references"):
        model.metadata.notice = "GROBID 未识别参考文献，引用关联可能不完整。"
    timings["normalizingSeconds"] = round(time.perf_counter() - normalizing, 2)
    return PdfParseResult(model, timings, used_ocr)
