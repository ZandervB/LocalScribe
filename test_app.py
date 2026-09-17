import asyncio
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import unittest.mock

from fastapi.testclient import TestClient
from PIL import Image
import pymupdf

from app import (clean_correction, corrector_sections, create_app, INFERENCE_PIXELS, line_bands,
                 LocalEngine, map_lines_to_bands, refit_lines, Store, strip_preamble, TextCorrector,
                 trim_background, uncertain_spans)


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
        scored = [("Meeting", 0.99), (" notes", 0.98), ("\n", 0.99), ("Call", 0.97), (" ", 0.99),
                  ("Jane", 0.31), (" at", 0.98), (" 10", 0.95), (":", 0.99), ("30", 0.9), (".", 0.99)]
        return "Meeting notes\nCall Jane at 10:30.", self.truncated, scored


class FakeCorrector:
    model = "Fake corrector"

    def __init__(self):
        self.drafts = []
        self.doubted = []

    def ready(self):
        return True

    def correct(self, draft, doubted=()):
        self.drafts.append(draft)
        self.doubted.append(list(doubted))
        return draft.replace("Jane", "June")


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = FakeEngine()
        self.corrector = FakeCorrector()
        self.app = create_app(self.directory.name, self.engine, "test-secret", self.corrector)
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
        # The sidebar groups purely from these three columns of the list response.
        listed = sorted(self.client.get("/api/notes").json(), key=lambda row: row["page_number"])
        self.assertEqual({row["document_id"] for row in listed}, {notes[0]["document_id"]})
        self.assertEqual([row["document_title"] for row in listed], ["journal", "journal"])
        self.assertEqual([row["page_number"] for row in listed], [1, 2])

    def test_dense_portrait_page_is_segmented_in_reading_order(self):
        stream = BytesIO()
        page = Image.new("RGB", (600, 1800), "white")
        page.save(stream, format="PNG")
        response = self.client.post("/api/notes", data={"segment": "true"},
                                    files={"file": ("dense.png", stream.getvalue(), "image/png")})
        note = self.wait(response.json()["id"])
        self.assertGreaterEqual(len(self.engine.calls), 2)
        self.assertNotIn("\n\n", note["raw_text"])
        for region in note["regions"]:
            self.assertEqual(note["raw_text"][region["start"]:region["end"]], region["text"])
        for span in note["uncertain"]:
            self.assertEqual(note["raw_text"][span["start"]:span["end"]], span["text"])
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
        self.assertEqual(engine.transcribe(path), ("Exact text", False, []))
        self.assertTrue(engine.client.payload["logprobs"])
        self.assertEqual(engine.client.payload["messages"][0]["content"][0]["text"], "OCR")

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

    def test_correction_is_queued_automatically_once_transcription_finishes(self):
        original = self.wait(self.upload())
        enhanced = self.wait_enhancement(original["id"])
        self.assertEqual(enhanced["enhance_status"], "ready")
        self.assertIn("June", enhanced["enhanced_text"])
        # Automatic or not, the correction is never written into the editable text.
        self.assertEqual(enhanced["text"], original["text"])
        self.assertEqual(enhanced["raw_text"], original["raw_text"])
        self.assertEqual(self.corrector.drafts, [original["raw_text"]])

    def test_automatic_correction_can_be_switched_off(self):
        with tempfile.TemporaryDirectory() as directory:
            corrector = FakeCorrector()
            app = create_app(directory, FakeEngine(), "test-secret", corrector, auto_correct=False)
            with TestClient(app, headers={"Authorization": "Bearer test-secret"}) as client:
                note = client.post("/api/notes", files={"file": ("n.png", self.image, "image/png")}).json()
                end = time.monotonic() + 5
                while time.monotonic() < end and client.get("/api/notes/" + note["id"]).json()["status"] != "ready":
                    time.sleep(0.01)
                time.sleep(0.3)
                after = client.get("/api/notes/" + note["id"]).json()
                self.assertEqual(after["enhance_status"], "idle")
                self.assertEqual(corrector.drafts, [])
                self.assertEqual(client.post(f"/api/notes/{note['id']}/enhance").status_code, 202)

    def test_ai_enhancement_is_separate_and_preserves_original_ocr(self):
        original = self.wait(self.upload())
        enhanced = self.wait_enhancement(original["id"])
        self.assertEqual(enhanced["enhance_status"], "ready")
        self.assertEqual(enhanced["raw_text"], original["raw_text"])
        self.assertIn("Jane", enhanced["raw_text"])
        self.assertIn("June", enhanced["enhanced_text"])
        self.assertEqual(enhanced["text"], original["text"])
        self.assertEqual(self.corrector.drafts, [original["raw_text"]])
        self.assertEqual(self.corrector.doubted, [["Jane"]])

    def test_ai_correction_is_unavailable_without_a_text_model(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, FakeEngine(), "test-secret")
            with TestClient(app, headers={"Authorization": "Bearer test-secret"}) as client:
                self.assertFalse(client.get("/api/status").json()["corrector_ready"])
                note = client.post("/api/notes", files={"file": ("note.png", self.image, "image/png")}).json()
                end = time.monotonic() + 5
                while time.monotonic() < end and client.get("/api/notes/" + note["id"]).json()["status"] != "ready":
                    time.sleep(0.01)
                self.assertEqual(client.post(f"/api/notes/{note['id']}/enhance").status_code, 503)

    def test_corrector_sends_the_whole_page_and_strips_model_decoration(self):
        class JsonResponse(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()

        class CapturingClient:
            payloads = []
            def open(inner_self, request, timeout):
                inner_self.payloads.append(json.loads(request.data))
                return JsonResponse(json.dumps({"choices": [{"message": {
                    "content": "```\nCorrected text:\nMeeting notes\nCall June at 10:30.\n```"},
                    "finish_reason": "stop"}]}).encode())

        corrector = TextCorrector()
        corrector.client = CapturingClient()
        draft = "Meeting notes\nCall Jane at 10:30."
        self.assertEqual(corrector.correct(draft), "Meeting notes\nCall June at 10:30.")
        prompt = corrector.client.payloads[0]["messages"][1]["content"]
        self.assertIn(draft, prompt)
        self.assertEqual(corrector.client.payloads[0]["temperature"], 0)

    def test_reflowed_correction_is_put_back_on_the_page_lines(self):
        draft = "gelatin all agree in keep asi by dis=\nsolved by the secretion, but more of them"
        reflowed = "gelatin all agree in keeping as by dissolved by the secretion, but none of them"
        fitted = refit_lines(draft, reflowed)
        self.assertEqual(len(fitted.split("\n")), 2)
        self.assertTrue(fitted.startswith("gelatin all agree in keeping as by"))
        self.assertTrue(fitted.split("\n")[1].endswith("but none of them"))
        blank = "first line\n\nlast line"
        self.assertEqual(refit_lines(blank, "first line\n\nlast line"), blank)
        self.assertEqual(refit_lines("kept as is", ""), "kept as is")

    def test_corrector_discards_a_rewrite_that_diverges_from_the_transcription(self):
        class JsonResponse(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()

        class RamblingClient:
            def open(inner_self, request, timeout):
                return JsonResponse(json.dumps({"choices": [{"message": {
                    "content": "Here is a summary of the page. " * 40}, "finish_reason": "stop"}]}).encode())

        corrector = TextCorrector()
        corrector.client = RamblingClient()
        with self.assertRaises(RuntimeError):
            corrector.correct("Meeting notes\nCall Jane at 10:30.")

    def test_long_pages_are_split_into_context_sized_sections(self):
        blocks = ["Paragraph %d %s" % (index, "word " * 100) for index in range(12)]
        sections = corrector_sections("\n\n".join(blocks), limit=1000)
        self.assertGreater(len(sections), 1)
        self.assertTrue(all(len(section) <= 1000 for section in sections))
        self.assertEqual("\n\n".join(sections), "\n\n".join(blocks))
        unbroken = corrector_sections("word " * 900, limit=1000)
        self.assertTrue(all(len(section) <= 1000 for section in unbroken))
        self.assertEqual(clean_correction("Correction:\nplain text"), "plain text")

    def test_low_confidence_words_are_recorded_against_the_page_text(self):
        note = self.wait(self.upload())
        self.assertEqual([(span["text"], span["p"]) for span in note["uncertain"]], [("Jane", 0.31)])
        span = note["uncertain"][0]
        self.assertEqual(note["raw_text"][span["start"]:span["end"]], "Jane")
        self.assertIsNone(span["line"])

    def test_chat_preamble_is_removed_so_it_cannot_shift_the_scan_highlights(self):
        page = "I have told this to a few people\nnever will again, but one day"
        for preamble in ("Here are the OCR - results of the text in the image:\n\n",
                         "The text in the image reads:\n",
                         "Transcription:\n\n"):
            self.assertEqual(strip_preamble(preamble + page), page)
        self.assertEqual(strip_preamble(page), page)
        self.assertEqual(strip_preamble("Notes: buy milk\nand eggs"), "Notes: buy milk\nand eggs")
        self.assertEqual(strip_preamble("Transcription:\n\n"), "Transcription:\n\n")

    def test_uncertainty_ignores_scores_that_do_not_match_the_text(self):
        self.assertEqual(uncertain_spans("Call Jane", [("something", 0.1)]), [])
        self.assertEqual(uncertain_spans("Call Jane", []), [])
        spans = uncertain_spans("  Call Jane. ".strip(), [("  Call", 0.9), (" Ja", 0.2), ("ne", 0.4), (".", 0.9), (" ", 0.9)])
        self.assertEqual([(span["text"], span["p"]) for span in spans], [("Jane.", 0.2)])

    def test_detected_ink_lines_locate_words_in_the_scan(self):
        page = Image.new("L", (200, 300), 255)
        for top in (40, 120, 200):
            for row in range(top, top + 18):
                for column in range(20, 180):
                    page.putpixel((column, row), 0)
        bands = line_bands(page, 0.0, 1.0)
        self.assertEqual(len(bands), 3)
        self.assertAlmostEqual(bands[0]["top"], 40 / 300, places=2)
        self.assertAlmostEqual(bands[2]["bottom"], 218 / 300, places=2)
        self.assertEqual(map_lines_to_bands("one\ntwo\nthree", bands, 0), {0: 0, 1: 1, 2: 2})
        self.assertEqual(map_lines_to_bands("one\n\ntwo", bands, 5), {0: 5, 2: 7})
        self.assertEqual(map_lines_to_bands("text", [], 0), {})

    def test_non_ascii_access_code_is_rejected_not_crashed(self):
        async def send_header(raw):
            statuses = []
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "GET", "path": "/api/notes", "raw_path": b"/api/notes",
                     "query_string": b"", "root_path": "", "scheme": "http",
                     "headers": [(b"host", b"test"), (b"authorization", raw)],
                     "client": ("127.0.0.1", 1), "server": ("test", 80)}

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                if message["type"] == "http.response.start":
                    statuses.append(message["status"])
            await self.app(scope, receive, send)
            return statuses

        self.assertEqual(asyncio.run(send_header("Bearer éé".encode("latin-1"))), [401])
        self.assertEqual(asyncio.run(send_header(b"Bearer wrong")), [401])
        self.assertEqual(asyncio.run(send_header(b"Bearer test-secret")), [200])

    def test_note_list_reports_correction_state_for_the_sidebar(self):
        note_id = self.upload()
        self.wait(note_id)
        self.wait_enhancement(note_id)
        row = next(item for item in self.client.get("/api/notes").json() if item["id"] == note_id)
        self.assertEqual(row["enhance_status"], "ready")

    def test_desk_around_a_photographed_page_is_trimmed_but_a_full_scan_is_not(self):
        photo = Image.new("RGB", (400, 600), (40, 35, 30))
        photo.paste(Image.new("RGB", (360, 400), "white"), (20, 40))
        left, top, right, bottom = trim_background(photo)
        self.assertLess(top, 60)
        self.assertGreater(bottom, 400)
        self.assertLess(bottom, 520)
        self.assertLess(right - left, 400)
        scan = Image.new("RGB", (400, 600), "white")
        for row in range(40, 560, 40):
            scan.paste(Image.new("RGB", (360, 12), (30, 30, 30)), (20, row))
        self.assertIsNone(trim_background(scan), "a page-to-edge scan must never be cropped")
        self.assertIsNone(trim_background(Image.new("RGB", (400, 600), (12, 12, 12))))

    def test_inference_copy_is_bounded_and_the_original_is_untouched(self):
        stream = BytesIO()
        Image.new("RGB", (3000, 2400), "white").save(stream, format="PNG")
        note = self.app.state.store.add(stream.getvalue(), "big.png")
        with Image.open(self.app.state.store.files / (note["id"] + ".preview.jpg")) as preview:
            self.assertEqual(max(preview.size), INFERENCE_PIXELS)
        with Image.open(self.app.state.store.files / note["original"]) as original:
            self.assertEqual(original.size, (3000, 2400))

    def test_correction_model_may_still_be_loading_when_the_app_starts(self):
        self.corrector.ready = lambda: False
        note_id = self.upload()
        self.wait(note_id)
        self.assertFalse(self.client.get("/api/status").json()["corrector_ready"])
        refused = self.client.post("/api/notes/" + note_id + "/enhance")
        self.assertEqual(refused.status_code, 503)
        self.assertIn("still starting", refused.json()["detail"])
        self.assertEqual(self.client.get("/api/notes/" + note_id).json()["enhance_status"], "idle")
        self.corrector.ready = lambda: True
        self.assertTrue(self.client.get("/api/status").json()["corrector_ready"])
        self.assertEqual(self.client.post("/api/notes/" + note_id + "/enhance").status_code, 202)
        self.assertEqual(self.wait_enhancement(note_id)["enhance_status"], "ready")

    def test_decoding_an_upload_does_not_stall_other_requests(self):
        holding, released = threading.Event(), threading.Event()
        original = Store.add

        def blocking_add(self, *args, **kwargs):
            holding.set()
            released.wait(timeout=10)
            return original(self, *args, **kwargs)

        answers = []
        with unittest.mock.patch.object(Store, "add", blocking_add):
            uploader = threading.Thread(target=lambda: answers.append(
                self.client.post("/api/notes", files={"file": ("note.png", self.image, "image/png")}).status_code))
            uploader.start()
            self.assertTrue(holding.wait(timeout=5), "upload never reached image decoding")
            started = time.monotonic()
            answered = self.client.get("/api/status")
            waited = time.monotonic() - started
            released.set()
            uploader.join(timeout=15)
        self.assertEqual(answers, [202])
        self.assertEqual(answered.status_code, 200)
        self.assertLess(waited, 2, "a decoding upload blocked an unrelated request")


if __name__ == "__main__":
    unittest.main()
