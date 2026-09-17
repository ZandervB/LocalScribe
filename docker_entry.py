"""Download missing model files on first launch, then run everything locally."""
import os
from pathlib import Path
import signal
import sys

from setup_model import MODEL_FILES, REV, ROOT, download


def main():
    for name, checksum in MODEL_FILES.items():
        target = ROOT / "models" / name
        if target.exists():
            continue
        if os.environ.get("LOCALSCRIBE_OFFLINE", "0") == "1":
            sys.exit(f"Offline mode: missing models/{name}. Run once online or copy the downloaded models into this folder.")
        download(f"https://huggingface.co/ggml-org/HunyuanOCR-GGUF/resolve/{REV}/{name}", target, checksum)
    # Forward Docker's stop signal through run.py's cleanup of the child model.
    def shutdown(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    sys.argv = ["run.py", "--no-browser", "--lan", "--engine-path", "/app/llama-server",
                "--threads", os.environ.get("LOCALSCRIBE_THREADS", "4")]
    from run import main as run
    run()


if __name__ == "__main__":
    main()
