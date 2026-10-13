#!/usr/bin/env python3
"""Verify source ancestry, runtime inputs, receipt replay, hashes, and media decoding."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir", type=Path, default=ROOT / "docs" / "demo" / "deployed-receipt-guard")
    args = parser.parse_args()
    demo = args.demo_dir
    receipt_path = demo / "capture.json"
    receipt = json.loads(receipt_path.read_text())
    manifest = json.loads((demo / "source-manifest.json").read_text())
    source = receipt["source_commit"]
    _run(["git", "merge-base", "--is-ancestor", source, "HEAD"])
    if manifest["source_commit"] != source:
        raise SystemExit("source commit mismatch")
    source_tree = _run(["git", "rev-parse", f"{source}^{{tree}}"]).stdout.strip()
    if receipt["source_tree"] != source_tree or manifest["source_tree"] != source_tree:
        raise SystemExit("source tree mismatch")
    for item in manifest["inputs"]:
        payload = subprocess.run(
            ["git", "show", f"{source}:{item['path']}"], cwd=ROOT, capture_output=True, check=True
        ).stdout
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise SystemExit(f"source input changed: {item['path']}")
    for name, expected in manifest["artifacts"].items():
        path = demo / name
        if _sha256(path) != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            raise SystemExit(f"artifact mismatch: {name}")
    for name in ("deployed-receipt-guard.gif", "deployed-receipt-guard.mp4"):
        path = demo / name
        _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height,nb_frames,duration", "-of", "json", str(path)])
        _run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
    with tempfile.TemporaryDirectory(prefix="deployed-demo-verify-") as tmp:
        regenerated = Path(tmp) / "capture.json"
        _run([sys.executable, "scripts/demo_deployed_guard.py", "--output", str(regenerated),
              "--source-commit", source])
        if regenerated.read_bytes() != receipt_path.read_bytes():
            raise SystemExit("capture receipt is not byte-reproducible")
    print(json.dumps({"ok": True, "source_commit": source, "artifacts": sorted(manifest["artifacts"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
