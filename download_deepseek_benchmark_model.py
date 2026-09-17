"""Fetch DeepSeek-OCR for the 32 GB CPU benchmark, with checksum validation."""
from setup_model import ROOT, download

REVISION = "7086620c3e417c744eb30dd3090fd053c6bb2e5e"
FILES = {
    "DeepSeek-OCR-Q8_0.gguf": "81ede3e256230707dccf7fa052570c3a939d57db99de655f43cbb1a830d14d92",
    "mmproj-DeepSeek-OCR-Q8_0.gguf": "786c9b5159898de3d1d94a102836df559fed0bcf09f41a32f62c3219b0e278e0",
}


def main():
    directory = ROOT / "models" / "benchmark" / "deepseek-ocr"
    for name, digest in FILES.items():
        download(f"https://huggingface.co/ggml-org/DeepSeek-OCR-GGUF/resolve/{REVISION}/{name}",
                 directory / name, digest)
    print("DeepSeek-OCR CPU benchmark model is ready.")


if __name__ == "__main__":
    main()
