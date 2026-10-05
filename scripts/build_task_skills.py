"""Regenerate each task's skill copy (environment/skills/simcloud) from the base skill plus the
drifts listed in the task's tests/drift_manifest.json. Run after changing the base docs.

How much documentation the agent gets is per task, `metadata.docs` in task.toml:
- "full" (default): SKILL.md and the generated reference
- "partial": SKILL.md only (no reference/; the agent reads `--help`, /v1/kinds, /openapi.json)
- "none": no SimCloud skill at all; the platform must be discovered by probing
Vendor skills listed in metadata.skills are copied verbatim either way.

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


DOC_LEVELS = ("full", "partial", "none")


def build(task: Path, out: Path | None = None) -> list[str]:
    """SimCloud's skill with the task's drifts at the task's docs level, plus vendor skills verbatim."""
    manifest = task / "tests" / "drift_manifest.json"
    ids = [d["id"] for d in json.loads(manifest.read_text())] if manifest.exists() else []
    out = out or task / "environment" / "skills"
    if out.exists():
        shutil.rmtree(out)
    meta = tomllib.loads((task / "task.toml").read_text()).get("metadata", {})
    level = meta.get("docs", "full")
    if level not in DOC_LEVELS:
        raise ValueError(f"{task.name}: metadata.docs must be one of {DOC_LEVELS}")
    out.mkdir(parents=True)
    if level != "none":
        apply(ids, out / "simcloud")
    if level == "partial":
        shutil.rmtree(out / "simcloud" / "reference")
        skill = out / "simcloud" / "SKILL.md"
        text = skill.read_text()
        start = text.index("Reference (generated")
        end = text.index("\n## ", start)
        skill.write_text(text[:start] + text[end + 1:])
    (out / ".keep").write_text("")
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
