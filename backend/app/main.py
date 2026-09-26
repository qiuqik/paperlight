"""FastAPI upload, processing-status, document, and asset endpoints."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from threading import Lock
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .model import DocumentModel, ProcessingStatus
from .normalizer import extract_pdf_references, normalize_docling, parse_grobid

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PAPERLIGHT_DATA_DIR", APP_DIR / "storage" / "documents")).resolve()
GROBID_URL = os.environ.get("GROBID_URL", "http://127.0.0.1:8070").rstrip("/")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(80 * 1024 * 1024)))
ALLOWED_ORIGINS = [value.strip() for value in os.environ.get("PAPERLIGHT_CORS_ORIGINS", "*").split(",") if value.strip()]

app = FastAPI(title="Paperlight Document API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS or ["*"], allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = Lock()


def _job_path(document_id: str) -> Path:
    return DATA_DIR / document_id / "status.json"


def _set_job(document_id: str, **changes: Any) -> None:
    with jobs_lock:
        current = jobs.setdefault(document_id, {"documentId": document_id, "status": "processing", "stage": "queued", "progress": 0.0})
        current.update(changes)
        snapshot = dict(current)
    folder = DATA_DIR / document_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")


def _get_job(document_id: str) -> dict[str, Any] | None:
    with jobs_lock:
        if document_id in jobs:
            return dict(jobs[document_id])
    path = _job_path(document_id)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _needs_ocr(docling_doc: Any) -> bool:
    """Only initialize OCR models when the PDF text layer is mostly absent."""
    page_text: dict[int, int] = {}
    total_chars = 0
    for item, _depth in docling_doc.iterate_items():
        text = str(getattr(item, "text", "") or "").strip()
        if not text:
            continue
        total_chars += len(text)
        provenance = getattr(item, "prov", None) or []
        if provenance:
            page_no = getattr(provenance[0], "page_no", None)
            if page_no is not None:
                page_text[page_no] = page_text.get(page_no, 0) + len(text)
    page_count = len(getattr(docling_doc, "pages", {}) or {})
    if total_chars < 120:
        return True
    if page_count and page_text:
        sparse_pages = sum(1 for page_no in range(1, page_count + 1) if page_text.get(page_no, 0) < 40)
        return sparse_pages / page_count >= 0.3
    return False


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
        docling_available = True
    except ImportError:
        docling_available = False
    return {"status": "ok", "doclingAvailable": docling_available, "grobidUrl": GROBID_URL}


@app.post("/api/documents", response_model=ProcessingStatus, status_code=202)
async def create_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> ProcessingStatus:
    filename = Path(file.filename or "paper.pdf").name
    if not filename.lower().endswith(".pdf") and file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Please upload a PDF file.")
    document_id = uuid.uuid4().hex
    folder = DATA_DIR / document_id
    folder.mkdir(parents=True, exist_ok=False)
    original = folder / "original.pdf"
    size = 0
    try:
        with original.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"PDF exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.")
                target.write(chunk)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        await file.close()
    if size == 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")
    _set_job(document_id, documentId=document_id, status="processing", stage="queued", progress=0.02)
    background_tasks.add_task(_process_document, document_id, filename)
    return ProcessingStatus(documentId=document_id, status="processing", stage="queued", progress=0.02)


@app.get("/api/documents/{document_id}", response_model=ProcessingStatus)
def get_document(document_id: str) -> ProcessingStatus:
    state = _get_job(document_id)
    if not state:
        raise HTTPException(status_code=404, detail="Document not found.")
    document_path = DATA_DIR / document_id / "document.json"
    if state.get("status") == "ready" and document_path.is_file():
        try:
            state["document"] = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state.update(status="failed", error="Processed document data could not be read.")
    return ProcessingStatus(**state)


@app.get("/api/documents/{document_id}/assets/{asset_name}")
def get_asset(document_id: str, asset_name: str) -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", asset_name) or asset_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid asset path.")
    folder = DATA_DIR / document_id / "assets"
    target = (folder / asset_name).resolve()
    if target.parent != folder.resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail="Asset not found.")
    return FileResponse(target)


def _process_document(document_id: str, filename: str) -> None:
    folder = DATA_DIR / document_id
    pdf_path = folder / "original.pdf"
    try:
        _set_job(document_id, stage="loading_parser", progress=0.08)
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:
            raise RuntimeError("Docling is not installed. Install backend/requirements.txt before processing PDFs.") from exc

        _set_job(document_id, stage="extracting_structure", progress=0.15)
        options = PdfPipelineOptions(do_ocr=False, do_table_structure=True, generate_picture_images=True, images_scale=2.0)
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        result = converter.convert(str(pdf_path))
        docling_doc = result.document
        ocr_mode = os.environ.get("PAPERLIGHT_OCR_MODE", "auto").lower()
        use_ocr = ocr_mode == "always" or (ocr_mode == "auto" and _needs_ocr(docling_doc))
        if use_ocr and not options.do_ocr:
            _set_job(document_id, stage="recognizing_scanned_pages", progress=0.48)
            ocr_options = PdfPipelineOptions(do_ocr=True, do_table_structure=True, generate_picture_images=True, images_scale=2.0)
            converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=ocr_options)})
            result = converter.convert(str(pdf_path))
            docling_doc = result.document
        (folder / "docling.json").write_text(json.dumps(docling_doc.export_to_dict(), ensure_ascii=False, default=str), encoding="utf-8")

        _set_job(document_id, stage="linking_references", progress=0.74)
        grobid_data: dict[str, Any] = {}
        grobid_xml: str | None = None
        try:
            with pdf_path.open("rb") as source:
                response = httpx.post(
                    f"{GROBID_URL}/api/processFulltextDocument",
                    files={"input": (filename, source, "application/pdf")},
                    data={
                        "consolidateHeader": "1",
                        "consolidateCitations": "1",
                        "includeRawCitations": "1",
                        "teiCoordinates": ["figure", "table"],
                    },
                    timeout=900,
                )
            response.raise_for_status()
            grobid_xml = response.text
            (folder / "grobid.xml").write_text(grobid_xml, encoding="utf-8")
            grobid_data = parse_grobid(grobid_xml)
        except (httpx.HTTPError, OSError, ValueError, ET.ParseError) as exc:
            # GROBID is a citation enhancement. A temporary outage must not discard
            # a usable Docling document.
            (folder / "grobid-error.txt").write_text(str(exc), encoding="utf-8")

        _set_job(document_id, stage="normalizing_document", progress=0.9)
        fallback_references = extract_pdf_references(pdf_path)
        model = normalize_docling(docling_doc, document_id, folder, grobid_data, fallback_references)
        if not grobid_data:
            model.metadata.notice = "GROBID 暂不可用；已尝试从 PDF 文本恢复编号参考文献，期刊、DOI 等出版元数据可能不完整。"
        elif not grobid_data.get("references"):
            model.metadata.notice = "正文结构已提取；GROBID 未识别参考文献，因此引用关联可能不完整。"
        document_json = model.model_dump(mode="json")
        (folder / "document.json").write_text(json.dumps(document_json, ensure_ascii=False), encoding="utf-8")
        _set_job(document_id, status="ready", stage="ready", progress=1.0)
    except Exception as exc:  # Persist failure so the client can explain it.
        _set_job(document_id, status="failed", stage="failed", progress=1.0, error=str(exc)[:800])
