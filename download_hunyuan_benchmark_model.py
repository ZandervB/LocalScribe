"""Fetch the fixed CPU comparison model with checksum validation."""
from pathlib import Path
from setup_model import ROOT, download

REVISION = "8e070c9ad79e4ca97a9b4daa2f1ce17e8759afb1"
FILES = {
    "HunyuanOCR-Q8_0.gguf": "cdafc794cafeae377868d7a40a70e282a737e39abe77c0d8b73614447b364a21",
    "mmproj-HunyuanOCR-Q8_0.gguf": "b77913164ff73d4c0dc4d994e236ed72bacbbe5c5db1ec9b2828627b46c32804",
}


def main():
    directory = ROOT / "models" / "benchmark" / "hunyuanocr"
    for name, digest in FILES.items():
        download(f"https://huggingface.co/ggml-org/HunyuanOCR-GGUF/resolve/{REVISION}/{name}",
                 directory / name, digest)
    print("HunyuanOCR CPU benchmark model is ready.")


if __name__ == "__main__":
    main()
