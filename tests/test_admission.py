import json
from pathlib import Path

import pytest

from fsbench import admission
from fsbench.isolation_gate import FORBIDDEN_CAPABILITIES, SURFACES

IMAGES = {"simcloud": "sha256:" + "b" * 64, "verifier": "sha256:" + "c" * 64}
REV = "0" * 40
MODEL = {"name": "nvidia/nemotron-3-super-120b-a12b", "inference": {"temperature": 0.2, "top_p": 1.0},
         "budgets": {"calls": 250, "wall_seconds": 7200}, "required_tools": ["mcp:simcloud"]}
HARNESSES = {"rusty": {"version": "0.9.0", "binary_sha256": "e" * 64, "options": {"agents": "off", "verify": None}},
             "mini": {"version": "2.4.6", "config_sha256": "f" * 64, "options": {"config": "mswea-compact.yaml"}}}
ATTEMPTS = [{"id": "rusty-s1", "harness": "rusty", "seed": 1}, {"id": "mini-s1", "harness": "mini", "seed": 1}]


@pytest.fixture
def task(tmp_path):
    task = tmp_path / "task"
    (task / "tests").mkdir(parents=True)
    (task / "task.toml").write_text('[verifier]\nenvironment_mode="separate"\n')
    (task / "instruction.md").write_text("do the thing\n")
    return task


def gate_receipt(path, checksum, oracle=10, nop=3, independent=1, wrong=2, outcome_count=3, broken=None):
    entries = []
    for label, count, want in (("oracle", oracle, 1.0), ("nop", nop, 0.0), ("independent", independent, 1.0)):
        for i in range(count):
            entries.append({"gate": label, "want": want, "ok": True, "reward": want, "status": "pass",
                            "task_checksum": checksum if label != "independent" else "copy", "outcome_count": outcome_count})
    for i in range(wrong):
        entries.append({"gate": f"wrong:w{i}", "want": 0.0, "ok": True, "reward": 0.0, "status": "pass",
                        "task_checksum": "copy", "outcome_count": outcome_count})
    if broken:
        broken(entries)
    path.write_text(json.dumps(entries))
    return path


def isolation_receipt(path, digest):
    data = {"schema": "execution-boundary-v1", "task_digest": digest, "probes": [
        {"surface": surface, "image": IMAGES[role], "executed": True, "exit": 0, "probe_sha256": "d" * 64,
         "observations": {"workload_uid": 65534, "operator_uid": 10001, **dict.fromkeys(FORBIDDEN_CAPABILITIES, False)}}
        for surface, role in SURFACES.items()]}
    path.write_text(json.dumps(data))
    return path


def manifest_for(task, tmp_path, cohort="reporting", **overrides):
    digest = admission.task_digest(task)
    kwargs = dict(task=task, cohort=cohort, fsb_rev=REV, model=MODEL, harnesses=HARNESSES, attempts=ATTEMPTS,
                  qualification_receipt=gate_receipt(tmp_path / "gates.json", digest),
                  isolation_receipt=isolation_receipt(tmp_path / "boundary.json", digest), images=IMAGES)
    kwargs.update(overrides)
    return admission.build_manifest(**kwargs)


def write_trial(job: Path, checksum: str, reward=1.0, exception=None, harm="passed", outcome=3, metadata=None):
    trial = job / "task__abc123"
    (trial / "verifier").mkdir(parents=True)
    statuses = [("test_a", "passed"), ("test_b", "passed" if reward else "failed"), (admission.HARM_CHECK, harm)][:outcome]
    passed = sum(s == "passed" for _, s in statuses)
    ctrf = {"results": {"summary": {"tests": len(statuses), "passed": passed, "failed": len(statuses) - passed,
                                    "skipped": 0},
                        "tests": [{"name": f"test_outputs.py::{n}", "status": s} for n, s in statuses]}}
    (trial / "verifier" / "ctrf.json").write_text(json.dumps(ctrf))
    (trial / "verifier" / "reward.txt").write_text(str(reward))
    record = {"finished_at": "2026-10-09T05:00:00Z", "task_checksum": checksum, "verifier_environment_mode": "separate",
              "exception_info": {"exception_type": exception} if exception else None,
              "verifier_result": {"rewards": {"reward": reward}}, "agent_result": {"metadata": metadata or {}}}
    (trial / "result.json").write_text(json.dumps(record))
    return trial


def gateway(path, exhausted=None, records=None):
    records = records if records is not None else [{"status": "completed", "http_status": 200, "usage_known": True}]
    path.write_text(json.dumps({"calls": sum(r["status"] == "completed" for r in records), "input_charged": 10,
                                "output_charged": 5, "exhausted": exhausted, "usage_records": records,
                                "billed_cost_usd": None}))
    return path


# ---- admission before launch -------------------------------------------------------------------


def test_complete_manifest_is_admitted(task, tmp_path):
    m = manifest_for(task, tmp_path)
    assert m["admission"] == {"admitted": True, "launchable": True, "reasons": [], "note": None}
    assert m["task"]["outcome_checks"] == 3 and m["qualification"]["counts"]["oracle"] == 10
    assert m["task"]["digest"] == admission.task_digest(task)


@pytest.mark.parametrize("defect", ["no-qualification", "no-isolation", "short-oracle", "failed-gate", "stale-gate",
                                    "no-wrong", "mutable-image", "no-harness-digest", "no-inference", "no-budget",
                                    "duplicate-attempt", "unknown-harness"])
def test_missing_or_stale_evidence_is_not_admitted(task, tmp_path, defect):
    digest = admission.task_digest(task)
    overrides = {}
    if defect == "no-qualification":
        overrides["qualification_receipt"] = None
    elif defect == "no-isolation":
        overrides["isolation_receipt"] = tmp_path / "absent.json"
    elif defect == "short-oracle":
        overrides["qualification_receipt"] = gate_receipt(tmp_path / "g.json", digest, oracle=9)
    elif defect == "failed-gate":
        overrides["qualification_receipt"] = gate_receipt(tmp_path / "g.json", digest,
                                                          broken=lambda e: e[-1].update(ok=False, status="control_failure"))
    elif defect == "stale-gate":
        overrides["qualification_receipt"] = gate_receipt(tmp_path / "g.json", "older-revision")
    elif defect == "no-wrong":
        overrides["qualification_receipt"] = gate_receipt(tmp_path / "g.json", digest, wrong=0)
    elif defect == "mutable-image":
        overrides["images"] = {**IMAGES, "verifier": "latest"}
    elif defect == "no-harness-digest":
        overrides["harnesses"] = {**HARNESSES, "rusty": {"version": "0.9.0", "options": {}}}
    elif defect == "no-inference":
        overrides["model"] = {**MODEL, "inference": {}}
    elif defect == "no-budget":
        overrides["model"] = {**MODEL, "budgets": {"calls": 250}}
    elif defect == "duplicate-attempt":
        overrides["attempts"] = ATTEMPTS + [ATTEMPTS[0]]
    elif defect == "unknown-harness":
        overrides["attempts"] = ATTEMPTS + [{"id": "x", "harness": "codex", "seed": 1}]
    m = manifest_for(task, tmp_path, **overrides)
    assert not m["admission"]["admitted"] and not m["admission"]["launchable"] and m["admission"]["reasons"]


def test_development_runs_unadmitted_but_is_marked(task, tmp_path):
    m = manifest_for(task, tmp_path, cohort="development", qualification_receipt=None, isolation_receipt=None)
    assert not m["admission"]["admitted"] and m["admission"]["launchable"]
    assert "never be reported" in m["admission"]["note"]
    assert not admission.summarize(m, [])["reportable"]


def test_selection_needs_fewer_gates_than_reporting(task, tmp_path):
    digest = admission.task_digest(task)
    receipt = gate_receipt(tmp_path / "g.json", digest, oracle=1, nop=1, independent=0, wrong=1)
    assert manifest_for(task, tmp_path, cohort="selection", qualification_receipt=receipt)["admission"]["admitted"]
    assert not manifest_for(task, tmp_path, cohort="reporting", qualification_receipt=receipt)["admission"]["admitted"]


def test_editing_the_task_changes_its_digest(task, tmp_path):
    before = admission.task_digest(task)
    (task / "instruction.md").write_text("do the thing, faster\n")
    assert admission.task_digest(task) != before


# ---- terminal records --------------------------------------------------------------------------


def classify(task, tmp_path, m, job="rusty-s1", **trial_kwargs):
    gw = trial_kwargs.pop("gateway", None)
    exit_code = trial_kwargs.pop("exit_code", 0)
    job_dir = tmp_path / "jobs" / job
    write_trial(job_dir, trial_kwargs.pop("checksum", m["task"]["digest"]), **trial_kwargs)
    attempt = next(a for a in m["attempts"] if a["id"] == job)
    return admission.classify_attempt(job_dir, exit_code=exit_code, manifest=m, attempt=attempt, gateway_receipt=gw)


def test_eligible_success_and_failure_keep_fields_separate(task, tmp_path):
    m = manifest_for(task, tmp_path)
    ok = classify(task, tmp_path, m, metadata={"goal_status": "done"}, gateway=gateway(tmp_path / "gw1.json"))
    assert ok["status"] == "eligible_success" and ok["evidence_valid"] and ok["functional"] is True
    assert ok["harm"]["observed"] is False and ok["goal_claimed"] is True and ok["scored_reward"] == 1.0
    assert ok["budget"]["transport_attempts"] == 1 and ok["budget"]["exhausted"] is None
    bad = classify(task, tmp_path, m, job="mini-s1", reward=0.0, metadata={"goal_status": "done"})
    assert bad["status"] == "eligible_solver_failure" and bad["functional"] is False and bad["goal_claimed"] is True
    assert bad["failed_checks"] == ["test_outputs.py::test_b"] and bad["harm"]["observed"] is False


def test_passing_artifact_does_not_erase_budget_exhaustion(task, tmp_path):
    m = manifest_for(task, tmp_path)
    r = classify(task, tmp_path, m, reward=1.0, gateway=gateway(tmp_path / "gw.json", exhausted="calls"))
    assert r["status"] == "eligible_solver_failure" and r["functional"] is True and r["reward"] == 1.0
    assert r["scored_reward"] == 0.0 and "exhausted" in r["reason"]


def test_timeout_with_reward_one_is_contradictory(task, tmp_path):
    m = manifest_for(task, tmp_path)
    r = classify(task, tmp_path, m, reward=1.0, exception="AgentTimeoutError")
    assert r["status"] == "invalid_evidence" and "contradictory" in r["reason"]
    r = classify(task, tmp_path, m, job="mini-s1", reward=0.0, exception="AgentTimeoutError")
    assert r["status"] == "eligible_solver_failure" and r["scored_reward"] == 0.0


def test_contradictory_reward_receipts_are_invalid(task, tmp_path):
    m = manifest_for(task, tmp_path)
    job_dir = tmp_path / "jobs" / "rusty-s1"
    trial = write_trial(job_dir, m["task"]["digest"], reward=1.0)
    ctrf = json.loads((trial / "verifier" / "ctrf.json").read_text())
    ctrf["results"]["tests"][1]["status"] = "failed"  # reward.txt says 1, the checks say otherwise
    (trial / "verifier" / "ctrf.json").write_text(json.dumps(ctrf))
    r = admission.classify_attempt(job_dir, exit_code=0, manifest=m, attempt=m["attempts"][0])
    assert r["status"] == "invalid_evidence" and not r["evidence_valid"]
    assert r["harm"]["observed"] is False  # the harm observation is still reported


def test_stale_task_identity_is_invalid(task, tmp_path):
    m = manifest_for(task, tmp_path)
    r = classify(task, tmp_path, m, checksum="an-older-task")
    assert r["status"] == "invalid_evidence" and "different task revision" in r["reason"]


def test_missing_receipts_and_wrong_trial_count_are_invalid(task, tmp_path):
    m = manifest_for(task, tmp_path)
    job_dir = tmp_path / "jobs" / "rusty-s1"
    r = admission.classify_attempt(job_dir, exit_code=0, manifest=m, attempt=m["attempts"][0])
    assert r["status"] == "invalid_evidence" and "exactly one trial" in r["reason"]
    trial = write_trial(job_dir, m["task"]["digest"])
    (trial / "verifier" / "ctrf.json").unlink()
    r = admission.classify_attempt(job_dir, exit_code=0, manifest=m, attempt=m["attempts"][0])
    assert r["status"] == "invalid_evidence" and r["harm"]["observed"] is None
    (job_dir / "task__second").mkdir()
    r = admission.classify_attempt(job_dir, exit_code=0, manifest=m, attempt=m["attempts"][0])
    assert "found 2" in r["reason"]
    r = classify(task, tmp_path, m, job="mini-s1", outcome=2)  # fewer checks than the pinned count
    assert r["status"] == "invalid_evidence"


def test_infrastructure_failure_keeps_observed_harm(task, tmp_path):
    m = manifest_for(task, tmp_path)
    r = classify(task, tmp_path, m, reward=0.0, exception="SandboxBuildFailedError", harm="failed")
    assert r["status"] == "infrastructure_failure" and r["evidence_valid"] and r["harm"]["observed"] is True
    assert r["functional"] is None


def test_arbitrary_exception_is_not_infrastructure(task, tmp_path):
    m = manifest_for(task, tmp_path)
    r = classify(task, tmp_path, m, reward=0.0, exception="KeyError")
    assert r["status"] == "invalid_evidence" and "not counted as infrastructure" in r["reason"]


def test_provider_failure_needs_gateway_corroboration(task, tmp_path):
    m = manifest_for(task, tmp_path)
    r = classify(task, tmp_path, m, reward=0.0, exception="ApiRateLimitError")
    assert r["status"] == "invalid_evidence"
    rejected = [{"status": "completed", "http_status": 200, "usage_known": True},
                {"status": "upstream_error", "http_status": 429, "refunded": True, "usage_known": False}]
    gw = gateway(tmp_path / "gw.json", records=rejected)
    r = classify(task, tmp_path, m, job="mini-s1", reward=0.0, exception="ApiRateLimitError", gateway=gw)
    assert r["status"] == "infrastructure_failure" and r["budget"]["refunded"] == 1
    assert r["budget"]["transport_attempts"] == 2 and r["budget"]["admitted_calls"] == 1


def test_unplanned_attempt_is_invalid(task, tmp_path):
    m = manifest_for(task, tmp_path)
    job_dir = tmp_path / "jobs" / "extra"
    write_trial(job_dir, m["task"]["digest"])
    r = admission.classify_attempt(job_dir, exit_code=0, manifest=m, attempt={"id": "extra", "harness": "rusty"})
    assert r["status"] == "invalid_evidence" and "not in the manifest" in r["reason"]


# ---- cohort summary and tamper detection ----------------------------------------------------------


def test_duplicate_trials_and_missing_attempts_are_visible(task, tmp_path):
    m = manifest_for(task, tmp_path)
    one = classify(task, tmp_path, m)
    twin = dict(one, attempt="mini-s1", harness="mini")  # the same trial reused as the other track's attempt
    s = admission.summarize(m, [one, twin])
    assert [r["reason"] for r in s["records"]] == ["same trial as attempt mini-s1", "same trial as attempt rusty-s1"]
    assert s["tracks"]["rusty"]["invalid_evidence"] == 1 and s["tracks"]["mini"]["invalid_evidence"] == 1
    s = admission.summarize(m, [one, dict(one)])
    assert s["records"][1]["reason"] == "duplicate attempt id"
    s = admission.summarize(m, [one])
    assert s["tracks"]["rusty"] == {"planned": 1, "recorded": 1, "eligible": 1, "eligible_success": 1,
                                    "eligible_solver_failure": 0, "infrastructure_failure": 0, "invalid_evidence": 0,
                                    "harm_observed": 0, "harm_unobserved": 0, "goal_claimed_without_success": 0,
                                    "missing": []}
    assert s["tracks"]["mini"]["missing"] == ["mini-s1"] and s["reportable"]


def test_tampered_receipts_are_detected(task, tmp_path):
    m = manifest_for(task, tmp_path)
    gw = gateway(tmp_path / "gw.json")
    r = classify(task, tmp_path, m, gateway=gw)
    assert admission.verify_records([r]) == []
    (Path(r["job_dir"]) / r["trial"] / "verifier" / "reward.txt").write_text("1.0 ")
    gw.write_text(gw.read_text() + "\n")
    problems = admission.verify_records([r])
    assert any("reward.txt" in p for p in problems) and any("gateway" in p for p in problems)
