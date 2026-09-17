"""Download a fixed 10-item IAM handwriting line benchmark with ground truth.

The dataset card says Teklia/IAM-line is MIT licensed. This script stores only a
small deterministic sample and records the dataset revision and reference text.
"""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
REVISION = "fbdad97500ce54635c0d1ba306bf535cb40656cf"
OFFSETS = [0, 37, 111, 257, 401, 598, 733, 1011, 1444, 2001]


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "LocalScribe-benchmark/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def fetch(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "LocalScribe-benchmark/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main():
    directory = ROOT / "benchmarks" / "iam-line-10"
    directory.mkdir(parents=True, exist_ok=True)
    items = []
    for offset in OFFSETS:
        endpoint = "https://datasets-server.huggingface.co/rows?dataset=Teklia%2FIAM-line&config=default&split=test"
        row = get_json(endpoint + f"&offset={offset}&length=1")["rows"][0]["row"]
        filename = f"test-{offset:04d}.jpg"
        target = directory / filename
        digest = fetch(row["image"]["src"], target)
        items.append({"id": f"test-{offset:04d}", "file": f"iam-line-10/{filename}",
                      "text": row["text"], "sha256": digest,
                      "width": row["image"]["width"], "height": row["image"]["height"]})
        print(f"Downloaded {filename}")
    manifest = {"dataset": "Teklia/IAM-line", "revision": REVISION, "split": "test",
                "license": "MIT", "items": items}
    (ROOT / "benchmarks" / "iam-line-10.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Saved benchmarks/iam-line-10.json")


if __name__ == "__main__":
    main()
