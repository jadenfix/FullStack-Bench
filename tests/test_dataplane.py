import pytest

E = "/v1/projects/shop/envs/dev"


def make(client, headers, kind, name, spec=None):
    r = client.put(f"{E}/{kind}/{name}", json={"spec": spec or {}}, headers=headers)
    assert r.status_code == 200, r.text


# ---- secrets ---------------------------------------------------------------

def test_secret_versions_access_and_rotation(client, dev):
    make(client, dev, "secret", "db-password")
    assert client.post(f"{E}/secret/db-password/versions", json={"value": "hunter2"}, headers=dev).status_code == 201
    assert client.post(f"{E}/secret/db-password/access", json={}, headers=dev).json()["value"] == "hunter2"
    rotated = client.post(f"{E}/secret/db-password/rotate", headers=dev).json()
    assert rotated["version"] == 2 and rotated["stage"] == "current"
    new = client.post(f"{E}/secret/db-password/access", json={}, headers=dev).json()
    old = client.post(f"{E}/secret/db-password/access", json={"version": "previous"}, headers=dev).json()
    assert new["value"] != "hunter2" and old["value"] == "hunter2" and old["version"] == 1
    stages = [v["stage"] for v in client.get(f"{E}/secret/db-password/versions", headers=dev).json()["items"]]
    assert stages == ["previous", "current"]


def test_secret_value_never_in_resource_or_audit(client, cloud, dev):
    make(client, dev, "secret", "api-key")
    client.post(f"{E}/secret/api-key/versions", json={"value": "s3cr3t-value-xyz"}, headers=dev)
    client.post(f"{E}/secret/api-key/access", json={}, headers=dev)
    assert "s3cr3t-value-xyz" not in client.get(f"{E}/secret/api-key", headers=dev).text
    assert "s3cr3t-value-xyz" not in client.get("/v1/projects/shop/audit", headers=dev).text
    raw = cloud.store._db.execute("SELECT group_concat(value) FROM kv").fetchone()[0]
    assert "s3cr3t-value-xyz" not in raw  # encrypted at rest
    accesses = [a for a in client.get("/v1/projects/shop/audit", headers=dev).json()["items"]
                if a["action"] == "secret:access"]
    assert len(accesses) == 1


def test_secret_access_denied_without_permission(client, admin, nobody):
    client.put("/v1/projects/shop/envs/prod/secret/k", json={"spec": {}}, headers=admin)
    r = client.post("/v1/projects/shop/envs/prod/secret/k/access", json={}, headers=nobody)
    assert r.status_code == 403


# ---- kv --------------------------------------------------------------------

def test_kv_ttl(client, clock, dev):
    make(client, dev, "kv", "sessions", {"default_ttl_seconds": 60})
    client.put(f"{E}/kv/sessions/keys/user/42", json={"value": {"cart": [1, 2]}}, headers=dev)
    assert client.get(f"{E}/kv/sessions/keys/user/42", headers=dev).json()["value"] == {"cart": [1, 2]}
    clock.advance(60)
    assert client.get(f"{E}/kv/sessions/keys/user/42", headers=dev).status_code == 404
    client.put(f"{E}/kv/sessions/keys/a", json={"value": 1, "ttl_seconds": 5}, headers=dev)
    client.delete(f"{E}/kv/sessions/keys/a", headers=dev)
    assert client.get(f"{E}/kv/sessions/keys/a", headers=dev).status_code == 404


def test_kv_missing_store(client, dev):
    assert client.get(f"{E}/kv/nope/keys/a", headers=dev).status_code == 404


# ---- queues ----------------------------------------------------------------

def recv(client, headers, q, **kw):
    r = client.post(f"{E}/queue/{q}/receive", json=kw, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def test_visibility_timeout_and_redelivery(client, clock, dev):
    make(client, dev, "queue", "jobs", {"visibility_timeout_seconds": 30})
    client.post(f"{E}/queue/jobs/messages", json={"body": {"n": 1}}, headers=dev)
    first = recv(client, dev, "jobs")
    assert first[0]["body"] == {"n": 1} and first[0]["receives"] == 1
    assert recv(client, dev, "jobs") == []  # in flight
    clock.advance(30)
    again = recv(client, dev, "jobs")
    assert again[0]["id"] == first[0]["id"] and again[0]["receives"] == 2  # at-least-once
    # the first receipt is stale now
    assert client.post(f"{E}/queue/jobs/ack", json={"receipt": first[0]["receipt"]}, headers=dev).status_code == 404
    assert client.post(f"{E}/queue/jobs/ack", json={"receipt": again[0]["receipt"]}, headers=dev).status_code == 204
    clock.advance(60)
    assert recv(client, dev, "jobs") == []


def test_ack_after_timeout_is_rejected(client, clock, dev):
    make(client, dev, "queue", "jobs", {"visibility_timeout_seconds": 10})
    client.post(f"{E}/queue/jobs/messages", json={"body": 1}, headers=dev)
    m = recv(client, dev, "jobs")[0]
    clock.advance(11)
    r = client.post(f"{E}/queue/jobs/ack", json={"receipt": m["receipt"]}, headers=dev)
    assert r.status_code == 409 and "visibility timeout" in r.json()["error"]["message"]


def test_dead_letter_after_max_receives(client, clock, dev):
    make(client, dev, "queue", "orders-dlq")
    make(client, dev, "queue", "orders", {"visibility_timeout_seconds": 5, "max_receives": 2,
                                          "dead_letter_queue": "orders-dlq"})
    client.post(f"{E}/queue/orders/messages", json={"body": "poison"}, headers=dev)
    client.post(f"{E}/queue/orders/messages", json={"body": "good"}, headers=dev)
    for _ in range(2):
        msgs = recv(client, dev, "orders", max_messages=10)
        good = [m for m in msgs if m["body"] == "good"]
        for m in good:
            client.post(f"{E}/queue/orders/ack", json={"receipt": m["receipt"]}, headers=dev)
        clock.advance(5)
    assert recv(client, dev, "orders", max_messages=10) == []
    dlq = recv(client, dev, "orders-dlq")
    assert [m["body"] for m in dlq] == ["poison"]


def test_fifo_groups_are_ordered_and_block(client, dev):
    make(client, dev, "queue", "ledger", {"fifo": True})
    assert client.post(f"{E}/queue/ledger/messages", json={"body": 1}, headers=dev).status_code == 400
    for body, group in [(1, "acct-a"), (2, "acct-a"), (3, "acct-b")]:
        client.post(f"{E}/queue/ledger/messages", json={"body": body, "group": group}, headers=dev)
    batch = recv(client, dev, "ledger", max_messages=10)
    assert [m["body"] for m in batch] == [1, 3]  # 2 waits behind 1 in group acct-a
    client.post(f"{E}/queue/ledger/ack", json={"receipt": batch[0]["receipt"]}, headers=dev)
    assert [m["body"] for m in recv(client, dev, "ledger", max_messages=10)] == [2]


def test_topic_fanout(client, dev):
    make(client, dev, "queue", "emails")
    make(client, dev, "queue", "analytics")
    make(client, dev, "topic", "order-placed", {"subscriptions": ["emails", "analytics"]})
    assert client.post(f"{E}/topic/order-placed/publish", json={"body": {"id": 7}}, headers=dev).json() == \
        {"delivered_to": 2}
    assert recv(client, dev, "emails")[0]["body"] == {"id": 7}
    assert recv(client, dev, "analytics")[0]["body"] == {"id": 7}


# ---- object storage ---------------------------------------------------------------

def test_objects_and_signed_urls(client, clock, dev):
    make(client, dev, "bucket", "uploads")
    r = client.put(f"{E}/bucket/uploads/objects/a/b.txt", content=b"hello", headers={**dev, "Content-Type": "text/plain"})
    assert r.json()["size"] == 5
    assert client.get(f"{E}/bucket/uploads/objects/a/b.txt", headers=dev).content == b"hello"
    assert [o["key"] for o in client.get(f"{E}/bucket/uploads/objects", params={"prefix": "a/"},
                                         headers=dev).json()["items"]] == ["a/b.txt"]
    signed = client.post(f"{E}/bucket/uploads/sign", json={"key": "a/b.txt", "expires_in": 60}, headers=dev).json()
    assert client.get(signed["url"]).content == b"hello"  # no auth needed
    assert client.put(signed["url"], content=b"x").status_code == 403  # signed for GET only
    tampered = signed["url"].replace("a/b.txt", "a/c.txt")
    assert client.get(tampered).status_code == 403
    clock.advance(61)
    assert client.get(signed["url"]).status_code == 403


def test_signed_put_upload(client, dev):
    make(client, dev, "bucket", "uploads")
    signed = client.post(f"{E}/bucket/uploads/sign", json={"key": "in.bin", "method": "PUT", "expires_in": 60},
                         headers=dev).json()
    assert client.put(signed["url"], content=b"\x00\x01").status_code == 200
    assert client.get(f"{E}/bucket/uploads/objects/in.bin", headers=dev).content == b"\x00\x01"


def test_public_read_only_when_enabled(client, dev):
    make(client, dev, "bucket", "private")
    make(client, dev, "bucket", "site", {"public_read": True})
    for b in ("private", "site"):
        client.put(f"{E}/bucket/{b}/objects/index.html", content=b"<h1>", headers=dev)
    assert client.get("/v1/public/shop/dev/site/index.html").content == b"<h1>"
    assert client.get("/v1/public/shop/dev/private/index.html").status_code == 404


@pytest.mark.parametrize("path,body", [
    ("/queue/missing/messages", {"body": 1}),
    ("/topic/missing/publish", {"body": 1}),
    ("/secret/missing/access", {}),
])
def test_ops_on_missing_resources_404(client, dev, path, body):
    assert client.post(E + path, json=body, headers=dev).status_code == 404


def test_object_listing_paginates_in_key_order(cloud):
    from simcloud.dataplane import DataPlane
    from simcloud.identity import Principal
    admin = Principal("admin")
    cloud.put(admin, "shop", "prod", "bucket", "logs", {})
    d = DataPlane(cloud)
    for k in ["b/2", "a/1", "b/1", "c/9", "b/3"]:
        d.put_object(admin, "shop", "prod", "logs", k, b"x", "text/plain")
    page1 = d.list_objects(admin, "shop", "prod", "logs", "b/", limit=2)
    assert [i["key"] for i in page1] == ["b/1", "b/2"]
    assert [i["key"] for i in d.list_objects(admin, "shop", "prod", "logs", "b/", limit=2, after="b/2")] == ["b/3"]
    assert [i["key"] for i in d.list_objects(admin, "shop", "prod", "logs")] == ["a/1", "b/1", "b/2", "b/3", "c/9"]
