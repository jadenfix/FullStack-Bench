"""Structural checks for every task in tasks/. Harbor runs (oracle/nop/wrong solutions) are
scripts/gate_task.py; these are the fast checks that run in CI."""

import filecmp
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from fsbench.drift import CATALOGUE, apply

ROOT = Path(__file__).resolve().parent.parent
TASKS = sorted(p for p in (ROOT / "tasks").iterdir() if p.is_dir())
CANARY = "fullstack-bench canary GUID"
REQUIRED = ["task.toml", "instruction.md", "environment/Dockerfile", "environment/docker-compose.yaml",
            "environment/simcloud/Dockerfile", "environment/simcloud/seed.yaml", "solution/solve.sh",
            "tests/test.sh", "tests/test_outputs.py", "tests/Dockerfile"]


@pytest.fixture(params=TASKS, ids=[t.name for t in TASKS])
def task(request):
    return request.param


def test_layout(task):
    missing = [f for f in REQUIRED if not (task / f).exists()]
    assert not missing, missing
    assert list((task / "wrong_solutions").glob("*.sh")), "every task needs wrong solutions"


def test_task_toml_validates_with_harbor(task):
    from harbor.models.task.config import TaskConfig
    cfg = TaskConfig.model_validate(tomllib.loads((task / "task.toml").read_text()))
    assert cfg.verifier.environment_mode == "separate"
    assert "docker_image" not in (task / "task.toml").read_text()


def test_canary_everywhere_but_the_instruction(task):
    assert CANARY not in (task / "instruction.md").read_text()
    for f in ("environment/Dockerfile", "environment/simcloud/Dockerfile", "solution/solve.sh", "tests/test.sh",
              "tests/test_outputs.py"):
        assert CANARY in (task / f).read_text(), f


def test_skill_copy_matches_manifest(task, tmp_path):
    manifest = json.loads((task / "tests" / "drift_manifest.json").read_text())
    ids = [d["id"] for d in manifest]
    assert set(ids) <= set(CATALOGUE)
    apply(ids, tmp_path / "simcloud")
    cmp = filecmp.dircmp(tmp_path / "simcloud", task / "environment" / "skills" / "simcloud")

    def diffs(c):
        return c.diff_files + c.left_only + c.right_only + [d for sub in c.subdirs.values() for d in diffs(sub)]
    assert not diffs(cmp), f"stale skill copy: run scripts/build_task_skills.py ({diffs(cmp)})"


def test_repo_unit_tests_pass(task):
    repo = task / "environment" / "repo"
    if not (repo / "tests").exists():
        pytest.skip("no repo tests")
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=repo, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
