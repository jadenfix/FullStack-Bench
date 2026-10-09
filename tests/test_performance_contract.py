"""Counterexamples: fast wrong answers, quadratic repairs and omitted traffic."""

import copy

import pytest

from fsbench.hard_suite import get_case
from fsbench.performance_contract import assess_live_traffic, assess_performance

CONTRACT = get_case("export-join-budget")["performance"]
PINS = {"task_sha256": "a" * 64, "candidate_sha256": "b" * 64,
        "baseline_sha256": "c" * 64, "machine_id": "pinned-cpu-2"}


def receipts():
    expected, candidate, baseline = {}, [], []
    for n in CONTRACT["sizes"]:
        for distribution in ("uniform", "skewed"):
            for r in range(CONTRACT["repetitions"]):
                key = n, distribution, r
                expected[key] = {"input_sha256": "d" * 64, "output_sha256": "e" * 64}
                common = {"size": n, "distribution": distribution, "repeat": r,
                          "task_sha256": PINS["task_sha256"], "machine_id": PINS["machine_id"],
                          **expected[key], "exit_code": 0, "peak_rss_mb": 80.0}
                candidate.append({**common, "artifact_sha256": PINS["candidate_sha256"], "elapsed_ms": n / 10})
                baseline.append({**common, "artifact_sha256": PINS["baseline_sha256"], "elapsed_ms": n * n / 1000})
    return expected, candidate, baseline


def assess(expected, candidate, baseline):
    return assess_performance(CONTRACT, candidate, baseline, expected=expected, **PINS)


def test_complete_correct_linear_work_meets_budget_on_both_distributions():
    result = assess(*receipts())
    assert result["ok"] and result["status"] == "pass"
    assert result["distributions"]["skewed"]["growth"] == [4, 4]


def test_correctness_repair_that_keeps_quadratic_work_fails():
    expected, candidate, baseline = receipts()
    for row in candidate:
        row["elapsed_ms"] = row["size"] ** 2 / 10000
    result = assess(expected, candidate, baseline)
    assert not result["ok"] and result["status"] == "performance_failure"
    assert result["distributions"]["uniform"]["speedup"] >= 3
    assert result["distributions"]["uniform"]["growth"] == [16, 16]


def test_fast_but_wrong_cannot_earn_performance_credit():
    expected, candidate, baseline = receipts()
    candidate[-1]["output_sha256"] = "f" * 64
    candidate[-1]["elapsed_ms"] = 1
    result = assess(expected, candidate, baseline)
    assert result["status"] == "incorrect_output" and not result["ok"]


def test_buggy_starting_program_is_not_a_valid_performance_reference():
    expected, candidate, baseline = receipts()
    baseline[0]["output_sha256"] = "f" * 64
    assert assess(expected, candidate, baseline)["status"] == "incorrect_output"


@pytest.mark.parametrize("mutate", [
    lambda c: c.pop(), lambda c: c.append(copy.deepcopy(c[0])),
    lambda c: c[0].update(elapsed_ms=float("nan")),
    lambda c: c[0].update(elapsed_ms=-1),
    lambda c: c[0].update(peak_rss_mb=float("inf")),
    lambda c: c[0].update(machine_id="faster-machine"),
    lambda c: c[0].update(input_sha256="f" * 64),
    lambda c: c[0].update(artifact_sha256="f" * 64),
    lambda c: c[0].update(exit_code=False),
    lambda c: c[0].update(repeat=False),
])
def test_incomplete_or_unpinned_samples_never_pass(mutate):
    expected, candidate, baseline = receipts()
    mutate(candidate)
    result = assess(expected, candidate, baseline)
    assert result["status"] == "invalid_evidence" and not result["ok"]


def test_memory_and_skew_cannot_be_hidden_by_aggregate_speedup():
    expected, candidate, baseline = receipts()
    candidate[-1]["peak_rss_mb"] = 300
    assert not assess(expected, candidate, baseline)["ok"]
    candidate[-1]["peak_rss_mb"] = 80
    for row in candidate:
        if row["distribution"] == "skewed":
            row["elapsed_ms"] *= row["size"]
    result = assess(expected, candidate, baseline)
    assert result["distributions"]["uniform"]["ok"]
    assert not result["distributions"]["skewed"]["ok"]
    assert not result["ok"]


def traffic():
    arrivals = {str(i): i * 1_000_000 for i in range(2000)}
    rows = [{"request_id": key, "arrival_ns": start, "completed_ns": start + 100_000_000,
             "application_ok": True} for key, start in arrivals.items()]
    return arrivals, rows


def live(arrivals, rows):
    return assess_live_traffic(rows, arrivals_ns=arrivals, p99_ms=700, error_budget=0.001)


def test_open_loop_latency_includes_backlog_and_retries():
    arrivals, rows = traffic()
    assert live(arrivals, rows)["ok"]
    for row in rows[:25]:
        row["completed_ns"] = row["arrival_ns"] + 900_000_000
    result = live(arrivals, rows)
    assert result["p99_ms"] == 900 and not result["ok"]


def test_success_only_traffic_and_missing_arrivals_cannot_pass():
    arrivals, rows = traffic()
    rows.pop()
    assert live(arrivals, rows)["status"] == "invalid_evidence"


def test_fast_terminal_errors_cannot_disappear_from_slo():
    arrivals, rows = traffic()
    for row in rows[:3]:
        row["application_ok"] = False
    result = live(arrivals, rows)
    assert result["p99_ms"] == 100 and result["error_rate"] == 0.0015
    assert not result["ok"]


@pytest.mark.parametrize("mutate", [
    lambda r: r.append(copy.deepcopy(r[0])),
    lambda r: r[0].update(arrival_ns=1),
    lambda r: r[0].update(completed_ns=-1),
    lambda r: r[0].update(application_ok="true"),
    lambda r: r[0].update(completed_ns=True),
])
def test_malformed_live_traffic_fails_closed(mutate):
    arrivals, rows = traffic()
    mutate(rows)
    assert live(arrivals, rows)["status"] == "invalid_evidence"
