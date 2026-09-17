from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient
from PIL import Image
import pymupdf

from app import create_app, LocalEngine, Store


class FakeEngine:
    def __init__(self):
        self.failure = False
        self.truncated = False
        self.calls = []
        self.gate = None
        self.started = threading.Event()

    def ready(self):
        return True

    def transcribe(self, path):
        with Image.open(path) as image:
            image.verify()
        self.calls.append(Path(path).name)
        self.started.set()
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.failure:
            raise RuntimeError("Simulated interruption")
        return "Meeting notes\nCall Jane at 10:30.", self.truncated

    def enhance(self, path, draft):
        with Image.open(path) as image:
            image.verify()
        return draft.replace("Jane", "June"), False


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = FakeEngine()
        self.app = create_app(self.directory.name, self.engine, "test-secret")
        self.client = TestClient(self.app, headers={"Authorization": "Bearer test-secret"})
        self.client.__enter__()
        stream = BytesIO()
        Image.new("RGB", (120, 80), "white").save(stream, format="PNG")
        self.image = stream.getvalue()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def upload(self):
        response = self.client.post("/api/notes", files={"file": ("note.png", self.image, "image/png")})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["id"]

    def wait(self, note_id):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            note = self.client.get("/api/notes/" + note_id).json()
            if note["status"] not in ("queued", "running"):
                return note
            time.sleep(0.01)
        self.fail("Worker did not complete")

    def wait_enhancement(self, note_id):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            note = self.client.get("/api/notes/" + note_id).json()
            if note["enhance_status"] not in ("queued", "running"):
                return note
            time.sleep(0.01)
        self.fail("Enhancement did not complete")

    def test_auth_required_for_notes_and_images(self):
        note_id = self.upload()
        for path in ("/api/notes", "/api/notes/" + note_id + "/image", "/api/status"):
            self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code, 401)

    def test_upload_edit_export_preserves_original_and_raw_text(self):
        note_id = self.upload()
        note = self.wait(note_id)
        self.assertEqual(note["status"], "ready")
        original_text = note["raw_text"]
        response = self.client.put("/api/notes/" + note_id, json={
            "title": "Corrected meeting", "text": "Call June at 10:30.", "reviewed": True, "revision": note["revision"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["raw_text"], original_text)
        self.assertEqual(self.client.get("/api/notes/" + note_id + "/export").text, "Call June at 10:30.")
        self.assertEqual(self.client.get("/api/notes/" + note_id + "/image?original=true").content, self.image)
        self.assertEqual(len(self.client.get("/api/notes?q=June").json()), 1)
        self.assertEqual(self.client.get("/api/notes?q=does-not-exist").json(), [])

    def test_stale_editor_cannot_overwrite_newer_edits(self):
        note_id = self.upload()
        note = self.wait(note_id)
        value = {"title": "Note", "text": "First correction", "reviewed": False, "revision": note["revision"]}
        self.assertEqual(self.client.put("/api/notes/" + note_id, json=value).status_code, 200)
        value["text"] = "Stale correction"
        self.assertEqual(self.client.put("/api/notes/" + note_id, json=value).status_code, 409)
        self.assertEqual(self.client.get("/api/notes/" + note_id).json()["text"], "First correction")

    def test_invalid_image_not_saved(self):
        response = self.client.post("/api/notes", files={"file": ("bad.png", b"not an image", "image/png")})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/notes").json(), [])

    def test_failure_can_retry_without_duplicate_note(self):
        self.engine.failure = True
        note_id = self.upload()
        self.assertEqual(self.wait(note_id)["status"], "error")
        self.engine.failure = False
        self.assertEqual(self.client.post("/api/notes/" + note_id + "/retry").status_code, 200)
        self.assertEqual(self.wait(note_id)["status"], "ready")
        self.assertEqual(len(self.client.get("/api/notes").json()), 1)
        self.assertEqual(self.client.post("/api/notes/" + note_id + "/retry").status_code, 409)

    def test_truncated_output_is_marked_for_review(self):
        self.engine.truncated = True
        note = self.wait(self.upload())
        self.assertEqual(note["truncated"], 1)
        self.assertEqual(note["reviewed"], 0)

    def test_interrupted_jobs_recover_as_retryable(self):
        note_id = self.upload()
        self.wait(note_id)
        with self.app.state.store.connect() as db:
            db.execute("UPDATE notes SET status='running',text='',raw_text='' WHERE id=?", (note_id,))
        restarted = Store(self.directory.name)
        self.assertEqual(restarted.get(note_id)["status"], "error")
        self.assertIn("interrupted", restarted.get(note_id)["error"])

    def test_exif_rotation_applied_only_to_preview(self):
        source = Image.new("RGB", (80, 40), "white")
        exif = source.getexif()
        exif[274] = 6
        stream = BytesIO()
        source.save(stream, "JPEG", exif=exif)
        note = self.app.state.store.add(stream.getvalue(), "phone.jpg")
        with Image.open(self.app.state.store.files / (note["id"] + ".preview.jpg")) as preview:
            self.assertEqual(preview.size, (40, 80))
        self.assertEqual((self.app.state.store.files / note["original"]).read_bytes(), stream.getvalue())

    def test_pdf_import_creates_one_note_per_page(self):
        document = pymupdf.open()
        for number in (1, 2):
            page = document.new_page(width=300, height=400)
            page.insert_text((40, 80), f"Handwritten page {number}")
        content = document.tobytes()
        document.close()
        response = self.client.post("/api/notes", data={"segment": "false"},
                                    files={"file": ("journal.pdf", content, "application/pdf")})
        self.assertEqual(response.status_code, 202, response.text)
        notes = response.json()["notes"]
        self.assertEqual(len(notes), 2)
        self.assertTrue(notes[0]["document_id"])
        self.assertEqual(notes[0]["document_id"], notes[1]["document_id"])
        self.assertEqual([note["page_number"] for note in notes], [1, 2])
        self.assertEqual([note["title"] for note in notes],
                         ["journal - page 001", "journal - page 002"])
        for note in notes:
            ready = self.wait(note["id"])
            self.assertEqual(ready["status"], "ready")
            self.assertEqual(ready["segment"], 0)
            original = self.client.get(f"/api/notes/{note['id']}/image?original=true")
            self.assertEqual(original.headers["content-type"], "image/jpeg")
        exported = self.client.get(f"/api/documents/{notes[0]['document_id']}/export")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("--- Page 1 ---", exported.text)
        self.assertIn("--- Page 2 ---", exported.text)

    def test_dense_portrait_page_is_segmented_in_reading_order(self):
        stream = BytesIO()
        page = Image.new("RGB", (600, 1800), "white")
        page.save(stream, format="PNG")
        response = self.client.post("/api/notes", data={"segment": "true"},
                                    files={"file": ("dense.png", stream.getvalue(), "image/png")})
        note = self.wait(response.json()["id"])
        self.assertGreaterEqual(len(self.engine.calls), 2)
        self.assertIn("\n\n", note["raw_text"])
        self.assertEqual(len(note["regions"]), len(self.engine.calls))
        self.assertEqual(note["regions"][0]["top"], 0)
        self.assertEqual(note["regions"][-1]["bottom"], 1)
        self.assertTrue(all(left["bottom"] == right["top"]
                            for left, right in zip(note["regions"], note["regions"][1:])))

    def test_hunyuan_request_uses_ocr_prompt(self):
        class JsonResponse(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()

        class CapturingClient:
            payload = None
            def open(inner_self, request, timeout):
                inner_self.payload = json.loads(request.data)
                return JsonResponse(b'{"choices":[{"message":{"content":"Exact text"},"finish_reason":"stop"}]}')

        path = Path(self.directory.name) / "prompt.jpg"
        path.write_bytes(self.image)
        engine = LocalEngine()
        engine.client = CapturingClient()
        self.assertEqual(engine.transcribe(path), ("Exact text", False))
        self.assertEqual(engine.client.payload["messages"][0]["content"][0]["text"], "OCR")
        self.assertEqual(engine.enhance(path, "A draft that must not confuse the OCR model"), ("Exact text", False))
        self.assertEqual(engine.client.payload["messages"][0]["content"][0]["text"], "OCR")
        self.assertEqual(engine.client.payload["max_tokens"], 4096)

    def test_queued_transcription_can_be_cancelled(self):
        self.engine.gate = threading.Event()
        first = self.upload()
        self.assertTrue(self.engine.started.wait(timeout=2))
        second = self.upload()
        response = self.client.post(f"/api/notes/{second}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "cancelled")
        self.engine.gate.set()
        self.assertEqual(self.wait(first)["status"], "ready")
        time.sleep(0.1)
        self.assertEqual(self.client.get(f"/api/notes/{second}").json()["status"], "cancelled")

    def test_ready_note_can_be_deleted_with_files(self):
        note = self.wait(self.upload())
        original = self.app.state.store.files / note["original"]
        preview = self.app.state.store.files / f"{note['id']}.preview.jpg"
        self.assertTrue(original.exists() and preview.exists())
        self.assertEqual(self.client.delete(f"/api/notes/{note['id']}").status_code, 204)
        self.assertFalse(original.exists() or preview.exists())
        self.assertEqual(self.client.get(f"/api/notes/{note['id']}").status_code, 404)

    def test_existing_notes_can_be_grouped_in_selected_order(self):
        first, second = self.wait(self.upload()), self.wait(self.upload())
        response = self.client.post("/api/documents", json={
            "title": "Project notes", "note_ids": [second["id"], first["id"]],
        })
        self.assertEqual(response.status_code, 201, response.text)
        document_id = response.json()["id"]
        notes = {note["id"]: note for note in self.client.get("/api/notes").json()}
        self.assertEqual(notes[second["id"]]["page_number"], 1)
        self.assertEqual(notes[first["id"]]["page_number"], 2)
        self.assertEqual(notes[first["id"]]["document_title"], "Project notes")
        exported = self.client.get(f"/api/documents/{document_id}/export").text
        self.assertLess(exported.index("--- Page 1 ---"), exported.index("--- Page 2 ---"))

    def test_pages_can_be_appended_to_an_existing_document(self):
        first, second, third = (self.wait(self.upload()) for _ in range(3))
        created = self.client.post("/api/documents", json={
            "title": "Field notes", "note_ids": [first["id"]],
        }).json()
        response = self.client.post("/api/documents", json={
            "title": "Ignored for an existing document", "note_ids": [third["id"], second["id"]],
            "document_id": created["id"],
        })
        self.assertEqual(response.status_code, 201, response.text)
        notes = {note["id"]: note for note in self.client.get("/api/notes").json()}
        self.assertEqual([notes[note["id"]]["page_number"] for note in (first, third, second)], [1, 2, 3])
        self.assertTrue(all(notes[note["id"]]["document_title"] == "Field notes" for note in (first, second, third)))

    def test_whole_document_can_be_deleted_with_all_files(self):
        pages = [self.wait(self.upload()), self.wait(self.upload())]
        document = self.client.post("/api/documents", json={
            "title": "Disposable", "note_ids": [page["id"] for page in pages],
        }).json()
        paths = [path for page in pages for path in (
            self.app.state.store.files / page["original"],
            self.app.state.store.files / f"{page['id']}.preview.jpg",
        )]
        self.assertTrue(all(path.exists() for path in paths))
        self.assertEqual(self.client.delete(f"/api/documents/{document['id']}").status_code, 204)
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertTrue(all(self.client.get(f"/api/notes/{page['id']}").status_code == 404 for page in pages))

    def test_ai_enhancement_is_separate_and_preserves_original_ocr(self):
        original = self.wait(self.upload())
        response = self.client.post(f"/api/notes/{original['id']}/enhance")
        self.assertEqual(response.status_code, 202, response.text)
        enhanced = self.wait_enhancement(original["id"])
        self.assertEqual(enhanced["enhance_status"], "ready")
        self.assertEqual(enhanced["raw_text"], original["raw_text"])
        self.assertIn("Jane", enhanced["raw_text"])
        self.assertIn("June", enhanced["enhanced_text"])
        self.assertEqual(enhanced["text"], original["text"])


if __name__ == "__main__":
    unittest.main()
