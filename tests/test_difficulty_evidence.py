"""A hard screen cannot be manufactured from missing or mismatched trials."""

import copy
import hashlib
import json

import pytest

from fsbench.difficulty_evidence import PIN_FIELDS, assess_screen


def screen(tmp_path, success=None):
    manifest = {"schema_version": 1, "model": "pinned-test-model", "model_revision": "frozen-revision",
                "inference_sha256": "1" * 64, "environment_sha256": "2" * 64,
                "workload_sha256": "3" * 64, "budget_sha256": "4" * 64,
                "cohort_role": "selection", "task_checksum": "frozen-task", "test_count": 2,
                "planned_seeds": list(range(5)),
                "tracks": {"mini-swe": {"revision": "swe-rev", "config_sha256": "5" * 64},
                           "rusty": {"revision": "rusty-rev", "config_sha256": "6" * 64, "agents": "off"}},
                "runs": []}
    for harness, track in manifest["tracks"].items():
        for seed in manifest["planned_seeds"]:
            job = f"{harness}-{seed}"
            trial = tmp_path / job / "trial"
            (trial / "verifier").mkdir(parents=True)
            reward = float((harness, seed) == success)
            record = {"finished_at": f"2026-10-09T04:00:{seed:02}Z", "trial_id": job,
                      "task_checksum": "frozen-task", "exception_info": None,
                      "verifier_environment_mode": "separate",
                      "verifier_result": {"rewards": {"reward": reward}}}
            statuses = ["passed", "passed" if reward else "failed"]
            ctrf = {"results": {"tests": [{"name": f"test_{i}", "status": s} for i, s in enumerate(statuses)],
                                "summary": {"tests": 2, "passed": statuses.count("passed"), "failed": statuses.count("failed")}}}
            (trial / "result.json").write_text(json.dumps(record))
            (trial / "verifier/reward.txt").write_text(str(reward))
            (trial / "verifier/ctrf.json").write_text(json.dumps(ctrf))
            hashes = {str(p.relative_to(trial)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in trial.rglob("*") if p.is_file()}
            manifest["runs"].append({"harness": harness, "seed": seed, "job": job, "exit_code": 0,
                                     "files_sha256": hashes,
                                     "pins": {**{k: manifest[k] for k in PIN_FIELDS},
                                              "harness_revision": track["revision"],
                                              "harness_config_sha256": track["config_sha256"]}})
    return manifest


def test_zero_successes_are_only_an_observed_screen_not_proof_of_inability(tmp_path):
    report = assess_screen(screen(tmp_path), tmp_path)
    assert report["ok"] and report["status"] == "zero_success_screen"
    assert report["cannot_pass_established"] is False
    assert report["qualification_established"] is False
    for track in report["tracks"].values():
        assert track["attempts"] == 5 and track["successes"] == 0
        assert track["zero_success_95pct_upper_bound_if_independent"] == pytest.approx(0.450719728)
        assert track["failed_probes"] == ["test_1"]


def test_one_success_prevents_a_zero_success_claim_and_tracks_are_not_pooled(tmp_path):
    report = assess_screen(screen(tmp_path, success=("rusty", 3)), tmp_path)
    assert report["status"] == "observed_success"
    assert report["tracks"]["rusty"]["observed_success_rate"] == 0.2
    assert report["tracks"]["mini-swe"]["observed_success_rate"] == 0


@pytest.mark.parametrize("mutation", [
    lambda m: m["runs"].pop(),
    lambda m: m["runs"].append(copy.deepcopy(m["runs"][0])),
    lambda m: m["runs"][1].update(job=m["runs"][0]["job"]),
    lambda m: m["runs"][0].update(job="../outside"),
    lambda m: m["runs"][0].update(exit_code=1),
    lambda m: m["runs"][0].update(exit_code=True),
    lambda m: m["runs"][0]["pins"].update(model="other-model"),
    lambda m: m["runs"][0]["pins"].update(budget_sha256="9" * 64),
    lambda m: m["runs"][0]["pins"].update(harness_revision="other-revision"),
    lambda m: m["runs"][0].update(files_sha256={}),
    lambda m: m["tracks"]["rusty"].update(agents="on"),
    lambda m: m.update(planned_seeds=[0] * 5),
    lambda m: m.update(cohort_role="pooled-selection-reporting"),
    lambda m: m.update(inference_sha256="unfrozen"),
    lambda m: m.update(test_count=3),
    lambda m: m.update(task_checksum="changed-task"),
])
def test_missing_mismatched_or_cherry_picked_evidence_cannot_show_difficulty(tmp_path, mutation):
    manifest = screen(tmp_path)
    mutation(manifest)
    report = assess_screen(manifest, tmp_path)
    assert not report["ok"] and report["status"] == "invalid_evidence"
    assert report["cannot_pass_established"] is False


@pytest.mark.parametrize("exception", ["EnvironmentBuildError", "ProviderError", "AgentTimeoutError"])
def test_exceptions_do_not_become_solver_failures_without_external_audit(tmp_path, exception):
    manifest = screen(tmp_path)
    path = tmp_path / manifest["runs"][0]["job"] / "trial/result.json"
    record = json.loads(path.read_text())
    record["exception_info"] = {"exception_type": exception}
    path.write_text(json.dumps(record))
    manifest["runs"][0]["files_sha256"]["result.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = assess_screen(manifest, tmp_path)
    assert not report["ok"] and "do not count as solver failure" in report["reason"]


def test_changed_raw_receipt_cannot_be_hidden_by_a_summary(tmp_path):
    manifest = screen(tmp_path)
    path = tmp_path / manifest["runs"][0]["job"] / "trial/verifier/reward.txt"
    path.write_text("1")
    assert "changed" in assess_screen(manifest, tmp_path)["reason"]


def test_one_original_trial_cannot_be_copied_to_inflate_attempt_count(tmp_path):
    manifest = screen(tmp_path)
    source = tmp_path / manifest["runs"][0]["job"] / "trial/result.json"
    dest = tmp_path / manifest["runs"][1]["job"] / "trial/result.json"
    dest.write_bytes(source.read_bytes())
    manifest["runs"][1]["files_sha256"]["result.json"] = hashlib.sha256(dest.read_bytes()).hexdigest()
    assert "reused" in assess_screen(manifest, tmp_path)["reason"]


def test_design_only_or_empty_manifest_has_no_difficulty_result(tmp_path):
    for manifest in ({}, {"schema_version": 1, "status": "design_only"}, None):
        assert assess_screen(manifest, tmp_path)["status"] == "invalid_evidence"
