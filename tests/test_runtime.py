import concurrent.futures
import socket
import sys
import time
from pathlib import Path

import httpx
import pytest

from simcloud.router import Router, serve_router, stop_router
from simcloud.runtime import ReleaseRun, Supervisor

APP = str(Path(__file__).parent / "apps" / "echo_app.py")
KEY = ("shop", "prod", "api")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(release, **env):
    return ReleaseRun(release_id=release, workdir=str(Path(APP).parent), command=[sys.executable, APP],
                      env={"VERSION": release, **{k: str(v) for k, v in env.items()}},
                      probe_interval=0.2, probe_timeout=1.0, failure_threshold=2, drain_seconds=5.0)


@pytest.fixture
def rt(tmp_path):
    sup = Supervisor(tmp_path, port_range=(23000, 23999))
    traffic = {}
    router = Router(sup, lambda key: traffic.get(key, {}))
    port = free_port()
    server = serve_router(router, sup, "127.0.0.1", port)
    base = f"http://127.0.0.1:{port}/_svc/shop/prod/api"
    yield sup, router, traffic, base
    stop_router(server)
    sup.shutdown()


def test_rollout_and_route_by_path_and_host(rt):
    sup, router, traffic, base = rt
    assert sup.rollout(KEY, run("r1"), count=2, timeout=20)["ready"]
    traffic[KEY] = {"r1": 100}
    r = httpx.get(base + "/")
    assert r.status_code == 200 and r.text == "r1" and r.headers["x-simcloud-release"] == "r1"
    port = base.split(":")[2].split("/")[0]
    r = httpx.get(f"http://127.0.0.1:{port}/", headers={"Host": "api.prod.shop.simcloud.internal"})
    assert r.text == "r1"
    assert httpx.get(f"http://127.0.0.1:{port}/").status_code == 404
    instances = {httpx.get(base + "/").headers["x-simcloud-instance"] for _ in range(4)}
    assert len(instances) == 2  # round-robin across both instances


def test_weighted_split_is_exact(rt):
    sup, router, traffic, base = rt
    sup.rollout(KEY, run("r1"), timeout=20)
    sup.scale_release(KEY, run("r2"), 1, timeout=20)
    traffic[KEY] = {"r1": 90, "r2": 10}
    seen = [httpx.get(base + "/").text for _ in range(50)]
    assert seen.count("r2") == 5 and seen.count("r1") == 45
    summary = router.metrics.summary(KEY, "r2")
    assert summary["requests"] == 5 and summary["status"] == {"2xx": 5}


def test_crash_is_restarted_with_backoff(rt):
    sup, router, traffic, base = rt
    sup.rollout(KEY, run("r1"), timeout=20)
    traffic[KEY] = {"r1": 100}
    assert httpx.get(base + "/crash").status_code == 502
    deadline = time.time() + 15
    while time.time() < deadline:
        inst = sup.instances(KEY)[0]
        if inst.restarts == 1 and inst.state == "ready":
            break
        time.sleep(0.1)
    assert inst.restarts == 1 and inst.last_exit_code == 3 and inst.state == "ready"
    assert httpx.get(base + "/").status_code == 200
    lines = [l["line"] for l in sup.logs.read(KEY)]
    assert "crashing on request" in lines and any("exited with code 3" in l for l in lines)


def test_bad_release_never_replaces_good_one(rt):
    sup, router, traffic, base = rt
    sup.rollout(KEY, run("r1"), timeout=20)
    traffic[KEY] = {"r1": 100}
    result = sup.rollout(KEY, run("r2", READY_AFTER=999), timeout=2)
    assert result == {"ready": False, "reason": "new instances did not pass readiness in time"}
    assert {i.release_id for i in sup.instances(KEY)} == {"r1"}
    assert httpx.get(base + "/").text == "r1"


def test_no_ready_instances_is_503(rt):
    sup, router, traffic, base = rt
    traffic[KEY] = {"r9": 100}
    r = httpx.get(base + "/")
    assert r.status_code == 503 and r.headers["retry-after"] == "1"


def _load_during_rollout(sup, traffic, base, graceful: bool) -> list[int]:
    sup.rollout(KEY, run("r1", GRACEFUL=int(graceful)), timeout=20)
    traffic[KEY] = {"r1": 100}
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        futures = [pool.submit(lambda: httpx.get(base + "/slow?ms=1500", timeout=10).status_code) for _ in range(8)]
        time.sleep(0.5)  # requests are in flight on r1
        sup.rollout(KEY, run("r2", GRACEFUL=int(graceful)), timeout=20)
        traffic[KEY] = {"r2": 100}
        return [f.result() for f in futures]


def test_graceful_shutdown_drains_in_flight_requests(rt):
    sup, router, traffic, base = rt
    assert _load_during_rollout(sup, traffic, base, graceful=True) == [200] * 8


def test_abrupt_shutdown_drops_in_flight_requests(rt):
    sup, router, traffic, base = rt
    codes = _load_during_rollout(sup, traffic, base, graceful=False)
    assert codes.count(502) == 8
