"""Copy the practice/style scorer and a pristine copy of the starting repo into each task's verifier.

    uv run python scripts/sync_quality.py [tasks/<name> ...]

For every task with tests/quality.toml:
- fsbench/quality.py  -> tests/quality.py   (the verifier image can't import fsbench)
- fsbench/deployed_check.py -> tests/deployed_check.py  (deployed code == repo, for a [[check]])
- environment/repo/   -> tests/base-repo/   (what the agent started from; never its own git history)
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".venv", "node_modules")


def sync(task: Path) -> bool:
    tests = task / "tests"
    if not (tests / "quality.toml").exists():
        return False
    shutil.copy(ROOT / "fsbench" / "quality.py", tests / "quality.py")
    shutil.copy(ROOT / "fsbench" / "deployed_check.py", tests / "deployed_check.py")
    base = tests / "base-repo"
    if base.exists():
        shutil.rmtree(base)
    shutil.copytree(task / "environment" / "repo", base, ignore=IGNORE)
    return True


def main() -> int:
    tasks = [Path(a) for a in sys.argv[1:]] or sorted(p for p in (ROOT / "tasks").iterdir() if p.is_dir())
    for t in tasks:
        print(f"{t.name}: {'synced' if sync(t) else 'no quality.toml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
