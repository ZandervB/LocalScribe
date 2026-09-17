"""Exercise a running local app with a real model (creates one sample note)."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--token", default=os.environ.get("LOCALSCRIBE_TOKEN"))
    args = parser.parse_args()
    if not args.token:
        parser.error("Set LOCALSCRIBE_TOKEN or pass --token")
    root = f"http://127.0.0.1:{args.port}"
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, method="GET", body=None, authorized=True):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = "Bearer " + args.token
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(root + path, data=data, method=method, headers=headers)
        with client.open(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content

    deadline = time.monotonic() + 180
    while True:
        try:
            call("/health", authorized=False)
            break
        except (OSError, urllib.error.URLError):
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    try:
        call("/api/notes", authorized=False)
        raise AssertionError("Notes were accessible without an access code")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    note = call("/api/sample", "POST", {})
    note_id = note["id"]
    print(f"Transcribing the public handwriting sample: {note_id}", flush=True)
    deadline = time.monotonic() + 900
    while note["status"] in ("running", "queued"):
        if time.monotonic() > deadline:
            raise TimeoutError("Transcription exceeded 15 minutes")
        time.sleep(2)
        note = call("/api/notes/" + note_id)
    assert note["status"] == "ready", note
    assert note["raw_text"].strip()
    assert not note["truncated"], "The example was truncated"
    print(json.dumps({"seconds": note["seconds"], "raw_text": note["raw_text"]}, indent=2), flush=True)
    corrected = "Smoke test correction\n" + note["text"]
    saved = call("/api/notes/" + note_id, "PUT", {
        "title": "Smoke test: handwritten letter", "text": corrected,
        "reviewed": True, "revision": note["revision"],
    })
    assert saved["raw_text"] == note["raw_text"]
    assert call("/api/notes/" + note_id + "/export").decode() == corrected
    assert call("/api/notes/" + note_id + "/image?original=true")
    assert any(item["id"] == note_id for item in call("/api/notes?q=Smoke"))
    # Leave the note's actual transcription unmodified after checking editing/export.
    call("/api/notes/" + note_id, "PUT", {
        "title": "Darwin letter - container test", "text": note["text"],
        "reviewed": False, "revision": saved["revision"],
    })
    print("PASS: access control, real transcription, editing, original preservation, search, and text export.", flush=True)


if __name__ == "__main__":
    main()
