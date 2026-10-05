import io
import socket
import sys
import tarfile
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_TOKEN, SEED, auth
from simcloud.api import create_app
from simcloud.clock import Clock
from simcloud.core import SimCloud
from simcloud.dataplane import DataPlane
from simcloud.delivery import Delivery, pack_directory
from simcloud.federation import Federation
from simcloud.identity import Principal
from simcloud.router import serve_router, stop_router
from simcloud.runtime import Supervisor
from simcloud.store import Store

APPS = Path(__file__).parent / "apps"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def env(tmp_path):
    clock = Clock()  # real time: instances and probes run for real
    cloud = SimCloud(Store(":memory:", clock), clock, ADMIN_TOKEN)
    cloud.issued = cloud.apply_seed(SEED, Principal("admin"))
    data, fed = DataPlane(cloud), Federation(cloud)
    sup = Supervisor(tmp_path, port_range=(24000, 24999))
    port = free_port()
    delivery = Delivery(cloud, data, fed, sup, tmp_path, router_url=f"http://127.0.0.1:{port}")
    server = serve_router(delivery.router, sup, "127.0.0.1", port)
    client = TestClient(create_app(cloud, data, fed, delivery))
    yield client, cloud, delivery, f"http://127.0.0.1:{port}"
    stop_router(server)
    sup.shutdown()


def service_spec(**over):
    spec = {"command": [sys.executable, "echo_app.py"], "env": {"VERSION": "v1", "GRACEFUL": "1"},
            "readiness": {"path": "/healthz", "interval_seconds": 1, "timeout_seconds": 1, "failure_threshold": 2},
            "drain_seconds": 3, "min_instances": 1}
    spec.update(over)
    return spec


def put_service(client, h, env_name, **over):
    r = client.put(f"/v1/projects/shop/envs/{env_name}/service/api", json={"spec": service_spec(**over)}, headers=h)
    assert r.status_code == 200, r.text


def deploy(client, h, env_name="dev", archive=None, **params):
    archive = pack_directory(APPS) if archive is None else archive
    return client.post(f"/v1/projects/shop/envs/{env_name}/service/api/deploy", params=params, content=archive,
                       headers={**h, "Content-Type": "application/gzip"})


def get(router, env_name="dev", path="/"):
    return httpx.get(f"{router}/_svc/shop/{env_name}/api{path}", timeout=10)


def test_rolling_deploy_then_reuse_after_config_change(env):
    client, cloud, delivery, router = env
    h = auth(cloud.issued["user:dev"])
    put_service(client, h, "dev")
    r = deploy(client, h)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["state"] == "ready" and first["traffic"] == {"r1": 100} and first["digest"].startswith("sha256:")
    assert get(router).text == "v1"
    put_service(client, h, "dev", env={"VERSION": "v2", "GRACEFUL": "1"})
    assert get(router).text == "v1"  # a spec change alone doesn't touch running instances
    r = client.post("/v1/projects/shop/envs/dev/service/api/deploy", params={"reuse": True}, headers=h).json()
    assert r["release"] == "r2" and r["digest"] == first["digest"]
    assert get(router).text == "v2"
    status = client.get("/v1/projects/shop/envs/dev/service/api/status", headers=h).json()
    assert [i["release"] for i in status["instances"]] == ["r2"]


def test_canary_then_rollback(env):
    client, cloud, delivery, router = env
    h = auth(cloud.issued["user:dev"])
    put_service(client, h, "dev")
    deploy(client, h)
    put_service(client, h, "dev", env={"VERSION": "v2", "GRACEFUL": "1"})
    r = deploy(client, h, strategy="canary", canary_weight=20).json()
    assert r["traffic"] == {"r1": 80, "r2": 20}
    seen = [get(router).text for _ in range(10)]
    assert seen.count("v2") == 2
    r = client.post("/v1/projects/shop/envs/dev/service/api/rollback", json={}, headers=h).json()
    assert r["release"] == "r1" and r["traffic"] == {"r1": 100}
    assert {get(router).text for _ in range(5)} == {"v1"}


def test_set_traffic_and_metrics(env):
    client, cloud, delivery, router = env
    h = auth(cloud.issued["user:dev"])
    put_service(client, h, "dev")
    deploy(client, h)
    deploy(client, h, strategy="none")
    r = client.post("/v1/projects/shop/envs/dev/service/api/traffic", json={"weights": {"r1": 50, "r2": 50}},
                    headers=h)
    assert r.status_code == 200 and r.json()["traffic"] == {"r1": 50, "r2": 50}
    for _ in range(10):
        get(router)
    m = client.get("/v1/projects/shop/envs/dev/service/api/metrics", params={"release": "r2"}, headers=h).json()
    assert m["requests"] == 5 and m["status"] == {"2xx": 5} and m["p99_ms"] is not None
    bad = client.post("/v1/projects/shop/envs/dev/service/api/traffic", json={"weights": {"r1": 60, "r2": 50}},
                      headers=h)
    assert bad.status_code == 400


def test_promote_uses_same_artifact_and_target_config(env):
    client, cloud, delivery, router = env
    admin = auth(ADMIN_TOKEN)
    put_service(client, admin, "staging", env={"VERSION": "staging-config", "GRACEFUL": "1"})
    put_service(client, admin, "prod", env={"VERSION": "prod-config", "GRACEFUL": "1"})
    staged = deploy(client, admin, "staging").json()
    r = client.post("/v1/projects/shop/services/api/promote", json={"from_env": "staging", "to_env": "prod"},
                    headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["digest"] == staged["digest"]
    assert get(router, "prod").text == "prod-config"
    releases = client.get("/v1/projects/shop/envs/prod/service/api/status", headers=admin).json()["releases"]
    assert releases[0]["promoted_from"] == "staging/r1"
    # the developer may read prod but not promote into it
    dev = auth(cloud.issued["user:dev"])
    r = client.post("/v1/projects/shop/services/api/promote", json={"from_env": "staging", "to_env": "prod"},
                    headers=dev)
    assert r.status_code == 403


def test_secrets_need_service_account_permission(env):
    client, cloud, delivery, router = env
    admin = auth(ADMIN_TOKEN)
    base = "/v1/projects/shop/envs"
    client.put(f"{base}/_/service_account/api-sa", json={"spec": {}}, headers=admin)
    client.put(f"{base}/dev/secret/db-password", json={"spec": {}}, headers=admin)
    client.post(f"{base}/dev/secret/db-password/versions", json={"value": "pw-123"}, headers=admin)
    put_service(client, admin, "dev", service_account="api-sa", secrets={"DB_PASSWORD": "db-password"})
    r = deploy(client, admin)
    assert r.status_code == 403 and "cannot read secret db-password" in r.json()["error"]["message"]
    client.put(f"{base}/_/policy/api-secrets", json={"spec": {"statements": [{
        "effect": "allow", "actions": ["secret:access"], "resources": ["srn:simcloud:shop:dev:secret/db-password"]}]}},
        headers=admin)
    client.put(f"{base}/_/binding/api-sa-b", json={"spec": {"principal": "service-account:api-sa",
                                                            "policies": ["api-secrets"]}}, headers=admin)
    assert deploy(client, admin).json()["state"] == "ready"
    assert get(router, path="/env/DB_PASSWORD").text == "pw-123"
    assert get(router, path="/env/SIMCLOUD_TOKEN").text.startswith("sct_")


def test_failed_build_keeps_serving_release(env):
    client, cloud, delivery, router = env
    h = auth(cloud.issued["user:dev"])
    put_service(client, h, "dev")
    deploy(client, h)
    put_service(client, h, "dev", build=["sh", "-c", "echo compiling; exit 2"])
    r = deploy(client, h).json()
    assert r["state"] == "build_failed" and r["traffic"] == {"r1": 100}
    logs = client.get("/v1/projects/shop/envs/dev/service/api/logs", params={"source": "build/"}, headers=h).json()
    assert any(l["line"] == "compiling" for l in logs["items"])
    assert get(router).text == "v1"


def test_unready_release_fails_without_outage(env):
    client, cloud, delivery, router = env
    h = auth(cloud.issued["user:dev"])
    put_service(client, h, "dev")
    deploy(client, h)
    put_service(client, h, "dev", env={"VERSION": "v2", "READY_AFTER": "999"}, rollout_timeout_seconds=5)
    t = time.time()
    r = client.post("/v1/projects/shop/envs/dev/service/api/deploy", params={"reuse": True}, headers=h).json()
    assert r["state"] == "failed" and "readiness" in r["error"] and r["traffic"] == {"r1": 100}
    assert get(router).text == "v1"
    assert 5 <= time.time() - t < 20


def test_archive_path_traversal_rejected(env):
    client, cloud, delivery, router = env
    h = auth(cloud.issued["user:dev"])
    put_service(client, h, "dev")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"bad"))
    r = deploy(client, h, archive=buf.getvalue())
    assert r.status_code == 400 and "escapes" in r.json()["error"]["message"]
    assert deploy(client, h, archive=b"not a tarball").status_code == 400


def test_delete_stops_instances(env):
    client, cloud, delivery, router = env
    h = auth(cloud.issued["user:dev"])
    put_service(client, h, "dev")
    deploy(client, h)
    assert client.delete("/v1/projects/shop/envs/dev/service/api", headers=h).status_code == 204
    assert delivery.supervisor.instances(("shop", "dev", "api")) == []
    assert get(router).status_code == 503
