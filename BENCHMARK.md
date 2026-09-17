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

## Per-word confidence and scan positions (17 September 2026)

llama.cpp returns OpenAI-style `logprobs` for the multimodal chat endpoint, so the
recognition model's own probability for each generated token is available. This was
confirmed against the running container: the tokens returned reconstruct the output
text exactly, so probabilities can be mapped to character offsets.

On the Darwin letter (624 x 1008, 82 words), with words scored below 0.65 flagged:

| Threshold | Words flagged | Notes |
| --- | ---: | --- |
| p < 0.50 | 25 | Caught every misreading inspected, including `Iuinally` (0.07), `fihiu` (0.08), `selatin` (0.06), `spicion` (0.18), `Bechenham` (0.23). |
| p < 0.65 (shipped) | 32 | Adds correct readings such as `Sanderson` (0.60) and `digestion` (0.56). |
| p < 0.80 | 43 | Too many correct words to be useful. |

This is a deliberately hard 1874 cursive page. The rate on ordinary handwriting is
not yet measured, and a confident score is not evidence of a correct reading.

Scan positions come from a row-ink profile of each segment, with a 2% inset so the
photograph's dark frame is not counted as a line, and each text line placed
proportionally down the written area then snapped to the nearest band. Measured on
the same page by overlaying the chosen band on the scan and inspecting it: **14 of
15 lines landed on the correct line of handwriting**. The first line was one band
high, because the scan's dark top edge survives as a band of its own. Horizontal
position is not estimated; the marker spans the page width.

## Local AI text correction (17 September 2026)

Correction no longer re-runs OCR. A second `llama-server` holds a text-only
instruction model that receives the finished transcription, plus the list of words
the recognition model scored as unlikely.

### Choosing the model

Qwen2.5-1.5B-Instruct Q4_K_M was tried first. On the Darwin page it changed exactly
one thing, `Octg. 74` to `Oct. 74`, and passing it the low-confidence words made it
worse: it dropped a line and shifted the remainder. A 1.5B model at Q4 is too weak
for this. Qwen3-4B-Instruct-2507 Q4_K_M (Apache-2.0, 2.5 GB) replaced it and does
repair words, so it is the default. `LOCALSCRIBE_CORRECTOR_MODEL` still accepts a
smaller GGUF, and `LOCALSCRIBE_CORRECTOR=0` skips the model entirely.

### Measured on a real page

A modern cursive page written by the user, photographed with both side margins
cropped and a pen lying across the right of the frame, scored against the
transcript they supplied. Word error rate is computed on lowercased word tokens.

| Stage | Word error rate |
| --- | ---: |
| HunyuanOCR alone | 8.1% |
| After AI correction | **6.4%** |

That is a 1.7 point absolute, roughly 21% relative, reduction. OCR took 87.7 s and
correction 57.7 s on four CPU threads. 21 of about 180 words were flagged as
uncertain, and 22 lines of ink were detected.

The corrections were not uniformly right. `pelump` to `plump`, `he mother` to
`the mother`, and `wh` to `who` are correct. `roses` to `flesh` is wrong, where the
page reads `roes`, and the model appended `the bank`, which is not on the page.
This is why the corrected text is stored separately, shown as a word-level diff,
and never applied without the user pressing a button.

### Prompt and structure findings

- Asking for numbered output (`7| text`) preserved line structure perfectly but
  suppressed corrections almost entirely: the model returned the input unchanged.
- Free-form output corrects well but reflows the page, joining words split across
  lines with `=`. `refit_lines` now realigns the corrected words onto the draft's
  own lines with `difflib`, which keeps both the corrections and the layout. Verified
  on the Darwin page: 16 lines in, 16 out, with a merged line correctly re-split.
- The low-confidence word list only helps once structure is enforced. Before
  `refit_lines`, it caused line drift; after, it is what makes the model act.
- The correction prompt needs an explicit "Transcription to correct, and the only
  thing to reply with:" label, or the model echoes the instructions back as output
  and the length guard rejects the whole result.

### An OCR-side bug this exposed

HunyuanOCR sometimes prefixes its answer with a chat sentence such as
`Here are the OCR - results of the text in the image:`. That line entered the saved
transcript and, because scan positions are mapped per text line, pushed every
highlight on the page down by one line. `strip_preamble` removes it in
`LocalEngine.transcribe`, before offsets are computed.

## Application checks

Twenty-six automated tests passed: authenticated access, upload/edit/export with raw
text and original preservation, optimistic edit concurrency, malformed images,
failed-job retry, truncated-output flags, interrupted-job recovery, and phone
EXIF orientation, plus the Hunyuan prompt and logprob-request regression, per-word
uncertainty spans and ink-line mapping, the corrector's prompt/length guard/absence,
PDF page fan-out, dense-page segmentation/review metadata, document
grouping/export/append/whole-group deletion, queued cancellation, note deletion, and
isolated correction storage. The worker in these tests is deliberately fake; they test the
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
