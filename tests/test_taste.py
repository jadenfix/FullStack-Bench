"""The taste catalogue's shape, and the rule that tasks only grade verified entries."""

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CAT = yaml.safe_load((ROOT / "taste" / "catalogue.yaml").read_text())
ENTRIES = {e["id"]: e for e in CAT["entries"]}
REQUIRED = {"id", "domain", "decision", "options", "better", "conditions", "sources", "check", "never_grade",
            "verified_by"}


def test_entries_are_well_formed():
    assert len(ENTRIES) == len(CAT["entries"]), "duplicate ids"
    for e in CAT["entries"]:
        missing = REQUIRED - set(e)
        assert not missing, (e.get("id"), missing)
        assert e["better"] in e["options"] and len(e["options"]) >= 2, e["id"]
        assert e["conditions"], e["id"]
        assert all(s["url"].startswith("https://") and s["title"] for s in e["sources"]), e["id"]


def test_option_names_are_neutral():
    loaded = {"secure", "insecure", "modern", "legacy", "recommended", "best", "bad", "good"}
    for e in CAT["entries"]:
        for text in e["options"].values():
            assert not loaded & set(text.lower().replace(",", " ").split()), (e["id"], text)


def test_tasks_only_grade_verified_entries():
    for task in sorted((ROOT / "tasks").iterdir()):
        if not (task / "task.toml").exists():
            continue
        meta = tomllib.loads((task / "task.toml").read_text()).get("metadata", {})
        for tid in meta.get("taste", []):
            assert tid in ENTRIES, (task.name, tid)
            assert ENTRIES[tid]["verified_by"], f"{task.name} grades unverified taste entry {tid}"
