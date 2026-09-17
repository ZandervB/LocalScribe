"""Small, local handwriting notebook. One worker, SQLite, ordinary files."""
import base64
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from io import BytesIO
import json
import math
import os
from pathlib import Path
import queue
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
import warnings

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
import pymupdf
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 15 * 1024 * 1024
MAX_PDF_UPLOAD = 50 * 1024 * 1024
MAX_PDF_PAGES = 50
PDF_DPI = 160
Image.MAX_IMAGE_PIXELS = 25_000_000
FORMATS = {"JPEG": (".jpg", "image/jpeg"), "PNG": (".png", "image/png"), "WEBP": (".webp", "image/webp")}
CORRECTOR_SECTION_CHARS = 4500
CORRECTOR_CONTEXT_CHARS = 9000


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.files = self.directory / "pages"
        self.files.mkdir(exist_ok=True)
        self.database = self.directory / "notes.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL,
                status TEXT NOT NULL, original TEXT NOT NULL, mime TEXT NOT NULL,
                raw_text TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
                reviewed INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                seconds REAL, truncated INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 0,
                segment INTEGER NOT NULL DEFAULT 1,
                regions TEXT NOT NULL DEFAULT '[]',
                document_id TEXT NOT NULL DEFAULT '',
                document_title TEXT NOT NULL DEFAULT '',
                page_number INTEGER,
                enhanced_text TEXT NOT NULL DEFAULT '',
                enhance_status TEXT NOT NULL DEFAULT 'idle',
                enhance_error TEXT NOT NULL DEFAULT '',
                uncertain TEXT NOT NULL DEFAULT '[]',
                lines TEXT NOT NULL DEFAULT '[]'
            )""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(notes)")}
            migrations = {
                "segment": "ALTER TABLE notes ADD COLUMN segment INTEGER NOT NULL DEFAULT 1",
                "regions": "ALTER TABLE notes ADD COLUMN regions TEXT NOT NULL DEFAULT '[]'",
                "document_id": "ALTER TABLE notes ADD COLUMN document_id TEXT NOT NULL DEFAULT ''",
                "document_title": "ALTER TABLE notes ADD COLUMN document_title TEXT NOT NULL DEFAULT ''",
                "page_number": "ALTER TABLE notes ADD COLUMN page_number INTEGER",
                "enhanced_text": "ALTER TABLE notes ADD COLUMN enhanced_text TEXT NOT NULL DEFAULT ''",
                "enhance_status": "ALTER TABLE notes ADD COLUMN enhance_status TEXT NOT NULL DEFAULT 'idle'",
                "enhance_error": "ALTER TABLE notes ADD COLUMN enhance_error TEXT NOT NULL DEFAULT ''",
                "uncertain": "ALTER TABLE notes ADD COLUMN uncertain TEXT NOT NULL DEFAULT '[]'",
                "lines": "ALTER TABLE notes ADD COLUMN lines TEXT NOT NULL DEFAULT '[]'",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    db.execute(statement)
            db.execute("UPDATE notes SET status='error', error='Processing was interrupted. You can retry.' WHERE status IN ('queued','running')")
            db.execute("""UPDATE notes SET enhance_status='error',
                enhance_error='AI correction was interrupted. You can run it again.'
                WHERE enhance_status IN ('queued','running')""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, note_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
        note = dict(row)
        for field in ("regions", "uncertain", "lines"):
            try:
                note[field] = json.loads(note.get(field) or "[]")
            except json.JSONDecodeError:
                note[field] = []
        return note

    def add(self, content, filename, segment=True, document_id="", document_title="", page_number=None):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as source:
                    if source.format not in FORMATS or getattr(source, "n_frames", 1) != 1:
                        raise ValueError("Use a single-page JPEG, PNG, or WebP image.")
                    suffix, mime = FORMATS[source.format]
                    source.load()
                    oriented = ImageOps.exif_transpose(source)
                    if oriented.mode in ("RGBA", "LA") or "transparency" in oriented.info:
                        rgba = oriented.convert("RGBA")
                        preview = Image.new("RGB", rgba.size, "white")
                        preview.paste(rgba, mask=rgba.getchannel("A"))
                    else:
                        preview = oriented.convert("RGB")
                    # Keep the full-resolution original. Bound only the inference copy.
                    preview.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise HTTPException(400, "Use a readable JPEG, PNG, or WebP image under 25 megapixels.") from exc
        note_id = uuid.uuid4().hex
        original = note_id + suffix
        title = Path(filename.replace("\\", "/")).stem[:160] or "Untitled note"
        (self.files / original).write_bytes(content)
        preview.save(self.files / f"{note_id}.preview.jpg", quality=95)
        with self.connect() as db:
            db.execute("""INSERT INTO notes(id,title,created,status,original,mime,segment,
                       document_id,document_title,page_number) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       (note_id, title, now(), "queued", original, mime, int(segment),
                        document_id, document_title, page_number))
        return self.get(note_id)

    def add_pdf(self, content, filename, segment=True):
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
            if document.needs_pass:
                raise ValueError("Password-protected PDFs are not supported.")
            if not 1 <= document.page_count <= MAX_PDF_PAGES:
                raise ValueError(f"PDFs must contain between 1 and {MAX_PDF_PAGES} pages.")
            rendered = []
            stem = Path(filename.replace("\\", "/")).stem[:140] or "Imported PDF"
            for number, page in enumerate(document, start=1):
                matrix = pymupdf.Matrix(PDF_DPI / 72, PDF_DPI / 72)
                pixmap = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
                if pixmap.width * pixmap.height > Image.MAX_IMAGE_PIXELS:
                    raise ValueError(f"PDF page {number} is too large to render safely.")
                rendered.append((pixmap.tobytes("jpeg", jpg_quality=94),
                                 f"{stem} - page {number:03}.jpg"))
            document.close()
        except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc) or "Use a readable PDF.") from exc
        document_id = uuid.uuid4().hex
        return [self.add(page, name, segment, document_id, stem, number)
                for number, (page, name) in enumerate(rendered, start=1)]


@contextmanager
def segmented_inputs(image_path, enabled):
    """Split tall pages near low-ink rows, preserving top-to-bottom order."""
    if not enabled:
        yield [{"path": image_path, "top": 0.0, "bottom": 1.0}]
        return
    with Image.open(image_path) as source:
        width, height = source.size
        if height <= 1200 or height <= width * 1.15:
            yield [{"path": image_path, "top": 0.0, "bottom": 1.0}]
            return
        pieces = min(4, max(2, (height + 849) // 850))
        probe = source.convert("L").resize((min(width, 256), height))
        pixels = probe.load()
        boundaries = [0]
        for index in range(1, pieces):
            target = round(height * index / pieces)
            low, high = max(boundaries[-1] + 300, target - 120), min(height - 300, target + 120)
            cut = min(range(low, high + 1), key=lambda row: sum(255 - pixels[column, row] for column in range(probe.width)))
            boundaries.append(cut)
        boundaries.append(height)
        crops = [source.crop((0, top, width, bottom)).convert("RGB")
                 for top, bottom in zip(boundaries, boundaries[1:])]
    import tempfile
    with tempfile.TemporaryDirectory(prefix="localscribe-segments-", dir=image_path.parent) as directory:
        inputs = []
        for index, (crop, top, bottom) in enumerate(zip(crops, boundaries, boundaries[1:]), start=1):
            path = Path(directory) / f"segment-{index:02}.jpg"
            crop.save(path, "JPEG", quality=95)
            inputs.append({"path": path, "top": top / height, "bottom": bottom / height})
        yield inputs


class ChatClient:
    def __init__(self, port, key="", log="data/engine.log"):
        # Never accept an arbitrary upstream URL: documents must remain on this PC.
        self.url = f"http://127.0.0.1:{int(port)}"
        self.headers = {"Authorization": "Bearer " + key} if key else {}
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.log = log

    def ready(self):
        try:
            request = urllib.request.Request(self.url + "/health", headers=self.headers)
            with self.client.open(request, timeout=2) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def complete(self, messages, max_tokens, logprobs=False):
        payload = {"messages": messages, "temperature": 0, "max_tokens": max_tokens, "stream": False}
        if logprobs:
            payload["logprobs"] = True
        request = urllib.request.Request(self.url + "/v1/chat/completions",
                                         data=json.dumps(payload).encode(),
                                         headers={**self.headers, "Content-Type": "application/json"})
        try:
            with self.client.open(request, timeout=900) as response:
                result = json.load(response)
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError(f"The local model did not finish. Check {self.log}, then retry.") from exc
        choice = result["choices"][0]
        text = choice["message"].get("content")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("No text was returned. Try a clearer photo or a smaller section of the page.")
        scored = [(item.get("token", ""), math.exp(item["logprob"]))
                  for item in ((choice.get("logprobs") or {}).get("content") or [])
                  if isinstance(item.get("logprob"), (int, float))]
        return text.strip(), choice.get("finish_reason") == "length", scored


class LocalEngine(ChatClient):
    def __init__(self, port=8091, key=""):
        super().__init__(port, key, "data/engine.log")

    def _infer(self, image_path, prompt, max_tokens=2048, logprobs=False):
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return self.complete([{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded}},
        ]}], max_tokens, logprobs)

    def transcribe(self, image_path):
        """Returns the text, whether it was cut short, and the model's own per-token scores."""
        return self._infer(image_path, "OCR", logprobs=True)


UNCERTAIN_PROBABILITY = 0.65


def uncertain_spans(text, scored, minimum=UNCERTAIN_PROBABILITY):
    """Whole words whose tokens the recognition model itself scored as unlikely."""
    joined = "".join(token for token, _ in scored)
    offset = joined.find(text)
    if not scored or offset < 0:
        return []
    spans, position = [], -offset
    for token, probability in scored:
        start, position = position, position + len(token)
        if probability >= minimum or position <= 0 or start >= len(text):
            continue
        low, high = max(start, 0), min(position, len(text))
        while low < high and text[low].isspace():
            low += 1
        while high > low and text[high - 1].isspace():
            high -= 1
        if low >= high:
            continue
        while low > 0 and not text[low - 1].isspace():
            low -= 1
        while high < len(text) and not text[high].isspace():
            high += 1
        if not any(character.isalnum() for character in text[low:high]):
            continue
        if spans and low <= spans[-1]["end"]:
            spans[-1]["end"] = max(spans[-1]["end"], high)
            spans[-1]["text"] = text[spans[-1]["start"]:spans[-1]["end"]]
            spans[-1]["p"] = min(spans[-1]["p"], round(probability, 3))
            continue
        spans.append({"start": low, "end": high, "text": text[low:high], "p": round(probability, 3)})
    return spans


def ink_lines(image, floor=0.16, gap=2):
    """Rows of ink in a page image, as (top, bottom) pixel pairs."""
    # Photographed pages carry a dark frame; profiling the inside avoids reading it as a line.
    inset = max(2, round(min(image.width, image.height) * 0.02))
    if image.width <= 2 * inset or image.height <= 2 * inset:
        return []
    inside = image.convert("L").crop((inset, inset, image.width - inset, image.height - inset))
    column = inside.resize((1, inside.height), Image.Resampling.BOX)
    profile = [255 - column.getpixel((0, row)) for row in range(inside.height)]
    page = min(profile)
    peak = max(profile) - page
    if peak < 6:
        return []
    inked = [value - page > peak * floor for value in profile]
    lines, start, quiet = [], None, 0
    for row, on in enumerate(inked + [False] * (gap + 1)):
        if on:
            start, quiet = (row if start is None else start), 0
        elif start is not None:
            quiet += 1
            if quiet > gap:
                if row - quiet - start >= 3:
                    lines.append((start + inset, row - quiet + inset))
                start = None
    return lines


def line_bands(image, top, bottom):
    """Detected text lines of one segment, as fractions of the whole page."""
    height = image.height or 1
    span = bottom - top
    return [{"top": top + (first / height) * span, "bottom": top + (last / height) * span}
            for first, last in ink_lines(image)]


def text_rows(text):
    return [index for index, line in enumerate(text.split("\n")) if line.strip()]


def map_lines_to_bands(text, bands, first_band):
    """Estimate which detected band of ink each text line came from.

    The recognition model gives no coordinates, so each line is placed
    proportionally down the written area and snapped to the nearest band. A scan
    can hold marks the model never transcribed, so this is a pointer for review
    rather than a measured position.
    """
    rows = text_rows(text)
    if not bands or not rows:
        return {}
    first, last = bands[0]["top"], bands[-1]["bottom"]
    centres = [(band["top"] + band["bottom"]) / 2 for band in bands]
    mapping = {}
    for position, row in enumerate(rows):
        estimate = first + (last - first) * (position + 0.5) / len(rows)
        nearest = min(range(len(centres)), key=lambda index: abs(centres[index] - estimate))
        mapping[row] = first_band + nearest
    return mapping


CORRECTOR_SYSTEM = (
    "You repair transcriptions produced by a handwriting-recognition model. "
    "The words are real; only the reading of them may be wrong. "
    "You reply with the corrected transcription and nothing else."
)
CORRECTOR_RULES = """Correct the handwriting-OCR transcription below.

Rules:
- Use the whole page - its subject, vocabulary, names and style - to work out what a garbled word must have been.
- Fix only recognition mistakes: wrong letters, merged or split words, missing spaces, confusions such as rn/m, l/1, O/0, c/e, and broken punctuation.
- Keep the writer's own wording, spelling, abbreviations, capitalisation and line breaks. Do not translate, rephrase, summarise, reorder or add anything.
- Never invent content. If a word cannot be resolved with confidence, repeat it exactly as it is.
- Leave names, numbers, dates and amounts alone unless the page itself makes the correct reading obvious.
- Reply with the corrected text only: no commentary, no headings, no code fences."""


def corrector_sections(text, limit=CORRECTOR_SECTION_CHARS):
    """Split a long page on blank lines so each request fits the model's context."""
    blocks = []
    for block in text.split("\n\n"):
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            blocks.append(block[:cut])
            block = block[cut:].lstrip("\n")
        blocks.append(block)
    sections, current = [], ""
    for block in blocks:
        if current and len(current) + len(block) + 2 > limit:
            sections.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        sections.append(current)
    return sections or [text]


def clean_correction(text):
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
        while lines and not lines[-1].startswith("```"):
            lines.pop()
        if lines:
            lines.pop()
    if lines and lines[0].rstrip(":").strip().lower() in ("corrected text", "corrected transcription", "correction"):
        lines = lines[1:]
    return "\n".join(lines).strip()


class TextCorrector(ChatClient):
    """Instruction-following text model that repairs a finished OCR draft."""

    def __init__(self, port=8092, key="", model="the local text model"):
        super().__init__(port, key, "data/corrector.log")
        self.model = model

    def correct(self, draft):
        draft = draft.strip()
        if not draft:
            raise RuntimeError("There is no transcription to correct yet.")
        sections = corrector_sections(draft)
        page = draft[:CORRECTOR_CONTEXT_CHARS]
        results = []
        for index, section in enumerate(sections, start=1):
            prompt = CORRECTOR_RULES
            if len(sections) > 1:
                prompt += (f"\n\nWhole page, for context only - do not reply with it:\n\n{page}"
                           f"\n\nSection {index} of {len(sections)}, the part to correct now:")
            prompt += f"\n\n{section}"
            text, truncated, _ = self.complete(
                [{"role": "system", "content": CORRECTOR_SYSTEM}, {"role": "user", "content": prompt}],
                min(4096, len(section) // 2 + 600))
            if truncated:
                raise RuntimeError("The correction reached its length limit. The original OCR is unchanged; try a page with less text.")
            corrected = clean_correction(text)
            if not 0.6 <= len(corrected) / max(len(section), 1) <= 1.6:
                raise RuntimeError("The correction differed too much from the transcription to be trusted, so it was discarded. The original OCR is unchanged.")
            results.append(corrected)
        return "\n\n".join(results)


class Edit(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(max_length=100_000)
    reviewed: bool = False
    revision: int = Field(ge=0)


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    note_ids: list[str] = Field(min_length=1, max_length=50)
    document_id: str | None = None


def create_app(data_dir=None, engine=None, token=None, corrector=None):
    store = Store(data_dir or ROOT / "data")
    engine = engine or LocalEngine(int(os.environ.get("LOCALSCRIBE_ENGINE_PORT", "8091")))
    token = token or os.environ.get("LOCALSCRIBE_TOKEN") or secrets.token_urlsafe(24)
    jobs = queue.Queue()
    stopping = threading.Event()

    def worker():
        while not stopping.is_set():
            try:
                kind, note_id = jobs.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if kind == "enhance":
                    with store.connect() as db:
                        claimed = db.execute("""UPDATE notes SET enhance_status='running',enhance_error=''
                            WHERE id=? AND enhance_status='queued'""", (note_id,))
                        if claimed.rowcount != 1:
                            continue
                    note = store.get(note_id)
                    if corrector is None:
                        raise RuntimeError("The local correction model is not running. Start LocalScribe without --no-corrector to use AI correction.")
                    enhanced = corrector.correct(note["raw_text"])
                    with store.connect() as db:
                        db.execute("""UPDATE notes SET enhanced_text=?,enhance_status='ready',
                            enhance_error='' WHERE id=?""", (enhanced, note_id))
                    continue
                with store.connect() as db:
                    claimed = db.execute("UPDATE notes SET status='running', error='' WHERE id=? AND status='queued'",
                                         (note_id,))
                    if claimed.rowcount != 1:
                        continue
                start = time.monotonic()
                note = store.get(note_id)
                outputs, regions, lines, uncertain, truncated = [], [], [], [], False
                with segmented_inputs(store.files / f"{note_id}.preview.jpg", bool(note["segment"])) as inputs:
                    for index, item in enumerate(inputs, start=1):
                        segment_text, segment_truncated, scored = engine.transcribe(item["path"])
                        start_offset = sum(len(output) + 2 for output in outputs)
                        outputs.append(segment_text)
                        regions.append({"index": index, "top": item["top"], "bottom": item["bottom"],
                                        "start": start_offset, "end": start_offset + len(segment_text),
                                        "text": segment_text})
                        truncated = truncated or segment_truncated
                        with Image.open(item["path"]) as crop:
                            bands = line_bands(crop, item["top"], item["bottom"])
                        rows = map_lines_to_bands(segment_text, bands, len(lines))
                        lines.extend(bands)
                        for span in uncertain_spans(segment_text, scored):
                            row = segment_text.count("\n", 0, span["start"])
                            uncertain.append({**span, "start": span["start"] + start_offset,
                                              "end": span["end"] + start_offset, "line": rows.get(row)})
                text = "\n\n".join(outputs)
                with store.connect() as db:
                    db.execute("""UPDATE notes SET status='ready', raw_text=?, text=?,
                        seconds=?, truncated=?, regions=?, uncertain=?, lines=?,
                        reviewed=0, revision=revision+1 WHERE id=?""",
                               (text, text, time.monotonic() - start, int(truncated),
                                json.dumps(regions), json.dumps(uncertain), json.dumps(lines), note_id))
            except Exception as exc:
                with store.connect() as db:
                    if kind == "enhance":
                        db.execute("UPDATE notes SET enhance_status='error',enhance_error=? WHERE id=?",
                                   (str(exc)[:500], note_id))
                    else:
                        db.execute("UPDATE notes SET status='error', error=? WHERE id=?", (str(exc)[:500], note_id))
            finally:
                jobs.task_done()

    @asynccontextmanager
    async def lifespan(app):
        thread = threading.Thread(target=worker, daemon=True, name="transcription")
        thread.start()
        yield
        stopping.set()
        thread.join(timeout=1)

    app = FastAPI(title="LocalScribe", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.token = store, token

    def authorize(request: Request):
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, "Bearer " + token):
            raise HTTPException(401, "Enter the access code shown in the LocalScribe terminal.")

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/health")
    def health():
        if not engine.ready():
            raise HTTPException(503, "Local model is not ready")
        return {"status": "ready"}

    @app.get("/api/status", dependencies=[Depends(authorize)])
    def status():
        return {"engine_ready": engine.ready(), "model": "HunyuanOCR Q8", "local_only": True,
                "corrector_ready": bool(corrector and corrector.ready()),
                "corrector_model": corrector.model if corrector else ""}

    @app.get("/api/notes", dependencies=[Depends(authorize)])
    def list_notes(q: str = ""):
        with store.connect() as db:
            rows = db.execute("""SELECT id,title,created,status,reviewed,seconds,error,truncated,
                document_id,document_title,page_number,
                substr(text,1,160) AS excerpt FROM notes
                WHERE instr(lower(title),lower(?)) OR instr(lower(text),lower(?))
                ORDER BY created DESC LIMIT 200""", (q[:200], q[:200])).fetchall()
        return [dict(row) for row in rows]

    @app.post("/api/notes", status_code=202, dependencies=[Depends(authorize)])
    async def upload(file: UploadFile = File(...), segment: bool = Form(True)):
        content = await file.read(MAX_PDF_UPLOAD + 1)
        await file.close()
        if not engine.ready():
            raise HTTPException(503, "The local model is still starting. Please try again shortly.")
        is_pdf = content.startswith(b"%PDF-")
        if is_pdf:
            if len(content) > MAX_PDF_UPLOAD:
                raise HTTPException(413, "Please use a PDF smaller than 50 MB.")
            notes = store.add_pdf(content, file.filename or "Imported PDF", segment)
            for note in notes:
                jobs.put(("ocr", note["id"]))
            return {"notes": notes}
        if len(content) > MAX_UPLOAD:
            raise HTTPException(413, "Please use an image smaller than 15 MB.")
        note = store.add(content, file.filename or "Untitled note", segment)
        jobs.put(("ocr", note["id"]))
        return note

    @app.post("/api/sample", status_code=202, dependencies=[Depends(authorize)])
    def sample():
        path = ROOT / "samples" / "darwin-letter.jpg"
        if not path.exists():
            raise HTTPException(404, "Run python setup_model.py --sample once to download the example.")
        if not engine.ready():
            raise HTTPException(503, "The local model is still starting. Please try again shortly.")
        note = store.add(path.read_bytes(), "Darwin letter - handwriting example.jpg")
        jobs.put(("ocr", note["id"]))
        return note

    @app.get("/api/notes/{note_id}", dependencies=[Depends(authorize)])
    def get_note(note_id: str):
        return store.get(note_id)

    @app.get("/api/notes/{note_id}/image", dependencies=[Depends(authorize)])
    def get_image(note_id: str, original: bool = False):
        note = store.get(note_id)
        if original:
            return FileResponse(store.files / note["original"], media_type=note["mime"])
        return FileResponse(store.files / f"{note_id}.preview.jpg", media_type="image/jpeg")

    @app.put("/api/notes/{note_id}", dependencies=[Depends(authorize)])
    def edit(note_id: str, value: Edit):
        note = store.get(note_id)
        if note["status"] in ("queued", "running"):
            raise HTTPException(409, "Wait for transcription to finish before editing.")
        with store.connect() as db:
            result = db.execute("""UPDATE notes SET title=?,text=?,reviewed=?,revision=revision+1
                WHERE id=? AND revision=? AND status IN ('ready','error')""",
                                (value.title.strip() or "Untitled note", value.text, int(value.reviewed), note_id, value.revision))
            if result.rowcount != 1:
                raise HTTPException(409, "This note changed in another window. Copy your edits before reloading.")
        return store.get(note_id)

    @app.post("/api/notes/{note_id}/retry", dependencies=[Depends(authorize)])
    def retry(note_id: str):
        note = store.get(note_id)
        if not engine.ready():
            raise HTTPException(503, "The local engine is not ready.")
        with store.connect() as db:
            result = db.execute("""UPDATE notes SET status='queued',error='' WHERE id=?
                AND status IN ('error','cancelled') AND text=''""", (note_id,))
            if result.rowcount != 1:
                raise HTTPException(409, "Retry is available only for failed notes with no saved text. Upload again to keep existing edits.")
        jobs.put(("ocr", note_id))
        return store.get(note_id)

    @app.post("/api/notes/{note_id}/cancel", dependencies=[Depends(authorize)])
    def cancel(note_id: str):
        store.get(note_id)
        with store.connect() as db:
            result = db.execute("""UPDATE notes SET status='cancelled',
                error='Transcription was cancelled before it started.'
                WHERE id=? AND status='queued'""", (note_id,))
        if result.rowcount != 1:
            raise HTTPException(409, "Only queued transcription can be cancelled.")
        return store.get(note_id)

    @app.post("/api/notes/{note_id}/enhance", status_code=202, dependencies=[Depends(authorize)])
    def enhance(note_id: str):
        note = store.get(note_id)
        if note["status"] != "ready" or not note["raw_text"]:
            raise HTTPException(409, "Finish the original transcription before running AI correction.")
        if note["enhance_status"] in ("queued", "running"):
            raise HTTPException(409, "AI correction is already running.")
        if corrector is None:
            raise HTTPException(503, "The local correction model is not running on this installation.")
        if not corrector.ready():
            raise HTTPException(503, "The local correction model is still starting. Please try again shortly.")
        with store.connect() as db:
            db.execute("""UPDATE notes SET enhance_status='queued',enhance_error='',enhanced_text=''
                WHERE id=?""", (note_id,))
        jobs.put(("enhance", note_id))
        return store.get(note_id)

    @app.delete("/api/notes/{note_id}", status_code=204, dependencies=[Depends(authorize)])
    def delete_note(note_id: str):
        note = store.get(note_id)
        if note["status"] == "running" or note["enhance_status"] == "running":
            raise HTTPException(409, "Wait for the active local AI task to finish before deleting it.")
        with store.connect() as db:
            db.execute("DELETE FROM notes WHERE id=?", (note_id,))
        (store.files / note["original"]).unlink(missing_ok=True)
        (store.files / f"{note_id}.preview.jpg").unlink(missing_ok=True)
        return Response(status_code=204)

    @app.delete("/api/documents/{document_id}", status_code=204, dependencies=[Depends(authorize)])
    def delete_document(document_id: str):
        with store.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM notes WHERE document_id=?", (document_id,))]
        if not rows:
            raise HTTPException(404, "Document not found")
        if any(row["status"] == "running" or row["enhance_status"] == "running" for row in rows):
            raise HTTPException(409, "Wait for active local AI tasks in this document to finish before deleting it.")
        with store.connect() as db:
            db.execute("DELETE FROM notes WHERE document_id=?", (document_id,))
        for note in rows:
            (store.files / note["original"]).unlink(missing_ok=True)
            (store.files / f"{note['id']}.preview.jpg").unlink(missing_ok=True)
        return Response(status_code=204)

    @app.post("/api/documents", status_code=201, dependencies=[Depends(authorize)])
    def create_document(value: DocumentCreate):
        note_ids = list(dict.fromkeys(value.note_ids))
        if len(note_ids) != len(value.note_ids):
            raise HTTPException(400, "Each note can appear only once in a document.")
        placeholders = ",".join("?" for _ in note_ids)
        with store.connect() as db:
            rows = db.execute(f"SELECT id,document_id FROM notes WHERE id IN ({placeholders})", note_ids).fetchall()
            if len(rows) != len(note_ids):
                raise HTTPException(404, "One or more selected notes no longer exist.")
            if value.document_id:
                target = db.execute("""SELECT document_title,max(page_number) AS last_page
                    FROM notes WHERE document_id=? GROUP BY document_title""", (value.document_id,)).fetchone()
                if not target:
                    raise HTTPException(404, "The selected destination document no longer exists.")
                document_id, title = value.document_id, target["document_title"]
                already_there = {row["id"] for row in rows if row["document_id"] == document_id}
                note_ids = [note_id for note_id in note_ids if note_id not in already_there]
                if not note_ids:
                    raise HTTPException(409, "All selected notes are already in that document.")
                first_page = target["last_page"] + 1
            else:
                document_id, title, first_page = uuid.uuid4().hex, value.title.strip(), 1
            for page_number, note_id in enumerate(note_ids, start=first_page):
                db.execute("""UPDATE notes SET document_id=?,document_title=?,page_number=?
                    WHERE id=?""", (document_id, title, page_number, note_id))
        return {"id": document_id, "title": title, "pages": len(note_ids)}

    @app.get("/api/notes/{note_id}/export", dependencies=[Depends(authorize)])
    def export(note_id: str):
        note = store.get(note_id)
        return Response(note["text"], media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="note-{note_id}.txt"'})

    @app.get("/api/documents/{document_id}/export", dependencies=[Depends(authorize)])
    def export_document(document_id: str):
        with store.connect() as db:
            rows = db.execute("""SELECT document_title,page_number,text FROM notes
                WHERE document_id=? ORDER BY page_number""", (document_id,)).fetchall()
        if not rows:
            raise HTTPException(404, "Document not found")
        title = rows[0]["document_title"] or "document"
        body = "\n\n".join(f"--- Page {row['page_number']} ---\n{row['text']}" for row in rows)
        safe = "".join(character if character.isascii() and (character.isalnum() or character in "-_ ") else "_"
                       for character in title)[:100]
        return Response(body, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{safe or "document"}.txt"'})

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app
