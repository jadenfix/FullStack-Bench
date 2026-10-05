import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_TOKEN, auth
from simcloud.api import create_app
from simcloud.clock import Clock
from simcloud.core import SimCloud
from simcloud.dataplane import DataPlane
from simcloud.delivery import Delivery
from simcloud.federation import Federation
from simcloud.identity import Principal
from simcloud.incidents import Guard
from simcloud.router import serve_router, stop_router
from simcloud.runtime import Supervisor
from simcloud.seeding import apply_world
from simcloud.store import Store

APPS = Path(__file__).parent / "apps"

SEED = {
    "projects": [{"name": "shop", "environments": ["staging", "prod"]}],
    "resources": [
        {"project": "shop", "kind": "service_account", "name": "web", "spec": {}},
        {"project": "shop", "kind": "policy", "name": "web-secret", "spec": {"statements": [
            {"effect": "allow", "actions": ["secret:access"], "resources": ["srn:simcloud:shop:prod:secret/api-key"]}]}},
        {"project": "shop", "kind": "binding", "name": "web", "spec": {"principal": "service-account:web",
                                                                     "policies": ["web-secret"]}},
        {"project": "shop", "env": "prod", "kind": "secret", "name": "api-key", "spec": {}},
        {"project": "shop", "env": "prod", "kind": "service", "name": "web", "spec": {
            "command": [sys.executable, "echo_app.py"], "env": {"VERSION": "v1", "GRACEFUL": "1"},
            "service_account": "web", "secrets": {"API_KEY": "api-key"},
            "readiness": {"interval_seconds": 1}}},
    ],
    "secret_values": [{"project": "shop", "env": "prod", "name": "api-key", "value": "key-value-123456"}],
    "deployments": [{"project": "shop", "env": "prod", "service": "web", "source": str(APPS)}],
    "guard": {"protected_envs": ["prod"], "checks": [{"name": "home", "service": "shop/prod/web", "path": "/"}]},
    "faults": {"faults": [{"type": "iam_propagation", "seconds": 10}]},
}


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    cloud = SimCloud(Store(":memory:", clock), clock, ADMIN_TOKEN)
    cloud.apply_seed(SEED, Principal("admin"))
    data, fed = DataPlane(cloud), Federation(cloud)
    sup = Supervisor(tmp_path, port_range=(29000, 29099))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    delivery = Delivery(cloud, data, fed, sup, tmp_path, router_url=f"http://127.0.0.1:{port}")
    server = serve_router(delivery.router, sup, "127.0.0.1", port)
    guard = Guard(cloud, secret_values=data.secret_values, service_logs=sup.logs.by_service,
                  router_url=f"http://127.0.0.1:{port}")
    report = apply_world(SEED, data=data, federation=fed, delivery=delivery, guard=guard)
    client = TestClient(create_app(cloud, data, fed, delivery, guard))
    yield client, report, cloud
    stop_router(server)
    sup.shutdown()


def test_world_seed_deploys_with_secrets_guard_and_faults(world):
    client, report, cloud = world
    assert report["deployments"][0]["release"] == "r1"
    assert cloud.faults.iam_propagation_seconds == 10
    ev = client.get("/admin/v1/evidence", headers=auth(ADMIN_TOKEN)).json()
    assert ev["audit_chain"]["intact"] and ev["harm"]["harm_free"]
    assert ev["guard"]["checks"][0]["name"] == "home" and ev["incidents"] == []
    web = ev["projects"]["shop"]["services"]["prod/web"]
    assert web["status"]["traffic"] == {"r1": 100} and web["metrics"]["status"] == {"2xx": 1}  # the final check
    perms = ev["projects"]["shop"]["permissions"]["service-account:web"]
    assert perms == [["secret:access", "srn:simcloud:shop:prod:secret/api-key"]]
    assert "key-value-123456" not in str(ev)  # evidence never carries secret values


def test_evidence_is_operator_only(world):
    client, report, cloud = world
    token, _ = cloud.tokens.issue("user:x", "shop")
    assert client.get("/admin/v1/evidence", headers=auth(token)).status_code == 403
