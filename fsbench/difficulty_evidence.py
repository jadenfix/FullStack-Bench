"""Summarize a precommitted paired screen from original Harbor receipts.

Operator-owned manifests and isolated collectors are prerequisites. This checks
receipt consistency, not manifest authenticity, qualification or universal inability.
It launches no solver and makes no paid calls.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

from fsbench.gates import assess_gate

PIN_FIELDS = ("model", "model_revision", "inference_sha256", "environment_sha256",
              "workload_sha256", "budget_sha256", "cohort_role")
RECEIPT_FILES = ("result.json", "verifier/reward.txt", "verifier/ctrf.json")


def assess_screen(manifest: dict, jobs_root: Path) -> dict:
    """Zero successes describe only this frozen screen, separately by harness.

    No infrastructure exception or missing receipt becomes a solver failure.
    Proven solver-budget failures need the external exhaustion audit; this
    importer does not infer that audit from a timeout or exception name.
    """
    def invalid(reason):
        return {"ok": False, "status": "invalid_evidence", "reason": reason,
                "cannot_pass_established": False}

    try:
        if manifest["schema_version"] != 1:
            return invalid("unsupported screen schema")
        pins = {key: manifest[key] for key in PIN_FIELDS}
        if any(not isinstance(value, str) or not value.strip() for value in pins.values()):
            return invalid("screen pins must be nonempty strings")
        if any(not re.fullmatch(r"[0-9a-f]{64}", pins[key]) for key in PIN_FIELDS if key.endswith("sha256")):
            return invalid("screen digest pins must be SHA-256")
        if pins["cohort_role"] not in {"selection", "reporting"}:
            return invalid("selection and reporting must remain separate")
        checksum, test_count = manifest["task_checksum"], manifest["test_count"]
        if (not isinstance(checksum, str) or not checksum.strip()
                or type(test_count) is not int or test_count < 1):
            return invalid("task checksum and outcome count must be pinned")
        seeds = manifest["planned_seeds"]
        if (not isinstance(seeds, list) or len(seeds) < 5
                or any(type(seed) is not int or seed < 0 for seed in seeds)
                or len(set(seeds)) != len(seeds)):
            return invalid("precommit at least five distinct nonnegative seeds")
        tracks = manifest["tracks"]
        if set(tracks) != {"mini-swe", "rusty"} or tracks["rusty"]["agents"] != "off":
            return invalid("paired screen requires mini-swe and Rusty with agents off")
        for track in tracks.values():
            if (not isinstance(track["revision"], str) or not track["revision"].strip()
                    or not isinstance(track["config_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", track["config_sha256"])):
                return invalid("harness revision and configuration must be pinned")
        wanted = {(harness, seed) for harness in tracks for seed in seeds}
        observed, jobs, receipts = {}, set(), set()
        root = jobs_root.resolve()
        for run in manifest["runs"]:
            harness, seed = run["harness"], run["seed"]
            if not isinstance(harness, str) or type(seed) is not int:
                return invalid("malformed screen slot")
            slot = harness, seed
            if slot not in wanted or slot in observed:
                return invalid("unexpected or duplicated screen slot")
            track = tracks[harness]
            expected_pins = {**pins, "harness_revision": track["revision"],
                             "harness_config_sha256": track["config_sha256"]}
            if run["pins"] != expected_pins:
                return invalid("run differs from frozen model harness or budget pins")
            if not isinstance(run["job"], str) or not run["job"]:
                return invalid("job path is missing")
            job = (root / run["job"]).resolve()
            if not job.is_relative_to(root) or job == root or job in jobs:
                return invalid("job paths must be distinct and remain inside jobs_root")
            jobs.add(job)
            trials = [path for path in job.iterdir() if path.is_dir()]
            if len(trials) != 1:
                return invalid("screen job must contain one original trial")
            trial = trials[0].resolve()
            if not trial.is_relative_to(root):
                return invalid("trial escapes the operator receipt root")
            files = set(RECEIPT_FILES)
            if (trial / "verifier/reward.json").exists():
                files.add("verifier/reward.json")
            if set(run["files_sha256"]) != files:
                return invalid("raw receipt digest manifest is incomplete")
            for name in files:
                path = (trial / name).resolve()
                if not path.is_relative_to(trial):
                    return invalid("raw receipt path escapes the trial")
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != run["files_sha256"][name]:
                    return invalid("raw receipt changed after screen collection")
            result_digest = run["files_sha256"]["result.json"]
            if result_digest in receipts:
                return invalid("one trial cannot be reused as another independent attempt")
            receipts.add(result_digest)
            if type(run["exit_code"]) is not int:
                return invalid("runner exit code must be observed")
            gate = assess_gate(job, exit_code=run["exit_code"], expected=1.0,
                               expected_test_count=test_count, expected_verifier_mode="separate")
            if gate["status"] == "invalid_run":
                return invalid(f"{harness}/{seed}: {gate['reason']}; do not count as solver failure")
            if gate["task_checksum"] != checksum:
                return invalid("trial uses a different task revision")
            observed[slot] = gate
        if set(observed) != wanted:
            return invalid("precommitted attempts are missing; no success-only or failure-only subset")
        summary = {}
        for harness in tracks:
            rows = [observed[harness, seed] for seed in seeds]
            successes = sum(row["reward"] == 1.0 for row in rows)
            summary[harness] = {
                "attempts": len(rows), "successes": successes,
                "observed_success_rate": successes / len(rows),
                "failed_probes": sorted({probe for row in rows for probe in row["failed"]}),
                # Exact one-sided binomial bound is meaningful only if attempts
                # are independent; correlated seeds and selection weaken inference.
                "zero_success_95pct_upper_bound_if_independent":
                    1 - 0.05 ** (1 / len(rows)) if successes == 0 else None,
            }
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return invalid("screen manifest or raw trial receipts are missing or malformed")
    zero = all(track["successes"] == 0 for track in summary.values())
    return {"ok": True, "status": "zero_success_screen" if zero else "observed_success",
            "cannot_pass_established": False, "task_checksum": checksum,
            "pins": pins, "tracks": summary,
            "qualification_established": False,
            "interpretation": "Observed results for the frozen screen only; not proof of inability or a reporting-cohort success rate."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--jobs-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text())
    except (OSError, ValueError):
        print(json.dumps({"ok": False, "status": "invalid_evidence", "reason": "screen manifest is missing or malformed"}))
        return 1
    report = assess_screen(manifest, args.jobs_root)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
