"""Contract checks for both legacy and canonical annotation payloads."""

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main


class AnnotationApiTests(unittest.TestCase):
    document_id = "a" * 32

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = main.DATA_DIR
        self.old_library_dir = main.LIBRARY_DIR
        main.DATA_DIR = Path(self.temp.name)
        main.LIBRARY_DIR = Path(self.temp.name) / "library"
        folder = main.DATA_DIR / self.document_id
        folder.mkdir()
        (folder / "status.json").write_text('{"status":"ready"}', encoding="utf-8")
        (folder / "document.json").write_text(json.dumps({"sections": [{"blocks": [{"id": "p1", "text": "first block"}, {"id": "p2", "text": "second block"}, {"id": "figure-1", "type": "figure"}]}]}), encoding="utf-8")
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.DATA_DIR = self.old_data_dir
        main.LIBRARY_DIR = self.old_library_dir
        self.temp.cleanup()

    def test_cross_block_note_round_trip(self) -> None:
        note = {"id": "b" * 32, "documentId": self.document_id, "type": "note", "color": "#ef7474", "note": "", "anchor": {
            "start": {"blockId": "p1", "offset": 3}, "end": {"blockId": "p2", "offset": 6},
            "quote": "st block second", "prefix": "fir", "suffix": " block"}}
        created = self.client.post(f"/api/documents/{self.document_id}/annotations", json=note)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["id"], note["id"])
        changed = self.client.patch(f"/api/documents/{self.document_id}/annotations/{note['id']}", json={"note": "A useful point"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["note"], "A useful point")
        records = self.client.get(f"/api/documents/{self.document_id}/annotations").json()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["anchor"], note["anchor"])
        self.assertEqual(self.client.delete(f"/api/documents/{self.document_id}/annotations/{note['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/documents/{self.document_id}/annotations").json(), [])

    def test_area_coordinates_are_normalized(self) -> None:
        area = {"type": "area", "color": "#f8d86a", "anchor": {"blockId": "figure-1", "page": 2, "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}}}
        self.assertEqual(self.client.post(f"/api/documents/{self.document_id}/annotations", json=area).status_code, 201)
        area["anchor"]["bbox"]["width"] = 1.5
        self.assertEqual(self.client.post(f"/api/documents/{self.document_id}/annotations", json=area).status_code, 422)

    def test_legacy_highlight_still_works(self) -> None:
        old = {"blockId": "p1", "quote": "first", "start": 0, "end": 5, "mode": "highlight", "color": "#f8d86a"}
        self.assertEqual(self.client.post(f"/api/documents/{self.document_id}/annotations", json=old).status_code, 201)

    def test_same_pdf_reuses_processing_document(self) -> None:
        old_process = main._process_document
        main._process_document = lambda document_id, filename: None
        try:
            payload = b"%PDF-1.4\nexample"
            first = self.client.post("/api/documents", files={"file": ("paper.pdf", payload, "application/pdf")})
            second = self.client.post("/api/documents", files={"file": ("renamed.pdf", payload, "application/pdf")})
            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            self.assertEqual(first.json()["documentId"], second.json()["documentId"])
        finally:
            main._process_document = old_process

    def test_old_model_cache_is_reparsed_on_import(self) -> None:
        payload = b"%PDF-1.4\nlegacy model"
        fingerprint = hashlib.sha256(payload).hexdigest()
        folder = main.DATA_DIR / self.document_id
        (folder / "status.json").write_text(json.dumps({"status": "ready", "documentId": self.document_id,
                                                         "fingerprint": fingerprint}), encoding="utf-8")
        old_process = main._process_document
        main._process_document = lambda document_id, filename: None
        try:
            response = self.client.post("/api/documents", files={"file": ("paper.pdf", payload, "application/pdf")})
            self.assertEqual(response.status_code, 202)
            self.assertNotEqual(response.json()["documentId"], self.document_id)
            self.assertEqual(response.json()["status"], "processing")
        finally:
            main._process_document = old_process

    def test_library_uses_opaque_id_and_reuses_document(self) -> None:
        nested = main.LIBRARY_DIR / "papers"
        nested.mkdir(parents=True)
        (nested / "sample.pdf").write_bytes(b"%PDF-1.4\nlibrary")
        (nested / "notes.txt").write_text("not a PDF", encoding="utf-8")
        items = self.client.get("/api/library").json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "sample.pdf")
        self.assertEqual(items[0]["folder"], "papers")
        self.assertNotIn("path", items[0])
        self.assertEqual(self.client.post("/api/library/../../open").status_code, 404)
        self.assertEqual(self.client.post("/api/library/not-a-library-id/open").status_code, 404)
        old_process = main._process_document
        main._process_document = lambda document_id, filename: None
        try:
            first = self.client.post(f"/api/library/{items[0]['id']}/open")
            second = self.client.post(f"/api/library/{items[0]['id']}/open")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(first.json()["documentId"], second.json()["documentId"])
        finally:
            main._process_document = old_process


if __name__ == "__main__":
    unittest.main()
