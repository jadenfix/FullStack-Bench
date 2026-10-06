"""Validate finished Harbor control runs before comparing their binary reward."""

import json
from pathlib import Path


def assess_gate(job_dir: Path, *, exit_code: int | None, expected: float,
                expected_test_count: int | None = None,
                expected_verifier_mode: str | None = None,
                solver_budget_exhausted: bool = False) -> dict:
    """Controls reject all exceptions. Cohorts may supply externally proven exhaustion.

    The exception allowance validates the original functional receipts, without rewriting
    them. The caller must score a proven exhausted solver as a failure, even if its final
    artifact passes. Provider and operator errors never qualify for this allowance.
    """
    result = {"status": "invalid_run", "ok": False, "reward": None, "failed": []}

    def invalid(reason):
        return {**result, "reason": reason}

    if exit_code != 0:
        return invalid("Harbor did not exit successfully")
    trials = [p for p in job_dir.iterdir() if p.is_dir()] if job_dir.exists() else []
    if len(trials) != 1:
        return invalid("expected exactly one finished trial")
    trial = trials[0]
    result["trial"] = trial.name
    try:
        record = json.loads((trial / "result.json").read_text())
        exception = record.get("exception_info")
        allowed_budget_failure = solver_budget_exhausted and isinstance(exception, dict) and exception.get('exception_type') in {'AgentTimeoutError', 'NonZeroAgentExitCodeError'}
        if (exception or (trial / "exception.txt").exists()) and not allowed_budget_failure:
            return invalid("trial contains an exception")
        if not record.get("finished_at") or not record.get("task_checksum"):
            return invalid("trial lacks completion or task identity")
        if expected_verifier_mode and record.get("verifier_environment_mode") != expected_verifier_mode:
            return invalid("verifier environment mode differs from the task pin")
        rewards = record["verifier_result"]["rewards"]
        reward = float((trial / "verifier/reward.txt").read_text().strip())
        if reward not in (0.0, 1.0) or float(rewards["reward"]) != reward:
            return invalid("binary reward receipts disagree")
        quality_reward = trial / "verifier/reward.json"
        if quality_reward.exists() and float(json.loads(quality_reward.read_text())["reward"]) != reward:
            return invalid("quality reward receipt disagrees")
        ctrf = json.loads((trial / "verifier/ctrf.json").read_text())["results"]
        tests, summary = ctrf["tests"], ctrf["summary"]
        names = [t["name"] for t in tests]
        statuses = [t["status"] for t in tests]
        if not tests or len(names) != len(set(names)):
            return invalid("verifier checks are missing or duplicated")
        if any(s not in ("passed", "failed") for s in statuses):
            return invalid("verifier checks were skipped or did not finish")
        if summary["tests"] != len(tests) or summary["passed"] != statuses.count("passed") or summary["failed"] != statuses.count("failed"):
            return invalid("verifier summary disagrees with its checks")
        if any(summary.get(k, 0) for k in ("skipped", "pending", "other")):
            return invalid("verifier summary contains unfinished checks")
        if expected_test_count is not None and len(tests) != expected_test_count:
            return invalid("verifier did not run the pinned number of outcome checks")
        if reward != float(all(s == "passed" for s in statuses)):
            return invalid("binary reward disagrees with outcome checks")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return invalid("required trial or verifier receipts are missing or malformed")
    result.update(reward=reward, task_checksum=record["task_checksum"],
                  outcome_count=len(tests), failed=[t["name"] for t in tests if t["status"] == "failed"],
                  status="pass" if reward == expected else "control_failure", ok=reward == expected)
    return result
