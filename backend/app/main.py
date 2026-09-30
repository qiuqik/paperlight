"""FastAPI upload, processing-status, document, and asset endpoints."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import shutil
import time
import uuid
import sqlite3
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .accounts import AccountStore
from .model import DOCUMENT_MODEL_VERSION, DocumentModel, ProcessingStatus
from .arxiv_html import parse_arxiv_html
from .arxiv_source import ArxivSource, fetch_pinned_pdf, official_get
from .normalizer import finalize_document_model
from .pdf_pipeline import parse_pdf
from .vision import configured_provider, transcribe_file

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PAPERLIGHT_DATA_DIR", APP_DIR / "storage" / "documents")).resolve()
LIBRARY_DIR = Path(os.environ.get("PAPERLIGHT_LIBRARY_DIR", APP_DIR / "storage" / "library")).resolve()
RESULT_DIR = Path(os.environ["PAPERLIGHT_RESULT_DIR"]).resolve() if os.environ.get("PAPERLIGHT_RESULT_DIR") else None
GROBID_URL = os.environ.get("GROBID_URL", "http://127.0.0.1:8070").rstrip("/")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(80 * 1024 * 1024)))
ALLOWED_ORIGINS = [value.strip() for value in os.environ.get("PAPERLIGHT_CORS_ORIGINS", "*").split(",") if value.strip()]

app = FastAPI(title="Paperlight Document API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS or ["*"], allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["*"])
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = Lock()
annotation_lock = Lock()
import_lock = Lock()
formula_lock = Lock()
formula_data_lock = Lock()
formula_active: set[str] = set()
formula_executor = ThreadPoolExecutor(max_workers=1)
logger = logging.getLogger(__name__)
ACCOUNTS = AccountStore(Path(os.environ.get("PAPERLIGHT_DB_PATH", DATA_DIR.parent / "paperlight.db")))
CURRENT_USER: ContextVar[dict[str, Any] | None] = ContextVar("paperlight_user", default=None)
COOKIE_NAME = "paperlight_session"


def _user() -> dict[str, Any]:
    user = CURRENT_USER.get()
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user


def _admin() -> dict[str, Any]:
    user = _user()
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


def _document_record(document_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{32}", document_id):
        raise HTTPException(status_code=404, detail="Document not found.")
    record = ACCOUNTS.document(document_id)
    user = _user()
    if not record or (record["owner_id"] != (owner_id or user["id"]) and user["role"] != "admin"):
        raise HTTPException(status_code=404, detail="Document not found.")
    return record


def _document_folder(document_id: str) -> Path:
    record = ACCOUNTS.document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found.")
    root = DATA_DIR.parent.resolve()
    folder = (root / record["storage_path"]).resolve()
    if not folder.is_relative_to(root):
        raise HTTPException(status_code=500, detail="Invalid document storage path.")
    return folder


def _migrate_legacy_documents(admin_id: str) -> None:
    if not DATA_DIR.is_dir():
        return
    for folder in DATA_DIR.iterdir():
        if not folder.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", folder.name) or ACCOUNTS.document(folder.name):
            continue
        try:
            state = json.loads((folder / "status.json").read_text(encoding="utf-8"))
            model = json.loads((folder / "document.json").read_text(encoding="utf-8")) if (folder / "document.json").is_file() else {}
            metadata = model.get("metadata", {})
            ACCOUNTS.add_document(folder.name, admin_id, state.get("fingerprint", ""),
                                  state.get("filename", "paper.pdf"), f"documents/{folder.name}",
                                  state.get("status", "processing"), state.get("createdAt"))
            ACCOUNTS.update_document(folder.name, status=state.get("status", "processing"),
                                     title=metadata.get("title"), authors=metadata.get("authors"),
                                     page_count=metadata.get("pageCount"))
            for annotation in _read_annotations(folder):
                ACCOUNTS.save_annotation(folder.name, admin_id, {**annotation, "documentId": folder.name})
        except (OSError, ValueError, TypeError, sqlite3.Error):
            logger.exception("Could not register legacy document %s", folder.name)


def initialize_accounts() -> None:
    ACCOUNTS.initialize()
    admins = [user for user in ACCOUNTS.list_users() if user["role"] == "admin"]
    if admins:
        _migrate_legacy_documents(admins[0]["id"])


@app.middleware("http")
async def require_session(request: Request, call_next):
    path = request.url.path
    if path == "/health" or path in {"/api/auth/login", "/api/auth/me", "/api/auth/logout"}:
        return await call_next(request)
    if not (path.startswith("/api/") or path.startswith("/parser/")):
        return await call_next(request)
    user = ACCOUNTS.session_user(request.cookies.get(COOKIE_NAME))
    if not user:
        return JSONResponse({"detail": "Sign in required."}, status_code=401)
    token = CURRENT_USER.set(user)
    try:
        return await call_next(request)
    finally:
        CURRENT_USER.reset(token)


@app.post("/api/auth/login")
def login(credentials: dict[str, Any], response: Response, request: Request) -> dict[str, Any]:
    username, password = credentials.get("username"), credentials.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        raise HTTPException(status_code=400, detail="Username and password required.")
    user = ACCOUNTS.authenticate(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    response.set_cookie(COOKIE_NAME, ACCOUNTS.create_session(user["id"]), httponly=True,
                        secure=request.headers.get("x-forwarded-proto") == "https", samesite="lax",
                        max_age=30 * 24 * 60 * 60, path="/")
    return user


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    user = ACCOUNTS.session_user(request.cookies.get(COOKIE_NAME))
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    ACCOUNTS.delete_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")


@app.patch("/api/profile")
def update_profile(changes: dict[str, Any]) -> dict[str, Any]:
    name = changes.get("displayName")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise HTTPException(status_code=422, detail="Display name must be 1–80 characters.")
    return ACCOUNTS.update_user(_user()["id"], display_name=name)


@app.post("/api/profile/password", status_code=204)
def change_password(changes: dict[str, Any]) -> None:
    current, new = changes.get("currentPassword"), changes.get("newPassword")
    if not isinstance(current, str) or not isinstance(new, str):
        raise HTTPException(status_code=422, detail="Current and new passwords required.")
    try:
        if not ACCOUNTS.change_password(_user()["id"], current, new):
            raise HTTPException(status_code=401, detail="Current password is incorrect.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return ACCOUNTS.settings(_user()["id"])


@app.put("/api/settings")
def put_settings(settings: dict[str, Any]) -> dict[str, Any]:
    allowed = {"fontFamily", "fontSize", "lineHeight", "contentWidth", "theme", "toolbarDock",
               "activeColor", "customApp", "customPaper", "customText", "customAccent"}
    value = {key: item for key, item in settings.items() if key in allowed and isinstance(item, (str, int, float))}
    if len(json.dumps(value)) > 4000:
        raise HTTPException(status_code=413, detail="Settings are too large.")
    ACCOUNTS.save_settings(_user()["id"], value)
    return value


@app.get("/api/documents/{document_id}/progress")
def get_reading_progress(document_id: str) -> dict[str, Any]:
    _document_record(document_id)
    return ACCOUNTS.progress(_user()["id"], document_id) or {}


@app.put("/api/documents/{document_id}/progress")
def put_reading_progress(document_id: str, progress: dict[str, Any]) -> dict[str, Any]:
    _document_record(document_id)
    percent, offset = progress.get("percent"), progress.get("blockOffset", 0)
    block_id = progress.get("blockId")
    if not isinstance(percent, (int, float)) or not 0 <= percent <= 100 or not isinstance(offset, (int, float)) or not 0 <= offset <= 1:
        raise HTTPException(status_code=422, detail="Invalid reading progress.")
    if block_id is not None and (not isinstance(block_id, str) or len(block_id) > 200):
        raise HTTPException(status_code=422, detail="Invalid block ID.")
    ACCOUNTS.save_progress(_user()["id"], document_id, float(percent), block_id, float(offset))
    return {"percent": percent, "blockId": block_id, "blockOffset": offset}


@app.get("/api/admin/users")
def list_users() -> list[dict[str, Any]]:
    _admin()
    return ACCOUNTS.list_users()


@app.post("/api/admin/users", status_code=201)
def create_user(account: dict[str, Any]) -> dict[str, Any]:
    _admin()
    if not isinstance(account.get("username"), str) or not isinstance(account.get("password"), str):
        raise HTTPException(status_code=422, detail="Username and password required.")
    if not isinstance(account.get("displayName", ""), str):
        raise HTTPException(status_code=422, detail="Invalid display name.")
    try:
        return ACCOUNTS.create_user(account.get("username", ""), account.get("password", ""),
                                    account.get("role", "user"), account.get("displayName", ""))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Username already exists.") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/admin/users/{user_id}")
def update_user(user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    _admin()
    if not ACCOUNTS.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found.")
    if user_id == _user()["id"] and changes.get("disabled"):
        raise HTTPException(status_code=409, detail="Cannot disable your own account.")
    if "disabled" in changes and not isinstance(changes["disabled"], bool):
        raise HTTPException(status_code=422, detail="Invalid disabled flag.")
    if any(key in changes and not isinstance(changes[key], str) for key in ("username", "displayName", "password")):
        raise HTTPException(status_code=422, detail="Invalid account changes.")
    try:
        return ACCOUNTS.update_user(user_id, username=changes.get("username"), display_name=changes.get("displayName"),
                                    password=changes.get("password"), disabled=changes.get("disabled"))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Username already exists.") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/admin/users/{user_id}", status_code=204)
def delete_user(user_id: str) -> None:
    _admin()
    if user_id == _user()["id"]:
        raise HTTPException(status_code=409, detail="Cannot delete your own account.")
    if not ACCOUNTS.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found.")
    documents = ACCOUNTS.list_documents(user_id)
    if any(document["parse_status"] == "processing" for document in documents):
        raise HTTPException(status_code=409, detail="Cannot delete a user with processing documents.")
    for document in documents:
        _delete_document_data(document["id"])
    ACCOUNTS.delete_user(user_id)
    user_folder = (DATA_DIR.parent / "users" / user_id).resolve()
    if user_folder.is_relative_to((DATA_DIR.parent / "users").resolve()) and user_folder.is_dir():
        shutil.rmtree(user_folder)


def _save_result(document_id: str) -> None:
    """Keep a local copy of every completed or failed parsing attempt."""
    if RESULT_DIR is None:
        return
    try:
        record = ACCOUNTS.document(document_id)
        if not record:
            return
        destination = RESULT_DIR / "users" / record["owner_id"] / document_id if record["storage_path"].startswith("users/") else RESULT_DIR / document_id
        shutil.copytree(_document_folder(document_id), destination, dirs_exist_ok=True)
    except OSError:
        logger.exception("Could not save parsing result for %s", document_id)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _job_path(document_id: str) -> Path:
    return _document_folder(document_id) / "status.json"


def _set_job(document_id: str, **changes: Any) -> None:
    with jobs_lock:
        folder = _document_folder(document_id)
        current = jobs.get(document_id)
        if current is None:
            try:
                current = json.loads((folder / "status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = {"documentId": document_id, "status": "processing", "stage": "queued", "progress": 0.0}
            jobs[document_id] = current
        current.update(changes)
        snapshot = dict(current)
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "status.json.tmp"
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        temporary.replace(folder / "status.json")
    if "status" in changes:
        ACCOUNTS.update_document(document_id, status=changes["status"])


def _get_job(document_id: str) -> dict[str, Any] | None:
    with jobs_lock:
        if document_id in jobs:
            return dict(jobs[document_id])
    try:
        path = _job_path(document_id)
    except HTTPException:
        return None
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
        docling_available = True
    except ImportError:
        docling_available = False
    return {"status": "ok", "doclingAvailable": docling_available, "grobidUrl": GROBID_URL}


def _annotation_block_ids(document_id: str) -> set[str]:
    protected: set[str] = set()
    for annotation in ACCOUNTS.annotations(document_id):
        anchor = annotation.get("anchor")
        if not isinstance(anchor, dict):
            continue
        for part in (anchor, anchor.get("start"), anchor.get("end")):
            if isinstance(part, dict) and isinstance(part.get("blockId"), str):
                protected.add(part["blockId"])
    return protected


@app.post("/api/documents/import", response_model=ProcessingStatus, status_code=202)
@app.post("/parser/jobs", response_model=ProcessingStatus, status_code=202)
@app.post("/api/documents", response_model=ProcessingStatus, status_code=202)
async def create_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> ProcessingStatus:
    user = _user()
    filename = Path(file.filename or "paper.pdf").name
    if not filename.lower().endswith(".pdf") and file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Please upload a PDF file.")
    document_id = uuid.uuid4().hex
    folder = DATA_DIR.parent / "users" / user["id"] / "documents" / document_id
    folder.mkdir(parents=True, exist_ok=False)
    original = folder / "original.pdf"
    size = 0
    digest = hashlib.sha256()
    try:
        with original.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"PDF exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.")
                target.write(chunk)
                digest.update(chunk)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        await file.close()
    if size == 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")
    fingerprint = digest.hexdigest()
    requested_parser = os.environ.get("PAPERLIGHT_PDF_PARSER", "docling").lower()
    with import_lock:
        existing = ACCOUNTS.find_document(user["id"], fingerprint)
        if existing:
            status = _get_job(existing["id"])
            if status and status.get("status") in {"processing", "ready"}:
                if status["status"] == "ready":
                    try:
                        document = json.loads((_document_folder(existing["id"]) / "document.json").read_text(encoding="utf-8"))
                        same_parser = (document.get("source") == "mineru_basic") if requested_parser == "mineru" else str(document.get("source", "")).startswith("docling")
                        if document.get("modelVersion") == DOCUMENT_MODEL_VERSION and same_parser:
                            status["document"] = finalize_document_model(DocumentModel(**document), _annotation_block_ids(existing["id"]), _document_folder(existing["id"])).model_dump()
                        else:
                            status = None
                    except (OSError, ValueError):
                        status = None
                elif status.get("requestedParser") != requested_parser:
                    status = None
                if status:
                    shutil.rmtree(folder, ignore_errors=True)
                    return ProcessingStatus(**status)
        ACCOUNTS.add_document(document_id, user["id"], fingerprint, filename,
                              f"users/{user['id']}/documents/{document_id}")
        _set_job(document_id, documentId=document_id, status="processing", stage="queued", progress=0.02,
                 filename=filename, createdAt=time.time(), fingerprint=fingerprint, sourceKind="pdf_upload",
                 requestedParser=requested_parser)
    background_tasks.add_task(_process_document, document_id, filename)
    return ProcessingStatus(documentId=document_id, status="processing", stage="queued", progress=0.02)


def _read_annotations(folder: Path) -> list[dict[str, Any]]:
    path = folder / "annotations.json"
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        deleted = set(_read_annotation_deletions(folder))
        return [item for item in value if isinstance(item, dict) and item.get("id") not in deleted] if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _read_annotation_deletions(folder: Path) -> list[str]:
    try:
        value = json.loads((folder / "annotation-deletions.json").read_text(encoding="utf-8"))
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


@app.get("/api/documents")
def list_documents(request: Request) -> list[dict[str, Any]]:
    user = _user()
    owner_id = request.query_params.get("ownerId") or user["id"]
    if owner_id != user["id"] and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Cannot list another user's documents.")
    if owner_id != user["id"] and not ACCOUNTS.get_user(owner_id):
        raise HTTPException(status_code=404, detail="User not found.")
    records: list[dict[str, Any]] = []
    for item in ACCOUNTS.list_documents(owner_id):
        folder = _document_folder(item["id"])
        status_path = folder / "status.json"
        if not status_path.is_file():
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            records.append({"documentId": item["id"], "fingerprint": item["fingerprint"],
                            "title": item["title"], "status": status.get("status", item["parse_status"]),
                            "parseSource": status.get("parseSource", ""), "arxivId": status.get("arxivId", ""),
                            "arxivVersion": status.get("arxivVersion"),
                            "createdAt": item["created_at"], "lastOpenedAt": item["last_opened_at"],
                            "pageCount": item["page_count"], "annotationCount": len(ACCOUNTS.annotations(item["id"])),
                            "authors": json.loads(item["authors"]), "favorite": bool(item["favorite"]),
                            "progress": (ACCOUNTS.progress(owner_id, item["id"]) or {}).get("scroll_progress", 0)})
        except (OSError, ValueError, TypeError):
            continue
    return sorted(records, key=lambda item: item["createdAt"], reverse=True)


@app.patch("/api/documents/{document_id}/favorite")
def set_document_favorite(document_id: str, changes: dict[str, Any]) -> dict[str, bool]:
    _document_record(document_id)
    favorite = changes.get("favorite")
    if not isinstance(favorite, bool):
        raise HTTPException(status_code=422, detail="Favorite flag required.")
    ACCOUNTS.set_favorite(document_id, favorite)
    return {"favorite": favorite}


def _library_items() -> list[tuple[str, Path, str]]:
    """Enumerate only regular PDFs beneath the configured library root."""
    if not LIBRARY_DIR.is_dir():
        return []
    items: list[tuple[str, Path, str]] = []
    for candidate in LIBRARY_DIR.rglob("*"):
        if candidate.suffix.lower() != ".pdf" or candidate.is_symlink() or not candidate.is_file():
            continue
        path = candidate.resolve()
        if not path.is_relative_to(LIBRARY_DIR):
            continue
        relative = path.relative_to(LIBRARY_DIR).as_posix()
        library_id = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
        items.append((library_id, path, relative))
    return sorted(items, key=lambda entry: entry[2].casefold())


@app.get("/api/library")
def list_library() -> list[dict[str, Any]]:
    _admin()
    return [{"id": library_id, "name": path.name, "folder": str(Path(relative).parent).replace("\\", "/"),
             "size": path.stat().st_size} for library_id, path, relative in _library_items()]


@app.post("/api/library/{library_id}/open", response_model=ProcessingStatus, status_code=202)
async def open_library_document(library_id: str, background_tasks: BackgroundTasks) -> ProcessingStatus:
    _admin()
    if not re.fullmatch(r"[a-f0-9]{24}", library_id):
        raise HTTPException(status_code=404, detail="Library document not found.")
    item = next((path for item_id, path, _ in _library_items() if item_id == library_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Library document not found.")
    with item.open("rb") as source:
        return await create_document(background_tasks, UploadFile(file=source, filename=item.name))


@app.get("/api/documents/{document_id}/annotations")
def get_annotations(document_id: str) -> list[dict[str, Any]]:
    _document_record(document_id)
    return ACCOUNTS.annotations(document_id)


@app.get("/api/documents/{document_id}/annotation-deletions")
def get_annotation_deletions(document_id: str) -> list[str]:
    _document_record(document_id)
    return _read_annotation_deletions(_document_folder(document_id))


@app.post("/api/documents/{document_id}/annotations", status_code=201)
def create_annotation(document_id: str, annotation: dict[str, Any]) -> dict[str, Any]:
    owner = _document_record(document_id)["owner_id"]
    folder = _document_folder(document_id)
    if not (folder / "document.json").is_file():
        raise HTTPException(status_code=404, detail="Document not found.")
    if isinstance(annotation.get("anchor"), dict):
        return _create_v2_annotation(folder, document_id, annotation, owner)
    block_id = str(annotation.get("blockId", ""))[:100]
    quote = str(annotation.get("quote", ""))[:3000]
    note = str(annotation.get("note", ""))[:5000]
    start, end = annotation.get("start"), annotation.get("end")
    if not block_id or not quote.strip() or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end - start > 3000:
        raise HTTPException(status_code=422, detail="Invalid text selection.")
    document = json.loads((folder / "document.json").read_text(encoding="utf-8"))
    if not any(block.get("id") == block_id for section in document.get("sections", []) for block in section.get("blocks", [])):
        raise HTTPException(status_code=422, detail="Selected paragraph does not exist.")
    mode = annotation.get("mode", "highlight")
    if mode not in {"highlight", "underline", "area"}:
        raise HTTPException(status_code=422, detail="Invalid annotation mode.")
    color = str(annotation.get("color", "#ffe59a"))
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise HTTPException(status_code=422, detail="Invalid annotation color.")
    record = {"id": uuid.uuid4().hex, "blockId": block_id, "quote": quote, "note": note, "start": start, "end": end, "mode": mode, "color": color, "createdAt": time.time()}
    ACCOUNTS.save_annotation(document_id, owner, record)
    return record


def _create_v2_annotation(folder: Path, document_id: str, annotation: dict[str, Any], owner_id: str) -> dict[str, Any]:
    """Store stable block offsets or normalized page/figure coordinates."""
    kind = annotation.get("type")
    if kind not in {"highlight", "underline", "note", "area"}:
        raise HTTPException(status_code=422, detail="Invalid annotation type.")
    style = annotation.get("style")
    if style is not None and style not in {"highlight", "underline"}:
        raise HTTPException(status_code=422, detail="Invalid annotation style.")
    note_enabled = annotation.get("noteEnabled", False)
    if not isinstance(note_enabled, bool):
        raise HTTPException(status_code=422, detail="Invalid note setting.")
    color = str(annotation.get("color", ""))
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise HTTPException(status_code=422, detail="Invalid annotation color.")
    anchor = annotation["anchor"]
    document = json.loads((folder / "document.json").read_text(encoding="utf-8"))
    blocks = {block.get("id"): block for section in document.get("sections", []) for block in section.get("blocks", [])}
    if kind == "area":
        block_id = anchor.get("blockId")
        box = anchor.get("bbox")
        page = anchor.get("page")
        if block_id not in blocks or not isinstance(box, dict) or not isinstance(page, int) or page < 1:
            raise HTTPException(status_code=422, detail="Invalid area anchor.")
        if blocks[block_id].get("page") is not None and blocks[block_id]["page"] != page:
            raise HTTPException(status_code=422, detail="Area anchor page does not match its block.")
        if anchor.get("space", "block") not in {"block", "page"} or anchor.get("surface") not in {None, "image"}:
            raise HTTPException(status_code=422, detail="Invalid area coordinate space.")
        try:
            x, y, width, height = (float(box[name]) for name in ("x", "y", "width", "height"))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid area coordinates.") from None
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 - x and 0 < height <= 1 - y):
            raise HTTPException(status_code=422, detail="Area coordinates are outside the block.")
        anchor = {**anchor, "bbox": {"x": x, "y": y, "width": width, "height": height}}
    else:
        start, end = anchor.get("start"), anchor.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            raise HTTPException(status_code=422, detail="Invalid text anchor.")
        if start.get("blockId") not in blocks or end.get("blockId") not in blocks:
            raise HTTPException(status_code=422, detail="Selected block does not exist.")
        if not all(isinstance(point.get("offset"), int) and point["offset"] >= 0 for point in (start, end)):
            raise HTTPException(status_code=422, detail="Invalid text offsets.")
        quote = anchor.get("quote")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 10000:
            raise HTTPException(status_code=422, detail="Invalid text quote.")
    record_id = str(annotation.get("id", ""))
    if not re.fullmatch(r"[a-f0-9-]{32,36}", record_id):
        record_id = uuid.uuid4().hex
    note = str(annotation.get("note") or "")[:5000]
    record = {"id": record_id, "documentId": document_id, "type": kind, "color": color, "anchor": anchor, "note": note, "createdAt": time.time()}
    if style is not None:
        record["style"] = style
    if note_enabled:
        record["noteEnabled"] = True
    with annotation_lock:
        if record_id in _read_annotation_deletions(folder):
            raise HTTPException(status_code=409, detail="Annotation was deleted.")
        existing = ACCOUNTS.annotation(record_id)
        if existing:
            if existing.get("documentId") != document_id:
                raise HTTPException(status_code=409, detail="Annotation ID already exists.")
            return existing
        ACCOUNTS.save_annotation(document_id, owner_id, record)
    return record


@app.patch("/api/documents/{document_id}/annotations/{annotation_id}")
def update_annotation(document_id: str, annotation_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    owner = _document_record(document_id)["owner_id"]
    if not isinstance(changes.get("note"), str) or len(changes["note"]) > 5000:
        raise HTTPException(status_code=422, detail="Invalid note.")
    with annotation_lock:
        record = ACCOUNTS.annotation(annotation_id)
        if record is None or record.get("documentId") != document_id:
            raise HTTPException(status_code=404, detail="Annotation not found.")
        record["note"] = changes["note"]
        record["updatedAt"] = time.time()
        ACCOUNTS.save_annotation(document_id, owner, record)
    return record


@app.delete("/api/documents/{document_id}/annotations/{annotation_id}", status_code=204)
def delete_annotation(document_id: str, annotation_id: str) -> None:
    _document_record(document_id)
    folder = _document_folder(document_id)
    with annotation_lock:
        record = ACCOUNTS.annotation(annotation_id)
        if record is None or record.get("documentId") != document_id:
            raise HTTPException(status_code=404, detail="Annotation not found.")
        deletions = _read_annotation_deletions(folder)
        if annotation_id not in deletions:
            deletions.append(annotation_id)
        deleted_tmp = folder / "annotation-deletions.json.tmp"
        deleted_tmp.write_text(json.dumps(deletions), encoding="utf-8")
        deleted_tmp.replace(folder / "annotation-deletions.json")
        ACCOUNTS.delete_annotation(annotation_id)


def _annotation_document_id(annotation_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9-]{32,36}", annotation_id):
        raise HTTPException(status_code=404, detail="Annotation not found.")
    record = ACCOUNTS.annotation(annotation_id)
    if not record:
        raise HTTPException(status_code=404, detail="Annotation not found.")
    _document_record(record["documentId"])
    return record["documentId"]


@app.patch("/api/annotations/{annotation_id}")
def update_annotation_by_id(annotation_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    return update_annotation(_annotation_document_id(annotation_id), annotation_id, changes)


@app.delete("/api/annotations/{annotation_id}", status_code=204)
def delete_annotation_by_id(annotation_id: str) -> None:
    delete_annotation(_annotation_document_id(annotation_id), annotation_id)


@app.get("/parser/jobs/{document_id}", response_model=ProcessingStatus)
@app.get("/api/documents/{document_id}", response_model=ProcessingStatus)
def get_document(document_id: str) -> ProcessingStatus:
    record = _document_record(document_id)
    state = _get_job(document_id)
    if not state:
        raise HTTPException(status_code=404, detail="Document not found.")
    if state.get("status") == "ready" and state.get("formulaStatus") == "processing":
        _ensure_formula_job(document_id)
    state["ownerId"] = record["owner_id"]
    state["ownerUsername"] = (ACCOUNTS.get_user(record["owner_id"]) or {}).get("username")
    document_path = _document_folder(document_id) / "document.json"
    if state.get("status") == "ready" and document_path.is_file():
        try:
            document = json.loads(document_path.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                document.setdefault("modelVersion", 1)
                model = DocumentModel(**document)
                state["document"] = (model if model.source == "arxiv_html" else finalize_document_model(
                    model, _annotation_block_ids(document_id), _document_folder(document_id))).model_dump()
        except (OSError, json.JSONDecodeError):
            state.update(status="failed", error="Processed document data could not be read.")
    ACCOUNTS.touch_document(document_id)
    return ProcessingStatus(**state)


@app.get("/api/documents/{document_id}/model", response_model=DocumentModel)
def get_document_model(document_id: str) -> DocumentModel:
    state = get_document(document_id)
    if state.status != "ready" or state.document is None:
        raise HTTPException(status_code=409, detail=state.error or "Document is not ready.")
    return state.document


def _delete_document_data(document_id: str) -> None:
    record = ACCOUNTS.document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found.")
    if record["parse_status"] == "processing":
        raise HTTPException(status_code=409, detail="Cannot delete a document while it is processing.")
    folder = _document_folder(document_id)
    root = DATA_DIR.parent.resolve()
    trash_root = (root / ".trash").resolve()
    trash_root.mkdir(parents=True, exist_ok=True)
    trash = trash_root / f"{document_id}-{uuid.uuid4().hex}"
    if folder.is_dir():
        folder.rename(trash)
    try:
        ACCOUNTS.delete_document(document_id)
    except Exception:
        if trash.is_dir():
            trash.rename(folder)
        raise
    with jobs_lock:
        jobs.pop(document_id, None)
    if trash.is_dir():
        shutil.rmtree(trash)
    if RESULT_DIR is not None:
        for result_folder in (RESULT_DIR / "users" / record["owner_id"] / document_id, RESULT_DIR / document_id):
            if result_folder.is_symlink():
                continue
            target = result_folder.resolve()
            if target.is_relative_to(RESULT_DIR.resolve()) and target.is_dir():
                shutil.rmtree(target)


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str) -> None:
    _document_record(document_id)
    _delete_document_data(document_id)


@app.get("/api/documents/{document_id}/assets/{asset_name}")
def get_asset(document_id: str, asset_name: str) -> FileResponse:
    _document_record(document_id)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", asset_name) or asset_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid asset path.")
    folder = _document_folder(document_id) / "assets"
    target = (folder / asset_name).resolve()
    if target.parent != folder.resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail="Asset not found.")
    return FileResponse(target)


@app.get("/api/documents/{document_id}/original.pdf")
def get_original_pdf(document_id: str) -> FileResponse:
    _document_record(document_id)
    path = _document_folder(document_id) / "original.pdf"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This document has no PDF original.")
    return FileResponse(path, media_type="application/pdf", content_disposition_type="inline")


@app.patch("/api/documents/{document_id}/formulas/{block_id}")
def revise_formula(document_id: str, block_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    _document_record(document_id)
    revised = changes.get("revised")
    if not isinstance(revised, str) or len(revised) > 5000:
        raise HTTPException(status_code=422, detail="Formula revision must be text under 5000 characters.")
    folder = _document_folder(document_id)
    path = folder / "document.json"
    with formula_data_lock:
        model = DocumentModel(**json.loads(path.read_text(encoding="utf-8")))
        block = next((item for section in model.sections for item in section.blocks if item.id == block_id and item.type == "equation"), None)
        if not block:
            raise HTTPException(status_code=404, detail="Formula not found.")
        record = dict(block.recognition)
        record["originalAsset"] = Path(block.src).name if block.src else ""
        record["revised"] = revised.strip()
        record["revisionSource"] = "user"
        record["revisedBy"] = _user()["id"]
        record["revisedAt"] = time.time()
        block.recognition = record
        block.latex = record["revised"] or record.get("rawOutput", "")
        _atomic_json(path, model.model_dump(mode="json"))
        evidence_path = folder / "formula-recognitions.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {}
        evidence[block_id] = record
        _atomic_json(evidence_path, evidence)
    with import_lock:
        _save_result(document_id)
    return record


def _process_document(document_id: str, filename: str) -> None:
    folder = _document_folder(document_id)
    started = time.perf_counter()
    timings: dict[str, float] = {}
    try:
        _set_job(document_id, stage="loading_parser", progress=0.08)
        state = _get_job(document_id) or {}
        source_kind = state.get("sourceKind", "pdf_upload")
        source = None
        fallback_reason = ""
        if source_kind == "arxiv":
            source = ArxivSource(state["arxivId"], int(state["arxivVersion"]), state["sourceUrl"])
            _set_job(document_id, stage="fetching_arxiv_html", progress=0.12)
            try:
                with httpx.Client(headers={"User-Agent": "Paperlight/0.3"}, timeout=30) as client:
                    html = official_get(client, source.html_url, max_bytes=25_000_000)
                    (folder / "official.html").write_bytes(html)
                    model = parse_arxiv_html(html, source, document_id, folder, client)
                parse_source = "arxiv_html"
            except (httpx.HTTPError, ValueError, OSError) as error:
                fallback_reason = str(error)[:300]
                logger.info("Official arXiv HTML unavailable for %s: %s; using PDF", source.versioned_id, fallback_reason)
                _set_job(document_id, stage="fetching_arxiv_pdf", progress=0.14, fallbackReason=fallback_reason)
                with httpx.Client(headers={"User-Agent": "Paperlight/0.3"}, timeout=90) as client:
                    pdf = fetch_pinned_pdf(source, client, MAX_UPLOAD_BYTES)
                if not pdf.startswith(b"%PDF"):
                    raise ValueError("arXiv did not return a PDF for the pinned version.")
                (folder / "original.pdf").write_bytes(pdf)
                parsed = parse_pdf(folder / "original.pdf", document_id, filename, folder, GROBID_URL,
                                   lambda stage, progress: _set_job(document_id, stage=stage, progress=progress))
                model, parse_source = parsed.model, parsed.model.source
                timings.update(parsed.timings)
        else:
            parsed = parse_pdf(folder / "original.pdf", document_id, filename, folder, GROBID_URL,
                               lambda stage, progress: _set_job(document_id, stage=stage, progress=progress))
            model, parse_source = parsed.model, parsed.model.source
            timings.update(parsed.timings)
        model.fingerprint = str(state.get("fingerprint", ""))
        model.source = parse_source
        model.sourceUrl = source.abs_url if source else ""
        model.arxivId = source.arxiv_id if source else ""
        model.arxivVersion = source.version if source else None
        model.fallbackReason = fallback_reason
        document_json = model.model_dump(mode="json")
        temporary = folder / "document.json.tmp"
        temporary.write_text(json.dumps(document_json, ensure_ascii=False), encoding="utf-8")
        temporary.replace(folder / "document.json")
        ACCOUNTS.update_document(document_id, status="ready", title=model.metadata.title,
                                 authors=model.metadata.authors, page_count=model.metadata.pageCount)
        timings["totalSeconds"] = round(time.perf_counter() - started, 2)
        has_equations = any(block.type == "equation" and block.src or any(node.type == "inlineEquation" and node.src for node in block.content)
                            for section in model.sections for block in section.blocks)
        recognize = parse_source != "arxiv_html" and bool(configured_provider()) and has_equations
        _set_job(document_id, status="ready", stage="ready", progress=1.0, timings=timings,
                 parseSource=parse_source, fallbackReason=fallback_reason,
                 formulaStatus="processing" if recognize else "ready")
    except Exception as exc:  # Persist failure so the client can explain it.
        timings["totalSeconds"] = round(time.perf_counter() - started, 2)
        _set_job(document_id, status="failed", stage="failed", progress=1.0, error=str(exc)[:800], timings=timings)
    finally:
        with import_lock:
            _save_result(document_id)
    if (_get_job(document_id) or {}).get("formulaStatus") == "processing":
        _ensure_formula_job(document_id)


def _ensure_formula_job(document_id: str) -> None:
    with formula_lock:
        if document_id in formula_active:
            return
        formula_active.add(document_id)
    formula_executor.submit(_enrich_formulas, document_id)


def _enrich_formulas(document_id: str) -> None:
    started = time.perf_counter()
    try:
        provider = configured_provider()
        if not provider:
            _set_job(document_id, formulaStatus="ready")
            return
        folder = _document_folder(document_id)
        path = folder / "document.json"
        model = DocumentModel(**json.loads(path.read_text(encoding="utf-8")))
        evidence_path = folder / "formula-recognitions.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {}
        inline_candidates = [(f"{block.id}:inline:{index}", node.src) for section in model.sections
                             for block in section.blocks for index, node in enumerate(block.content)
                             if node.type == "inlineEquation" and node.src]
        display_candidates = [(block.id, block.src) for section in model.sections for block in section.blocks
                              if block.type == "equation" and block.src]
        candidates = inline_candidates[:8] + display_candidates[:12]
        failures = 0
        for key, source in candidates:
            if key in evidence:
                continue
            asset = folder / "assets" / Path(source or "").name
            if not asset.is_file():
                continue
            try:
                record = transcribe_file(provider, asset)
                with formula_data_lock:
                    current = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {}
                    current[key] = {**record, **current.get(key, {})}
                    _atomic_json(evidence_path, current)
            except (httpx.HTTPError, OSError, ValueError, RuntimeError):
                failures += 1
                logger.exception("Vision transcription failed for %s/%s", document_id, key)
        if not ACCOUNTS.document(document_id):
            return
        with formula_data_lock:
            current_model = DocumentModel(**json.loads(path.read_text(encoding="utf-8")))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {}
            for section in current_model.sections:
                for block in section.blocks:
                    if block.id in evidence:
                        block.recognition = evidence[block.id]
                        block.latex = evidence[block.id].get("revised") or evidence[block.id].get("rawOutput", "")
                    for index, node in enumerate(block.content):
                        record = evidence.get(f"{block.id}:inline:{index}")
                        if record:
                            node.latex = record.get("revised") or record.get("rawOutput", "")
                            node.source = "pdf_crop_with_vision_transcript"
            _atomic_json(path, current_model.model_dump(mode="json"))
        state = _get_job(document_id) or {}
        timings = dict(state.get("timings") or {})
        timings["formulaSeconds"] = round(time.perf_counter() - started, 2)
        _set_job(document_id, formulaStatus="ready", timings=timings, formulaFailures=failures)
        logger.info("Formula transcription completed for %s: %s saved, %s failed", document_id, len(evidence), failures)
        with import_lock:
            _save_result(document_id)
    except Exception:
        logger.exception("Formula recognition failed for %s", document_id)
        if ACCOUNTS.document(document_id):
            _set_job(document_id, formulaStatus="failed")
    finally:
        with formula_lock:
            formula_active.discard(document_id)


initialize_accounts()
