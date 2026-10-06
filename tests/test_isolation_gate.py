import json

import pytest

from fsbench.isolation_gate import FORBIDDEN_CAPABILITIES, SURFACES, assess_isolation


DIGEST = "a" * 64
IMAGES = {"simcloud": "sha256:" + "b" * 64, "verifier": "sha256:" + "c" * 64}


def receipt(tmp_path):
    data = {"schema": "execution-boundary-v1", "task_digest": DIGEST, "probes": [
        {"surface": surface, "image": IMAGES[role], "executed": True, "exit": 0,
         "probe_sha256": "d" * 64, "observations": {
             "workload_uid": 65534, "operator_uid": 10001,
             **dict.fromkeys(FORBIDDEN_CAPABILITIES, False)}}
        for surface, role in SURFACES.items()]}
    path = tmp_path / "boundary.json"
    path.write_text(json.dumps(data))
    return path, data


def test_complete_image_bound_execution_receipt(tmp_path):
    path, _ = receipt(tmp_path)
    assert assess_isolation(path, task_digest=DIGEST, images=IMAGES)["ok"]


@pytest.mark.parametrize("capability", FORBIDDEN_CAPABILITIES)
@pytest.mark.parametrize("surface", SURFACES)
def test_any_operator_capability_blocks_model_calls(tmp_path, surface, capability):
    path, data = receipt(tmp_path)
    next(probe for probe in data["probes"] if probe["surface"] == surface)["observations"][capability] = True
    # A top-level assertion cannot override the actual executed observations.
    data["passed"] = True
    path.write_text(json.dumps(data))
    result = assess_isolation(path, task_digest=DIGEST, images=IMAGES)
    assert not result["ok"] and result["status"] == "isolation_failed"


@pytest.mark.parametrize("defect", ["missing-surface", "duplicate", "old-task", "old-image",
                                    "no-execution", "failed-execution", "no-probe-hash",
                                    "unknown-capability", "string-false", "root", "same-uid"])
def test_incomplete_or_stale_execution_cannot_qualify(tmp_path, defect):
    path, data = receipt(tmp_path)
    probe = data["probes"][0]
    if defect == "missing-surface":
        data["probes"].pop()
    elif defect == "duplicate":
        data["probes"].append(probe)
    elif defect == "old-task":
        data["task_digest"] = "e" * 64
    elif defect == "old-image":
        probe["image"] = "sha256:" + "f" * 64
    elif defect == "no-execution":
        probe["executed"] = False
    elif defect == "failed-execution":
        probe["exit"] = 1
    elif defect == "no-probe-hash":
        probe.pop("probe_sha256")
    elif defect == "unknown-capability":
        probe["observations"].pop(FORBIDDEN_CAPABILITIES[0])
    elif defect == "string-false":
        probe["observations"][FORBIDDEN_CAPABILITIES[0]] = "false"
    elif defect == "root":
        probe["observations"]["workload_uid"] = 0
    elif defect == "same-uid":
        probe["observations"]["workload_uid"] = probe["observations"]["operator_uid"]
    path.write_text(json.dumps(data))
    assert not assess_isolation(path, task_digest=DIGEST, images=IMAGES)["ok"]


def test_missing_receipt_and_mutable_image_hold(tmp_path):
    path, _ = receipt(tmp_path)
    assert not assess_isolation(path, task_digest=DIGEST, images={**IMAGES, "verifier": "latest"})["ok"]
    path.unlink()
    assert not assess_isolation(path, task_digest=DIGEST, images=IMAGES)["ok"]
