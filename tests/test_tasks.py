"""Structural checks for every task in tasks/. Harbor runs (oracle/nop/wrong solutions) are
scripts/gate_task.py; these are the fast checks that run in CI."""

import filecmp
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from fsbench.drift import CATALOGUE

ROOT = Path(__file__).resolve().parent.parent
# A task still under construction carries BUILD_NOTES.md (open platform work); it joins once that is resolved.
TASKS = sorted(p for p in (ROOT / "tasks").iterdir() if p.is_dir() and not (p / "BUILD_NOTES.md").exists())
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
    from fsbench.checks import image_pin_errors
    assert not image_pin_errors(tomllib.loads((task / "task.toml").read_text()))


def test_solution_heredocs_compile(task):
    from fsbench.checks import heredoc_python_errors
    for sh in [task / "solution/solve.sh", *sorted((task / "wrong_solutions").glob("*.sh"))]:
        assert not heredoc_python_errors(sh.read_text()), sh


def test_canary_everywhere_but_the_instruction(task):
    assert CANARY not in (task / "instruction.md").read_text()
    for f in ("environment/Dockerfile", "environment/simcloud/Dockerfile", "solution/solve.sh", "tests/test.sh",
              "tests/test_outputs.py"):
        assert CANARY in (task / f).read_text(), f


def test_skill_copy_matches_manifest(task, tmp_path):
    import importlib.util
    manifest = json.loads((task / "tests" / "drift_manifest.json").read_text())
    assert {d["id"] for d in manifest} <= set(CATALOGUE)
    spec = importlib.util.spec_from_file_location("bts", ROOT / "scripts" / "build_task_skills.py")
    bts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bts)
    bts.build(task, tmp_path / "skills")

    def diffs(c):
        return c.diff_files + c.left_only + c.right_only + [d for sub in c.subdirs.values() for d in diffs(sub)]
    assert not diffs(filecmp.dircmp(tmp_path / "skills", task / "environment" / "skills")), \
        "stale skill copy: run scripts/build_task_skills.py"

def test_repo_unit_tests_pass(task):
    repo = task / "environment" / "repo"
    if not (repo / "tests").exists():
        pytest.skip("no repo tests")
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=repo, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]


def test_grep_only_localiser_fails(task):
    from fsbench.greplocalize import localise
    r = localise(task)
    meta = tomllib.loads((task / "task.toml").read_text())["metadata"]
    assert meta.get("causal_path") and meta.get("hidden_literals"), "declare causal_path and hidden_literals"
    assert not r.hidden_literal_hits, f"hidden literals appear verbatim in code: {r.hidden_literal_hits}"
    assert r.causal_missed, f"grepping the brief finds the whole causal path: {r.top_k}"


def test_quality_files_are_current(task):
    import filecmp
    tests = task / "tests"
    if not (tests / "quality.toml").exists():
        pytest.skip("no practice/style scoring")
    assert filecmp.cmp(ROOT / "fsbench" / "quality.py", tests / "quality.py", shallow=False), \
        "stale tests/quality.py: run scripts/sync_quality.py"
    c = filecmp.dircmp(task / "environment" / "repo", tests / "base-repo", ignore=[".git", "__pycache__"])

    def diffs(c):
        return c.diff_files + c.left_only + c.right_only + [d for sub in c.subdirs.values() for d in diffs(sub)]
    assert not diffs(c), "stale tests/base-repo: run scripts/sync_quality.py"
    from fsbench.quality import load_config
    cfg = load_config(tests / "quality.toml")
    assert cfg.langs and cfg.scope, "quality.toml needs [[lang]] entries and a scope"


def test_every_outcome_check_belongs_to_exactly_one_view(task):
    import re

    names = set(re.findall(r"^def (test_\w+)", (task / "tests" / "test_outputs.py").read_text(), re.M))
    views = json.loads((task / "tests" / "views.json").read_text())
    assert set(views) == {"final_artifact", "deployed_at_handoff", "whole_episode", "recovery"}
    listed = [n for group in views.values() for n in group]
    assert sorted(listed) == sorted(names), set(names) ^ set(listed)
    assert "test_no_incidents_caused" in views["whole_episode"] and "test_challenges_ran" in views["recovery"]
    assert (task / "tests" / "conftest.py").exists() and "views.json" in (task / "tests" / "test.sh").read_text()


def test_reward_json_holds_only_finite_numbers(task, tmp_path):
    """Harbor 0.23 rejects any reward.json value that is not a finite number, so the views
    hook's flat block and test.sh's merge must never put a dict, list, bool-as-string or null
    there. Exercises the hook and the merge block the way a verifier run does."""
    import math
    import os
    import shutil

    work = tmp_path / "tests"
    shutil.copytree(task / "tests", work, ignore=shutil.ignore_patterns("__pycache__"))
    names = [n for group in json.loads((work / "views.json").read_text()).values() for n in group]
    body = "import pytest\n" + "\n".join(
        f"def {n}(): {'pytest.skip(\'x\')' if i == 0 else ('assert False' if i == 1 else 'pass')}"
        for i, n in enumerate(names))
    (work / "test_outputs.py").write_text(body)
    verifier, evidence = tmp_path / "verifier", tmp_path / "evidence"
    verifier.mkdir(), evidence.mkdir()
    (evidence / "evidence.json").write_text(json.dumps({"harm": {"observation": {"complete": True, "by_phase": {
        "post_handoff": {"observed": True}}}}, "audit_chain": {"intact": True}}))
    (evidence / "challenges.json").write_text(json.dumps({"c": {"status": "ran"}}))
    env = {**os.environ, "FSB_VERIFIER_DIR": str(verifier), "FSB_EVIDENCE_DIR": str(evidence)}
    subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "test_outputs.py"],
                   cwd=work, env=env, capture_output=True)
    views = json.loads((verifier / "views.json").read_text())
    assert any(v["passed"] is None for v in views["views"].values())  # the skipped check
    merge = (work / "test.sh").read_text().split("python - \"$outcome\" <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    merge = merge.replace('sys.path.insert(0, "/tests")', f'sys.path.insert(0, {str(work)!r})')
    subprocess.run([sys.executable, "-", "0"], input=merge, text=True, cwd=work, env=env, check=True,
                   capture_output=True)
    reward = json.loads((verifier / "reward.json").read_text())
    assert reward["reward"] == 0.0 and reward["safe_success"] == 0.0 and reward["measurement_eligible"] == 1.0
    for key, value in reward.items():
        assert isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), (key, value)
    assert "views" not in reward and "eligibility" not in reward
    assert {k for k in reward if k.startswith("view_")} < {f"view_{v}" for v in views["views"]}
