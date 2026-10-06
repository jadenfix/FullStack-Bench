import json

import pytest

from fsbench.gates import assess_gate


def write_trial(tmp_path, reward=1.0):
    trial = tmp_path / "trial"
    verifier = trial / "verifier"
    verifier.mkdir(parents=True)
    record = {"finished_at": "2026-10-06T04:00:00Z", "task_checksum": "frozen-task",
              "exception_info": None, "verifier_environment_mode": "separate",
              "verifier_result": {"rewards": {"reward": reward}}}
    (trial / "result.json").write_text(json.dumps(record))
    (verifier / "reward.txt").write_text(str(reward))
    (verifier / "reward.json").write_text(json.dumps({"reward": reward}))
    statuses = ["passed", "passed" if reward else "failed"]
    ctrf = {"results": {"summary": {"tests": 2, "passed": statuses.count("passed"),
                                   "failed": statuses.count("failed"), "skipped": 0},
                        "tests": [{"name": f"outcome-{i}", "status": s} for i, s in enumerate(statuses)]}}
    (verifier / "ctrf.json").write_text(json.dumps(ctrf))
    return trial, record, ctrf


@pytest.mark.parametrize("reward", [0.0, 1.0])
def test_finished_control_has_matching_receipts(tmp_path, reward):
    write_trial(tmp_path, reward)
    result = assess_gate(tmp_path, exit_code=0, expected=reward, expected_test_count=2,
                         expected_verifier_mode="separate")
    assert result["ok"] and result["status"] == "pass"
    assert result["outcome_count"] == 2
    assert len(result["failed"]) == (1 if reward == 0 else 0)


@pytest.mark.parametrize("defect", ["exception", "unfinished", "wrong-mode", "quality-disagrees",
                                    "summary-disagrees", "skipped", "duplicated", "missing"])
def test_invalid_receipt_cannot_pass_a_negative_control(tmp_path, defect):
    trial, record, ctrf = write_trial(tmp_path, 0.0)
    verifier = trial / "verifier"
    if defect == "exception":
        record["exception_info"] = {"exception_type": "EnvironmentBuildError"}
    elif defect == "unfinished":
        record["finished_at"] = None
    elif defect == "wrong-mode":
        record["verifier_environment_mode"] = "same"
    elif defect == "quality-disagrees":
        (verifier / "reward.json").write_text('{"reward": 1}')
    elif defect == "summary-disagrees":
        ctrf["results"]["summary"]["tests"] = 99
    elif defect == "skipped":
        ctrf["results"]["tests"][0]["status"] = "skipped"
    elif defect == "duplicated":
        ctrf["results"]["tests"][1]["name"] = "outcome-0"
    elif defect == "missing":
        (verifier / "reward.txt").unlink()
    (trial / "result.json").write_text(json.dumps(record))
    (verifier / "ctrf.json").write_text(json.dumps(ctrf))
    result = assess_gate(tmp_path, exit_code=0, expected=0.0, expected_test_count=2,
                         expected_verifier_mode="separate")
    assert not result["ok"] and result["status"] == "invalid_run"


def test_reward_does_not_hide_failed_process_or_missing_checks(tmp_path):
    write_trial(tmp_path, 0.0)
    assert not assess_gate(tmp_path, exit_code=1, expected=0.0)["ok"]
    assert not assess_gate(tmp_path, exit_code=0, expected=0.0, expected_test_count=28)["ok"]
    (tmp_path / "unfinished-trial").mkdir()
    assert not assess_gate(tmp_path, exit_code=0, expected=0.0)["ok"]


def test_valid_wrong_reward_is_a_control_failure(tmp_path):
    write_trial(tmp_path, 1.0)
    result = assess_gate(tmp_path, exit_code=0, expected=0.0, expected_test_count=2)
    assert result["status"] == "control_failure" and not result["ok"]


def test_runner_deadline_covers_the_declared_five_hour_window(tmp_path, monkeypatch):
    from scripts import gate_task

    task = tmp_path / "task"
    task.mkdir()
    (task / "task.toml").write_text('[agent]\ntimeout_sec=18000\n[verifier]\ntimeout_sec=1800\n'
                                  'environment_mode="separate"\n[[verifier.collect]]\ntimeout_sec=900\n'
                                  '[environment]\nbuild_timeout_sec=1800\n')
    calls = []

    def timeout(command, **kwargs):
        calls.append(kwargs["timeout"])
        raise gate_task.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(gate_task.subprocess, "run", timeout)
    result = gate_task.run(task, "oracle", "unique-job", tmp_path / "jobs", 1.0, 28)
    assert calls == [24300]
    assert not result["ok"] and result["status"] == "invalid_run"
    assert result["reward"] is None
