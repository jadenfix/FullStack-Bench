"""Experiment manifests: what a cohort will run, validated before any model call.

    uv run python -m fsbench.experiment validate manifest.json
    uv run python -m fsbench.experiment plan manifest.json --out plan.json

A manifest pins everything the episodes must share (model, inference settings, one gateway
envelope, tasks, seeds, the public check each task offers) and lists the tracks. Tracks may
differ only in their declared treatment. `rusty_ablation()` builds the 2x2 Rusty ablation:
fixed public verification on/off crossed with careful execution on/off, memory and
delegation off.

Equal access: a task's public check reaches every track through the same instruction
template (Harbor's `prompt_template_path`), so the only difference verification makes is
whether Rusty enforces the check before accepting completion. The hidden grader is never a
public check.

`plan` writes every episode with its job name, gateway envelope and Harbor command. It runs
nothing. Admission (task qualification, isolation, image digests) and result validation are
separate steps that consume the plan.
"""

import argparse
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path

SCHEMA = "fsb-experiment-v1"
COHORT_ROLES = ("development", "selection", "reporting")
HEX64 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
ENVELOPE_KEYS = ("calls", "input_tokens", "output_tokens", "wall_seconds")
INFERENCE_KEYS = ("temperature", "top_p", "max_reply", "reasoning_effort")
# Settings a Rusty track pins explicitly, so a binary's changing defaults can't move a cohort.
RUSTY_PINS = ("execution", "memory", "agents", "verify")
TEMPLATE = "{{ instruction }}\n\n## Public acceptance check\n\nYou can run this check at any time:\n\n"


def rusty_ablation(binary: str, binary_sha256: str) -> list[dict]:
    """The four Rusty conditions of the verification x careful-execution ablation."""
    return [{"name": f"rusty-{name}", "harness": "rusty", "binary": binary, "binary_sha256": binary_sha256,
             "execution": execution, "memory": "off", "agents": "off", "verify": verify}
            for name, execution, verify in (("baseline", "standard", False), ("verify", "standard", True),
                                            ("careful", "careful", False), ("combined", "careful", True))]


def template_text(check: str) -> str:
    """The shared instruction template for one task. The check is literal text to Jinja."""
    return TEMPLATE + "{% raw %}" + check + "{% endraw %}\n"


def validate(m: dict, task_root: Path | None = None) -> list[str]:
    """Every reason this manifest cannot be planned. Empty means plannable, not qualified."""
    errors: list[str] = []

    def need(ok: bool, message: str) -> None:
        if not ok:
            errors.append(message)

    need(m.get("schema") == SCHEMA, f"schema must be {SCHEMA}")
    need(isinstance(m.get("name"), str) and NAME.fullmatch(m.get("name") or "") is not None,
         "name must be a short lowercase slug")
    role = m.get("cohort_role")
    need(role in COHORT_ROLES, f"cohort_role must be one of {', '.join(COHORT_ROLES)}")
    model = m.get("model") or {}
    need(isinstance(model.get("id"), str) and bool(model.get("id")), "model.id is required")
    need(isinstance(model.get("revision"), str) and bool(model.get("revision")),
         "model.revision is required (the provider's model version or the date it was pinned)")
    inference = m.get("inference") or {}
    need(set(inference) == set(INFERENCE_KEYS), f"inference must pin exactly {', '.join(INFERENCE_KEYS)}")
    envelope = m.get("envelope") or {}
    need(set(envelope) == set(ENVELOPE_KEYS) and all(isinstance(envelope[k], int) and envelope[k] > 0
                                                    for k in ENVELOPE_KEYS if k in envelope),
         f"envelope must pin positive integers for exactly {', '.join(ENVELOPE_KEYS)}")
    if role == "reporting":
        # A model used to choose or filter tasks has already seen the selection signal.
        filters = m.get("task_filtering_models")
        need(isinstance(filters, list), "a reporting cohort must list task_filtering_models (may be empty)")
        need(model.get("id") not in (filters or []), "a reporting cohort's model must not have filtered its tasks")
    seeds = m.get("seeds")
    need(isinstance(seeds, list) and seeds and all(isinstance(s, int) and s >= 0 for s in seeds)
         and len(set(seeds)) == len(seeds), "seeds must be a non-empty list of distinct non-negative integers")

    tasks = m.get("tasks")
    need(isinstance(tasks, list) and bool(tasks), "tasks must be a non-empty list")
    names = []
    for t in tasks or []:
        name = t.get("name")
        names.append(name)
        need(isinstance(name, str) and bool(name), "every task needs a name")
        need(isinstance(t.get("checksum"), str) and HEX64.fullmatch(t.get("checksum") or "") is not None,
             f"task {name}: checksum must be a 64-hex task digest")
        check = t.get("public_check")
        need(check is None or (isinstance(check, str) and check.strip() != "" and "{% endraw %}" not in check
                               and "\n" not in check),
             f"task {name}: public_check must be a one-line command or null")
        if task_root is not None and isinstance(name, str):
            need((task_root / name / "task.toml").is_file(), f"task {name}: not found under {task_root}")
    need(len(set(names)) == len(names), "task names must be unique")

    tracks = m.get("tracks")
    need(isinstance(tracks, list) and bool(tracks), "tracks must be a non-empty list")
    track_names = [t.get("name") for t in tracks or []]
    need(len(set(track_names)) == len(track_names), "track names must be unique")
    for t in tracks or []:
        name = t.get("name")
        need(isinstance(name, str) and NAME.fullmatch(name or "") is not None, f"track {name!r}: name must be a slug")
        harness = t.get("harness")
        need(harness in ("rusty", "mini-swe"), f"track {name}: harness must be rusty or mini-swe")
        if harness == "rusty":
            need(HEX64.fullmatch(t.get("binary_sha256") or "") is not None, f"track {name}: binary_sha256 required")
            need(all(k in t for k in RUSTY_PINS), f"track {name}: must pin {', '.join(RUSTY_PINS)}")
            need(t.get("execution") in ("standard", "careful"), f"track {name}: execution must be standard or careful")
            need(t.get("memory") == "off" and t.get("agents") == "off",
                 f"track {name}: memory and agents must be off in this study")
            need(isinstance(t.get("verify"), bool), f"track {name}: verify must be true or false")
            if t.get("verify"):
                need(all(task.get("public_check") for task in tasks or []),
                     f"track {name}: verify needs a public_check on every task")
            # Rusty's own counter must never stop it before the gateway would; otherwise
            # its exhaustion is not provable from the gateway receipt.
            for limit, floor in (("max_requests", envelope.get("calls")),):
                if t.get(limit) is not None and isinstance(floor, int):
                    need(t[limit] >= floor, f"track {name}: {limit} below the gateway's {floor} could bind first")
        elif harness == "mini-swe":
            need(isinstance(t.get("version"), str) and bool(t.get("version")), f"track {name}: version required")
            need(HEX64.fullmatch(t.get("config_sha256") or "") is not None, f"track {name}: config_sha256 required")
            need(isinstance(t.get("config_file"), str) and bool(t.get("config_file")), f"track {name}: config_file required")
    return errors


def episodes(m: dict) -> list[dict]:
    """One entry per (track, task, seed), with a job name unique to this manifest."""
    return [{"episode": f"{m['name']}--{track['name']}--{task['name']}--s{seed}",
             "track": track["name"], "task": task["name"], "seed": seed}
            for track in m["tracks"] for task in m["tasks"] for seed in m["seeds"]]


def plan(m: dict, task_root: Path, template_dir: Path) -> dict:
    """The episodes, their shared instruction templates and their Harbor commands. Runs nothing."""
    errors = validate(m, task_root)
    if errors:
        raise ValueError("manifest is not plannable:\n- " + "\n- ".join(errors))
    template_dir.mkdir(parents=True, exist_ok=True)
    templates = {}
    for task in m["tasks"]:
        if task.get("public_check"):
            path = template_dir / f"{task['name']}.j2"
            path.write_text(template_text(task["public_check"]))
            templates[task["name"]] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    tracks = {t["name"]: t for t in m["tracks"]}
    tasks = {t["name"]: t for t in m["tasks"]}
    planned = []
    for ep in episodes(m):
        track, task = tracks[ep["track"]], tasks[ep["task"]]
        template = templates.get(task["name"])
        command = harbor_command(m, track, task, ep["episode"], template["path"] if template else None)
        planned.append({**ep, "template_sha256": template["sha256"] if template else None, "command": command})
    manifest_sha256 = hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
    return {"schema": "fsb-experiment-plan-v1", "manifest_sha256": manifest_sha256, "cohort_role": m["cohort_role"],
            "envelope": m["envelope"], "inference": m["inference"], "model": m["model"],
            "templates": templates, "episodes": planned, "executed": False}


def harbor_command(m: dict, track: dict, task: dict, job: str, template: str | None) -> list[str]:
    common = ["--job-name", job, "-n", "1", "-y"]
    shared = ["--ak", f"prompt_template_path={template}"] if template else []
    if track["harness"] == "rusty":
        args = ["harbor", "run", "-p", f"tasks/{task['name']}", "-a", "fsbench.agents.rusty:Rusty",
                "-m", m["model"]["id"], "--ak", f"binary={track['binary']}", "--ak", f"execution={track['execution']}",
                "--ak", f"memory={track['memory']}", "--ak", f"agents={track['agents']}", *shared]
        if track["verify"]:
            # Harbor parses --ak values as JSON/literals and strips them; a JSON string
            # arrives as exactly this command (`true` would otherwise become a bool).
            args += ["--ak", f"verify={json.dumps(task['public_check'])}"]
        for limit in ("max_requests", "max_budget_tokens", "budget_secs"):
            if track.get(limit) is not None:
                args += ["--ak", f"{limit}={track[limit]}"]
        return args + common
    return ["harbor", "run", "-p", f"tasks/{task['name']}", "-a", "mini-swe-agent", "-m", f"openai/{m['model']['id']}",
            "--ak", f"version={track['version']}", "--ak", f"config_file={track['config_file']}",
            *shared] + common


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("manifest", type=Path)
    v.add_argument("--tasks", type=Path, default=Path("tasks"))
    p = sub.add_parser("plan")
    p.add_argument("manifest", type=Path)
    p.add_argument("--tasks", type=Path, default=Path("tasks"))
    p.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if args.cmd == "validate":
        errors = validate(manifest, args.tasks)
        print("\n".join(errors) if errors else "plannable (not a qualification or admission decision)")
        return 1 if errors else 0
    result = plan(manifest, args.tasks, args.out.parent / (args.out.stem + "-templates"))
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{len(result['episodes'])} episodes planned in {args.out}; nothing was run")
    for ep in result["episodes"][:3]:
        print("  " + shlex.join(ep["command"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
