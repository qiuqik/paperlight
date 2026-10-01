"""SQLite account, session, ownership, and per-user reader state storage."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from argon2.low_level import Type


PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, type=Type.ID)
SESSION_SECONDS = 60 * 60 * 24 * 30


class AccountStore:
    def __init__(self, database: Path):
        self.database = database

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    display_name TEXT NOT NULL, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL, last_login_at REAL, disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '[]',
                    original_filename TEXT NOT NULL, page_count INTEGER NOT NULL DEFAULT 0,
                    parse_status TEXT NOT NULL, storage_path TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, last_opened_at REAL
                );
                CREATE INDEX IF NOT EXISTS documents_owner ON documents(owner_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS documents_hash ON documents(owner_id, fingerprint);
                CREATE TABLE IF NOT EXISTS annotations (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    type TEXT NOT NULL, color TEXT NOT NULL, anchor_json TEXT NOT NULL,
                    note_text TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS annotations_document ON annotations(document_id);
                CREATE TABLE IF NOT EXISTS reading_progress (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    block_id TEXT, scroll_progress REAL NOT NULL DEFAULT 0,
                    block_offset REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id, document_id)
                );
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    settings_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reading_activity (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL, document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                    day TEXT NOT NULL, seconds INTEGER NOT NULL CHECK(seconds BETWEEN 1 AND 60),
                    PRIMARY KEY(user_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS reading_activity_user_day ON reading_activity(user_id,day);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(documents)")}
            if "favorite" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")

    def user_count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password: str, role: str = "user", display_name: str = "") -> dict[str, Any]:
        username = username.strip()
        if not (3 <= len(username) <= 40) or not all(char.isalnum() or char in "._-" for char in username):
            raise ValueError("Username must be 3–40 letters, digits, dots, underscores, or hyphens.")
        if len(password) < 4 or len(password) > 1024:
            raise ValueError("Password must be 4–1024 characters.")
        if role not in {"admin", "user"}:
            raise ValueError("Invalid role.")
        identifier = uuid.uuid4().hex
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO users(id,username,password_hash,role,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (identifier, username, PASSWORDS.hash(password), role, display_name.strip() or username, now, now))
        return self.get_user(identifier)

    def get_user(self, identifier: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT id,username,role,display_name,created_at,updated_at,last_login_at,disabled FROM users WHERE id=?", (identifier,)).fetchone()
            return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT u.id,u.username,u.role,u.display_name,u.created_at,u.updated_at,
                   u.last_login_at,u.disabled,COUNT(d.id) AS document_count FROM users u
                   LEFT JOIN documents d ON d.owner_id=u.id GROUP BY u.id ORDER BY u.created_at""")]

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT id,password_hash,disabled FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
            if not row or row["disabled"]:
                return None
            try:
                PASSWORDS.verify(row["password_hash"], password)
            except (VerifyMismatchError, VerificationError):
                return None
            now = time.time()
            if PASSWORDS.check_needs_rehash(row["password_hash"]):
                db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (PASSWORDS.hash(password), now, row["id"]))
            db.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, row["id"]))
            return self.get_user(row["id"])

    def change_password(self, user_id: str, current_password: str, new_password: str) -> bool:
        if not 4 <= len(new_password) <= 1024:
            raise ValueError("Password must be 4–1024 characters.")
        with self.connect() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return False
            try:
                PASSWORDS.verify(row["password_hash"], current_password)
            except (VerifyMismatchError, VerificationError):
                return False
            db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
                       (PASSWORDS.hash(new_password), time.time(), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return True

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO sessions(id_hash,user_id,expires_at) VALUES(?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + SESSION_SECONDS))
        return token

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self.connect() as db:
            row = db.execute("""SELECT u.id FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.id_hash=? AND s.expires_at>? AND u.disabled=0""",
                   (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return self.get_user(row["id"]) if row else None

    def delete_session(self, token: str | None) -> None:
        if token:
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE id_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def update_user(self, identifier: str, *, username: str | None = None, display_name: str | None = None,
                    password: str | None = None, disabled: bool | None = None) -> dict[str, Any] | None:
        changes: dict[str, Any] = {}
        if username is not None:
            username = username.strip()
            if not (3 <= len(username) <= 40) or not all(char.isalnum() or char in "._-" for char in username):
                raise ValueError("Invalid username.")
            changes["username"] = username
        if display_name is not None:
            changes["display_name"] = display_name.strip()[:80]
        if password is not None:
            if len(password) < 4 or len(password) > 1024:
                raise ValueError("Password must be 4–1024 characters.")
            changes["password_hash"] = PASSWORDS.hash(password)
        if disabled is not None:
            changes["disabled"] = int(disabled)
        if not changes:
            return self.get_user(identifier)
        changes["updated_at"] = time.time()
        with self.connect() as db:
            db.execute(f"UPDATE users SET {','.join(f'{key}=?' for key in changes)} WHERE id=?", (*changes.values(), identifier))
            if password is not None or disabled:
                db.execute("DELETE FROM sessions WHERE user_id=?", (identifier,))
        return self.get_user(identifier)

    def document(self, identifier: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identifier,)).fetchone()
            return dict(row) if row else None

    def list_documents(self, owner_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM documents WHERE owner_id=? ORDER BY created_at DESC", (owner_id,))]

    def find_document(self, owner_id: str, fingerprint: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("""SELECT * FROM documents WHERE owner_id=? AND fingerprint=?
                   AND parse_status IN ('processing','ready') ORDER BY created_at DESC LIMIT 1""", (owner_id, fingerprint)).fetchone()
            return dict(row) if row else None

    def add_document(self, identifier: str, owner_id: str, fingerprint: str, filename: str,
                     storage_path: str, status: str = "processing", created_at: float | None = None) -> None:
        now = time.time()
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO documents(id,owner_id,fingerprint,title,original_filename,
                       parse_status,storage_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                       (identifier, owner_id, fingerprint, filename, filename, status, storage_path, created_at or now, now))

    def update_document(self, identifier: str, *, status: str, title: str | None = None,
                        authors: list[str] | None = None, page_count: int | None = None) -> None:
        changes: dict[str, Any] = {"parse_status": status, "updated_at": time.time()}
        if title is not None:
            changes["title"] = title
        if authors is not None:
            changes["authors"] = json.dumps(authors)
        if page_count is not None:
            changes["page_count"] = page_count
        with self.connect() as db:
            db.execute(f"UPDATE documents SET {','.join(f'{key}=?' for key in changes)} WHERE id=?", (*changes.values(), identifier))

    def touch_document(self, identifier: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE documents SET last_opened_at=? WHERE id=?", (time.time(), identifier))

    def set_favorite(self, identifier: str, favorite: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE documents SET favorite=?,updated_at=? WHERE id=?",
                       (int(favorite), time.time(), identifier))

    def delete_document(self, identifier: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM documents WHERE id=?", (identifier,))

    def settings(self, user_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
            return json.loads(row[0]) if row else {}

    def save_settings(self, user_id: str, value: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES(?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
                   (user_id, json.dumps(value, ensure_ascii=False), time.time()))

    def progress(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM reading_progress WHERE user_id=? AND document_id=?", (user_id, document_id)).fetchone()
            return dict(row) if row else None

    def save_progress(self, user_id: str, document_id: str, percent: float, block_id: str | None,
                      block_offset: float) -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO reading_progress(user_id,document_id,scroll_progress,block_id,block_offset,updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,document_id) DO UPDATE SET
                   scroll_progress=excluded.scroll_progress,block_id=excluded.block_id,
                   block_offset=excluded.block_offset,updated_at=excluded.updated_at""",
                   (user_id, document_id, percent, block_id, block_offset, time.time()))

    def annotations(self, document_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload_json FROM annotations WHERE document_id=? ORDER BY created_at", (document_id,))]

    def annotation(self, identifier: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT payload_json FROM annotations WHERE id=?", (identifier,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_annotation(self, document_id: str, owner_id: str, value: dict[str, Any]) -> None:
        now = time.time()
        with self.connect() as db:
            existing = db.execute("SELECT document_id FROM annotations WHERE id=?", (value["id"],)).fetchone()
            if existing and existing["document_id"] != document_id:
                raise ValueError("Annotation ID belongs to another document.")
            db.execute("""INSERT INTO annotations(id,document_id,owner_id,type,color,anchor_json,note_text,payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET note_text=excluded.note_text,
                   payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                   (value["id"], document_id, owner_id, value.get("type", "highlight"), value.get("color", "#f8d86a"),
                    json.dumps(value.get("anchor", {})), value.get("note", ""), json.dumps(value, ensure_ascii=False),
                    value.get("createdAt", now), now))

    def delete_annotation(self, identifier: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM annotations WHERE id=?", (identifier,))

    def delete_user(self, identifier: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM users WHERE id=?", (identifier,))
