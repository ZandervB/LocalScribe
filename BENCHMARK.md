# Prototype validation — 17 September 2026

## Handwriting model comparison (CPU, 8 threads)

Ten deterministic labelled lines from the MIT-licensed `Teklia/IAM-line` test
split were processed with GPU and vision-projector offload disabled. Character
error rate (CER) and word error rate (WER) are lower-is-better means across the
same ten lines.

| Model | Lines | Mean CER | Mean WER | Mean time/line | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| HunyuanOCR Q8 | 10 | **0.4%** | **3.3%** | **12.3 s** | Selected default |
| GLM-OCR Q8 | 10 | 2.3% | 18.2% | 14.4 s | Retained for comparison |
| DeepSeek-OCR Q8 | 3 of 10 | 8.6%, 0%, 17.3% CER | 55.6%, 0%, 66.7% WER | 72.9-86.7 s | Stopped early |

DeepSeek required llama.cpp's dedicated multimodal CLI because its server route
rejected the image marker. Its partial run is reported transparently and is not a
ten-line aggregate. Exact completed result files are under `benchmarks/results/`.

A private six-page handwritten PDF supplied by the user was rendered locally at
180 DPI and visually inspected. It has no accuracy score because no verified
reference transcript exists. Dense 3-megapixel pages took several minutes on CPU
even with bounded output, so page segmentation is recommended. The private PDF,
rendered pages, and outputs are excluded by `.gitignore`.

## Native Windows CPU run

- Python 3.12, Windows 11 x64.
- GLM-OCR Q8 + Q8 vision projector, pinned model revision in `setup_model.py`.
- Official llama.cpp Windows CPU runtime b11020.
- Four inference threads, one concurrent page, 4096-token context, 2048-token output limit.
- GPU offload explicitly disabled, including the vision encoder.

| Input | Wall time | Result |
| --- | ---: | --- |
| Darwin letter, 624 × 1008 pixels | 65.593 seconds | Nonempty editable text; not truncated; several recognition errors. |
| IAM fixture, tiny cropped word fragment | 1.640 seconds | Returned `industrie`; far too small a sample to indicate page accuracy. |

One process-memory snapshot during the letter transcription showed **1662 MiB**
for llama-server and about **56 MiB** for the app process. This was not a peak
measurement, does not include Windows/Docker overhead, and is not a minimum-RAM
guarantee. The engine fit comfortably within the intended 16 GB system budget.

The letter result included clear errors: the salutation's `Sanderson` became
`Anderson`, and the phrase `going to beg` was transcribed as `giving the`. The
output also omitted the letterhead/date. No word-error-rate or accuracy percentage
is claimed: there is no independently verified full transcription in this project.
This demonstrates a working local pipeline, not production-ready handwriting
accuracy. Testing several representative modern notes is still needed.

## Application checks

Seventeen automated tests passed: authenticated access, upload/edit/export with raw
text and original preservation, optimistic edit concurrency, malformed images,
failed-job retry, truncated-output flags, interrupted-job recovery, and phone
EXIF orientation, plus the Hunyuan prompt regression, PDF page fan-out, dense-page
segmentation/review metadata, document grouping/export/append/whole-group deletion,
queued cancellation, note deletion, and isolated AI re-read storage. The worker in these tests is deliberately fake; they test the
application, not recognition accuracy. JavaScript syntax checking also passed.

Browser automation was unavailable in the session. The visual layout and phone
interaction have not been verified in a real browser. No claim of browser or
mobile end-to-end coverage is made.

## Docker validation

Built and started successfully with Docker Desktop 28.5.1 and Compose 2.40.3,
using Linux containers on Windows. The Docker runtime is the official CPU build
b11011, pinned by image digest in `Dockerfile`; it is a different build from the
native Windows runtime.

The real-model smoke check ran in a separate temporary container with
**`--network none`**, offline mode enabled, read-only model/sample mounts, and no
mount of the user's note directory. The Darwin letter took **52.924 seconds**
and returned the same text as the native run, including its recognition errors.
The check passed authenticated access, actual model transcription, saving edits,
preserving raw text, downloading the original, searching, and exporting text.
The temporary container and its test-only note were removed afterward.

During processing, one Docker memory snapshot was **1.514 GiB** for the test
container. The separate idle app container used **1.262 GiB** at that moment.
Again, these are snapshots, not measured peaks or minimum hardware requirements.

The main Compose service became healthy on `127.0.0.1:8090`. Its health endpoint,
HTML, JavaScript, and CSS returned successfully, and both sample notes from the
native run remained accessible through the shared data folder. The Docker and
native app were not run against that folder simultaneously.

The first startup exposed a native shared-library search-path issue, fixed by
setting `LD_LIBRARY_PATH=/app` in the image. The resulting container passed the
checks above. Phone access, ARM hardware, and browser visual QA remain untested.
