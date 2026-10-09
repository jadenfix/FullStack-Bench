"""One admission and result-validation path for every reported trial.

Before a model call, `build_manifest` freezes what the attempt is: the task's content digest,
the executed qualification and isolation receipts, the image digests, each harness's build
identity and resolved configuration, the model and its inference settings and budgets, the
required tool capabilities and the cohort the attempt belongs to. `admit` fails closed: a
reporting or selection attempt whose evidence is missing, stale or contradictory is not launched.

After the run, `classify_attempt` writes a terminal record for the attempt, whatever happened to
it, and sorts it into exactly one of:

- `eligible_success`: complete, consistent receipts; every outcome check passed; budget kept;
- `eligible_solver_failure`: complete receipts and the solver did not do it, or exhausted its
  budget (a passing final artifact does not erase a proven budget failure);
- `infrastructure_failure`: a named environment or provider failure the solver did not cause,
  corroborated by the gateway receipt when it is a provider failure;
- `invalid_evidence`: anything else, including an unclassified exception, a missing or
  inconsistent receipt, a stale task identity or a trial count other than one.

Functional outcome, independently observed harm, the agent's own completion claim, the public
check and evidence validity are kept as separate fields so each can be reconstructed; a harm
observation survives whatever happens to the other fields. Three completion events stay apart:
the model proposed completion, the runtime accepted it, and the independent grader accepted the
result. A failure also carries a class: `operator_setup` (the benchmark's own service never
started), `coverage_limitation` (the harness declares it does not support an interface the task
needs; the attempt stays in the results and the cohort summary says the full-benchmark claim
does not hold), `harness` (recorded only when an operator's trace review sets it) or `solver`.
A missing capability is never an infrastructure failure. `summarize` then rejects duplicate
attempts and unplanned slots across a cohort, and `verify_records` recomputes receipt digests.

This validates receipts. It does not establish that a task is solvable, that a cohort is large
enough, or that any harness is better than another.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path

from fsbench.gates import assess_gate
from fsbench.isolation_gate import assess_isolation

SCHEMA = "fsbench-admission-v1"
COHORTS = ("development", "selection", "reporting")
FAILURE_CLASSES = ("operator_setup", "coverage_limitation", "harness", "solver")
RECEIPT_FILES = ("result.json", "verifier/reward.txt", "verifier/ctrf.json", "verifier/reward.json")
HARM_CHECK = "test_no_incidents_caused"
# Harbor exception types that are the environment's or the operator's, never the solver's.
INFRASTRUCTURE_EXCEPTIONS = frozenset({
    "SandboxBuildFailedError", "EnvironmentStartTimeoutError", "HealthcheckError", "AgentSetupTimeoutError",
    "SandboxLikelyOutOfMemoryError", "GKEExecStreamClosedError", "DownloadVerifierDirError", "AddTestsDirError",
})
# Provider failures the solver reports; they count as infrastructure only when the gateway saw them.
PROVIDER_EXCEPTIONS = frozenset({
    "ApiRateLimitError", "ApiInternalServerError", "ApiOverloadedError", "ApiConnectionClosedError",
    "NetworkConnectionError", "ApiResponseStalledError",
})
# The solver ran out of something: time, turns or model budget.
SOLVER_EXCEPTIONS = frozenset({"AgentTimeoutError", "NonZeroAgentExitCodeError", "ContextWindowExceededError",
                               "OutputTokenExceededError", "ApiUsageLimitError"})
GATE_MINIMUMS = {  # executed gates a cohort needs before any attempt in it is admitted
    "development": {"oracle": 0, "nop": 0, "independent": 0},
    "selection": {"oracle": 1, "nop": 1, "independent": 0},
    "reporting": {"oracle": 10, "nop": 3, "independent": 1},
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task_digest(task: Path) -> str:
    """The same content hash Harbor writes to `result.json` as `task_checksum`.

    Harbor hashes a fresh checkout, which has no bytecode caches; a developer's checkout does
    once the task's tests have been run, so those are ignored here. Any other local file that
    is not in the checkout still changes the digest, and admission then refuses the receipt."""
    from dirhash import dirhash

    return dirhash(task, "sha256", ignore=["__pycache__/", "*.pyc"])


# ---- qualification ---------------------------------------------------------------------


def assess_qualification(receipt: Path | list[Path], task_checksum: str, cohort: str) -> dict:
    """Executed gates from `scripts/gate_task.py`, bound to this exact task revision.

    Oracle and nop gates ran on the task itself, so their recorded checksum must match; the
    independent and wrong-solution gates run on copies and are checked by outcome only. Several
    receipts (for example the gates and a later independent-solution run) combine only when
    every gate in all of them binds to the same task revision and base images."""
    result = {"ok": False, "status": "unqualified", "counts": {}, "outcome_checks": None}

    def held(reason):
        return {**result, "reason": reason}

    try:
        receipts = receipt if isinstance(receipt, list) else [receipt]
        entries = [entry for path in receipts for entry in json.loads(path.read_text())]
        if not isinstance(entries, list) or not entries or any(not isinstance(e, dict) for e in entries):
            return held("qualification receipt holds no gates")
        counts, outcome_counts, bases = {"oracle": 0, "nop": 0, "independent": 0, "wrong": 0}, set(), set()
        for entry in entries:
            bases.add(json.dumps(entry.get("base_images"), sort_keys=True))
            label = entry["gate"].split(":")[0]
            if not entry["ok"]:
                return held(f"gate {entry['gate']} did not hold ({entry.get('reason') or entry.get('status')})")
            if label in ("oracle", "nop") and entry["task_checksum"] != task_checksum:
                return held(f"gate {entry['gate']} ran on a different task revision")
            if entry["reward"] != entry["want"]:
                return held(f"gate {entry['gate']} reward disagrees with its expectation")
            counts[label] = counts.get(label, 0) + 1
            outcome_counts.add(int(entry["outcome_count"]))
        if len(outcome_counts) != 1:
            return held("gates disagree on the number of outcome checks")
        if len(bases) != 1:
            return held("gates ran on different base images")
        base_images = json.loads(bases.pop())
        if cohort != "development" and (not isinstance(base_images, dict) or not base_images
                                        or any(not v for v in base_images.values())):
            return held(f"{cohort} needs the base image IDs recorded in every gate")
        minimums = GATE_MINIMUMS[cohort]
        short = [f"{gate} {counts.get(gate, 0)}/{need}" for gate, need in minimums.items() if counts.get(gate, 0) < need]
        if short:
            return held(f"{cohort} needs more executed gates: {', '.join(short)}")
        if cohort != "development" and counts["wrong"] < 1:
            return held(f"{cohort} needs at least one executed wrong-solution gate")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return held("qualification receipt is missing or malformed")
    return {**result, "ok": True, "status": "pass", "counts": counts, "outcome_checks": outcome_counts.pop(),
            "base_images": base_images}


# ---- the manifest ----------------------------------------------------------------------


def build_manifest(*, task: Path, cohort: str, fsb_rev: str, model: dict, harnesses: dict, attempts: list[dict],
                   qualification_receipt: Path | list[Path] | None, isolation_receipt: Path | None, images: dict[str, str],
                   verifier_mode: str = "separate", harm_check: str = HARM_CHECK,
                   runtime: dict | None = None) -> dict:
    """Everything pinned before any model call, with the admission verdict inside it."""
    if cohort not in COHORTS:
        raise ValueError(f"cohort must be one of {COHORTS}, not {cohort!r}")
    digest = task_digest(task)
    receipts = ([p for p in qualification_receipt] if isinstance(qualification_receipt, list)
                else [qualification_receipt] if qualification_receipt else [])
    qualification = ({"receipt": [str(p) for p in receipts] if len(receipts) > 1 else str(receipts[0]),
                      "sha256": [sha256_file(p) for p in receipts] if len(receipts) > 1 else sha256_file(receipts[0]),
                      **assess_qualification(receipts, digest, cohort)}
                     if receipts and all(p.is_file() for p in receipts)
                     else {"receipt": None, "ok": False, "status": "unqualified", "reason": "no executed qualification receipt",
                           "outcome_checks": None})
    isolation = ({"receipt": str(isolation_receipt), "sha256": sha256_file(isolation_receipt),
                  **assess_isolation(isolation_receipt, task_digest=digest, images=images)}
                 if isolation_receipt and isolation_receipt.is_file()
                 else {"receipt": None, "ok": False, "status": "isolation_unproven",
                       "reason": "no executed isolation receipt"})
    manifest = {
        "schema": SCHEMA, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cohort": cohort,
        "fsb_rev": fsb_rev,
        "task": {"name": task.name, "path": str(task), "digest": digest, "verifier_mode": verifier_mode,
                 "outcome_checks": qualification.get("outcome_checks"), "harm_check": harm_check},
        "qualification": qualification, "isolation": isolation, "images": images,
        "harnesses": harnesses, "model": model, "attempts": attempts,
        "runtime": runtime if runtime is not None else task_runtime(task),
    }
    manifest["admission"] = admit(manifest)
    return manifest


def task_runtime(task: Path) -> dict:
    """The resource allocation the task declares; what was actually enforced is recorded per run."""
    import tomllib

    try:
        env = tomllib.loads((task / "task.toml").read_text()).get("environment", {})
    except (OSError, ValueError):
        env = {}
    return {k: env.get(k) for k in ("cpus", "memory_mb", "storage_mb")}


def admit(manifest: dict) -> dict:
    """Fail closed. Development attempts may run unadmitted but are marked as such; selection and
    reporting attempts need every piece of evidence."""
    reasons = []
    cohort = manifest.get("cohort")
    if cohort not in COHORTS:
        reasons.append("cohort must be development, selection or reporting")
    task = manifest.get("task") or {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(task.get("digest", ""))):
        reasons.append("task digest is not frozen")
    if task.get("verifier_mode") != "separate":
        reasons.append("verifier must run in separate mode")
    if not manifest.get("qualification", {}).get("ok"):
        reasons.append(f"qualification: {manifest.get('qualification', {}).get('reason', 'missing')}")
    elif type(task.get("outcome_checks")) is not int or task["outcome_checks"] < 1:
        reasons.append("the number of outcome checks is not pinned")
    if not manifest.get("isolation", {}).get("ok"):
        reasons.append(f"isolation: {manifest.get('isolation', {}).get('reason') or 'probes failed'}")
    if not isinstance(manifest.get("fsb_rev"), str) or not re.fullmatch(r"[0-9a-f]{40}", manifest["fsb_rev"]):
        reasons.append("benchmark revision is not a commit hash")
    harnesses = manifest.get("harnesses") or {}
    if not harnesses:
        reasons.append("no harness identity")
    for name, h in harnesses.items():
        if not any(re.fullmatch(r"[0-9a-f]{64}", str(h.get(k, ""))) for k in ("binary_sha256", "config_sha256")):
            reasons.append(f"harness {name} lacks a build or configuration digest")
        if not isinstance(h.get("version"), str) or h["version"].strip() in ("", "unavailable", "unknown"):
            reasons.append(f"harness {name} lacks a version")
        if not isinstance(h.get("options"), dict):
            reasons.append(f"harness {name} lacks its resolved configuration")
    model = manifest.get("model") or {}
    if not isinstance(model.get("name"), str) or not model["name"].strip():
        reasons.append("model identity is missing")
    if not isinstance(model.get("inference"), dict) or "temperature" not in model["inference"]:
        reasons.append("inference settings are not pinned")
    budgets = model.get("budgets") or {}
    if any(type(budgets.get(k)) is not int or budgets[k] < 1 for k in ("calls", "wall_seconds")):
        reasons.append("call and wall-clock budgets are not pinned")
    if not isinstance(model.get("required_tools"), list):
        reasons.append("required tool capabilities are not listed")
    runtime = manifest.get("runtime") or {}
    if any(type(runtime.get(k)) not in (int, float) or runtime[k] <= 0 for k in ("cpus", "memory_mb")):
        reasons.append("cpu and memory allocation are not pinned")
    attempts = manifest.get("attempts") or []
    ids = [a.get("id") for a in attempts]
    if not attempts or any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        reasons.append("attempts must have distinct ids")
    if any(a.get("harness") not in harnesses for a in attempts):
        reasons.append("every attempt must name a pinned harness")
    admitted = not reasons
    return {"admitted": admitted, "launchable": admitted or cohort == "development", "reasons": reasons,
            "note": None if admitted else ("development attempts run unadmitted and can never be reported"
                                           if cohort == "development" else "not launched")}


# ---- terminal records --------------------------------------------------------------------


def _gateway(receipt: Path | None) -> dict | None:
    if receipt is None or not receipt.is_file():
        return None
    try:
        data = json.loads(receipt.read_text())
        records = data["usage_records"]
        statuses: dict[str, int] = {}
        for r in records:
            key = r["status"] + (f"_{r['http_status']}" if r.get("http_status") not in (None, 200) else "")
            statuses[key] = statuses.get(key, 0) + 1
        return {"receipt": str(receipt), "sha256": sha256_file(receipt), "exhausted": data["exhausted"],
                "admitted_calls": data["calls"], "transport_attempts": len(records),
                "upstream_errors": sum(1 for r in records if r["status"] == "upstream_error"),
                "refunded": sum(1 for r in records if r.get("refunded")),
                "usage_unknown": sum(1 for r in records if not r.get("usage_known")),
                "input_charged": data["input_charged"], "output_charged": data["output_charged"],
                "billed_cost_usd": data.get("billed_cost_usd"), "attempts_by_status": statuses,
                "last_status": records[-1]["status"] if records else None}
    except (OSError, ValueError, TypeError, KeyError):
        return {"receipt": str(receipt), "malformed": True}


def _harm(ctrf_path: Path, harm_check: str) -> dict:
    """Independently observed harm from the verifier's own check, kept whatever else happened."""
    try:
        tests = json.loads(ctrf_path.read_text())["results"]["tests"]
        status = next((t["status"] for t in tests if t["name"].split("::")[-1] == harm_check), None)
    except (OSError, ValueError, TypeError, KeyError):
        status = None
    return {"check": harm_check, "status": status,
            "observed": None if status not in ("passed", "failed") else status == "failed"}


def _views(reward_json: Path) -> dict:
    """The verifier's flat view and eligibility flags (`view_*`, `eligibility_*`, `safe_success`,
    `measurement_eligible`), kept on the record so reports never reread trial directories. A
    missing or malformed file leaves them absent, never zero."""
    try:
        data = json.loads(reward_json.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if k != "reward" and isinstance(v, (int, float))
            and not isinstance(v, bool) and math.isfinite(v)}


def classify_attempt(job_dir: Path, *, exit_code: int | None, manifest: dict, attempt: dict,
                     gateway_receipt: Path | None = None) -> dict:
    """The terminal record of one launched attempt. Every path writes one."""
    record = _classify(job_dir, exit_code=exit_code, manifest=manifest, attempt=attempt,
                       gateway_receipt=gateway_receipt)
    status = record["status"]
    record["completion"]["independent"] = (status == "eligible_success") if status.startswith("eligible") else None
    if status == "infrastructure_failure":
        record["failure_class"] = "operator_setup"
    elif status == "eligible_solver_failure":
        record["failure_class"] = "coverage_limitation" if record["coverage"]["restricted"] else "solver"
    return record


def _classify(job_dir: Path, *, exit_code: int | None, manifest: dict, attempt: dict,
              gateway_receipt: Path | None) -> dict:
    task = manifest["task"]
    record = {"schema": SCHEMA, "attempt": attempt.get("id"), "harness": attempt.get("harness"),
              "seed": attempt.get("seed"), "cohort": manifest.get("cohort"), "job_dir": str(job_dir),
              "harbor_exit": exit_code, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "status": "invalid_evidence", "reason": None, "evidence_valid": False,
              "functional": None, "harm": {"check": task.get("harm_check", HARM_CHECK), "status": None, "observed": None},
              "goal_claimed": None, "public_check_passed": None, "reward": None, "scored_reward": None,
              "completion": {"proposed": None, "accepted": None, "independent": None},
              "coverage": {"restricted": False, "missing": []}, "failure_class": None,
              "exception": None, "task_checksum": None, "files_sha256": {}, "budget": _gateway(gateway_receipt),
              "views": {}}

    def invalid(reason):
        return {**record, "status": "invalid_evidence", "reason": reason}

    if attempt.get("id") not in {a.get("id") for a in manifest.get("attempts", [])}:
        return invalid("attempt is not in the manifest")
    trials = [p for p in job_dir.iterdir() if p.is_dir()] if job_dir.is_dir() else []
    if len(trials) != 1:
        return invalid(f"expected exactly one trial, found {len(trials)}")
    trial = trials[0]
    record["trial"] = trial.name
    for name in RECEIPT_FILES:
        path = trial / name
        if path.is_file():
            record["files_sha256"][name] = sha256_file(path)
    record["harm"] = _harm(trial / "verifier" / "ctrf.json", record["harm"]["check"])
    record["views"] = _views(trial / "verifier" / "reward.json")
    try:
        result = json.loads((trial / "result.json").read_text())
    except (OSError, ValueError):
        return invalid("result.json is missing or malformed")
    record["task_checksum"] = result.get("task_checksum")
    if record["task_checksum"] != task.get("digest"):
        return invalid("trial ran a different task revision than the manifest")
    metadata = (result.get("agent_result") or {}).get("metadata") or {}
    record["goal_claimed"] = metadata.get("goal_status") == "done" if "goal_status" in metadata else None
    # The adapter's own pre-handoff observation of the public check; None means it did not run.
    passed = metadata.get("public_check_passed", metadata.get("public_check"))
    record["public_check_passed"] = bool(passed) if isinstance(passed, bool) else None
    record["public_check_outcome"] = metadata.get("public_check_outcome", "not_run" if passed is None else None)
    proposals = metadata.get("completion_proposals")
    record["completion"] = {
        "proposed": int(proposals) if isinstance(proposals, int) else None,
        "accepted": (bool(metadata["completion_accepted"]) if "completion_accepted" in metadata
                     else record["goal_claimed"]),
        "independent": None}
    missing = list(metadata.get("mcp_dropped") or [])
    record["coverage"] = {"restricted": bool(missing) or metadata.get("coverage") == "restricted", "missing": missing}
    reward_file = trial / "verifier" / "reward.txt"
    try:
        reward = float(reward_file.read_text().strip()) if reward_file.is_file() else None
    except ValueError:
        return invalid("reward.txt is not a number")
    record["reward"] = reward
    budget = record["budget"] or {}
    if budget.get("malformed"):
        return invalid("gateway receipt is malformed")
    exhausted = budget.get("exhausted")
    exception = result.get("exception_info") or {}
    kind = exception.get("exception_type") if isinstance(exception, dict) else None
    if kind or (trial / "exception.txt").is_file():
        record["exception"] = kind or "unknown"
        if kind in INFRASTRUCTURE_EXCEPTIONS:
            return {**record, "status": "infrastructure_failure", "evidence_valid": True,
                    "reason": f"{kind} is an environment failure; the attempt is replaced, its harm observation kept"}
        if kind in PROVIDER_EXCEPTIONS:
            if budget and budget.get("upstream_errors") and budget.get("last_status") == "upstream_error" and not exhausted:
                return {**record, "status": "infrastructure_failure", "evidence_valid": True,
                        "reason": f"{kind} corroborated by {budget['upstream_errors']} refunded provider rejections"}
            return invalid(f"{kind} claimed without gateway corroboration")
        if kind in SOLVER_EXCEPTIONS:
            if reward == 1.0:
                return invalid(f"{kind} with reward 1 is contradictory")
            return {**record, "status": "eligible_solver_failure", "evidence_valid": True, "functional": False,
                    "scored_reward": 0.0, "reason": f"{kind}: the solver ran out of its budget"
                    + (f" ({exhausted})" if exhausted else "")}
        return invalid(f"unclassified exception {kind or 'unknown'} is not counted as infrastructure")
    gate = assess_gate(job_dir, exit_code=exit_code, expected=1.0, expected_test_count=task.get("outcome_checks"),
                       expected_verifier_mode=task.get("verifier_mode"))
    if gate["status"] == "invalid_run":
        return invalid(gate["reason"])
    record.update(evidence_valid=True, reward=gate["reward"], functional=gate["reward"] == 1.0,
                  failed_checks=gate["failed"])
    if exhausted:
        return {**record, "status": "eligible_solver_failure", "scored_reward": 0.0,
                "reason": f"model budget exhausted ({exhausted}); a passing final artifact does not erase it"}
    if gate["reward"] == 1.0:
        return {**record, "status": "eligible_success", "scored_reward": 1.0, "reason": "every outcome check passed"}
    return {**record, "status": "eligible_solver_failure", "scored_reward": 0.0,
            "reason": "outcome checks failed: " + ", ".join(gate["failed"])}


def summarize(manifest: dict, records: list[dict]) -> dict:
    """Per-harness counts over the terminal records, after rejecting duplicates and unplanned slots.

    Nothing here is a solve rate: eligible attempts are counted, invalid and infrastructure
    attempts are listed, and the planned attempts that never produced a record are named."""
    planned = {a["id"]: a for a in manifest.get("attempts", [])}
    seen_ids, seen_results, rows = set(), {}, []
    for r in records:
        row = dict(r)
        digest = (r.get("files_sha256") or {}).get("result.json")
        if r.get("attempt") in seen_ids:
            row.update(status="invalid_evidence", reason="duplicate attempt id")
        elif r.get("attempt") not in planned:
            row.update(status="invalid_evidence", reason="attempt was not planned in the manifest")
        elif digest and digest in seen_results:
            row.update(status="invalid_evidence", reason=f"same trial as attempt {seen_results[digest]}")
            rows[[x["attempt"] for x in rows].index(seen_results[digest])].update(
                status="invalid_evidence", reason=f"same trial as attempt {r.get('attempt')}")
        seen_ids.add(r.get("attempt"))
        if digest:
            seen_results.setdefault(digest, r.get("attempt"))
        rows.append(row)
    by_harness = {}
    for name in manifest.get("harnesses", {}):
        mine = [x for x in rows if planned.get(x.get("attempt"), {}).get("harness") == name]
        eligible = [x for x in mine if x["status"].startswith("eligible")]
        by_harness[name] = {
            "planned": sum(1 for a in planned.values() if a["harness"] == name),
            "recorded": len(mine), "eligible": len(eligible),
            "eligible_success": sum(1 for x in eligible if x["status"] == "eligible_success"),
            "eligible_solver_failure": sum(1 for x in eligible if x["status"] == "eligible_solver_failure"),
            "infrastructure_failure": sum(1 for x in mine if x["status"] == "infrastructure_failure"),
            "invalid_evidence": sum(1 for x in mine if x["status"] == "invalid_evidence"),
            "harm_observed": sum(1 for x in mine if (x.get("harm") or {}).get("observed") is True),
            "harm_unobserved": sum(1 for x in mine if (x.get("harm") or {}).get("observed") is None),
            "goal_claimed_without_success": sum(1 for x in eligible if x.get("goal_claimed") and x["status"] != "eligible_success"),
            "missing": sorted(set(a for a, p in planned.items() if p["harness"] == name) - {x.get("attempt") for x in mine}),
            # the three completion events, each against all recorded attempts and against eligible ones
            "completion_proposed": sum(1 for x in mine if (x.get("completion") or {}).get("proposed")),
            "completion_accepted": sum(1 for x in mine if (x.get("completion") or {}).get("accepted")),
            "accepted_and_independent_success": sum(1 for x in eligible if (x.get("completion") or {}).get("accepted")
                                                    and x["status"] == "eligible_success"),
            "accepted_without_success": sum(1 for x in eligible if (x.get("completion") or {}).get("accepted")
                                            and x["status"] != "eligible_success"),
            "success_without_acceptance": sum(1 for x in eligible if x["status"] == "eligible_success"
                                              and not (x.get("completion") or {}).get("accepted")),
            "coverage_limited": sum(1 for x in mine if (x.get("coverage") or {}).get("restricted")),
            "failure_classes": {c: sum(1 for x in mine if x.get("failure_class") == c) for c in FAILURE_CLASSES},
        }
    limited = sum(t["coverage_limited"] for t in by_harness.values())
    return {"cohort": manifest.get("cohort"), "admitted": manifest.get("admission", {}).get("admitted", False),
            "reportable": bool(manifest.get("admission", {}).get("admitted")) and manifest.get("cohort") == "reporting",
            "full_benchmark_claim": limited == 0,
            "coverage_note": None if limited == 0 else (f"{limited} attempt(s) ran with a harness that declared an "
                                                        "interface unsupported; report the compatible subset as such"),
            "tracks": by_harness, "records": rows}


def verify_records(records: list[dict]) -> list[str]:
    """Receipts that changed after their terminal record was written."""
    problems = []
    for r in records:
        trial = Path(r.get("job_dir", "")) / (r.get("trial") or "")
        for name, digest in (r.get("files_sha256") or {}).items():
            path = trial / name
            if not path.is_file() or sha256_file(path) != digest:
                problems.append(f"{r.get('attempt')}: {name} changed or vanished after collection")
        budget = r.get("budget") or {}
        if budget.get("receipt") and budget.get("sha256"):
            path = Path(budget["receipt"])
            if not path.is_file() or sha256_file(path) != budget["sha256"]:
                problems.append(f"{r.get('attempt')}: gateway receipt changed or vanished after collection")
    return problems


# ---- bridging an experiment plan -------------------------------------------------------------


def from_tracks(tracks: list[dict], episodes: list[dict]) -> tuple[dict, list[dict]]:
    """The `harnesses` and `attempts` a manifest needs, from an experiment plan's tracks and episodes.

    A track is one pinned harness configuration (`name`, `harness`, `version`, a `binary_sha256`
    or `config_sha256`, and its pins); an episode is `{episode, track, task, seed}`. Track names
    become the manifest's harness keys, so one Rusty ablation cell is one harness entry, and
    episode names become attempt ids. Nothing here validates the plan itself."""
    identity = {"name", "harness", "version", "binary_sha256", "config_sha256"}
    harnesses = {}
    for t in tracks:
        name = t["name"]
        if name in harnesses:
            raise ValueError(f"track {name!r} appears twice")
        entry = {"harness": t.get("harness"), "version": t.get("version"),
                 "options": {k: v for k, v in t.items() if k not in identity}}
        for key in ("binary_sha256", "config_sha256"):
            if t.get(key):
                entry[key] = t[key]
        harnesses[name] = entry
    attempts = [{"id": e["episode"], "harness": e["track"], "seed": e.get("seed"), "task": e.get("task")}
                for e in episodes]
    return harnesses, attempts
