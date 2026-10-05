"""Docs drift: every drift applies cleanly, and its truth is discoverable.

PROBES holds one function per catalogue entry. Each one shows, against the
real platform, that an agent reading the drifted docs can recover the truth
from the sources the drift lists.
"""

import sys
import time
from pathlib import Path

import httpx
import pytest

from fsbench.drift import CATALOGUE, SKILL_ROOT, DriftError, apply, manifest
from simcloud.cli import build_parser
from simcloud.mcp_server import TOOLS
from simcloud.router import Router, serve_router, stop_router
from simcloud.runtime import ReleaseRun, Supervisor

APP = Path(__file__).parent / "apps" / "echo_app.py"
E = "/v1/projects/shop/envs/dev"


# ---- applying drifts ---------------------------------------------------------

def test_each_drift_applies_alone_and_all_together(tmp_path):
    for drift_id in CATALOGUE:
        apply([drift_id], tmp_path / drift_id)
    applied = apply(list(CATALOGUE), tmp_path / "all")
    assert len(applied) == len(CATALOGUE)
    for d in applied:
        text = (tmp_path / "all" / d.file).read_text()
        for original, replacement in d.edits:
            assert original not in text and replacement in text


def test_base_docs_untouched_and_errors(tmp_path):
    before = (SKILL_ROOT / "SKILL.md").read_text()
    apply(list(CATALOGUE), tmp_path / "x")
    assert (SKILL_ROOT / "SKILL.md").read_text() == before
    with pytest.raises(DriftError, match="unknown"):
        apply(["no-such-drift"], tmp_path / "y")


def test_manifest_is_for_reviewers(tmp_path):
    m = manifest(apply(["drain-default"], tmp_path / "m"))
    assert m == [{"id": "drain-default", "file": "reference/kinds.md", "wrong": "services drain for 30 s by default",
                  "truth_sources": list(CATALOGUE["drain-default"].truth_sources), "risk": "high"}]


def test_every_drift_has_a_probe_and_valid_risk():
    assert set(PROBES) == set(CATALOGUE)
    assert {d.risk for d in CATALOGUE.values()} <= {"low", "high"}


# ---- discoverability probes ----------------------------------------------------------------

def probe_queue_visibility_field(client, dev, **_):
    schema = client.get("/v1/kinds").json()["kinds"]["queue"]["schema"]["properties"]
    assert "visibility_timeout_seconds" in schema and "visibility_timeout" not in schema
    r = client.put(f"{E}/queue/q", json={"spec": {"visibility_timeout": 60}}, headers=dev)
    assert r.status_code == 400 and "visibility_timeout" in r.text and "extra" in r.text.lower()


def probe_drain_default(client, dev, **_):
    schema = client.get("/v1/kinds").json()["kinds"]["service"]["schema"]["properties"]
    assert schema["drain_seconds"]["default"] == 10
    r = client.put(f"{E}/service/s", json={"spec": {}}, headers=dev)
    assert r.json()["spec"]["drain_seconds"] == 10


def probe_iam_immediate(client, admin, nobody, clock, **_):
    client.put("/admin/v1/faults", json={"faults": [{"type": "iam_propagation", "seconds": 15}]}, headers=admin)
    client.put("/v1/projects/shop/envs/_/policy/r", json={"spec": {"statements": [
        {"effect": "allow", "actions": ["kv:list"], "resources": ["*"]}]}}, headers=admin)
    r = client.put("/v1/projects/shop/envs/_/binding/b", json={"spec": {"principal": "user:nobody",
                                                                      "policies": ["r"]}}, headers=admin)
    assert r.json()["status"]["effective_at"] > clock.now()
    assert client.get(f"{E}/kv", headers=nobody).status_code == 403
    clock.advance(15)
    assert client.get(f"{E}/kv", headers=nobody).status_code == 200


def probe_canary_default_weight(**_):
    sub = next(a for a in build_parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    assert "(default: 10)" in sub.choices["deploy"].format_help()


def probe_throttle_header(client, admin, dev, **_):
    client.put("/admin/v1/faults", json={"faults": [{"type": "throttle", "actions": ["kv:list"], "every": 1,
                                                     "retry_after": 4}]}, headers=admin)
    r = client.get(f"{E}/kv", headers=dev)
    assert r.status_code == 429 and r.headers["Retry-After"] == "4" and "X-RateLimit-Reset" not in r.headers


def _runtime(tmp_path):
    sup = Supervisor(tmp_path, port_range=(28000, 28099))
    router = Router(sup, lambda k: {"r1": 100})
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return sup, serve_router(router, sup, "127.0.0.1", port), port


def _wait(pred, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def probe_readiness_permanent(client, tmp_path, **_):
    defs = client.get("/v1/kinds").json()["kinds"]["service"]["schema"]["$defs"]
    assert defs["Probe"]["properties"]["failure_threshold"]["default"] == 3
    sup, server, port = _runtime(tmp_path)
    key = ("shop", "dev", "api")
    try:
        sup.rollout(key, ReleaseRun("r1", str(APP.parent), [sys.executable, str(APP)], {}, probe_interval=0.1,
                                    failure_threshold=2), timeout=20)
        inst = sup.instances(key)[0]
        httpx.get(f"http://127.0.0.1:{inst.port}/toggle-health")
        assert _wait(lambda: inst.state == "unready")
        assert any("removed from routing" in l["line"] for l in sup.logs.read(key, source_prefix="platform/"))
        httpx.get(f"http://127.0.0.1:{inst.port}/toggle-health")
        assert _wait(lambda: inst.state == "ready") and inst.restarts == 0  # back without a restart
    finally:
        stop_router(server)
        sup.shutdown()


def probe_stop_order(tmp_path, **_):
    sup, server, port = _runtime(tmp_path)
    key = ("shop", "dev", "api")
    try:
        run = ReleaseRun("r1", str(APP.parent), [sys.executable, str(APP)], {"GRACEFUL": "1"}, probe_interval=0.1)
        sup.rollout(key, run, timeout=20)
        sup.stop_service(key, 2)
        lines = [l["line"] for l in sup.logs.read(key, source_prefix="platform/")]
        assert "removed from routing; sending SIGTERM" in lines
    finally:
        stop_router(server)
        sup.shutdown()


def probe_rotation_disables_old(client, dev, **_):
    client.put(f"{E}/secret/k", json={"spec": {}}, headers=dev)
    client.post(f"{E}/secret/k/versions", json={"value": "first-value"}, headers=dev)
    client.post(f"{E}/secret/k/rotate", headers=dev)
    versions = client.get(f"{E}/secret/k/versions", headers=dev).json()["items"]
    assert versions[0]["stage"] == "previous"
    assert client.post(f"{E}/secret/k/access", json={"version": "previous"}, headers=dev).json()["value"] == \
        "first-value"


def probe_simulate_in_cli(**_):
    sub = next(a for a in build_parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    assert not [c for c in sub.choices if "simul" in c or "trace" in c]
    assert "simulate_access" in TOOLS


PROBES = {
    "queue-visibility-field": probe_queue_visibility_field,
    "drain-default": probe_drain_default,
    "iam-immediate": probe_iam_immediate,
    "canary-default-weight": probe_canary_default_weight,
    "throttle-header": probe_throttle_header,
    "readiness-permanent": probe_readiness_permanent,
    "stop-order": probe_stop_order,
    "rotation-disables-old": probe_rotation_disables_old,
    "simulate-in-cli": probe_simulate_in_cli,
}


@pytest.mark.parametrize("drift_id", sorted(CATALOGUE))
def test_truth_is_discoverable(drift_id, client, dev, admin, nobody, clock, tmp_path):
    PROBES[drift_id](client=client, dev=dev, admin=admin, nobody=nobody, clock=clock, tmp_path=tmp_path)
