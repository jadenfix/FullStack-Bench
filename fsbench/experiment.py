"""Experiment manifests: what a cohort will run, validated before any model call.

    uv run python -m fsbench.experiment validate manifest.json
    uv run python -m fsbench.experiment plan manifest.json --out plan.json

A manifest pins everything the episodes must share (model, inference settings, one gateway
envelope, tasks, seeds, the public check each task offers) and lists the tracks. Tracks may
differ only in their declared treatment. `rusty_ablation()` builds the 2x2 Rusty ablation:
fixed public verification on/off crossed with careful execution on/off, memory and
delegation off.

Equal access: a task's public check reaches every track the same way, either through the
qualified brief itself (`public_check_source: brief`, the brief is left untouched) or
through one shared instruction template (Harbor's `prompt_template_path`). Either way, the
only difference verification makes is whether Rusty enforces the check before accepting
completion. The hidden grader is never a public check.

Lineage: each task names its authoring template and causal mechanism. A reporting cohort
must declare which mechanisms development has already exposed (`development_mechanisms`)
and the frozen harness it reports on. A task whose mechanism development has seen counts
only as `familiar_family` evidence, never as a new causal mechanism.

Diagnostics: the `diagnostic` role is development-only and is the only role that may carry
privileged hints (an oracle fault location, a larger budget). It is never headline evidence.

Runtime conditions are pinned, not assumed: resource reservation vs hard limit, how many
trials share the host, cache treatment, and run ordering. Unknown metadata is written as
"unknown", never invented.

`plan` writes every episode with its job name, block, order, key slot and Harbor command.
It runs nothing. Admission (task qualification, isolation, image digests) and result
validation are separate steps that consume the plan.
"""

import argparse
import hashlib
import itertools
import random
import json
import re
import shlex
import sys
from pathlib import Path

SCHEMA = "fsb-experiment-v1"
COHORT_ROLES = ("development", "diagnostic", "selection", "reporting")
COMPARISON_KINDS = ("whole_system", "ablation", "transfer")
# What may differ between the two arms of an ablation or transfer comparison; everything else is held.
TREATMENTS = {"rusty": ("execution", "verify"), "mini-swe": ("verify",)}
GENERALIZATION = ("new_mechanism", "familiar_family")
RUNTIME_KEYS = ("cpus_reserved", "cpus_limit", "memory_reserved_mb", "memory_limit_mb", "max_concurrent_trials",
                "cache", "ordering", "order_seed", "key_slots")
HEX64 = re.compile(r"[0-9a-f]{64}")
GIT_SHA = re.compile(r"[0-9a-f]{40}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
ENVELOPE_KEYS = ("calls", "input_tokens", "output_tokens", "wall_seconds")
INFERENCE_KEYS = ("temperature", "top_p", "max_reply", "reasoning_effort")
# Settings a Rusty track pins explicitly, so a binary's changing defaults can't move a cohort.
RUSTY_PINS = ("execution", "memory", "agents", "verify", "allow_destructive")
TEMPLATE = "{{ instruction }}\n\n## Public acceptance check\n\nYou can run this check at any time:\n\n"


def rusty_ablation(binary: str, binary_sha256: str) -> list[dict]:
    """The four Rusty conditions of the verification x careful-execution ablation."""
    return [{"name": f"rusty-{name}", "harness": "rusty", "binary": binary, "binary_sha256": binary_sha256,
             "execution": execution, "memory": "off", "agents": "off", "verify": verify,
             "allow_destructive": False}
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
         "model.revision is required: the provider's model version, or \"unknown\" when the provider does not say")
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
        need(isinstance(m.get("development_mechanisms"), list),
             "a reporting cohort must list development_mechanisms (may be empty)")
        # What the cohort reports on is frozen before any run: the harness, the evaluator (FSB
        # verifier code) and the base images, which carry SimCloud and its evidence collectors.
        need(GIT_SHA.fullmatch(str(m.get("harness_frozen_at", ""))) is not None,
             "a reporting cohort must give harness_frozen_at, the harness's git commit")
        need(GIT_SHA.fullmatch(str(m.get("evaluator_frozen_at", ""))) is not None,
             "a reporting cohort must give evaluator_frozen_at, the FSB commit whose verifiers it uses")
        need(bool(m.get("base_images")), "a reporting cohort must pin base_images by image ID")
    images = m.get("base_images", {})
    need(isinstance(images, dict) and all(isinstance(k, str) and IMAGE_ID.fullmatch(str(v) or "") is not None
                                          for k, v in (images or {}).items()),
         "base_images maps each image name to its sha256 image ID")
    hints = m.get("privileged_hints")
    need(hints in (None, []) or role == "diagnostic",
         "privileged_hints are allowed only in a diagnostic cohort, never in headline evidence")
    runtime = m.get("runtime") or {}
    need(set(runtime) == set(RUNTIME_KEYS), f"runtime must pin exactly {', '.join(RUNTIME_KEYS)}")
    if set(runtime) == set(RUNTIME_KEYS):
        sized = all(isinstance(runtime[k], int) and not isinstance(runtime[k], bool) and runtime[k] > 0 for k in
                    ("cpus_reserved", "cpus_limit", "memory_reserved_mb", "memory_limit_mb", "max_concurrent_trials"))
        need(sized, "runtime resources and concurrency must be positive integers")
        if sized:
            need(runtime["cpus_reserved"] <= runtime["cpus_limit"]
                 and runtime["memory_reserved_mb"] <= runtime["memory_limit_mb"],
                 "a reservation cannot exceed its hard limit")
        need(runtime["cache"] in ("cold", "warm"), "runtime.cache must be cold or warm")
        need(runtime["ordering"] in ("counterbalanced", "randomized"), "runtime.ordering must be counterbalanced or randomized")
        need(isinstance(runtime["order_seed"], int), "runtime.order_seed must be an integer")
        slots = runtime["key_slots"]
        need(isinstance(slots, list) and bool(slots) and all(isinstance(k, int) and k > 0 for k in slots)
             and len(set(slots)) == len(slots), "runtime.key_slots must list distinct positive key numbers")
        need(not sized or not isinstance(slots, list) or runtime["max_concurrent_trials"] <= len(slots),
             "more concurrent trials than keys would make trials share a key and throttle each other")
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
        lineage = t.get("lineage") or {}
        need(isinstance(lineage.get("template"), str) and bool(lineage.get("template"))
             and isinstance(lineage.get("causal_mechanism"), str) and bool(lineage.get("causal_mechanism")),
             f"task {name}: lineage must name its template and causal_mechanism")
        need(isinstance(t.get("reference_needs_destructive", False), bool),
             f"task {name}: reference_needs_destructive must be true or false")
        need(t.get("generalization") in GENERALIZATION,
             f"task {name}: generalization must be one of {', '.join(GENERALIZATION)}")
        if role == "reporting" and t.get("generalization") == "new_mechanism":
            need(lineage.get("causal_mechanism") not in (m.get("development_mechanisms") or []),
                 f"task {name}: development has seen its mechanism, so it can only be familiar_family evidence")
        challenges = t.get("challenges")
        need(challenges is None or (isinstance(challenges, list) and all(isinstance(c, str) and c for c in challenges)
                                    and len(set(challenges)) == len(challenges)),
             f"task {name}: challenges must be a list of distinct names")
        if role == "reporting":
            # The lifecycle contract: every predeclared challenge must be shown to have run
            # (evidence challenges.json status "ran"), and the post-handoff window must be
            # observed (harm.observation.by_phase.post_handoff.observed). A challenge that
            # never ran is not a passed one.
            need(isinstance(challenges, list) and bool(challenges),
                 f"task {name}: a reporting cohort must list the task's predeclared challenges")
            need(t.get("post_handoff_observed_required") is True,
                 f"task {name}: a reporting cohort must require the post-handoff window to be observed")
        check = t.get("public_check")
        need(check is None or (isinstance(check, str) and check.strip() != "" and "{% endraw %}" not in check
                               and "\n" not in check),
             f"task {name}: public_check must be a one-line command or null")
        # How much of the requirements the public check covers. A partial check tests whether
        # the harness keeps working toward the full objective after the check passes.
        need(check is None or t.get("public_check_scope") in ("partial", "complete"),
             f"task {name}: public_check_scope must be partial or complete")
        # Equal access: either the qualified brief already names the check (then the brief
        # is left untouched), or every track gets the same template that appends it.
        need(check is None or t.get("public_check_source") in ("brief", "template"),
             f"task {name}: public_check_source must be brief or template")
        if check and t.get("public_check_source") == "brief" and task_root is not None and isinstance(name, str):
            brief = task_root / name / "instruction.md"
            need(brief.is_file() and check in brief.read_text(),
                 f"task {name}: public_check_source is brief, but its instruction.md does not name {check!r}")
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
            need(isinstance(t.get("allow_destructive"), bool), f"track {name}: allow_destructive must be true or false")
            # With nobody watching, Rusty's guard refuses destructive-classed calls. On a task whose
            # reference solution needs one, a guard-on track measures the guard, not its treatment.
            if t.get("allow_destructive") is False:
                for task in tasks or []:
                    need(task.get("reference_needs_destructive") is not True,
                         f"track {name}: task {task.get('name')} needs a destructive-classed action, which "
                         "Rusty's guard refuses unattended; pin allow_destructive true or leave the task out")
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
            need(isinstance(t.get("verify", False), bool), f"track {name}: verify must be true or false")
            if t.get("verify"):
                # The mechanism-transfer arm: the same fixed check, gated around mini-swe-agent.
                need(all(task.get("public_check") for task in tasks or []),
                     f"track {name}: verify needs a public_check on every task")
                need(isinstance(t.get("gate_rounds"), int) and not isinstance(t.get("gate_rounds"), bool)
                     and t["gate_rounds"] >= 1, f"track {name}: a gated mini-swe track must pin gate_rounds")
    errors.extend(comparison_errors(m))
    return errors


def comparison_errors(m: dict) -> list[str]:
    """Declared comparisons. A reporting cohort preregisters at least one primary comparison in the
    manifest, whose hash is fixed before any run; everything else is exploratory.
    - whole_system: two complete systems (different harnesses), as configured.
    - ablation: two Rusty arms differing in exactly one treatment, everything else held.
    - transfer: the same mechanism added to the baseline harness, everything else held."""
    errors: list[str] = []

    def need(ok: bool, message: str) -> None:
        if not ok:
            errors.append(message)

    tracks = {t.get("name"): t for t in m.get("tracks") or []}
    comparisons = m.get("comparisons", [])
    need(isinstance(comparisons, list), "comparisons must be a list")
    comparisons = comparisons if isinstance(comparisons, list) else []
    names = [c.get("name") for c in comparisons]
    need(len(set(names)) == len(names), "comparison names must be unique")
    if m.get("cohort_role") == "reporting":
        need(any(c.get("primary") is True for c in comparisons),
             "a reporting cohort must preregister at least one primary comparison")
    for c in comparisons:
        label = c.get("name")
        need(isinstance(label, str) and NAME.fullmatch(label or "") is not None, f"comparison {label!r}: name must be a slug")
        need(c.get("kind") in COMPARISON_KINDS, f"comparison {label}: kind must be one of {', '.join(COMPARISON_KINDS)}")
        need(isinstance(c.get("primary"), bool), f"comparison {label}: primary must be true or false")
        a, b = tracks.get(c.get("treatment")), tracks.get(c.get("control"))
        if a is None or b is None or a is b:
            errors.append(f"comparison {label}: treatment and control must be two different tracks")
            continue
        if c.get("kind") == "whole_system":
            need(a.get("harness") != b.get("harness"), f"comparison {label}: whole_system compares different harnesses")
            continue
        harness = "rusty" if c.get("kind") == "ablation" else "mini-swe"
        need(a.get("harness") == b.get("harness") == harness,
             f"comparison {label}: {c.get('kind')} compares two {harness} tracks")
        held = {k for k in set(a) | set(b) if k != "name"} - set(TREATMENTS.get(harness, ()))
        changed = [k for k in TREATMENTS.get(harness, ()) if a.get(k, False) != b.get(k, False)]
        if harness == "mini-swe":
            held.discard("gate_rounds")
        need(len(changed) == 1, f"comparison {label}: the arms must differ in exactly one treatment, not {changed}")
        need(all(a.get(k) == b.get(k) for k in held),
             f"comparison {label}: the arms differ outside the treatment: "
             f"{sorted(k for k in held if a.get(k) != b.get(k))}")
    return errors


def episodes(m: dict) -> list[dict]:
    """One entry per (track, task, seed), with a job name unique to this manifest."""
    return [{"episode": f"{m['name']}--{track['name']}--{task['name']}--s{seed}",
             "track": track["name"], "task": task["name"], "seed": seed}
            for track in m["tracks"] for task in m["tasks"] for seed in m["seeds"]]


def key_imbalance(episodes: list[dict], slots: list[int]) -> int:
    """How far tracks are from using every key equally: per track, its most-used key's count
    minus its least-used key's count, summed over tracks."""
    counts: dict[str, list[int]] = {}
    for e in episodes:
        counts.setdefault(e["track"], [0] * len(slots))[slots.index(e["key_slot"])] += 1
    return sum(max(c) - min(c) for c in counts.values())


def schedule(m: dict) -> list[dict]:
    """Episodes in run order. One block per (task, seed) holds every track. Within a block the
    track order rotates (counterbalanced) or is shuffled from `order_seed` (randomized).
    Episodes run in waves of `max_concurrent_trials`; trials in one wave always get different
    keys, and the keys are arranged so that each track uses every key about equally often, so
    no track is tied to a key."""
    runtime = m["runtime"]
    rng = random.Random(runtime["order_seed"])
    track_names = [t["name"] for t in m["tracks"]]
    slots, width = runtime["key_slots"], runtime["max_concurrent_trials"]
    out = []
    wave = 0
    for b, (task, seed) in enumerate((t["name"], s) for s in m["seeds"] for t in m["tasks"]):
        if runtime["ordering"] == "counterbalanced":
            k = b % len(track_names)
            order = track_names[k:] + track_names[:k]
        else:
            order = rng.sample(track_names, len(track_names))
        for start in range(0, len(order), width):
            for j, name in enumerate(order[start:start + width]):
                out.append({"episode": f"{m['name']}--{name}--{task}--s{seed}", "track": name, "task": task,
                            "seed": seed, "block": b, "position": start + j, "wave": wave,
                            "key_slot": slots[(wave + j) % len(slots)]})
            wave += 1
    # Start from a rotation, then rearrange each wave's keys while that lowers the imbalance.
    # Deterministic, and every wave keeps distinct keys.
    waves: dict[int, list[dict]] = {}
    for e in out:
        waves.setdefault(e["wave"], []).append(e)
    best = key_imbalance(out, slots)
    improved = True
    while improved and best:
        improved = False
        for members in waves.values():
            for keys in itertools.permutations(slots, len(members)):
                before = [e["key_slot"] for e in members]
                for e, key in zip(members, keys):
                    e["key_slot"] = key
                if (score := key_imbalance(out, slots)) < best:
                    best, improved = score, True
                else:
                    for e, key in zip(members, before):
                        e["key_slot"] = key
    return out


def plan(m: dict, task_root: Path, template_dir: Path) -> dict:
    """The episodes, their shared instruction templates and their Harbor commands. Runs nothing."""
    errors = validate(m, task_root)
    if errors:
        raise ValueError("manifest is not plannable:\n- " + "\n- ".join(errors))
    template_dir.mkdir(parents=True, exist_ok=True)
    templates = {}
    for task in m["tasks"]:
        if task.get("public_check") and task.get("public_check_source") == "template":
            path = template_dir / f"{task['name']}.j2"
            path.write_text(template_text(task["public_check"]))
            templates[task["name"]] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    tracks = {t["name"]: t for t in m["tracks"]}
    tasks = {t["name"]: t for t in m["tasks"]}
    planned = []
    for ep in schedule(m):
        track, task = tracks[ep["track"]], tasks[ep["task"]]
        template = templates.get(task["name"])
        command = harbor_command(m, track, task, ep["episode"], template["path"] if template else None)
        planned.append({**ep, "template_sha256": template["sha256"] if template else None,
                        "public_check_scope": task.get("public_check_scope"),
                        "challenges": task.get("challenges") or [],
                        "post_handoff_observed_required": task.get("post_handoff_observed_required") is True,
                        "command": command})
    manifest_sha256 = hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
    slots = m["runtime"]["key_slots"]
    key_uses = {name: {str(k): sum(1 for e in planned if e["track"] == name and e["key_slot"] == k) for k in slots}
                for name in tracks}
    return {"schema": "fsb-experiment-plan-v1", "manifest_sha256": manifest_sha256, "cohort_role": m["cohort_role"],
            "headline_eligible": m["cohort_role"] == "reporting", "runtime": m["runtime"],
            "envelope": m["envelope"], "inference": m["inference"], "model": m["model"],
            "comparisons": m.get("comparisons", []), "templates": templates, "key_uses": key_uses, "key_imbalance": key_imbalance(planned, slots),
            "episodes": planned, "executed": False}


def harbor_command(m: dict, track: dict, task: dict, job: str, template: str | None) -> list[str]:
    common = ["--job-name", job, "-n", "1", "-y"]
    shared = ["--ak", f"prompt_template_path={template}"] if template else []
    if track["harness"] == "rusty":
        args = ["harbor", "run", "-p", f"tasks/{task['name']}", "-a", "fsbench.agents.rusty:Rusty",
                "-m", m["model"]["id"], "--ak", f"binary={track['binary']}", "--ak", f"execution={track['execution']}",
                "--ak", f"memory={track['memory']}", "--ak", f"agents={track['agents']}",
                "--ak", f"allow_destructive={json.dumps(track['allow_destructive'])}", *shared]
        if track["verify"]:
            # Harbor parses --ak values as JSON/literals and strips them; a JSON string
            # arrives as exactly this command (`true` would otherwise become a bool).
            args += ["--ak", f"verify={json.dumps(task['public_check'])}"]
        for limit in ("max_requests", "max_budget_tokens", "budget_secs"):
            if track.get(limit) is not None:
                args += ["--ak", f"{limit}={track[limit]}"]
        return args + common
    gate = []
    if track.get("verify"):
        gate = ["--ak", f"verify={json.dumps(task['public_check'])}", "--ak", f"max_rounds={track['gate_rounds']}"]
    agent = "fsbench.agents.gated_mini:GatedMini" if track.get("verify") else "mini-swe-agent"
    return ["harbor", "run", "-p", f"tasks/{task['name']}", "-a", agent, "-m", f"openai/{m['model']['id']}",
            "--ak", f"version={track['version']}", "--ak", f"config_file={track['config_file']}",
            *shared, *gate] + common


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
