import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("bts", ROOT / "scripts" / "build_task_skills.py")
bts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bts)


def make_task(tmp_path, docs):
    t = tmp_path / "t"
    (t / "tests").mkdir(parents=True)
    (t / "tests" / "drift_manifest.json").write_text("[]")
    (t / "task.toml").write_text(f'[metadata]\nskills = ["simcloud", "tillpoint"]\n' +
                                 (f'docs = "{docs}"\n' if docs else ""))
    return t


@pytest.mark.parametrize("docs", [None, "full"])
def test_full_docs(tmp_path, docs):
    t = make_task(tmp_path, docs)
    bts.build(t)
    out = t / "environment" / "skills"
    assert (out / "simcloud" / "reference" / "cli.md").exists() and (out / "tillpoint" / "SKILL.md").exists()


def test_partial_docs_drop_the_reference(tmp_path):
    t = make_task(tmp_path, "partial")
    bts.build(t)
    skill = (t / "environment" / "skills" / "simcloud" / "SKILL.md").read_text()
    assert not (t / "environment" / "skills" / "simcloud" / "reference").exists()
    assert "reference/" not in skill and "## Getting started" in skill


def test_no_docs(tmp_path):
    t = make_task(tmp_path, "none")
    bts.build(t)
    out = t / "environment" / "skills"
    assert not (out / "simcloud").exists() and (out / "tillpoint").exists()
