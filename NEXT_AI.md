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

The CPU container was running and healthy on `127.0.0.1:8090` before the latest
source-only changes. **The latest sidebar/document-dialog/group-card/full-page
re-read changes have not been rebuilt into Docker.** The user explicitly stopped
the implementation pass and asked for documentation only, so the next AI must
build/recreate the service before expecting those controls in the browser. Do not
record or reuse an ephemeral access token here; obtain the current one from
`docker compose logs --tail 30 localscribe` after deployment. The CUDA image is
configured but has not been built or tested.

The previous healthy deployment used:

```text
localscribe-localscribe-1   localscribe:local   Up (healthy)
127.0.0.1:8090->8090/tcp
```

The last launch URL contained an ephemeral access token. Do **not** reuse or expose
that old token; read the current token from `docker compose logs --tail 30
localscribe` if the user asks to open the app.

The previously deployed CPU image is usable but does not contain the final pending
source changes. The CUDA image is intentionally not built yet; it should be built
and tested on the target 1050 Ti/1060 device.

## Important files

| File | Purpose |
| --- | --- |
| `README.md` | User-facing Docker/Desktop, phone, offline, backup, and direct-run instructions. |
| `compose.yaml` | Portable CPU-default service with bind-mounted `data/`, `models/`, and `samples/`; host port defaults to localhost only. |
| `compose.gpu.yaml` | Opt-in NVIDIA CUDA overlay; uses pinned llama.cpp b11011 CUDA image, `gpus: all`, 99 GPU layers, and vision-projector offload. |
| `Dockerfile` | Parameterized container based on a digest-pinned official CPU or CUDA llama.cpp server image. `LD_LIBRARY_PATH=/app` is required. |
| `docker_entry.py` | Downloads missing model files once, verifies SHA-256, then starts local app and local model. |
| `run.py` | Starts `llama-server`, waits for health, generates/uses access code, starts FastAPI. |
| `app.py` | FastAPI app, SQLite storage, one-item worker queue, local engine client, authenticated routes. |
| `static/index.html`, `static/style.css`, `static/app.js` | Browser interface. |
| `setup_model.py` | Native Windows setup and verified model/sample downloads. |
| `test_app.py` | Seventeen focused backend tests covering OCR/re-read prompts, PDF/segmentation, grouping/export/append/whole-group deletion, cancellation, note deletion, enhancement isolation, and existing safety behavior. |
| `benchmark_htr.py`, `benchmark_mtmd_cli.py` | Reproducible CPU handwriting model comparisons. |
| `render_private_pdf.py`, `qualitative_ocr.py` | Local-only private PDF rendering and qualitative OCR helpers. |
| `smoke_check.py` | End-to-end check against a running real model; creates a temporary sample note. |
| `BENCHMARK.md` | Measured native and Docker evidence and its limits. |
| `samples/SOURCES.md` | Public example provenance/limitations. |

## Architecture and intentional choices

- Model: HunyuanOCR Q8 plus Q8 vision projector from `ggml-org/HunyuanOCR-GGUF`, pinned by revision and SHA-256 in `setup_model.py`. It replaced GLM after the CPU benchmark documented in `BENCHMARK.md`.
- Runtime: llama.cpp. Docker defaults to a pinned CPU image; `compose.gpu.yaml`
  selects the matching pinned CUDA image. Native Windows setup downloads the
  b11020 CPU executable archive. Native GPU use requires supplying a CUDA-enabled
  `llama-server.exe` to `run.py`.
- Storage: `data/notes.sqlite3` plus originals/previews under `data/pages/`. These folders are bind-mounted and persist beyond container recreation.
- Review safety: `raw_text` is never overwritten. `enhanced_text` is a separate,
  optional full-page AI re-read result. `text` is editable/final and receives
  enhanced text only after explicit user confirmation. Each note begins unreviewed.
- Privacy: OCR engine binds to `127.0.0.1` inside the container and is never published. Browser API uses a bearer access code. The default Compose binding is `127.0.0.1:8090`.
- Processing: exactly one worker/job at a time. This deliberately keeps CPU/RAM predictable. CPU is the default; `compose.gpu.yaml` provides an opt-in pinned CUDA runtime with configurable layer and vision-projector offload.
- Supported inputs: JPEG, PNG, WebP (15 MB / 25 MP), and PDFs (50 MB / 50 pages). Every PDF page is rendered locally at 160 DPI and becomes a separate note. Dense-page segmentation is enabled by default and persisted per note.
- Segmentation: portrait previews taller than 1200 px and 1.15 times their width
  are divided into 2-4 horizontal bands near low-ink rows. OCR runs top-to-bottom
  and outputs are joined with blank lines. Users can disable this per upload.
- Guided review: every OCR segment stores normalized page bounds and raw-text
  offsets. Section buttons highlight the source band and select the corresponding
  text. Numbers/capitalized terms are heuristic review cues, not confidence.
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
- App test suite passed 17 tests. Run:

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
2. **No per-word confidence or image-to-text correspondence.** The UI cannot highlight questionable passages. This is the highest-value product improvement after collecting real user samples.
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
12. **The AI re-read is experimental.** HunyuanOCR only behaves reliably with its
    exact `OCR` instruction. The earlier long correction prompt repeated output
    until the token limit even on the 426-character Darwin test page. The source
    now performs an independent full-page OCR pass with a 4096-token allowance,
    stores it separately, and requires an explicit copy into the editor. It does
    not use the draft as context and can still misread facts or be slow on CPU.
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

The first review-map implementation is complete using segmentation bounds and
text offsets. Validate it in Chrome/mobile and decide whether genuine word-level
coordinates/confidence justify a layout detector or different OCR engine.

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
