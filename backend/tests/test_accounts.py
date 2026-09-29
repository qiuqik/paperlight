"""Access control across users, documents, notes, settings, and admin operations."""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.accounts import AccountStore


class AccountApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_data_dir, self.old_accounts = main.DATA_DIR, main.ACCOUNTS
        main.DATA_DIR = self.root / "documents"
        main.DATA_DIR.mkdir()
        main.ACCOUNTS = AccountStore(self.root / "paperlight.db")
        main.ACCOUNTS.initialize()
        self.admin = main.ACCOUNTS.create_user("admin", "admin-password-123", "admin")
        self.alice = main.ACCOUNTS.create_user("alice", "alice-password-123")
        self.bob = main.ACCOUNTS.create_user("bob", "bob-password-123")
        self.document_id = "1" * 32
        folder = self.root / "users" / self.alice["id"] / "documents" / self.document_id
        folder.mkdir(parents=True)
        (folder / "status.json").write_text(json.dumps({"documentId": self.document_id, "status": "ready"}), encoding="utf-8")
        (folder / "document.json").write_text(json.dumps({"id": self.document_id, "metadata": {"title": "Private"},
                                                       "sections": [{"blocks": [{"id": "p1", "text": "secret text"}]}]}), encoding="utf-8")
        (folder / "assets").mkdir()
        (folder / "assets" / "figure.png").write_bytes(b"private-image")
        main.ACCOUNTS.add_document(self.document_id, self.alice["id"], "a" * 64, "private.pdf",
                                   f"users/{self.alice['id']}/documents/{self.document_id}", "ready")
        main.ACCOUNTS.update_document(self.document_id, status="ready", title="Private")
        self.alice_client = TestClient(main.app)
        self.bob_client = TestClient(main.app)
        self.admin_client = TestClient(main.app)
        for client, username in ((self.alice_client, "alice"), (self.bob_client, "bob"), (self.admin_client, "admin")):
            response = client.post("/api/auth/login", json={"username": username, "password": f"{username}-password-123"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("httponly", response.headers["set-cookie"].lower())
            self.assertNotIn("password_hash", response.text)

    def tearDown(self) -> None:
        main.DATA_DIR, main.ACCOUNTS = self.old_data_dir, self.old_accounts
        main.jobs.clear()
        self.temp.cleanup()

    def test_documents_annotations_and_assets_are_user_scoped(self) -> None:
        doc = self.document_id
        self.assertEqual(TestClient(main.app).get("/api/documents").status_code, 401)
        self.assertEqual(len(self.alice_client.get("/api/documents").json()), 1)
        self.assertEqual(self.bob_client.get("/api/documents").json(), [])
        self.assertEqual(self.bob_client.get(f"/api/documents?ownerId={self.alice['id']}").status_code, 403)
        self.assertEqual(len(self.admin_client.get(f"/api/documents?ownerId={self.alice['id']}").json()), 1)
        self.assertEqual(self.admin_client.get("/api/documents").json(), [])
        for path in (f"/api/documents/{doc}", f"/api/documents/{doc}/model",
                     f"/api/documents/{doc}/annotations", f"/api/documents/{doc}/annotation-deletions",
                     f"/api/documents/{doc}/assets/figure.png", f"/api/documents/{doc}/progress"):
            self.assertEqual(self.bob_client.get(path).status_code, 404, path)
        self.assertEqual(self.bob_client.delete(f"/api/documents/{doc}").status_code, 404)
        self.assertEqual(self.alice_client.get(f"/api/documents/{doc}/assets/figure.png").content, b"private-image")

        note = {"id": "2" * 32, "documentId": doc, "type": "note", "style": "underline", "note": "private note",
                "color": "#ef7474", "anchor": {"start": {"blockId": "p1", "offset": 0},
                "end": {"blockId": "p1", "offset": 6}, "quote": "secret"}}
        self.assertEqual(self.alice_client.post(f"/api/documents/{doc}/annotations", json=note).status_code, 201)
        self.assertEqual(self.bob_client.patch(f"/api/annotations/{note['id']}", json={"note": "stolen"}).status_code, 404)
        self.assertEqual(self.bob_client.delete(f"/api/annotations/{note['id']}").status_code, 404)
        self.assertEqual(self.admin_client.get(f"/api/documents/{doc}/annotations").json()[0]["note"], "private note")
        self.assertEqual(self.admin_client.patch(f"/api/annotations/{note['id']}", json={"note": "admin edit"}).status_code, 200)
        self.assertEqual(self.alice_client.get(f"/api/documents/{doc}/annotations").json()[0]["note"], "admin edit")

    def test_settings_progress_and_account_management(self) -> None:
        doc = self.document_id
        self.assertEqual(self.alice_client.put("/api/settings", json={"theme": "warm", "fontSize": 19}).status_code, 200)
        self.assertEqual(self.bob_client.get("/api/settings").json(), {})
        self.assertEqual(self.alice_client.put(f"/api/documents/{doc}/progress",
                                               json={"percent": 37, "blockId": "p1", "blockOffset": .4}).status_code, 200)
        self.assertEqual(self.alice_client.get(f"/api/documents/{doc}/progress").json()["scroll_progress"], 37)
        self.assertEqual(self.bob_client.put(f"/api/documents/{doc}/progress", json={"percent": 99}).status_code, 404)
        other_device = TestClient(main.app)
        self.assertEqual(other_device.post("/api/auth/login", json={"username": "alice", "password": "alice-password-123"}).status_code, 200)
        self.assertEqual(other_device.get("/api/settings").json()["fontSize"], 19)
        self.assertEqual(other_device.get(f"/api/documents/{doc}/progress").json()["scroll_progress"], 37)
        self.assertEqual(self.bob_client.get("/api/admin/users").status_code, 403)
        self.assertEqual(len(self.admin_client.get("/api/admin/users").json()), 3)
        created = self.admin_client.post("/api/admin/users", json={"username": "charlie", "password": "charlie-password-123"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.admin_client.patch(f"/api/admin/users/{created.json()['id']}", json={"disabled": True}).status_code, 200)
        self.assertEqual(TestClient(main.app).post("/api/auth/login", json={"username": "charlie", "password": "charlie-password-123"}).status_code, 401)
        self.assertEqual(self.admin_client.delete(f"/api/admin/users/{created.json()['id']}").status_code, 204)

    def test_library_metadata_favorites_and_profile(self) -> None:
        doc = self.document_id
        main.ACCOUNTS.update_document(doc, status="ready", authors=["Alice Researcher"], page_count=12)
        self.assertEqual(self.alice_client.put(f"/api/documents/{doc}/progress", json={"percent": 37}).status_code, 200)
        self.assertEqual(self.bob_client.patch(f"/api/documents/{doc}/favorite", json={"favorite": True}).status_code, 404)
        self.assertEqual(self.alice_client.patch(f"/api/documents/{doc}/favorite", json={"favorite": True}).status_code, 200)
        paper = self.alice_client.get("/api/documents").json()[0]
        self.assertEqual(paper["authors"], ["Alice Researcher"])
        self.assertEqual(paper["progress"], 37)
        self.assertTrue(paper["favorite"])
        self.assertEqual(self.bob_client.get("/api/documents").json(), [])
        self.assertEqual(self.alice_client.patch("/api/profile", json={"displayName": "Alice A"}).json()["display_name"], "Alice A")
        self.assertEqual(self.alice_client.post("/api/profile/password", json={"currentPassword": "wrong", "newPassword": "new-password-123"}).status_code, 401)
        self.assertEqual(self.alice_client.post("/api/profile/password", json={"currentPassword": "alice-password-123", "newPassword": "new-password-123"}).status_code, 204)
        self.assertEqual(self.alice_client.get("/api/auth/me").status_code, 401)
        new_device = TestClient(main.app)
        self.assertEqual(new_device.post("/api/auth/login", json={"username": "alice", "password": "new-password-123"}).status_code, 200)
        self.assertEqual(new_device.get("/api/documents").json()[0]["favorite"], True)

    def test_four_digit_numeric_passwords_work_for_creation_reset_and_change(self) -> None:
        short = self.admin_client.post("/api/admin/users", json={"username": "short", "password": "123"})
        self.assertEqual(short.status_code, 422)
        created = self.admin_client.post("/api/admin/users", json={"username": "pinuser", "password": "1234"})
        self.assertEqual(created.status_code, 201)
        user_id = created.json()["id"]
        client = TestClient(main.app)
        self.assertEqual(client.post("/api/auth/login", json={"username": "pinuser", "password": "1234"}).status_code, 200)
        self.assertEqual(client.post("/api/profile/password", json={"currentPassword": "1234", "newPassword": "123"}).status_code, 422)
        self.assertEqual(client.post("/api/profile/password", json={"currentPassword": "1234", "newPassword": "5678"}).status_code, 204)
        self.assertEqual(client.get("/api/auth/me").status_code, 401)
        self.assertEqual(client.post("/api/auth/login", json={"username": "pinuser", "password": "5678"}).status_code, 200)
        self.assertEqual(self.admin_client.patch(f"/api/admin/users/{user_id}", json={"password": "999"}).status_code, 422)
        self.assertEqual(self.admin_client.patch(f"/api/admin/users/{user_id}", json={"password": "0000"}).status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 401)
        self.assertEqual(client.post("/api/auth/login", json={"username": "pinuser", "password": "0000"}).status_code, 200)

    def test_existing_document_is_cleaned_on_read_without_rewriting_saved_model(self) -> None:
        path = self.root / "users" / self.alice["id"] / "documents" / self.document_id / "document.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["metadata"]["authors"] = ["Alice Lee", "Bob Chen"]
        stored["sections"] = [{"id": "intro", "title": "1 Introduction", "blocks": [
            {"id": "authors", "type": "paragraph", "page": 1, "text": "Alice Lee, Bob Chen"},
            {"id": "figure-1", "type": "figure", "page": 1, "caption": "See [1]."}]},
            {"id": "intro2", "title": "2 1Introduction", "blocks": [
                {"id": "body", "type": "paragraph", "page": 1, "text": "Main text."}]}]
        stored["references"] = [{"id": "1", "number": 1, "title": "Full citation", "preview": "Full citation"}]
        path.write_text(json.dumps(stored), encoding="utf-8")
        response = self.alice_client.get(f"/api/documents/{self.document_id}")
        self.assertEqual(response.status_code, 200)
        document = response.json()["document"]
        self.assertEqual(len(document["sections"]), 1)
        self.assertEqual([block["id"] for block in document["sections"][0]["blocks"]], ["figure-1", "body"])
        self.assertEqual(document["sections"][0]["blocks"][0]["captionContent"][1]["referenceIds"], ["1"])
        self.assertEqual(document["references"][0]["preview"], "")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), stored)

    def test_logout_and_owned_document_deletion(self) -> None:
        self.assertEqual(self.alice_client.post("/api/auth/logout").status_code, 204)
        self.assertEqual(self.alice_client.get("/api/documents").status_code, 401)
        self.assertEqual(self.bob_client.delete(f"/api/documents/{self.document_id}").status_code, 404)
        self.assertEqual(self.admin_client.delete(f"/api/documents/{self.document_id}").status_code, 204)
        self.assertIsNone(main.ACCOUNTS.document(self.document_id))
        self.assertFalse((self.root / "users" / self.alice["id"] / "documents" / self.document_id).exists())

    def test_legacy_documents_register_to_admin_without_moving_files(self) -> None:
        legacy_id = "f" * 32
        folder = main.DATA_DIR / legacy_id
        folder.mkdir()
        (folder / "status.json").write_text(json.dumps({"status": "ready", "fingerprint": "b" * 64,
                                                         "filename": "legacy.pdf"}), encoding="utf-8")
        (folder / "document.json").write_text(json.dumps({"metadata": {"title": "Old paper", "pageCount": 3}}), encoding="utf-8")
        (folder / "annotations.json").write_text(json.dumps([{"id": "3" * 32, "note": "old note"}]), encoding="utf-8")
        main._migrate_legacy_documents(self.admin["id"])
        main._migrate_legacy_documents(self.admin["id"])
        self.assertEqual(main.ACCOUNTS.document(legacy_id)["owner_id"], self.admin["id"])
        self.assertEqual(len(main.ACCOUNTS.annotations(legacy_id)), 1)
        self.assertTrue((folder / "document.json").is_file())
        self.assertEqual(self.bob_client.get(f"/api/documents/{legacy_id}").status_code, 404)

    def test_equal_pdf_uploads_remain_separate_between_users(self) -> None:
        process = main._process_document
        main._process_document = lambda document_id, filename: None
        try:
            payload = b"%PDF-1.4\nsame paper"
            alice = self.alice_client.post("/api/documents", files={"file": ("same.pdf", payload, "application/pdf")})
            bob = self.bob_client.post("/api/documents", files={"file": ("same.pdf", payload, "application/pdf")})
            self.assertEqual(alice.status_code, 202)
            self.assertEqual(bob.status_code, 202)
            self.assertNotEqual(alice.json()["documentId"], bob.json()["documentId"])
            self.assertEqual(main.ACCOUNTS.document(alice.json()["documentId"])["owner_id"], self.alice["id"])
            self.assertEqual(main.ACCOUNTS.document(bob.json()["documentId"])["owner_id"], self.bob["id"])
            self.assertEqual(self.bob_client.get(f"/api/documents/{alice.json()['documentId']}").status_code, 404)
        finally:
            main._process_document = process

    def test_admin_user_deletion_cascades_database_and_files(self) -> None:
        note = {"id": "4" * 32, "documentId": self.document_id, "type": "note", "color": "#ef7474",
                "anchor": {"start": {"blockId": "p1", "offset": 0}, "end": {"blockId": "p1", "offset": 6}, "quote": "secret"}}
        self.assertEqual(self.alice_client.post(f"/api/documents/{self.document_id}/annotations", json=note).status_code, 201)
        self.assertEqual(self.admin_client.delete(f"/api/admin/users/{self.alice['id']}").status_code, 204)
        self.assertIsNone(main.ACCOUNTS.get_user(self.alice["id"]))
        self.assertIsNone(main.ACCOUNTS.document(self.document_id))
        self.assertIsNone(main.ACCOUNTS.annotation(note["id"]))
        self.assertFalse((self.root / "users" / self.alice["id"]).exists())
        self.assertIsNotNone(main.ACCOUNTS.get_user(self.bob["id"]))


if __name__ == "__main__":
    unittest.main()
