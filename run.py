"""Start the local model and notebook together; Ctrl+C stops both."""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from app import ROOT, LocalEngine, create_app


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--lan", action="store_true", help="Allow a phone on trusted Wi-Fi to connect using the access code")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--engine-path", type=Path)
    parser.add_argument("--engine-port", type=int, help="Use an already running local HunyuanOCR server")
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 2))
    parser.add_argument("--gpu-layers", type=int, default=int(os.environ.get("LOCALSCRIBE_GPU_LAYERS", "0")),
                        help="Number of model layers to offload; 0 forces CPU")
    parser.add_argument("--mmproj-offload", action=argparse.BooleanOptionalAction,
                        default=os.environ.get("LOCALSCRIBE_MMPROJ_OFFLOAD", "0") == "1",
                        help="Offload the vision projector when using a GPU backend")
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be positive")
    if args.gpu_layers < 0:
        parser.error("--gpu-layers cannot be negative")
    token = os.environ.get("LOCALSCRIBE_TOKEN") or secrets.token_urlsafe(24)
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    process = None
    log = None
    engine_port = args.engine_port or free_port()
    engine_key = os.environ.get("LOCALSCRIBE_ENGINE_KEY", "") if args.engine_port else secrets.token_urlsafe(24)
    try:
        if not args.engine_port:
            binary = args.engine_path or next((ROOT / "runtime").rglob("llama-server.exe"), None) or shutil.which("llama-server")
            model = ROOT / "models" / "HunyuanOCR-Q8_0.gguf"
            projector = ROOT / "models" / "mmproj-HunyuanOCR-Q8_0.gguf"
            if not binary or not model.exists() or not projector.exists():
                sys.exit("Run python setup_model.py --sample first. On macOS/Linux, also install llama.cpp.")
            log = (data / "engine.log").open("w", encoding="utf-8")
            command = [str(binary), "-m", str(model), "--mmproj", str(projector),
                       "--host", "127.0.0.1", "--port", str(engine_port), "-c", "8192",
                       "-t", str(args.threads), "-ngl", str(args.gpu_layers), "--parallel", "1",
                       "--no-webui", "--api-key", engine_key]
            if not args.mmproj_offload:
                command.append("--no-mmproj-offload")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        engine = LocalEngine(engine_port, engine_key)
        print("Loading the local handwriting model...", flush=True)
        deadline = time.monotonic() + 180
        while not engine.ready():
            if process and process.poll() is not None:
                sys.exit(f"The model could not start. See {data / 'engine.log'}")
            if time.monotonic() > deadline:
                sys.exit(f"Model startup timed out. See {data / 'engine.log'}")
            time.sleep(0.5)
        app = create_app(data, engine, token)
        url = f"http://127.0.0.1:{args.port}/#token={token}"
        print(f"\nLocalScribe: {url}\nAccess code: {token}\nCtrl+C stops the app and model.\n", flush=True)
        if args.lan:
            print(f"Phone: open http://YOUR-PC-LAN-IP:{args.port} and enter the code above.", flush=True)
            print("Use trusted private Wi-Fi. This prototype uses HTTP, not encrypted HTTPS.", flush=True)
        if not args.no_browser:
            def open_browser():
                time.sleep(1)
                webbrowser.open(url)
            threading.Thread(target=open_browser, daemon=True).start()
        uvicorn.run(app, host="0.0.0.0" if args.lan else "127.0.0.1", port=args.port, access_log=False)
    except KeyboardInterrupt:
        pass
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if log:
            log.close()


if __name__ == "__main__":
    main()
