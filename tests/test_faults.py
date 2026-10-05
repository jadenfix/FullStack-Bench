import socket
import sys
import time
from pathlib import Path

import httpx
import pytest

from simcloud.clock import FakeClock
from simcloud.errors import SimCloudError
from simcloud.faults import FaultEngine
from simcloud.router import Router, serve_router, stop_router
from simcloud.runtime import ReleaseRun, Supervisor

E = "/v1/projects/shop/envs/dev"


def test_engine_validation():
    eng = FaultEngine(FakeClock())
    with pytest.raises(SimCloudError):
        eng.load({"faults": [{"type": "meteor"}]})
    with pytest.raises(SimCloudError):
        eng.load({"faults": [{"type": "throttle", "every": 0}]})


def test_throttle_every_nth_with_retry_after(client, admin, dev):
    assert client.put("/admin/v1/faults", json={"faults": [
        {"type": "throttle", "actions": ["kv:create"], "every": 3, "retry_after": 7}]}, headers=admin).status_code == 200
    codes = [client.put(f"{E}/kv/k{i}", json={"spec": {}}, headers=dev) for i in range(6)]
    assert [r.status_code for r in codes] == [200, 200, 429, 200, 200, 429]
    assert codes[2].headers["Retry-After"] == "7" and codes[2].json()["error"]["code"] == "throttled"
    # other actions are unaffected; the operator is never throttled
    assert client.get(f"{E}/kv", headers=dev).status_code == 200


def test_iam_propagation_from_scenario(client, admin, nobody, clock):
    client.put("/admin/v1/faults", json={"faults": [{"type": "iam_propagation", "seconds": 20}]}, headers=admin)
    client.put("/v1/projects/shop/envs/_/policy/reader", json={"spec": {"statements": [
        {"effect": "allow", "actions": ["kv:list"], "resources": ["*"]}]}}, headers=admin)
    r = client.put("/v1/projects/shop/envs/_/binding/nobody-reader",
                   json={"spec": {"principal": "user:nobody", "policies": ["reader"]}}, headers=admin)
    assert r.json()["status"]["effective_at"] == pytest.approx(clock.now() + 20)
    assert client.get(f"{E}/kv", headers=nobody).status_code == 403
    clock.advance(20)
    assert client.get(f"{E}/kv", headers=nobody).status_code == 200


def test_region_outage_window(client, admin, dev, clock):
    client.put(f"{E}/service/api", json={"spec": {"regions": ["region-a"]}}, headers=dev)
    client.put(f"{E}/service/multi", json={"spec": {"regions": ["region-a", "region-b"]}}, headers=dev)
    client.put("/admin/v1/faults", json={"faults": [
        {"type": "region_outage", "region": "region-a", "start": 10, "duration": 30}]}, headers=admin)
    spec = {"spec": {"regions": ["region-a"], "min_instances": 2}}
    assert client.put(f"{E}/service/api", json=spec, headers=dev).status_code == 200
    clock.advance(10)
    r = client.put(f"{E}/service/api", json=spec, headers=dev)
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"
    # a multi-region service is still manageable
    assert client.put(f"{E}/service/multi", json={"spec": {"regions": ["region-a", "region-b"]}},
                      headers=dev).status_code == 200
    clock.advance(30)
    assert client.put(f"{E}/service/api", json=spec, headers=dev).status_code == 200


def test_only_operator_sets_faults(client, dev):
    assert client.put("/admin/v1/faults", json={"faults": []}, headers=dev).status_code == 403
    assert client.get("/admin/v1/faults", headers=dev).status_code == 403


def test_load_balancer_latency_and_errors(tmp_path):
    clock = FakeClock()
    eng = FaultEngine(clock)
    eng.load({"faults": [
        {"type": "errors", "services": ["shop/prod/*"], "every": 4, "status": 503},
        {"type": "latency", "services": ["shop/prod/api"], "ms": 300},
    ]})
    sup = Supervisor(tmp_path, port_range=(26000, 26099))
    key = ("shop", "prod", "api")
    router = Router(sup, lambda k: {"r1": 100}, faults=lambda k: eng.load_balancer("/".join(k), ["region-a"]))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = serve_router(router, sup, "127.0.0.1", port)
    try:
        app = Path(__file__).parent / "apps" / "echo_app.py"
        sup.rollout(key, ReleaseRun("r1", str(app.parent), [sys.executable, str(app)], {}, probe_interval=0.2), timeout=20)
        t = time.perf_counter()
        codes = [httpx.get(f"http://127.0.0.1:{port}/_svc/shop/prod/api/").status_code for _ in range(8)]
        elapsed = time.perf_counter() - t
        assert codes == [200, 200, 200, 503, 200, 200, 200, 503]
        assert elapsed >= 8 * 0.3
        assert router.metrics.summary(key)["status"] == {"2xx": 6, "5xx": 2}
    finally:
        stop_router(server)
        sup.shutdown()
