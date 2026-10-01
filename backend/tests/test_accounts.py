"""Access control across users, documents, notes, settings, and admin operations."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.accounts import AccountStore


class AccountApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_data_dir, self.old_accounts, self.old_result_dir = main.DATA_DIR, main.ACCOUNTS, main.RESULT_DIR
        main.DATA_DIR = self.root / "documents"
        main.DATA_DIR.mkdir()
        main.RESULT_DIR = self.root / "result"
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
        main.DATA_DIR, main.ACCOUNTS, main.RESULT_DIR = self.old_data_dir, self.old_accounts, self.old_result_dir
        main.jobs.clear()
        self.temp.cleanup()

    def test_library_tags_are_private_validated_and_independent_of_publication(self) -> None:
        endpoint = f"/api/documents/{self.document_id}/tags"
        tags = {"venue": "TVCG2026", "publishDate": "2026-08-25", "institutions": ["浙江大学", "浙江大学"], "other": [" 待精读 "]}
        self.assertEqual(self.bob_client.patch(endpoint, json=tags).status_code, 404)
        response = self.alice_client.patch(endpoint, json=tags)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["institutions"], ["浙江大学"])
        self.assertEqual(response.json()["other"], ["待精读"])
        self.assertEqual(self.alice_client.get("/api/documents").json()[0]["tags"]["venue"], "TVCG2026")
        self.assertEqual(self.bob_client.get("/api/documents").json(), [])
        self.assertEqual(self.alice_client.patch(endpoint, json={**tags, "publishDate": "2026-02-30"}).status_code, 422)
        folder = self.root / "users" / self.alice["id"] / "documents" / self.document_id
        (folder / "publication.json").write_text(json.dumps({"status": "ready", "information": {"venue_short": "NEW2027"}}), encoding="utf-8")
        self.assertEqual(self.alice_client.get("/api/documents").json()[0]["tags"]["venue"], "TVCG2026")

    def test_reading_activity_is_private_idempotent_and_survives_paper_deletion(self) -> None:
        from datetime import date
        today = date.today().isoformat()
        payload = {'eventId': '11111111-1111-4111-8111-111111111111', 'documentId': self.document_id, 'day': today, 'seconds': 30}
        self.assertEqual(self.bob_client.post('/api/activity', json=payload).status_code, 404)
        for _ in range(2):
            self.assertEqual(self.alice_client.post('/api/activity', json=payload).status_code, 204)
        result = self.alice_client.get(f'/api/activity?today={today}').json()
        self.assertEqual(result['totalSeconds'], 30)
        self.assertEqual(result['activeDays'], 1)
        self.assertEqual(len(result['days']), 84)
        self.assertEqual(self.bob_client.get(f'/api/activity?today={today}').json()['totalSeconds'], 0)
        self.assertEqual(self.admin_client.get(f'/api/activity?today={today}').json()['totalSeconds'], 0)
        self.assertEqual(TestClient(main.app).get(f'/api/activity?today={today}').status_code, 401)
        self.assertEqual(self.alice_client.delete(f'/api/documents/{self.document_id}').status_code, 204)
        self.assertEqual(self.alice_client.get(f'/api/activity?today={today}').json()['totalSeconds'], 30)

    def test_reading_activity_rejects_invalid_intervals_and_dates(self) -> None:
        payload = {'eventId': '11111111-1111-4111-8111-111111111111', 'documentId': self.document_id, 'day': '2000-01-01', 'seconds': 30}
        self.assertEqual(self.alice_client.post('/api/activity', json=payload).status_code, 422)
        self.assertEqual(self.alice_client.post('/api/activity', json={**payload, 'seconds': 1000}).status_code, 422)
        self.assertEqual(self.alice_client.get('/api/activity?today=2000-01-01').status_code, 422)

    def test_pdf_ranges_and_cache_revalidation_still_require_ownership(self) -> None:
        folder = main._document_folder(self.document_id)
        (folder / 'original.pdf').write_bytes(b'%PDF-private-document')
        url = f'/api/documents/{self.document_id}/original.pdf'
        response = self.alice_client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('private', response.headers['cache-control'])
        partial = self.alice_client.get(url, headers={'range': 'bytes=0-3'})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b'%PDF')
        headers = {'if-none-match': response.headers['etag']}
        self.assertEqual(self.alice_client.get(url, headers=headers).status_code, 304)
        self.assertEqual(self.bob_client.get(url, headers=headers).status_code, 404)
        self.assertEqual(TestClient(main.app).get(url, headers=headers).status_code, 401)

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
                     f"/api/documents/{doc}/assets/figure.png", f"/api/documents/{doc}/progress", f"/api/documents/{doc}/publication"):
            self.assertEqual(self.bob_client.get(path).status_code, 404, path)
        self.assertEqual(self.bob_client.post(f"/api/documents/{doc}/publication").status_code, 404)
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

    def test_publication_worker_saves_authors_without_changing_paper(self) -> None:
        from backend.app.publication import PublicationInfo
        folder = main._document_folder(self.document_id)
        model_path = folder / "document.json"
        model = json.loads(model_path.read_text())
        model["sections"][0].update(id="s1", title="Introduction", level=1)
        model["sections"][0]["blocks"][0]["type"] = "paragraph"
        model_path.write_text(json.dumps(model), encoding="utf-8")
        info = PublicationInfo(authors=["Verified Author"], institutions=["Verified University"])
        with patch.object(main, "lookup_publication", return_value=(info, {"sources": []})):
            main._enrich_publication(self.document_id)
        state = self.alice_client.get(f"/api/documents/{self.document_id}/publication").json()
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["information"]["authors"], ["Verified Author"])
        record = main.ACCOUNTS.document(self.document_id)
        self.assertEqual(record["title"], "Private")
        self.assertEqual(record["parse_status"], "ready")
        self.assertEqual(json.loads(record["authors"]), ["Verified Author"])
        with patch.object(main, 'lookup_publication', side_effect=ValueError('Unavailable')):
            with self.assertLogs(main.logger, level='ERROR'):
                main._enrich_publication(self.document_id)
        retained = self.alice_client.get(f"/api/documents/{self.document_id}/publication").json()
        self.assertEqual(retained['status'], 'ready')
        self.assertEqual(retained['information']['authors'], ['Verified Author'])
        self.assertIn('refreshError', retained)
        self.assertEqual(self.bob_client.get(f"/api/documents/{self.document_id}/publication").status_code, 404)

    def test_pdf_annotation_rectangles_are_saved_and_validated(self) -> None:
        path = main._document_folder(self.document_id) / "document.json"
        model = json.loads(path.read_text())
        model["metadata"]["pageCount"] = 2
        path.write_text(json.dumps(model))
        note = {"id": "a" * 32, "type": "highlight", "color": "#f8d86a", "anchor": {"start": {"blockId": "p1", "offset": 0}, "end": {"blockId": "p1", "offset": 6}, "quote": "secret", "pdfRects": [{"page": 1, "bbox": {"x": .1, "y": .1, "width": .2, "height": .03}}]}}
        endpoint = f"/api/documents/{self.document_id}/annotations"
        self.assertEqual(self.alice_client.post(endpoint, json=note).status_code, 201)
        self.assertEqual(self.alice_client.get(endpoint).json()[0]["anchor"]["pdfRects"][0]["page"], 1)
        note["anchor"]["pdfRects"][0]["page"] = 3
        self.assertEqual(self.alice_client.post(endpoint, json=note).status_code, 422)
        note["anchor"]["pdfRects"][0]["page"] = 1
        note["anchor"]["pdfRects"][0]["bbox"]["width"] = 1
        self.assertEqual(self.alice_client.post(endpoint, json=note).status_code, 422)

    def test_pdf_only_regions_require_an_owned_original_and_valid_page(self) -> None:
        folder = main._document_folder(self.document_id)
        path = folder / "document.json"
        model = json.loads(path.read_text())
        model["metadata"]["pageCount"] = 2
        path.write_text(json.dumps(model))
        value = {"id": "b" * 32, "type": "area", "color": "#f8d86a", "anchor": {"blockId": "pdf-page-2", "page": 2, "space": "page", "pdfOnly": True, "bbox": {"x": .1, "y": .1, "width": .2, "height": .03}}}
        endpoint = f"/api/documents/{self.document_id}/annotations"
        self.assertEqual(self.alice_client.post(endpoint, json=value).status_code, 422)
        (folder / "original.pdf").write_bytes(b"%PDF-test")
        self.assertEqual(self.alice_client.post(endpoint, json=value).status_code, 201)
        self.assertEqual(self.bob_client.post(endpoint, json=value).status_code, 404)
        value["anchor"].update(blockId="pdf-page-3", page=3)
        self.assertEqual(self.alice_client.post(endpoint, json=value).status_code, 422)

    def test_arxiv_import_route_is_retired(self) -> None:
        response = self.alice_client.post("/api/documents/arxiv", json={"url": "https://arxiv.org/abs/2603.17965v1"})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(len(self.alice_client.get("/api/documents").json()), 1)
        self.assertNotIn("/api/documents/arxiv", main.app.openapi()["paths"])

    def test_original_pdf_and_formula_revision_are_private(self) -> None:
        doc = self.document_id
        folder = main._document_folder(doc)
        (folder / "original.pdf").write_bytes(b"%PDF-private")
        model = json.loads((folder / "document.json").read_text(encoding="utf-8"))
        model["sections"][0].update(id="body", title="Body")
        model["sections"][0]["blocks"][0]["type"] = "paragraph"
        model["sections"][0]["blocks"].append({"id": "equation-1", "type": "equation", "src": f"/api/documents/{doc}/assets/figure.png", "text": "x = 1"})
        (folder / "document.json").write_text(json.dumps(model), encoding="utf-8")
        self.assertEqual(self.bob_client.get(f"/api/documents/{doc}/original.pdf").status_code, 404)
        self.assertEqual(self.alice_client.get(f"/api/documents/{doc}/original.pdf").content, b"%PDF-private")
        path = f"/api/documents/{doc}/formulas/equation-1"
        self.assertEqual(self.bob_client.patch(path, json={"revised": "x_1=1"}).status_code, 404)
        self.assertEqual(self.alice_client.patch(path, json={"revised": "x_1=1"}).status_code, 200)
        self.assertEqual(self.alice_client.get(f"/api/documents/{doc}").json()["document"]["sections"][0]["blocks"][1]["latex"], "x_1=1")
        self.assertTrue((main.RESULT_DIR / "users" / self.alice["id"] / doc / "formula-recognitions.json").is_file())
        self.assertFalse((main.RESULT_DIR / doc).exists())

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
