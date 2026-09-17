# LocalScribe

Turn photos of handwritten notes into editable text, entirely on your computer.
Upload a page, compare the transcription with the scan, make corrections, then
copy or export it. Originals and the unedited machine transcription are preserved.

This is an early prototype. Difficult cursive can produce wrong words or missing
lines. The words the model itself scored as unlikely are marked for you, but a
confident-looking word can still be wrong and a dropped line is never flagged.
Every transcription starts as **Needs review**.

## Recommended: Docker Desktop

One container runs the browser interface, local OCR model, processing queue, and
SQLite database. No GPU, CUDA installation, Python installation, cloud account,
or API key is needed on the host.

### 1. Install and start Docker Desktop

Install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/).
On Windows, use **Linux containers** with the **WSL 2 backend**. Follow Docker's
installer prompts for virtualization/WSL, and restart Windows if requested.
Start Docker Desktop and wait until its engine is running.

Check in PowerShell:

```powershell
docker version
docker compose version
```

`docker version` should show both Client and Server sections.

For a PC with 16 GB RAM, leave room for Windows and other applications. This
Compose service has a 9 GB memory limit and uses up to four CPU cores. Docker/WSL
must have enough memory available for it. Allow several GB of free disk space
for Docker layers, approximately **3.8 GB of model downloads** (handwriting plus
text-correction model), and your scanned pages.

### 2. Build and start

Open PowerShell in this folder:

```powershell
cd C:\PROJECTS\TOOLS\LocalScribe
docker compose up -d --build
docker compose logs -f localscribe
```

The first build downloads the container dependencies. On the first startup,
missing model files download into `models/` and are SHA-256 verified. If the
model files are already there from the Windows setup, they are reused. Initial
setup needs internet; normal transcription does not.

When the log prints `LocalScribe: http://127.0.0.1:8090/#token=...`, open that
**complete link**. It includes the temporary access code. Alternatively open
[http://localhost:8090](http://localhost:8090) and enter the code from the log.
Press Ctrl+C to leave log viewing; the container keeps running.

After the first build, you can also start/stop the `localscribe` service from
the **Containers** screen in Docker Desktop. A new code is generated on each
start unless you configure `LOCALSCRIBE_TOKEN` in `.env`.

### 3. Use it

1. Click **Add pages or PDF**, then choose JPEG, PNG, WebP, or a PDF.
   Each PDF page becomes a separate queued note. PDFs may contain up to 50 pages
   and be up to 50 MB.
2. Leave **Segment dense pages** checked for full notebook pages. LocalScribe
   splits tall pages near low-ink gaps, reads each section, and joins the text in
   top-to-bottom order. Turn it off for diagrams or layouts where bands are not
   the intended reading order.
3. Wait for transcription. Pages are processed one at a time.
4. Check the scan beside the editable text and make corrections.
5. Click **Save changes**, then **Copy text** or **Export .txt**.
6. Words the recognition model scored as unlikely are shaded in the editable text
   and boxed on the scan beside them. **Next uncertain word** steps through them.
   These are the model's own per-word scores rather than a guarantee, and the
   place marked on the scan is estimated from the page layout. **Review by page
   section** and the older “worth checking” cues sit under the same panel.
7. Optionally click **Fix with local AI** above the text. A second local model
   reads the whole transcription and repairs recognition mistakes in it. The
   **AI corrected** and **Compare** tabs show what changed word by word; you then
   choose whether to put it in the editor. The original OCR is never overwritten.
8. Use **Create document** to select existing notes in page order. The dialog can
   create a new document or append pages to an existing one, and its arrow buttons
   adjust page order before saving. Imported PDF pages are grouped automatically.
9. Queued jobs can be cancelled from the sidebar. Individual notes, or a complete
   document and all its pages, can be deleted through confirmation dialogs; an
   actively running local-AI task must finish first.
10. Use search to find saved notes. Mark a note reviewed once you have checked it.

Each image is one note in this prototype. Maximum upload: 15 MB and 25 megapixels.
The original file is preserved; an oriented copy with a maximum dimension of
2000 pixels is used for recognition. Dense pages may work better as several
closer photos. Output that hits the generation limit is flagged as incomplete.

**Crop the photo to the page before uploading.** LocalScribe has no page detection
yet, so a desk, tablecloth or floor left in the frame is treated as part of the
page. Measured on a real photo where the desk filled the bottom 22%: the ink
threshold that locates lines was thrown off enough to merge whole paragraphs into
one highlight band, and page segmentation spent a whole section on the desk, which
cost an entire paragraph of transcription. Your phone's built-in crop is enough.

To download the optional public test examples once:

```powershell
docker compose exec localscribe python setup_model.py --samples-only
```

Then use **Try the handwriting example**. This loads a deliberately difficult
Darwin letter. The second downloaded file, `samples/iam-line.jpg`, is only a tiny
handwriting fragment. Sources and usage notes are in [samples/SOURCES.md](samples/SOURCES.md).

### 4. Open it from your Android phone

Connect your phone and PC to the same trusted Wi-Fi network. Copy the settings file:

```powershell
Copy-Item .env.example .env
```

Change this line in `.env`:

```dotenv
LOCALSCRIBE_BIND=0.0.0.0
```

Recreate the container with the new port binding:

```powershell
docker compose up -d
ipconfig
docker compose logs --tail 30 localscribe
```

Find your PC's Wi-Fi/Ethernet IPv4 address, for example `192.168.1.50`. On your
phone open `http://192.168.1.50:8090` and enter the access code from the log. Choose
a photo or an image exported by your scanning app. Camera choices depend on the
phone's file picker. Photos and PDFs are supported; every PDF page is imported as
its own item in the notes list.

If Windows Firewall asks, allow access only on your private network. This is
plain HTTP with an access code; use it on trusted Wi-Fi, not through router port
forwarding or a public internet address. The model's own port is never published.

### Stop, restart, update

```powershell
docker compose stop
docker compose start
docker compose logs --tail 30 localscribe
```

After changing application files, rebuild:

```powershell
docker compose up -d --build
```

`docker compose down` removes the container and network but keeps the bind-mounted
`data/`, `models/`, and `samples/` folders. Those folders are where your files live.

### Offline use

After setup, set `LOCALSCRIBE_OFFLINE=1` in `.env`, then run `docker compose up -d`.
This prevents first-run model downloads if a file is missing. With the Docker
image and models already present, you can start and use the app without internet.
All transcription requests go to a loopback address inside the container. There
are no CDN scripts, remote fonts, analytics, or cloud OCR calls.

To move to another offline PC, copy `models/` and the project folder, and export
the built image on the original PC:

```powershell
docker save -o localscribe-image.tar localscribe:local
```

On the destination, with Docker installed:

```powershell
docker load -i localscribe-image.tar
docker compose up -d --no-build
```

The image must match the destination CPU architecture. A Windows/Linux Intel/AMD
image is not a native Apple Silicon image. The pinned upstream CPU image supports
amd64 and arm64, but each target should build/test its own image.

### Backups

Stop the service before copying `data/`, so the SQLite database and page files are
consistent. Back up the **whole `data/` folder**, including `notes.sqlite3` and
`pages/`. To restore, stop the app, restore that folder, then start it. Keep a copy
of `models/` for offline reinstalls. `.env` is optional configuration; keep any
persistent access code private.

### Troubleshooting

| Symptom | What to check |
| --- | --- |
| Cannot connect to Docker daemon / named pipe | Open Docker Desktop, wait for its engine, and check that Linux containers are selected. |
| Port 8090 already in use | Stop the direct Windows version, or set `LOCALSCRIBE_PORT=8092` in `.env`; then open port 8092. The startup log shows the internal default port, so adjust its URL. |
| First startup seems slow | Follow `docker compose logs -f`; the model downloads total about 3.8 GB. |
| Every startup takes minutes | Models load from the bind-mounted `models/` folder, which is slow through WSL. Transcription is usable as soon as the handwriting model is ready; the 2.5 GB correction model keeps loading in the background and **Fix with local AI** switches on by itself. Set `LOCALSCRIBE_CORRECTOR=0` in `.env` to skip it entirely. |
| Access code rejected after restart | Read the latest code from the log, or set a persistent `LOCALSCRIBE_TOKEN` in `.env`. |
| Model exited / out of memory | Inspect `data/engine.log` and `docker compose logs`; give Docker more memory or use smaller page images. |
| Poor handwriting recognition | Try better lighting, a straight page, or smaller sections; review all output. More RAM alone does not improve recognition quality. |
| Phone cannot connect | Check the bind address, PC IPv4 address, private-network firewall rules, and Wi-Fi client isolation. |
| Copy does not work on phone | On HTTP some browsers block clipboard access. The app selects the text so you can use the phone's Copy command, or export `.txt`. |

## Alternative: direct Windows setup

Install **Python 3.12, 64-bit**, then double-click `setup.cmd`. This creates an
isolated `.venv`, installs dependencies, downloads the verified CPU runtime and
models, and downloads the examples. Then double-click `start.cmd`.

Or use PowerShell:

```powershell
cd C:\PROJECTS\TOOLS\LocalScribe
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
.\.venv\Scripts\python.exe setup_model.py --sample
.\.venv\Scripts\python.exe run.py
```

For phone access, use `run.py --lan`. Ctrl+C stops both the app and its model.
For fewer CPU threads use `run.py --threads 2`.

On macOS/Linux, use a Python 3.11+ virtual environment, install the same Python
dependencies, and install [llama.cpp](https://github.com/ggml-org/llama.cpp) for
your platform. Run `python setup_model.py --sample`, then `python run.py` with
`llama-server` on PATH (or pass `--engine-path /path/to/llama-server`). These direct
platform paths are not yet verified. Docker is the simpler packaged route.

## Hardware and scope

CPU execution remains the portable baseline. To enable NVIDIA acceleration in
Docker Desktop, use the GPU overlay (Docker must already expose your NVIDIA GPU):

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml up --build -d
```

This selects the official CUDA llama.cpp image, requests the GPU, offloads up to
99 model layers, and offloads the vision projector. A 1050 Ti or 1060 should be
tested with the current driver before relying on it; if VRAM is tight, set
`LOCALSCRIBE_GPU_LAYERS=20` in `.env` and increase gradually. Return to portable
CPU mode with the ordinary `docker compose up --build -d` command. Native Windows
GPU mode requires a CUDA-enabled `llama-server.exe`, then `run.py --engine-path
PATH --gpu-layers 99 --mmproj-offload`.

Two local models run side by side, both served by llama.cpp. HunyuanOCR Q8 with
its separate vision projector reads the handwriting; it was selected after a
repeatable CPU comparison against GLM-OCR and DeepSeek-OCR, see `BENCHMARK.md`.
Qwen3-4B-Instruct-2507 Q4_K_M backs **Fix with local AI**: it never sees the page
image, only the finished transcription, which it repairs as text. Set
`LOCALSCRIBE_CORRECTOR=0` in `.env` to skip loading it and save about 3 GB of
RAM, or point `LOCALSCRIBE_CORRECTOR_MODEL` at a smaller GGUF in `models/`.

On one real page, AI correction took the word error rate from 8.1% to 6.4%, while
in the same pass inventing two words that were not on the page. It is a review aid,
not a proofreader. Review remains essential, especially for dense full-page cursive.

Implemented: image/PDF upload, queue and queued-job cancellation, dense-page
segmentation, per-word model confidence highlighted in both the text and the
scan, section-to-source review navigation, reversible AI text correction with a
word-level comparison, side-by-side editing, immutable raw transcription, review
flag, search, note deletion, document grouping/append/combined export/whole-group
deletion, original download, access code, and restart recovery.

Not yet implemented: renaming/reordering/ungrouping an already-saved document,
automatic camera edge detection/deskew, measured word-level bounding boxes (the
scan highlight is estimated from detected lines of ink), GPU performance
validation on the target device, HTTPS, multiple user accounts, and a packaged
phone app. CPU-only phones do not run the model; they use the PC-hosted web
interface.

See [BENCHMARK.md](BENCHMARK.md) for measured results and validation limits.

The Docker image has been built and exercised on this Windows machine. A real
handwriting sample was transcribed in a container with networking disabled in
about 53 seconds. Difficult words were still misread: review remains essential.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c constraints.txt
.\.venv\Scripts\python.exe -m unittest -v
node --check static/app.js
```

The unit tests use a fake recognizer to check data handling and recovery. Real
model transcription is tested separately and documented in the benchmark notes.

## Upstream projects

- [HunyuanOCR GGUF model files](https://huggingface.co/ggml-org/HunyuanOCR-GGUF)
- [GLM-OCR](https://github.com/zai-org/GLM-OCR) and [GGUF model files](https://huggingface.co/ggml-org/GLM-OCR-GGUF)
- [llama.cpp](https://github.com/ggml-org/llama.cpp) and its [Docker documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md)
- [PaddleOCR-VL hardware requirements](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md)
- [NVIDIA legacy GPU capabilities](https://developer.nvidia.com/cuda/gpus/legacy)

Upstream software/model licenses continue to apply. Public examples are downloaded
separately for evaluation; check their source terms before redistributing them.
