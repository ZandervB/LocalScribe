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

from app import ROOT, LocalEngine, TextCorrector, create_app
from setup_model import CORRECTOR_FILE


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(client, name, process, log_path, seconds=240):
    deadline = time.monotonic() + seconds
    while not client.ready():
        if process and process.poll() is not None:
            return f"The {name} model could not start. See {log_path}"
        if time.monotonic() > deadline:
            return f"{name.capitalize()} model startup timed out. See {log_path}"
        time.sleep(0.5)
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--lan", action="store_true", help="Allow a phone on trusted Wi-Fi to connect using the access code")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--engine-path", type=Path)
    parser.add_argument("--engine-port", type=int, help="Use an already running local HunyuanOCR server")
    parser.add_argument("--corrector-port", type=int, help="Use an already running local correction server")
    parser.add_argument("--corrector-model", type=Path,
                        default=ROOT / "models" / (os.environ.get("LOCALSCRIBE_CORRECTOR_MODEL") or CORRECTOR_FILE))
    parser.add_argument("--no-corrector", action="store_true", help="Skip the text model used for AI correction")
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
    corrector_process = None
    logs = []
    engine_port = args.engine_port or free_port()
    engine_key = os.environ.get("LOCALSCRIBE_ENGINE_KEY", "") if args.engine_port else secrets.token_urlsafe(24)
    corrector_port = args.corrector_port or free_port()
    corrector_key = os.environ.get("LOCALSCRIBE_CORRECTOR_KEY", "") if args.corrector_port else secrets.token_urlsafe(24)
    corrector_name = args.corrector_model.stem.replace("_", " ")
    corrector = None if args.no_corrector else TextCorrector(corrector_port, corrector_key, corrector_name)
    try:
        binary = args.engine_path or next((ROOT / "runtime").rglob("llama-server.exe"), None) or shutil.which("llama-server")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if not args.engine_port:
            model = ROOT / "models" / "HunyuanOCR-Q8_0.gguf"
            projector = ROOT / "models" / "mmproj-HunyuanOCR-Q8_0.gguf"
            if not binary or not model.exists() or not projector.exists():
                sys.exit("Run python setup_model.py --sample first. On macOS/Linux, also install llama.cpp.")
            log = (data / "engine.log").open("w", encoding="utf-8")
            logs.append(log)
            command = [str(binary), "-m", str(model), "--mmproj", str(projector),
                       "--host", "127.0.0.1", "--port", str(engine_port), "-c", "8192",
                       "-t", str(args.threads), "-ngl", str(args.gpu_layers), "--parallel", "1",
                       "--no-webui", "--api-key", engine_key]
            if not args.mmproj_offload:
                command.append("--no-mmproj-offload")
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        if corrector and not args.corrector_port:
            if not binary or not args.corrector_model.exists():
                print(f"AI correction is unavailable: {args.corrector_model} is missing. Run python setup_model.py to download it.", flush=True)
                corrector = None
            else:
                log = (data / "corrector.log").open("w", encoding="utf-8")
                logs.append(log)
                corrector_process = subprocess.Popen(
                    [str(binary), "-m", str(args.corrector_model), "--host", "127.0.0.1",
                     "--port", str(corrector_port), "-c", "8192", "-t", str(args.threads),
                     "-ngl", str(args.gpu_layers), "--parallel", "1", "--no-webui",
                     "--api-key", corrector_key],
                    stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        engine = LocalEngine(engine_port, engine_key)
        print("Loading the local handwriting model...", flush=True)
        failure = wait_for(engine, "handwriting", process, data / "engine.log")
        if failure:
            sys.exit(failure)
        if corrector:
            print("Loading the local correction model...", flush=True)
            # A multi-gigabyte text model loads slowly from a bind-mounted folder.
            failure = wait_for(corrector, "correction", corrector_process, data / "corrector.log", 900)
            if failure:
                # Transcription is the core feature; AI correction is optional.
                print(f"{failure}\nContinuing without AI correction.", flush=True)
                corrector = None
        app = create_app(data, engine, token, corrector)
        url = f"http://127.0.0.1:{args.port}/#token={token}"
        print(f"\nLocalScribe: {url}\nAccess code: {token}", flush=True)
        print(f"AI correction: {'ready (' + corrector.model + ')' if corrector else 'off'}", flush=True)
        print("Ctrl+C stops the app and the local models.\n", flush=True)
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
        for child in (process, corrector_process):
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
