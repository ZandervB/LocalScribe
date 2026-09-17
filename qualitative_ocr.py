"""Run a CPU-only qualitative OCR pass over locally supplied images.

Use this for private documents without ground-truth text. The caller chooses an
ignored output path; this utility makes no network calls or uploads.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import time

from benchmark_htr import await_server, port, request, start_server

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--mmproj", type=Path, required=True)
    parser.add_argument("--images", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt", default="OCR")
    parser.add_argument("--chat-template")
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 2))
    parser.add_argument("--context", type=int, default=12288)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--binary", type=Path, default=ROOT / "runtime" / "llama-server.exe")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    server_port, key = port(), secrets.token_urlsafe(24)
    log_path = args.output.with_suffix(".engine.log")
    process = None
    entries = []
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = start_server(args.binary, args.model, args.mmproj, server_port, key,
                                   args.threads, args.context, args.chat_template, log)
            url = f"http://127.0.0.1:{server_port}"
            await_server(url, key, process)
            for image in args.images:
                timer = time.monotonic()
                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                payload = {"messages": [{"role": "user", "content": [
                    {"type": "text", "text": args.prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                ]}], "temperature": 0, "top_p": 1, "max_tokens": args.max_tokens, "stream": False}
                response = request(url + "/v1/chat/completions", payload, key)
                text = response["choices"][0]["message"].get("content", "")
                entries.append({"image": str(image), "seconds": time.monotonic() - timer,
                                "output": text, "finish_reason": response["choices"][0].get("finish_reason")})
                print(f"{image.name}: {entries[-1]['seconds']:.1f}s", flush=True)
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except Exception:
                process.kill()
    args.output.write_text(json.dumps({"model": str(args.model), "mmproj": str(args.mmproj),
                                       "prompt": args.prompt, "chat_template": args.chat_template,
                                       "entries": entries}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
