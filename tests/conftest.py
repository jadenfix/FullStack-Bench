import os

import pytest
from fastapi.testclient import TestClient

from simcloud.api import create_app
from simcloud.clock import FakeClock
from simcloud.core import SimCloud
from simcloud.identity import Principal
from simcloud.store import Store

ADMIN_TOKEN = "verifier-only-admin-token"

SEED = {
    "projects": [{"name": "shop", "environments": ["dev", "staging", "prod"], "regions": ["region-a", "region-b"]}],
    "resources": [
        {"project": "shop", "kind": "policy", "name": "developer", "spec": {"statements": [
            {"effect": "allow", "actions": ["*"], "resources": ["srn:simcloud:shop:dev:*", "srn:simcloud:shop:staging:*"]},
            {"effect": "allow", "actions": ["*:read", "*:list", "audit:read"], "resources": ["*"]},
        ]}},
        {"project": "shop", "kind": "binding", "name": "dev-developer",
         "spec": {"principal": "user:dev", "policies": ["developer"]}},
    ],
    "principals": [{"name": "user:dev", "project": "shop"}, {"name": "user:nobody", "project": "shop"}],
}


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def cloud(clock):
    c = SimCloud(Store(":memory:", clock), clock, ADMIN_TOKEN)
    c.issued = c.apply_seed(SEED, Principal("admin"))
    return c


@pytest.fixture
def client(cloud):
    return TestClient(create_app(cloud))


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def dev(cloud):
    return auth(cloud.issued["user:dev"])


@pytest.fixture
def nobody(cloud):
    return auth(cloud.issued["user:nobody"])


@pytest.fixture
def admin():
    return auth(ADMIN_TOKEN)


@pytest.fixture(autouse=True, scope="session")
def _traversable_basetemp(tmp_path_factory):
    """Run as root, the suite hands workloads to the `workload` user (simcloud/privsep.py), which
    must be able to reach its workdir under tmp_path; pytest creates its temp tree 0700."""
    if os.geteuid() == 0:
        base = tmp_path_factory.getbasetemp()
        for d in (base, base.parent):
            os.chmod(d, 0o711)
