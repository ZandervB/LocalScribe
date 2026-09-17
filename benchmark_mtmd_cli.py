"""Benchmark an OCR GGUF through llama.cpp's local multimodal CLI.

This is used for models whose current llama-server implementation does not
accept image markers. It forces CPU execution with ``-ngl 0`` and records the
same CER/WER metrics as benchmark_htr.py.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from benchmark_htr import distance, normalized

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--mmproj", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--chat-template", required=True)
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 2))
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--manifest", type=Path, default=ROOT / "benchmarks" / "iam-line-10.json")
    parser.add_argument("--binary", type=Path, default=ROOT / "runtime" / "llama-mtmd-cli.exe")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    output_path = ROOT / "benchmarks" / "results" / (args.name + ".json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows, started = [], time.monotonic()
    for item in manifest["items"]:
        image = args.manifest.parent / item["file"]
        command = [str(args.binary), "-m", str(args.model), "--mmproj", str(args.mmproj),
                   "--image", str(image), "-p", args.prompt, "--chat-template", args.chat_template,
                   "-t", str(args.threads), "-ngl", "0", "--no-mmproj-offload", "-c", str(args.context),
                   "-n", str(args.max_tokens), "--no-perf", "-lv", "0"]
        timer = time.monotonic()
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=900)
        seconds = time.monotonic() - timer
        if result.returncode:
            raise RuntimeError(f"{item['id']} failed: {result.stderr[-1500:]}")
        transcription = result.stdout.strip()
        reference = item["text"]
        actual, expected = normalized(transcription), normalized(reference)
        character_distance = distance(actual, expected)
        word_distance = distance(actual.split(), expected.split())
        row = {"id": item["id"], "file": item["file"], "reference": reference,
               "output": transcription, "seconds": seconds,
               "cer": character_distance / max(1, len(expected)),
               "wer": word_distance / max(1, len(expected.split()))}
        rows.append(row)
        print(f"{item['id']}: CER {row['cer']:.3f}, WER {row['wer']:.3f}, {seconds:.1f}s", flush=True)
    summary = {"name": args.name, "created": datetime.now(timezone.utc).isoformat(),
               "model": str(args.model), "mmproj": str(args.mmproj), "prompt": args.prompt,
               "chat_template": args.chat_template, "threads": args.threads, "context": args.context,
               "max_tokens": args.max_tokens, "manifest": str(args.manifest),
               "model_start_and_run_seconds": time.monotonic() - started,
               "mean_cer": sum(row["cer"] for row in rows) / len(rows),
               "mean_wer": sum(row["wer"] for row in rows) / len(rows),
               "mean_seconds": sum(row["seconds"] for row in rows) / len(rows), "items": rows}
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with output_path.with_suffix(".csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "file", "seconds", "cer", "wer", "reference", "output"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{args.name}: mean CER {summary['mean_cer']:.3f}, mean WER {summary['mean_wer']:.3f}, mean {summary['mean_seconds']:.1f}s")


if __name__ == "__main__":
    main()
