# LocalScribe — handoff for the next AI

## Goal

LocalScribe converts photos of handwritten notes into editable text on the user's
own PC. The user wants a simple Docker Desktop deployment, with phone-to-PC upload
over trusted local Wi-Fi. It must work fully locally after the initial downloads.

The intended workflow is:

```text
Android scan/photo → local web app → local OCR → review/edit beside original → search/copy/export
```

The user has a GTX 1060 and up to 32 GB RAM, but asked for something runnable on
as much hardware as possible. CPU inference is therefore the baseline. Do not make
an NVIDIA GPU a requirement.

## Where things stand

Project root: `C:\PROJECTS\TOOLS\LocalScribe`

The CPU container has been rebuilt and is running with **both** models: HunyuanOCR
for recognition and Qwen3-4B-Instruct-2507 for text correction. Everything in this
document has been exercised against the running container, including a scored run
on a real user page (see `BENCHMARK.md`). Do not record or reuse an ephemeral
access token here; obtain the current one from
`docker compose logs --tail 30 localscribe`. The CUDA image is configured but has
not been built or tested.

Not verified in a real browser: the uncertainty overlay and the edit/AI-corrected/
compare tabs were checked by static ID/class cross-reference and one user
screenshot of the compare view, not by browser automation. The browser tool was
unavailable in that session.

The previous healthy deployment used:

```text
localscribe-localscribe-1   localscribe:local   Up (healthy)
127.0.0.1:8090->8090/tcp
```

The last launch URL contained an ephemeral access token. Do **not** reuse or expose
that old token; read the current token from `docker compose logs --tail 30
localscribe` if the user asks to open the app.

The CUDA image is intentionally not built yet; it should be built and tested on the
target 1050 Ti/1060 device.

## Important files

| File | Purpose |
| --- | --- |
| `README.md` | User-facing Docker/Desktop, phone, offline, backup, and direct-run instructions. |
| `compose.yaml` | Portable CPU-default service with bind-mounted `data/`, `models/`, and `samples/`; host port defaults to localhost only. |
| `compose.gpu.yaml` | Opt-in NVIDIA CUDA overlay; uses pinned llama.cpp b11011 CUDA image, `gpus: all`, 99 GPU layers, and vision-projector offload. |
| `Dockerfile` | Parameterized container based on a digest-pinned official CPU or CUDA llama.cpp server image. `LD_LIBRARY_PATH=/app` is required. |
| `docker_entry.py` | Downloads missing model files once, verifies SHA-256, then starts local app and local model. |
| `run.py` | Starts both `llama-server` processes (OCR, and the text corrector unless `--no-corrector`), waits for health, generates/uses access code, starts FastAPI. |
| `app.py` | FastAPI app, SQLite storage, one-item worker queue, both local model clients, uncertainty/ink-line analysis, authenticated routes. |
| `static/index.html`, `static/style.css`, `static/review.css`, `static/app.js` | Browser interface, including the uncertainty overlay and the edit/AI-corrected/compare tabs. |
| `setup_model.py` | Native Windows setup and verified model/sample downloads. |
| `test_app.py` | Twenty-six focused backend tests covering the OCR prompt and logprob request, per-word uncertainty and ink-line mapping, the text corrector's prompt/guardrails/absence, PDF/segmentation, grouping/export/append/whole-group deletion, cancellation, note deletion, correction isolation, and existing safety behavior. |
| `benchmark_htr.py`, `benchmark_mtmd_cli.py` | Reproducible CPU handwriting model comparisons. |
| `render_private_pdf.py`, `qualitative_ocr.py` | Local-only private PDF rendering and qualitative OCR helpers. |
| `smoke_check.py` | End-to-end check against a running real model; creates a temporary sample note. |
| `BENCHMARK.md` | Measured native and Docker evidence and its limits. |
| `samples/SOURCES.md` | Public example provenance/limitations. |

## Architecture and intentional choices

- Model: HunyuanOCR Q8 plus Q8 vision projector from `ggml-org/HunyuanOCR-GGUF`, pinned by revision and SHA-256 in `setup_model.py`. It replaced GLM after the CPU benchmark documented in `BENCHMARK.md`.
- Second model: `Qwen3-4B-Instruct-2507 Q4_K_M` (Apache-2.0) from `unsloth/Qwen3-4B-Instruct-2507-GGUF`,
  pinned by revision and SHA-256 the same way, served by a second `llama-server` on its own random
  loopback port with its own API key. It is text-only and backs **Fix with local
  AI**. It receives the finished OCR text, never the page image. `run.py
  --no-corrector` or `LOCALSCRIBE_CORRECTOR=0` skips it; the app then reports
  `corrector_ready: false` and the endpoint returns 503 instead of failing later.
- Per-word confidence: `transcribe()` asks llama.cpp for `logprobs` and returns
  `(text, truncated, [(token, probability), ...])`. `uncertain_spans()` expands
  every token below `UNCERTAIN_PROBABILITY` (0.65) to its whole word and merges
  overlaps. This is the recognition model's own score, not a heuristic.
- Scan localisation: `ink_lines()` takes a one-pixel-wide BOX resize of the
  segment as a row-ink profile (fast, no numpy) and returns bands of ink, with a
  2% inset so a photographed page's dark frame is not read as a line.
  `map_lines_to_bands()` places each text line proportionally down the written
  area and snaps it to the nearest band. There are no measured word boxes; the
  UI says so.
- Runtime: llama.cpp. Docker defaults to a pinned CPU image; `compose.gpu.yaml`
  selects the matching pinned CUDA image. Native Windows setup downloads the
  b11020 CPU executable archive. Native GPU use requires supplying a CUDA-enabled
  `llama-server.exe` to `run.py`.
- Storage: `data/notes.sqlite3` plus originals/previews under `data/pages/`. These folders are bind-mounted and persist beyond container recreation.
- Review safety: `raw_text` is never overwritten. `enhanced_text` is a separate,
  optional text-model correction of it. `text` is editable/final and receives
  corrected text only after explicit user confirmation. Each note begins unreviewed.
- Privacy: OCR engine binds to `127.0.0.1` inside the container and is never published. Browser API uses a bearer access code. The default Compose binding is `127.0.0.1:8090`.
- Processing: exactly one worker/job at a time. This deliberately keeps CPU/RAM predictable. CPU is the default; `compose.gpu.yaml` provides an opt-in pinned CUDA runtime with configurable layer and vision-projector offload.
- Supported inputs: JPEG, PNG, WebP (15 MB / 25 MP), and PDFs (50 MB / 50 pages). Every PDF page is rendered locally at 160 DPI and becomes a separate note. Dense-page segmentation is enabled by default and persisted per note.
- Segmentation: portrait previews taller than 1200 px and 1.15 times their width
  are divided into 2-4 horizontal bands near low-ink rows. OCR runs top-to-bottom
  and outputs are joined with blank lines. Users can disable this per upload.
- Guided review: the note stores `uncertain` (low-scoring words with page-text
  offsets, probability, and a line index) and `lines` (normalized bands of ink).
  The editor shades those words behind the textarea and draws their bands on the
  scan; **Next uncertain word** steps through them. Every OCR segment also stores
  normalized page bounds and raw-text offsets for section navigation. The old
  numbers/capitalized-term cues remain, now labelled as cues rather than scores.
- Documents: PDF pages share document metadata automatically. Existing notes can
  be selected and reordered before save with **Create document**, then placed in
  a new document or appended to an existing one. Document cards provide combined
  text export and whole-group deletion. Groups remain metadata on note rows rather
  than records in a separate documents table.
- Queue/data controls: queued OCR can be cancelled atomically and is then skipped
  by the worker. Sidebar actions cancel queued notes and delete non-running notes.
  Whole-document deletion removes every member note and original/preview file.

## Verified evidence

Read `BENCHMARK.md` before making claims about quality or performance.

- Native Windows CPU: difficult public Darwin letter (624 × 1008) processed in 65.593 seconds; model made obvious cursive errors.
- Docker offline test: the same page processed in 52.924 seconds inside a separate `--network none` container, with read-only models/samples and no user-data mount.
- Docker smoke test passed authenticated access, actual OCR, save/edit, raw-text preservation, original download, search, and `.txt` export.
- App test suite passed 26 tests. Run:

  ```powershell
  .\.venv\Scripts\python.exe -m unittest -v
  node --check static/app.js
  ```

- Browser automation was attempted after deployment, but the browser runtime
  reported no available browser/Chrome connection. Live HTTP checks confirmed the
  healthy model plus served review, enhancement, grouping, delete, cancel, JS,
  and CSS assets. The user visually reported the original raw segmentation
  checkbox looked poor; it was replaced with a compact two-line settings card.
- The user's real `FELA JOHANNES LIFER.PDF` imported through `Store.add_pdf` as
  exactly six page notes, titled page 001 through page 006. Page 1 and page 6
  previews were visually inspected and were sharp, complete, correctly oriented,
  and unclipped. Private assets and QA output are ignored under `benchmarks/private/`.
- Model comparison on ten labelled IAM lines selected HunyuanOCR Q8: mean CER
  0.4%, WER 3.3%, and 12.3 seconds/line on CPU. GLM was 2.3% / 18.2% / 14.4s.
  DeepSeek was stopped after three much slower, noncompetitive samples. See
  `BENCHMARK.md` for the exact limitations.

## Known limitations and bugs to preserve awareness of

1. **Handwriting accuracy is not sufficient to call this production-ready.** The Darwin page had errors such as `Sanderson` becoming `Anderson` and `going to beg` becoming `giving the`. Never silently “fix” uncertain OCR with a language model. Keep original and raw text visible.
2. **Word-level scan positions are estimated, not measured.** Per-word confidence
   is now genuine (llama.cpp `logprobs`), and low-scoring words are shaded in the
   editor and boxed on the scan. But the box comes from detected lines of ink plus
   a proportional text-line-to-band mapping, so a scan holding marks the model
   never transcribed can shift a line. Measured on the Darwin page: 14 of 15 lines
   landed on the right handwriting, the first line was one band high because of
   the scan's dark top border. Horizontal position is not estimated at all - the
   box spans the full width. Real per-word boxes need a layout detector or a
   different OCR engine.
3. **Document groups still use note metadata.** PDF pages group automatically;
   arbitrary notes can be grouped, reordered before creation, appended, exported,
   or deleted as a group. There is no rename, post-save reorder, or ungroup UI.
4. **No automatic crop/deskew page scanner.** Phone users should use a scanning app that exports JPG/PNG for now.
5. **Phone workflow is HTTP on LAN.** It uses an access code but no TLS. It should remain limited to trusted private Wi-Fi and should not be exposed by router port forwarding.
6. **Access token changes on every container start** unless `LOCALSCRIBE_TOKEN` is set in `.env`. The README explains this.
7. **`copy` on HTTP mobile browsers can be constrained.** The UI falls back to selecting the text for manual copy.
8. **Do not run native `run.py` and Docker Compose at the same time** against the same `data/` directory. SQLite supports normal locking but the app has no multi-process coordination.
9. **Docker library-path fix is necessary.** The base image's `/app/llama-server` initially failed because `libllama-server-impl.so` was not found. `LD_LIBRARY_PATH=/app` in the Dockerfile fixes this.
10. **GPU mode exists but is not performance-validated yet.** The optional CUDA
    profile is intended for the 1050 Ti/1060 and llama.cpp supports hybrid CUDA
    offload, but it must be tested on the actual target device. If full offload
    exhausts VRAM, set `LOCALSCRIBE_GPU_LAYERS=20` in `.env` and increase it.
11. **PDF pages are rendered images.** Each PDF page's downloadable “original” is
    the rendered JPEG page, not the source PDF file. The source PDF is not retained
    in the notebook database.
12. **AI correction is a separate text model, and it does invent things.**
    HunyuanOCR only behaves reliably with its exact `OCR` instruction: an earlier
    attempt to make it correct its own draft repeated output until the token limit,
    and a plain second OCR pass ignored the draft entirely. Correction now runs on
    Qwen3-4B-Instruct-2507, which reads the whole transcription as text plus the
    list of low-confidence words. Measured on a real user page it took word error
    rate from 8.1% to 6.4%, but in the same run it turned `roes` into `flesh` and
    appended `the bank`, which is not on the page. `temperature` is 0,
    `TextCorrector.correct` discards any result whose length falls outside 0.6-1.6x
    the input, and `refit_lines` forces the reply back onto the page's own lines.
    None of that can stop a plausible wrong word, so the UI never applies it
    silently. Do not change that.
13. **Important fixed regression:** Hunyuan must receive the exact `OCR` prompt
    for transcription. The old GLM-style `Text Recognition:` prompt caused image
    descriptions instead of verbatim OCR. A regression test now asserts `OCR`.

## Recommended next steps, in order

### 1. Validate GPU mode on the target NVIDIA device

Use `docker compose -f compose.yaml -f compose.gpu.yaml up --build -d`, inspect
the logs for CUDA detection/offload, then compare the same IAM lines and one
segmented private page against the CPU numbers. Test 1050 Ti/1060 VRAM behavior;
reduce `LOCALSCRIBE_GPU_LAYERS` if necessary. Return the service to stopped state
when the user asks. Do not claim a speedup until measured.

### 2. Expand representative handwriting benchmarks

This is the most important next action. Ask the user for 5–10 representative
pages (or locally available file paths), covering neat/rough writing, headings,
lists, numbers, names, and the language(s) they use. If privacy matters, let them
write disposable sample notes rather than share private material.

For each page:

1. Create an independently verified transcript.
2. Run the app/model.
3. Record page size, timing, omissions, and transcription errors.
4. Compare word error rate or character error rate only when the reference is reliable.
5. Update `BENCHMARK.md` with clear, limited claims.

Do not treat public historical cursive as representative of the user's writing.

### 3. Perform real browser and Android QA

Use the available browser skill if it exists in that session. Read its `SKILL.md`
before browser control. Check desktop and narrow/mobile layouts. Verify:

- Fresh token link opens the notebook.
- Access code prompt works without the token fragment.
- Image and PDF upload work; PDF pages fan out in order; the segmentation checkbox
  is usable; queued/running/ready/error states update.
- Original image, editor, review checkbox, save, copy fallback, export, and search all work.
- Unsaved-change handling works when switching notes/reloading.
- Phone can connect when `.env` uses `LOCALSCRIBE_BIND=0.0.0.0` on trusted Wi-Fi.
- Check the browser console/network for errors.

Do not leave LAN binding enabled by default. The current safe default is localhost.

### 4. Continue review-ergonomics validation

Section navigation, genuine per-word confidence, and estimated per-line scan
positions are all implemented. What remains:

1. Validate the uncertainty overlay in Chrome and at mobile width. The shading is
   a backdrop `div` sitting behind a transparent `textarea`; if its font, padding
   or wrapping ever drifts from the textarea's, the highlights slide off the
   words. This is the one piece most likely to break on a font or CSS change.
2. Decide the confidence threshold with real pages. `UNCERTAIN_PROBABILITY` is
   0.65, tiered in the UI at 0.4. On the hard Darwin letter that marked 32 of
   about 90 words, catching every genuine misreading seen so far plus some
   correct ones. Too aggressive for a clean page is worse than useless.
3. Only then decide whether measured word boxes justify a layout detector or a
   different OCR engine. The current estimate is free and was right on 14 of 15
   lines; a detector is a large change to beat it.

Keep the existing side-by-side editor as a fallback. Do not delete raw output or
the original page.

### 5. Consider further model comparison only after more real samples exist

Candidates discussed earlier:

- HunyuanOCR: selected current default and portable llama.cpp baseline.
- GLM-OCR: retained comparison model; it lost the ten-line CPU benchmark.
- PaddleOCR-VL: potentially stronger on complex layouts/handwriting, but modern
  GPU requirements exclude the user's GTX 1060 for the documented GPU path; CPU
  may be slow and needs a careful deployment evaluation.
- TrOCR: built for handwritten text but accepts individual line images, requiring
  reliable page/line segmentation before it is useful for full note pages.

Any model swap must preserve all local-only behavior, output review, original
storage, reproducible pinning/checksums, and benchmark evidence.

### 6. Extend document grouping only if needed

Grouping, ordered creation, append, combined export, and whole-group deletion are
complete using metadata on note rows. If rename/post-save reorder/ungroup or document-level review is needed, migrate to explicit
`documents` plus ordered `pages` tables without discarding page-level records.

### 7. Harden deployment after usability is proven

Possible follow-up work:

- local HTTPS or a trusted reverse proxy for LAN mobile use;
- explicit backup/restore command that pauses the service and makes a verified archive;
- database migrations (current schema uses `CREATE TABLE IF NOT EXISTS` only);
- user-configurable resource limits and worker settings;
- offline image/model import verification UX;
- a health/status panel that explains model startup and memory needs.

Do not add Redis, Celery, Postgres, object storage, cloud services, or a separate
Android app until the single-user workflow needs them. The current single-container
design is intentionally simple.

## Commands likely useful to the next AI

```powershell
cd C:\PROJECTS\TOOLS\LocalScribe

# Inspect app state / latest access URL
docker compose ps
docker compose logs --tail 30 localscribe

# Rebuild after source changes
docker compose up -d --build

# Opt-in NVIDIA GPU mode (build/test only on the GPU machine)
docker compose -f compose.yaml -f compose.gpu.yaml up --build -d

# App tests (native virtual environment already exists on this machine)
.\.venv\Scripts\python.exe -m unittest -v
node --check static/app.js

# Start a native copy only after stopping Docker; avoid concurrent database access
docker compose stop
.\.venv\Scripts\python.exe run.py
```

For an offline Docker test, use `smoke_check.py` only in an isolated environment
with a separate writable data folder. It creates an example note by design. Do not
run it against a user's real data directory without clearly warning them.

## User communication guidance

- Lead with the fact that the CPU Docker image is ready and running. GPU mode is
  configured but still needs target-device validation.
- Be candid: accurate handwritten transcription still requires review.
- Avoid calling it “offline” until the initial container/model downloads are complete;
  afterward its processing path is local.
- Mention the next decision: validate the model on their own handwriting before
  adding advanced features.
