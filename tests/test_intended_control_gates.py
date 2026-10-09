"""A negative control is meaningful only if its intended probe rejects it."""

import importlib.util
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("gate_task", Path(__file__).resolve().parent.parent / "scripts/gate_task.py")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def test_aggregate_zero_does_not_prove_a_shortcut_is_detected():
    receipt = {"ok": True, "reward": 0, "status": "pass", "failed": ["test_no_incidents_caused"]}
    result = GATE.check_intended_failures(receipt, {"test_parity"})
    assert not result["ok"] and result["status"] == "control_failure"
    assert receipt["ok"]  # Never rewrite the raw observation.


def test_named_parameterized_probe_rejects_the_intended_control():
    receipt = {"ok": True, "reward": 0, "failed": ["test_outputs.py::test_parity[skewed]"]}
    assert GATE.check_intended_failures(receipt, {"test_parity"}) == receipt


def test_multiple_intended_probes_must_all_reject():
    receipt = {"ok": True, "reward": 0, "failed": ["test_parity"]}
    assert not GATE.check_intended_failures(receipt, {"test_parity", "test_memory"})["ok"]


def test_invalid_run_cannot_be_promoted_by_named_failure():
    receipt = {"ok": False, "status": "invalid_run", "failed": ["test_parity"]}
    assert GATE.check_intended_failures(receipt, {"test_parity"}) == receipt


def test_existing_unmapped_tasks_retain_their_gate_behavior():
    receipt = {"ok": True, "reward": 0, "failed": ["test_no_incidents_caused"]}
    assert GATE.check_intended_failures(receipt, set()) == receipt
