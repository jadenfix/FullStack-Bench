import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import failure_mechanisms as fm  # noqa: E402

STAGE_OF = {"test_a": "final_artifact", "test_b": "deployed_at_handoff", "test_no_incidents_caused": "whole_episode",
            "test_r": "recovery"}


def row(views, **extra):
    base = {"status": "eligible_solver_failure", "harness": "rusty", "seed": 0, "views": views,
            "budget": {"exhausted": None}, "harbor_exit": 0, "agent_exit_status": 0}
    return {**base, **extra}


def test_earliest_stage_comes_from_the_verifier_checks_then_the_views():
    views = {"view_final_artifact": 1.0, "view_deployed_at_handoff": 0.0, "view_whole_episode": 0.0, "view_recovery": 0.0}
    c = fm.classify(row(views), ["test_b", "test_no_incidents_caused", "test_r"], STAGE_OF)
    assert c["earliest_stage"] == "deployed_at_handoff" and c["invariants"] == ["test_b"]
    assert c["incident"] is True and c["checks_from"] == "verifier" and c["termination"] == "handoff"
    # Without the verifier's output the recorded views decide, and the incident is read from the whole-episode view.
    c = fm.classify(row(views), None, STAGE_OF)
    assert c["earliest_stage"] == "deployed_at_handoff" and c["invariants"] == [] and c["checks_from"] == "views"
    assert c["incident"] is True
    # Every view passed but the envelope ended it: no stage, the budget.
    passed = {f"view_{s}": 1.0 for s in fm.STAGES}
    c = fm.classify(row(passed, budget={"exhausted": "input_tokens"}), [], STAGE_OF)
    assert c["earliest_stage"] == "none (budget or stop)" and c["termination"] == "budget_exhausted:input_tokens"
    assert c["incident"] is False


def test_failed_checks_are_read_from_the_trial_ctrf(tmp_path):
    trial = tmp_path / "evidence" / "job-1" / "trial-1" / "verifier"
    trial.mkdir(parents=True)
    trial.joinpath("ctrf.json").write_text(json.dumps({"results": {"tests": [
        {"name": "test_outputs.py::test_a", "status": "passed"}, {"name": "test_outputs.py::test_b", "status": "failed"}]}}))
    assert fm.failed_checks(tmp_path, {"job_dir": "/host/jobs/job-1", "trial": "trial-1"}) == ["test_b"]
    assert fm.failed_checks(tmp_path, {"job_dir": "/host/jobs/job-9", "trial": "trial-1"}) is None
    assert fm.failed_checks(tmp_path, {}) is None


def test_render_lists_every_task_and_the_gap():
    result = {"n_failures": 1, "checks_from_verifier": 1,
              "by_task": {"t": {"family": "f", "causal_defect": "d", "failures": 1, "earliest_stage": {"recovery": 1},
                                "first_invariants": {"test_r": 1}, "termination": {"handoff": 1}, "incidents": 0,
                                "by_harness": {"rusty": {"recovery": 1}}}},
              "gap": {"count": 1, "by_harness": {"rusty": 1}, "by_task": {"t": 1}, "by_stage": {"recovery": 1},
                      "attempts": [{"harness": "rusty", "task": "t", "seed": 0, "earliest_stage": "recovery",
                                    "invariants": ["test_r"], "termination": "handoff"}]}}
    text = fm.render(result)
    assert "| t | f | 1 | recovery 1 | test_r 1 | 0 | handoff 1 |" in text
    assert "SafeSuccess failed: 1 attempts" in text and "| rusty | t | 0 | recovery | test_r | handoff |" in text
