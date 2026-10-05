import pytest

from simcloud.clock import FakeClock
from simcloud.errors import SimCloudError
from simcloud.store import Store, srn


@pytest.fixture
def store():
    return Store(":memory:", FakeClock())


def test_put_get_versions(store):
    r1 = store.put("shop", "prod", "service", "api", {"image": "api:1"})
    assert r1["version"] == 1
    assert r1["srn"] == "srn:simcloud:shop:prod:service/api"
    r2 = store.put("shop", "prod", "service", "api", {"image": "api:2"})
    assert r2["version"] == 2 and r2["spec"] == {"image": "api:2"}
    assert r2["created_at"] == r1["created_at"]


def test_optimistic_concurrency(store):
    store.put("shop", "prod", "kv", "carts", {}, expect_version=0)
    with pytest.raises(SimCloudError) as e:
        store.put("shop", "prod", "kv", "carts", {}, expect_version=0)
    assert e.value.code == "conflict"
    with pytest.raises(SimCloudError) as e:
        store.put("shop", "prod", "kv", "carts", {"x": 1}, expect_version=5)
    assert e.value.code == "precondition_failed"
    assert store.put("shop", "prod", "kv", "carts", {"x": 1}, expect_version=1)["version"] == 2


def test_status_survives_spec_update(store):
    store.put("shop", "prod", "service", "api", {"image": "a"})
    store.set_status("shop", "prod", "service", "api", {"phase": "ready"})
    assert store.put("shop", "prod", "service", "api", {"image": "b"})["status"] == {"phase": "ready"}


def test_list_and_delete(store):
    store.put("shop", "prod", "service", "api", {})
    store.put("shop", "staging", "service", "api", {})
    store.put("shop", "prod", "queue", "orders", {})
    assert [r["kind"] for r in store.list_resources("shop", "prod")] == ["queue", "service"]
    assert len(store.list_resources("shop", kind="service")) == 2
    assert store.delete("shop", "prod", "queue", "orders")
    assert not store.delete("shop", "prod", "queue", "orders")


def test_kv_prefix_escapes_wildcards(store):
    store.kv_put("ns", "a_1", 1)
    store.kv_put("ns", "ab1", 2)
    assert store.kv_items("ns", "a_") == [("a_1", 1)]


def test_audit_chain_detects_tampering(store):
    target = srn("shop", "prod", "service", "api")
    for action in ("service:create", "service:deploy", "service:delete"):
        store.audit("user:ana", action, target, "allowed")
    assert store.verify_audit_chain() is None
    store._db.execute("UPDATE audit SET outcome='denied' WHERE seq=2")
    assert store.verify_audit_chain() == 2


def test_audit_chain_detects_deleted_record(store):
    for i in range(3):
        store.audit("user:ana", "kv:put", f"srn:x/{i}", "allowed")
    store._db.execute("DELETE FROM audit WHERE seq=2")
    assert store.verify_audit_chain() == 3
