"""Fail closed before model calls when workload/operator isolation is unproven.

This checks operator-produced execution receipts, not the trustworthiness of a
container by inference from its network policy or a green functional test suite.
"""

import json
import re
from pathlib import Path


SURFACES = {
    "runtime-service": "simcloud",
    "runtime-build": "simcloud",
    "runtime-job": "simcloud",
    "operator-export": "simcloud",
    "verifier-generator": "verifier",
    "verifier-quality": "verifier",
}
FORBIDDEN_CAPABILITIES = (
    "operator_environment_inherited",
    "private_operator_files_readable",
    "private_operator_directory_writable",
    "operator_evidence_writable",
    "operator_process_environment_readable",
)


def assess_isolation(receipt_path: Path, *, task_digest: str, images: dict[str, str]) -> dict:
    """Require executed negative probes on every submitted-code execution surface.

    A new task or image invalidates the receipt. Missing observations are unknown,
    never denials. These receipts cover the listed capabilities only; they do not
    establish resistance to arbitrary kernel/container exploits.
    """
    result = {"ok": False, "status": "isolation_unproven", "failed": []}

    def held(reason):
        return {**result, "reason": reason}

    if not re.fullmatch(r"[0-9a-f]{64}", task_digest):
        return held("task digest must be immutable")
    if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", images.get(role, ""))
           for role in set(SURFACES.values())):
        return held("all execution images must be pinned by digest")
    try:
        receipt = json.loads(receipt_path.read_text())
        if receipt["schema"] != "execution-boundary-v1" or receipt["task_digest"] != task_digest:
            return held("isolation receipt does not identify the frozen task")
        probes = receipt["probes"]
        if not isinstance(probes, list) or any(not isinstance(probe, dict) for probe in probes):
            return held("isolation probes are malformed")
        names = [probe["surface"] for probe in probes]
        if len(names) != len(set(names)) or set(names) != set(SURFACES):
            return held("every execution surface requires exactly one probe receipt")
        failed = []
        for probe in probes:
            surface = probe["surface"]
            if probe["image"] != images[SURFACES[surface]]:
                return held("isolation probe image differs from its runtime pin")
            if probe["executed"] is not True or type(probe["exit"]) is not int or probe["exit"] != 0:
                return held("isolation probe did not execute successfully")
            if not re.fullmatch(r"[0-9a-f]{64}", probe["probe_sha256"]):
                return held("executed probe lacks a source digest")
            observed = probe["observations"]
            worker, operator = observed["workload_uid"], observed["operator_uid"]
            if type(worker) is not int or type(operator) is not int or min(worker, operator) < 0:
                return held("process identity observations are malformed")
            if worker == 0 or worker == operator:
                failed.append(f"{surface}: workload retains operator privileges")
            for capability in FORBIDDEN_CAPABILITIES:
                value = observed[capability]
                if type(value) is not bool:
                    return held("capability observation must be an explicit boolean")
                if value:
                    failed.append(f"{surface}: {capability}")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return held("executed isolation receipts are missing or malformed")
    return {**result, "ok": not failed, "status": "pass" if not failed else "isolation_failed",
            "failed": failed, "task_digest": task_digest, "surface_count": len(probes)}
