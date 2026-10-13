#!/usr/bin/env python3
"""Run the deployed-source integrity check against honest and adversarial fixtures."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "fsbench" / "deployed_check.py"
SOURCE = b"def status():\n    return {'service': 'checkout', 'ready': True}\n"


def _archive(path: Path, members: list[tuple[str, bytes]] = ()) -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    payload = gzip.compress(raw.getvalue(), mtime=0)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _run(deployed: Path, repo: Path) -> dict:
    process = subprocess.run(
        [sys.executable, str(CHECK), str(deployed), str(repo)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"deployment check did not emit JSON: {process.stdout!r}") from error
    return {"exit_code": process.returncode, "report": report}


def generate(source_commit: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="deployed-guard-demo-") as tmp:
        fixture = Path(tmp)
        repo = fixture / "repo"
        service = repo / "services" / "checkout"
        deployed = fixture / "deployed"
        service.mkdir(parents=True)
        deployed.mkdir()
        (service / "app.py").write_bytes(SOURCE)
        archive_sha256 = _archive(deployed / "checkout.tar.gz", [("app.py", SOURCE)])
        honest = _run(deployed, repo)

        shutil.rmtree(repo / "services")
        outside = fixture / "outside-checkout"
        outside.mkdir()
        (outside / "app.py").write_bytes(SOURCE)
        (repo / "services").mkdir()
        (repo / "services" / "checkout").symlink_to(outside, target_is_directory=True)
        symlink = _run(deployed, repo)

        empty_deployed = fixture / "empty-deployed"
        empty_deployed.mkdir()
        empty_sha256 = _archive(empty_deployed / "checkout.tar.gz")
        empty = _run(empty_deployed, repo)

        duplicate_deployed = fixture / "duplicate-deployed"
        duplicate_deployed.mkdir()
        duplicate_sha256 = _archive(
            duplicate_deployed / "checkout.tar.gz",
            [("./app.py", SOURCE), ("app.py", b"print('ambiguous')\n")],
        )
        duplicate = _run(duplicate_deployed, repo)

    if honest["exit_code"] != 0:
        raise RuntimeError("honest nested service did not pass")
    if any(case["exit_code"] == 0 for case in (symlink, empty, duplicate)):
        raise RuntimeError("an adversarial deployment receipt was accepted")
    tree = subprocess.run(
        ["git", "rev-parse", f"{source_commit}^{{tree}}"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "schema_version": 1,
        "source_commit": source_commit,
        "source_tree": tree,
        "scope": "offline fictional deployment receipts; no model, provider, or performance claim",
        "workflow": {
            "honest_nested_service": honest,
            "same_content_external_symlink": symlink,
            "empty_archive": empty,
            "duplicate_normalized_path": duplicate,
        },
        "fixture_sha256": {
            "checkout_source": hashlib.sha256(SOURCE).hexdigest(),
            "honest_archive": archive_sha256,
            "empty_archive": empty_sha256,
            "duplicate_archive": duplicate_sha256,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    source_commit = args.source_commit or subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    receipt = generate(source_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt["workflow"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
