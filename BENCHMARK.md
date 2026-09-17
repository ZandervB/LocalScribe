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

That is a 1.7 point absolute, roughly 21% relative, reduction. After the preamble
fix below, OCR produced 169 words against a 172-word reference, and the corrected
text 172. Timings on four CPU threads varied between runs: OCR 10.9 s to 87.7 s,
correction 33.5 s to 57.7 s, for the same page and settings.

13 words were flagged as uncertain and 22 lines of ink detected. The flagged words
were the genuine error sites: `Its` (0.32) where the page reads `As`, `spoiled`
(0.40) and `on` (0.33) where it reads `spilled out`, and `split` (0.15) where the
photograph crops the preceding word off the left margin.

Rendering each flagged word's chosen band over the scan and inspecting it,
**all 9 bands sat on the correct line of handwriting**. This page has no dark
border, which is what cost the Darwin page its first line.

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

## Production-readiness pass (17 September 2026)

Four defects were found by inspection and confirmed by measurement, then fixed.
Each fix has a test that fails without it.

| Defect | Evidence before | After |
| --- | --- | --- |
| `POST /api/notes` decoded images and rendered PDF pages on the event loop, stalling every other request | 30-page PDF import: worst `/api/status` latency **3438 ms** (idle baseline 15 ms) | **750 ms**, the remainder being ordinary GIL contention from Pillow/PyMuPDF |
| An `Authorization` header with any non-ASCII byte raised `TypeError` from `secrets.compare_digest` | unauthenticated request → unhandled exception, HTTP 500 and a traceback | HTTP 401. The same bug made any non-ASCII `LOCALSCRIBE_TOKEN` return 500 on every request |
| `/api/notes` omitted `enhance_status`, so the sidebar offered **Delete** on a note whose AI correction was running | the button 409'd when pressed | column returned; the button is hidden while correction runs |
| The browser polled every 2.5 s forever, re-fetching the open note and (with `LOCALSCRIBE_CORRECTOR=0`) re-probing `llama-server`'s health on every tick | constant load on the same CPU cores the model needs | 2 s while work is in flight, 15 s idle; readiness probes cached 3 s server-side and gated 10 s client-side |

Not changed, deliberately: SQLite stays on the default rollback journal. WAL would
be the usual hardening, but `data/` is a Windows bind mount into WSL 2 for the
supported Docker path, where WAL's shared-memory file is unreliable. Measured
contention did not justify the risk — the notes list costs 6.3 ms at 800 notes.

Recognition settings were **not** touched. The `OCR` prompt, the 0.65 uncertainty
threshold, the 2000 px inference copy and the 2048-token output limit are all
values this document measured; changing them needs a new benchmark run, not an
edit. See "Measurable accuracy experiments" below.

## Line detection and page framing (17 September 2026)

Two user-supplied pages were analysed against their stored `regions`, `lines` and
`uncertain` data. Both showed the scan highlight landing on the wrong line, for two
unrelated reasons.

**Page A, cursive on plain paper.** `ink_lines` found 11 bands where the page has 9
lines of writing, because descenders were detected as separate 0.3%-tall bands. In
`map_lines_to_bands` those slivers are snap targets sitting right beside the real
line, so segment 2's first row snapped to a 0.6%-tall sliver instead of the line
above it — the visible off-by-one.

**Page B, ruled paper photographed on a dark desk.** The desk fills the bottom 22%
of the frame. `ink_lines` scales its threshold as `min(profile) + 0.16 x (max - min)`,
and because the desk sets `max`, the threshold lands at **84** while the entire
handwriting area measures **56-102**. Most lines therefore fall below it and
whole paragraphs collapse into one band: 19 text rows against 12 bands, with single
bands 9.6% and 21% of the page tall. The same framing costs real text — page
segmentation gave segment 2 a region that is roughly half desk, and the model
returned one line for it, so **a whole paragraph of that page is missing from the
transcription**.

### Fixes attempted and rejected

Each candidate was scored on page A's recorded bands, page B, and the six clean
scanned pages in `benchmarks/private/pdf-import-qa/`.

| Candidate | Result | Verdict |
| --- | --- | --- |
| Merge sub-median bands into their neighbour | Fixes page A. Clean scans lose bands (28->25, 31->28) and the worst blob grows 0.043 -> 0.072 of page height | Rejected |
| Exclude short bands from snap candidates only | Fixes page A's off-by-one exactly. Still changes the mapping on 4 of 6 clean scans | Rejected |
| Percentile threshold (10th/80th) instead of min/max | Page B seg1 12 -> 10 bands, no closer to its 19 rows; worst blob on clean scans 0.043 -> 0.082 | Rejected |
| Per-strip profiling (6-16 vertical strips), swept with the above | Best case page B seg1 12 -> 14 against a target of 19, while regressing clean scans | Rejected |
| Crop to the bright page before OCR | Page B: keeps 78%, removing exactly the desk, bands 2 -> 16. Clean scans are cropped to **10-33%** of themselves | Rejected as unsafe |

The decisive measurement is that band height cannot separate the two cases. Page A's
spurious sliver is 0.158 of its page's median band height; genuine short lines on the
clean scans measure 0.088, 0.125, 0.140, 0.161 and 0.172. The distributions overlap,
so no threshold fixes page A without disturbing pages that are currently correct.

**Conclusion:** the row-ink profile has reached its limit. Real per-word or per-line
boxes need a layout detector, and page framing needs a proper quadrilateral page
detector rather than a brightness heuristic. Neither was shipped. The one change made
here is unrelated to thresholds: segments are now joined with a single newline.

### Shipped: segment joins are no longer paragraph breaks

Segment outputs were joined with `

`, so a page cut mid-sentence produced a blank
line in the transcript. Page A's stored text contained
`...it was still alive, the bad

split and I remember...`, where the page simply
continues. Segments now join with `
`, and the `regions`/`uncertain` character
offsets were corrected from +2 to +1 to match. A test asserts every stored offset
still slices back to its own recorded text.

## Where OCR time actually goes (17 September 2026)

Read from `data/engine.log` of the running container, transcribing the user's ruled
page on four threads.

| | Segment 1 | Segment 2 |
| --- | ---: | ---: |
| Prompt eval (the page image) | 36.5 s / 761 tokens | 31.4 s / 700 tokens |
| Generation (the transcript) | 10.3 s / 259 tokens | 0.9 s / 27 tokens |
| Total | 46.7 s | 32.3 s |
| Share spent encoding the image | 78% | **97%** |

**Roughly 85% of transcription time is encoding the page image, not writing text.**
That makes pixel count, not output length, the variable worth attacking. From these
figures the image costs about **1003 prompt tokens per megapixel**.

Segment 2 is the clearest waste: **31.4 seconds encoding a photograph of a desk**,
returning 27 tokens, on the same page framing that also cost a whole paragraph of
transcription.

### Threads

Ten IAM lines, identical settings, only `--threads` varied:

| Threads | Mean CER | Mean WER | Mean time/line |
| ---: | ---: | ---: | ---: |
| 4 | 0.004 | 0.033 | 14.0 s |
| 8 | 0.004 | 0.033 | **10.8 s** |

**1.30x faster, byte-identical output** (expected: temperature is 0). This is a lower
bound for full pages: IAM lines are small, so per-call overhead dominates them,
whereas a page spends most of its time in compute-bound prompt eval. `LOCALSCRIBE_CPUS`,
`LOCALSCRIBE_THREADS` and `LOCALSCRIBE_MEMORY` are now configurable, defaults unchanged
at 4/4/9g so the documented 16 GB target still holds.

### Trimming a dark surround from the page

The earlier attempt to find the page as the largest bright region was rejected because
it cropped clean scans to 10-33% of themselves. `trim_background` instead removes only
**leading and trailing edge runs** that are far darker than the paper, which cannot
misfire on a page that already fills the frame.

| Input | Result |
| --- | --- |
| Ruled page on a dark desk | Trimmed to y 0.00-0.79, removing **22% of the frame** |
| Six clean scanned pages (`pdf-import-qa`) | **No crop on any of them** |
| Synthetic full-white and full-dark frames | No crop |

On the real page, end to end through `Store.add`: 899x1599 -> 899x1255, and detected
ink bands **2 -> 16** with the tallest blob 0.215 -> 0.183 of page height. The page
still has ~19 lines of writing, so line detection remains imperfect; it is no longer
collapsing whole paragraphs into one band.

### Inference resolution and context

The recognition copy is now capped at **1400 px** on its longest side rather than 2000.
For a 12 MP phone photo that is 1.47 MP instead of 3.00 MP, about **49% of the pixels**
and therefore about half the image tokens. The cap did not bind on the ruled page above,
whose recognition copy is only 1255 px tall, so its gain came entirely from the trim.

Engine context dropped 8192 -> **6144**, sized from the measured 1003 tokens/MP:

| Case | Image tokens | + 2048 output | Fits 4096 | Fits 6144 |
| --- | ---: | ---: | :---: | :---: |
| Segment at the 1400 cap | 702 | 2750 | yes | yes |
| Whole page, segmentation off, 1400 cap | 1474 | 3522 | yes | yes |
| Whole page, segmentation off, old 2000 cap | 3009 | 5057 | **no** | yes |

4096 was rejected: it would overflow a non-segmented page at the old cap, leaving no
margin if the cap is ever raised again. The corrector keeps 8192 — its prompt reaches
roughly 4000 tokens plus up to 2850 of output.

**Not yet measured: whether the 1400 px cap costs accuracy.** IAM's line crops cannot
answer it and no user page has a verified reference transcript. This is the one change
here shipped on reasoning rather than a measurement.

## Rebuilt container, verified end to end (17 September 2026)

Image rebuilt and `smoke_check.py` run against the real model inside it. The check
**passed**: access control, real transcription, editing, raw-text preservation,
original download, search and text export.

Applied limits confirmed by `docker inspect`: `NanoCpus=8000000000`, `Memory=16 GiB`.
Engine flags confirmed from `/proc`: `-c 6144 -t 8`.

| Darwin letter, 624 x 1008, in Docker | Wall time |
| --- | ---: |
| Earlier run, 4 threads | 52.9 s |
| This run, 8 threads | **39.2 s** |

A clean attribution: the image is 0.63 MP, so the new 1400 px cap did not bind, and
`trim_background` correctly returned `None` for it — the stored recognition copy is
624 x 1008, byte-for-byte the original size. The context reduction does not affect
inference speed. The difference is therefore the thread count: **1.35x**, consistent
with the 1.30x measured on the IAM set.

Sampling `docker stats` during a transcription showed **790-809% of the 800% allocated**,
confirming the vision encoder does use every allocated core. This had been in doubt
because prompt-eval throughput per token looked flat between the two thread counts;
that comparison was across different images and is not meaningful.

One measurement to discard: re-running the *same* image transcribed in 5.1 s rather
than 39.2 s. That is llama.cpp reusing the KV cache for an identical prompt, not a
speedup for new pages.

The startup change is visible in the log: the banner prints
`AI correction: still loading in the background` and the app is reachable while the
2.5 GB corrector is still loading.

## Why PDF imports were narrated instead of read (17 September 2026)

A six-page handwritten PDF imported as pure commentary: `### OCR - Text Analysis and
Description:`, `The text is in Chinese`, and one page that looped the same sentence
68 times to the token limit. The same content photographed directly had transcribed
fine, so the PDF path was suspect.

### Isolating it

One page-one top segment, same bytes, four framings:

| Variant | Size | Result |
| --- | --- | --- |
| As the app sent it | 1324x730 | narrates, then hides the real text in a code fence |
| Whole page, unsegmented | 1324x1872 | narrates, claims the page is Chinese |
| **Upscaled 2x** | 2648x1460 | **clean, correct transcription** |
| Larger top slice | 1324x973 | narrates + fenced text |

Identical pixels, merely enlarged, read perfectly. So it is neither the PDF renderer
nor the aspect ratio. Sweeping the scale and measuring the median detected ink-line
height against the outcome gives a sharp threshold:

| Median ink-line height | Outcome |
| ---: | --- |
| 21 px, 28 px | **narrates the image** |
| 34, 38, 42, 51 px | transcribes |

**The variable is how tall a written line is in pixels, not megapixels.** The
directly photographed page worked at only 0.72 MP per segment because it was shot
close up; the PDF renders the same handwriting at 21 px per line. This also explains
the earlier 1400 px experiment: it shrank lines further and made things worse.

### Three changes

1. **`PDF_DPI` 160 -> 200.** These PDFs embed 1654x2340 (3.9 MP) images while a
   160 DPI render produced 1324x1872, discarding 36% of real pixels before the model
   saw them.
2. **Adaptive enlargement.** `legible_scale` measures the median ink-line height of
   each section and enlarges it to `TARGET_LINE_PIXELS` (36). It is bounded to
   enlarge only (`max(1.0, ...)`), by `MAX_UPSCALE` 2.0, and by a 4 MP ceiling, and
   returns 1.0 when fewer than three lines are detected, so sparse pages, diagrams
   and already-large photographs are untouched and cost nothing extra.
3. **Escalation retry.** A section still refused after that is retried once at 1.4x,
   capped at 6.5 MP, because the alternative is losing that part of the page.

### Measured end to end, two pages through the real pipeline

Every section now lands at 34-37 px. Five of six transcribed on the first pass
(previously the top section failed on pages 1, 2 and 3), and the sixth was rescued:

| | Size | Time | Result |
| --- | --- | ---: | --- |
| page 1 section 1, first pass | 2213x1207 | 181 s | narrates |
| page 1 section 1, retry | 3098x1690 | 354 s | **transcribes correctly** |

Two pages cost 8.4 minutes on eight CPU threads. That is the price of reading a page
the model would otherwise narrate, and it is only paid where the writing is small.

### It is not only PDFs: ordinary photographs gain too

The threshold is document-dependent, not a clean line. The Darwin sample transcribes
at 29 px per line; page 2 of the private PDF narrated at 30 px. Between roughly 28 and
34 px the outcome depends on ink contrast and layout, so enlarging into the reliable
zone is the safer default. Measured on real images:

| Image | Median line | Action |
| --- | ---: | --- |
| Darwin letter sample | 29 px | enlarged x1.24 |
| Private PDF rendered at 180 DPI | 30-32 px | enlarged x1.12-1.13 |
| Six clean scanned pages | 28-32 px | enlarged x1.12-1.24 |
| IAM line fragment (one line) | n/a | untouched |

The Darwin letter already transcribed before this change, so it measures the cost and
benefit on a page that was never broken:

| | Before | After |
| --- | ---: | ---: |
| Wall time | 39.2 s | 45.9 s (+17%) |
| Opening lines | `I. xix` / `Bechenham. Kent` | `16` / `Down,` / `Beckenham Kent.` |

It recovered `Down,`, a line previously dropped altogether, corrected `Bechenham` to
the real `Beckenham` and `I. xix` to `16`, and moved `going to the` towards the true
`going to beg`. So the enlargement is not only a rescue for unreadable scans; it
improves ordinary photographs for about a sixth more time.

### Recovering text the model buried

Asked to read a hard page, the model often narrates first and then puts the real
transcription in a fenced block. `strip_preamble` now takes the fenced block when one
is present, so those pages yield their text instead of being discarded. Verified
against the exact strings the model returned.

### A Windows cleanup hazard found on the way

`segmented_inputs` cleans its temporary directory on exit. On Windows a file handle
held a moment too long (a virus scanner reading a freshly written JPEG is enough)
raises `PermissionError` there, which would fail a page *after* it had transcribed
successfully. Both temporary directories now use `ignore_cleanup_errors=True`: a
stray temp file is harmless, a lost transcription is not.

## Automatic correction, and three things it broke (17 September 2026)

Correction is now queued by the worker as soon as a page transcribes, controlled by
`LOCALSCRIBE_AUTO_CORRECT` (default on). Because the queue is FIFO and correction jobs
are appended, **every queued page is transcribed before any correction runs**, so a
50-page import still gets all its text first.

`NEXT_AI.md` item 12 says the correction must never be applied silently. That holds:
automatic or not, the result only lands in `enhanced_text` and is shown as a diff.

Three consequences found and fixed while implementing it:

1. **Delete was blocked for the length of every correction.** `delete_note` refused
   while `enhance_status='running'`. That was tolerable when a human pressed the
   button; with automatic correction it locked Delete for 30-60 s on every page for a
   job nobody asked for. Only transcription holds the page files open — the corrector
   is text-only and reads `raw_text` from the database — so deletion is now allowed
   during correction. This first showed up as a test failing roughly 1 run in 6.
2. **The view jumped to Compare on its own.** The UI switched tabs when a correction
   the reader was waiting on completed. Automatic corrections would have pulled every
   reader off their own text. The switch is now gated on `askedForFix`, so it only
   follows a button press.
3. **Queue copy** said "Waiting in the local queue", which read as though the reader
   had asked for it.

### A separate UI bug, reported from the browser

Clicking an uncertain word while the **Compare** tab was open, then returning to
**Your text**, left every shaded word collapsed to a sliver at the left of its line.
`matchBackdrop` sets the shading layer's width from `editor.clientWidth`, which is
**0** while the edit tab is hidden, so the layer was painted at zero width and nothing
repainted it on return. `paintBackdrop` now refuses to measure a hidden editor,
`setView('edit')` repaints once the tab is on screen, and an uncertain-word chip
switches to the edit tab before focusing, since chips are visible on every tab.

## Startup and interface (17 September 2026)

- **The app no longer waits for the correction model.** `run.py` blocked on
  `wait_for(corrector, ..., 900)` before starting uvicorn, so nothing was reachable
  until the 2.5 GB Qwen3-4B model had loaded — the slowest part of startup on a
  bind-mounted `models/` folder, and the cause of the "every startup takes minutes"
  troubleshooting entry. It now loads on a background thread while transcription is
  already usable; `/api/status` reports `corrector_ready` and the UI enables the
  button when it arrives.
- **iOS highlight lag.** The uncertain-word layer was a backdrop kept in step with
  `backdrop.scrollTop = editor.scrollTop` on the textarea's `scroll` event. Safari
  coalesces those events during momentum scrolling, so the highlights trailed the
  words and snapped into place when scrolling stopped. The textarea now grows to its
  content and `#panel-edit` scrolls instead, so the text and its highlights are one
  block and no synchronisation exists to fall behind. Reported by the user on an
  iPhone; the fix is structural but has **not** been verified in a real browser, as
  no browser automation is available here.
- **Mobile layout.** Inputs are 16px at <=640px so iOS stops zooming on focus,
  controls reach a 42px touch target, the toolbar/tabs/compare grid stack, and the
  scan pane is capped at 46vh. Also unverified in a real browser.

## Application checks

Twenty-nine automated tests passed: authenticated access, upload/edit/export with raw
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
