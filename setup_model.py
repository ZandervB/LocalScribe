"""Explicit, one-time downloads. The app itself never downloads anything."""
import argparse
import hashlib
from pathlib import Path
import platform
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
REV = "8e070c9ad79e4ca97a9b4daa2f1ce17e8759afb1"
MODEL_FILES = {
    "HunyuanOCR-Q8_0.gguf": "cdafc794cafeae377868d7a40a70e282a737e39abe77c0d8b73614447b364a21",
    "mmproj-HunyuanOCR-Q8_0.gguf": "b77913164ff73d4c0dc4d994e236ed72bacbbe5c5db1ec9b2828627b46c32804",
}
CORRECTOR_REPO = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
CORRECTOR_REV = "a06e946bb6b655725eafa393f4a9745d460374c9"
CORRECTOR_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
CORRECTOR_SHA = "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597"
RUNTIME_TAG = "b11020"
RUNTIME_SHA = "6b17164540cc85fe528c969027cee1c198d740f27588a893cd92bb3cb1c1ddfa"


def model_downloads():
    """Every GGUF the app needs, as (filename, url, sha256)."""
    files = [(name, f"https://huggingface.co/ggml-org/HunyuanOCR-GGUF/resolve/{REV}/{name}", sha)
             for name, sha in MODEL_FILES.items()]
    files.append((CORRECTOR_FILE,
                  f"https://huggingface.co/{CORRECTOR_REPO}/resolve/{CORRECTOR_REV}/{CORRECTOR_FILE}",
                  CORRECTOR_SHA))
    return files


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, destination, sha=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and (not sha or digest(destination) == sha):
        print(f"Already downloaded: {destination.name}", flush=True)
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, 6):
        existing = partial.stat().st_size if partial.exists() else 0
        print(f"Downloading {destination.name} (attempt {attempt}/5, resuming at {existing // (1024 * 1024)} MiB) ...", flush=True)
        headers = {"User-Agent": "LocalScribe-prototype/0.1"}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=180) as response:
                append = existing and response.status == 206
                total = existing if append else 0
                last_report = total
                with partial.open("ab" if append else "wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        total += len(chunk)
                        if total - last_report >= 100 * 1024 * 1024:
                            print(f"  {total // (1024 * 1024)} MiB", flush=True)
                            last_report = total
            break
        except (OSError, urllib.error.URLError) as error:
            if attempt == 5:
                raise
            print(f"  Download interrupted ({error}); retrying.", flush=True)
            time.sleep(attempt)
    if sha and digest(partial) != sha:
        raise RuntimeError(f"Checksum mismatch: {destination.name}; file was not installed.")
    partial.replace(destination)
    print(f"Ready: {destination.name}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help="Download a public handwriting example for testing")
    parser.add_argument("--samples-only", action="store_true", help="Only download the two public evaluation examples")
    args = parser.parse_args()
    if args.samples_only:
        download_samples()
        return
    if platform.system() == "Windows" and platform.machine().lower() in ("amd64", "x86_64"):
        archive = ROOT / "runtime" / f"llama-{RUNTIME_TAG}-bin-win-cpu-x64.zip"
        download(f"https://github.com/ggml-org/llama.cpp/releases/download/{RUNTIME_TAG}/{archive.name}", archive, RUNTIME_SHA)
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                target = (archive.parent / member.filename).resolve()
                if not target.is_relative_to(archive.parent.resolve()):
                    raise RuntimeError("Invalid path in runtime archive")
            zipped.extractall(archive.parent)
    else:
        print("Install llama.cpp for your OS and put llama-server on PATH (or use --engine-path).")
    for name, url, sha in model_downloads():
        download(url, ROOT / "models" / name, sha)
    if args.sample:
        download_samples()
    print("Setup complete. All transcription can now run offline.")


def download_samples():
    download("https://commons.wikimedia.org/wiki/Special:Redirect/file/Darwin_letter.jpg", ROOT / "samples" / "darwin-letter.jpg")
    download("https://huggingface.co/datasets/hf-internal-testing/fixtures_ocr/resolve/28fe12cdf7816b5dde94e22051b2ec8dc74267b7/iam_picture.jpeg", ROOT / "samples" / "iam-line.jpg")


if __name__ == "__main__":
    main()
