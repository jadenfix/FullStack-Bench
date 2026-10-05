"""Regenerate each task's skill copy (environment/skills/simcloud) from the base skill plus the
drifts listed in the task's tests/drift_manifest.json. Run after changing the base docs.

    uv run python scripts/build_task_skills.py [tasks/<name> ...]
"""

import json
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fsbench.drift import apply  # noqa: E402


def build(task: Path) -> list[str]:
    """SimCloud's skill with the task's drifts, plus any vendor skills listed in metadata.skills, verbatim."""
    manifest = task / "tests" / "drift_manifest.json"
    ids = [d["id"] for d in json.loads(manifest.read_text())] if manifest.exists() else []
    out = task / "environment" / "skills"
    if out.exists():
        shutil.rmtree(out)
    apply(ids, out / "simcloud")
    meta = tomllib.loads((task / "task.toml").read_text()).get("metadata", {})
    for name in meta.get("skills", ["simcloud"]):
        if name != "simcloud":
            shutil.copytree(ROOT / "skills" / name, out / name)
    return ids


def main() -> int:
    tasks = [Path(a) for a in sys.argv[1:]] or sorted(p for p in (ROOT / "tasks").iterdir() if p.is_dir())
    for t in tasks:
        print(f"{t.name}: drifts {build(t)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
