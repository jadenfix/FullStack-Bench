"""Practice check: what runs in production is what the repo says.

    python deployed_check.py <deployed dir> [<repo dir>]   (repo defaults to the current directory)

<deployed dir> holds one `<service>.tar.gz` per production service: the source archive of each release
that serves traffic, copied by the task's collect hook from SimCloud's artifact store. For each
archive, find the repo directory it was deployed from (the root whose files match it best: the repo
root or any directory up to three levels down), then require every deployed file to be identical to
the repo's file at that root. Build output and caches are ignored. Exit 0 if every service matches.

Copied into each task's tests/ by scripts/sync_quality.py; a task enables it with a [[check]] in
quality.toml.
"""

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

IGNORE_PARTS = {".git", "__pycache__", "node_modules", ".venv", ".build", ".pytest_cache", ".ruff_cache", ".gocache"}


def _ignored(rel: str) -> bool:
    parts = Path(rel).parts
    return any(p in IGNORE_PARTS for p in parts) or rel.endswith((".pyc", ".pyo"))


def archive_files(path: Path) -> dict[str, str]:
    out = {}
    with tarfile.open(fileobj=io.BytesIO(path.read_bytes()), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = m.name.lstrip("./")
            if m.isfile() and name and not _ignored(name):
                out[name] = hashlib.sha256(tar.extractfile(m).read()).hexdigest()
    return out


def candidate_roots(repo: Path) -> list[Path]:
    roots = [repo]
    for depth in (1, 2, 3):
        roots += [p for p in repo.glob("/".join(["*"] * depth)) if p.is_dir() and not _ignored(str(p.relative_to(repo)))]
    return roots


def compare(files: dict[str, str], root: Path) -> list[str]:
    bad = []
    for rel, digest in files.items():
        p = root / rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            bad.append(rel)
    return bad


def main(argv: list[str]) -> int:
    deployed = Path(argv[1])
    repo = Path(argv[2]) if len(argv) > 2 else Path.cwd()
    archives = sorted(deployed.glob("*.tar.gz"))
    if not archives:
        print(json.dumps({"error": f"no deployed archives in {deployed}"}))
        return 1
    report, ok = {}, True
    for a in archives:
        files = archive_files(a)
        best = min(((compare(files, r), r) for r in candidate_roots(repo)), key=lambda x: len(x[0]))
        mismatched, root = best
        report[a.name.removesuffix(".tar.gz")] = {"root": str(root.relative_to(repo)) or ".", "files": len(files),
                                                   "differs_from_repo": mismatched[:10], "n_differs": len(mismatched)}
        ok = ok and not mismatched
    print(json.dumps(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
