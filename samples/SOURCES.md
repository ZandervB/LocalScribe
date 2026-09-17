# Handwriting examples

`darwin-letter.jpg` is downloaded by `setup_model.py --sample`, not bundled in source control.

- Letter from Charles Darwin to John Burdon-Sanderson, 9 October 1874.
- Source: https://commons.wikimedia.org/wiki/File:Darwin_letter.jpg
- Digitization: UBC Library Digital Collections, linked from the source page.
- The source page labels the work public domain in the US and separately includes a UBC research/reference-use notice. This local copy is for prototype evaluation; consult those source terms before redistributing the image.
- Deliberately difficult example: cursive, insertions/deletions, bleed-through, faded ink, and a page that ends mid-sentence. It is not representative of all modern handwritten notes.
- No verified ground-truth transcription is bundled. Do not report an accuracy percentage from this example without independently transcribing it.

`iam-line.jpg` is the very small `iam_picture.jpeg` fixture downloaded from
https://huggingface.co/datasets/hf-internal-testing/fixtures_ocr at revision
`28fe12cdf7816b5dde94e22051b2ec8dc74267b7`. Despite its local filename, it is only
a tightly cropped word fragment, not a complete line or page. Its originating
dataset is IAM. It is used here as a local evaluation fixture; check the dataset's
terms before any redistribution or use beyond evaluation.

Model weights: https://huggingface.co/ggml-org/GLM-OCR-GGUF (conversion of https://huggingface.co/zai-org/GLM-OCR).

CPU runtime: https://github.com/ggml-org/llama.cpp/releases/tag/b11020

Downloads are pinned and SHA-256 checked for the executable archive and model weights. Upstream licenses continue to apply; the runtime archive includes its license files.
