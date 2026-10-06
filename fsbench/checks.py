"""Static checks for a candidate task: fast, no Docker. Each failure is a sentence a model can act on.

The Harbor gates (oracle 1, nop 0, wrong solutions 0) run after these pass: scripts/gate_task.py.
"""

import json
import re
import subprocess
import tomllib
from pathlib import Path

import yaml

from .drift import CATALOGUE
from .greplocalize import localise

CANARY = "fullstack-bench canary GUID"
REQUIRED = ["task.toml", "instruction.md", "environment/Dockerfile", "environment/docker-compose.yaml",
            "environment/simcloud/Dockerfile", "environment/simcloud/seed.yaml", "solution/solve.sh",
            "tests/test.sh", "tests/test_outputs.py", "tests/Dockerfile", "tests/drift_manifest.json"]
BRIEF_SECTIONS = ["## Situation", "## Current system", "## Done means", "## Deliverables", "## Change window"]
# Words that tell the agent it is being evaluated; briefs must read as real work.
EVAL_WORDS = re.compile(r"\b(benchmark|verifier|grader|graded|grading|evaluat\w*|test harness)\b", re.I)


def image_pin_errors(config: dict) -> list[str]:
    """Prebuilt solver and verifier images must have an immutable content digest."""
    errors = []
    for label, environment in (
        ("environment", config.get("environment", {})),
        ("verifier.environment", config.get("verifier", {}).get("environment", {})),
    ):
        image = environment.get("docker_image")
        if image is not None and (
            not isinstance(image, str)
            or not re.fullmatch(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}", image)
        ):
            errors.append(f"task.toml [{label}] docker_image must pin an immutable sha256 digest")
    return errors


def static_check(task: Path) -> list[str]:
    errors = [f"missing required file {f}" for f in REQUIRED if not (task / f).exists()]
    if errors:
        return errors
    try:
        meta_doc = tomllib.loads((task / "task.toml").read_text())
        from harbor.models.task.config import TaskConfig
        cfg = TaskConfig.model_validate(meta_doc)
        if cfg.verifier.environment_mode != "separate":
            errors.append("task.toml: [verifier] environment_mode must be \"separate\"")
    except Exception as e:  # noqa
        return [f"task.toml does not validate: {str(e)[:400]}"]
    meta = meta_doc.get("metadata", {})
    errors.extend(image_pin_errors(meta_doc))
    for key in ("causal_path", "hidden_literals", "skills", "layers"):
        if not meta.get(key):
            errors.append(f"task.toml [metadata] needs a non-empty {key}")
    if len(meta.get("layers", [])) < 3:
        errors.append("the task must span at least 3 layers (metadata.layers)")

    brief = (task / "instruction.md").read_text()
    if CANARY in brief:
        errors.append("instruction.md must not contain the canary")
    hits = sorted({m.group(0).lower() for m in EVAL_WORDS.finditer(brief)})
    if hits:
        errors.append(f"instruction.md mentions evaluation ({hits}); describe outcomes as the company's own needs")
    for sec in BRIEF_SECTIONS:
        if sec not in brief:
            errors.append(f"instruction.md is missing the section '{sec}'")
    for word in ("trap", "hint:", "the bug is", "root cause is"):
        if word in brief.lower():
            errors.append(f"instruction.md gives the game away ('{word}')")
    for f in ("environment/Dockerfile", "environment/simcloud/Dockerfile", "solution/solve.sh", "tests/test.sh",
              "tests/test_outputs.py"):
        if CANARY not in (task / f).read_text():
            errors.append(f"{f} is missing the canary comment")

    tests = (task / "tests/test_outputs.py").read_text()
    for name in ("test_no_incidents_caused", "test_evidence_intact"):
        if f"def {name}" not in tests:
            errors.append(f"tests/test_outputs.py must define {name}")
    wrong = sorted((task / "wrong_solutions").glob("*.sh")) if (task / "wrong_solutions").exists() else []
    if len(wrong) < 3:
        errors.append(f"need at least 3 wrong_solutions/*.sh (have {len(wrong)})")

    try:
        drifts = json.loads((task / "tests/drift_manifest.json").read_text())
        bad = [d.get("id") for d in drifts if d.get("id") not in CATALOGUE]
        if bad:
            errors.append(f"unknown drift ids {bad}; choose from {sorted(CATALOGUE)}")
    except (ValueError, AttributeError) as e:
        errors.append(f"tests/drift_manifest.json is not a JSON list of drifts: {e}")

    try:
        compose = yaml.safe_load((task / "environment/docker-compose.yaml").read_text())
        for svc in ("main", "simcloud"):
            if svc not in (compose.get("services") or {}):
                errors.append(f"docker-compose.yaml needs a '{svc}' service")
    except yaml.YAMLError as e:
        errors.append(f"docker-compose.yaml is not valid YAML: {e}")
    try:
        yaml.safe_load((task / "environment/simcloud/seed.yaml").read_text())
    except yaml.YAMLError as e:
        errors.append(f"seed.yaml is not valid YAML: {e}")

    for py in task.rglob("*.py"):
        try:
            compile(py.read_text(), str(py), "exec")
        except SyntaxError as e:
            errors.append(f"{py.relative_to(task)} does not compile: line {e.lineno}: {e.msg}")
    for sh in list(task.glob("solution/*.sh")) + wrong + [task / "tests/test.sh"]:
        r = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        if r.returncode:
            errors.append(f"{sh.relative_to(task)} has a shell syntax error: {r.stderr[:200]}")

    if (task / "environment/repo").exists() and meta.get("causal_path"):
        r = localise(task)
        if r.hidden_literal_hits:
            errors.append(f"hidden literals appear verbatim in code: {r.hidden_literal_hits}")
        if not r.causal_missed:
            errors.append(f"grepping the brief finds the whole causal path ({r.top_k}); make the path less "
                          "greppable or the codebase deeper")
        missing = [f for f in meta["causal_path"] if not (task / "environment/repo" / f).exists()]
        if missing:
            errors.append(f"causal_path files do not exist in environment/repo: {missing}")
    return errors
