from conftest import auth

BASE = "/v1/projects/shop/envs"


def test_health_needs_no_auth(client):
    assert client.get("/v1/health").json()["status"] == "ok"


def test_whoami(client, dev):
    r = client.get("/v1/whoami", headers=dev)
    assert r.json() == {"principal": "user:dev", "project": "shop", "claims": {}}


def test_unauthenticated(client):
    r = client.get(f"{BASE}/dev/kv")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    assert client.get(f"{BASE}/dev/kv", headers=auth("sct_dead_beef")).status_code == 401


def test_create_read_update_with_etag(client, dev):
    r = client.put(f"{BASE}/dev/queue/orders", json={"spec": {"max_receives": 5, "dead_letter_queue": "orders-dlq"}},
                   headers=dev)
    assert r.status_code == 200, r.text
    assert r.headers["ETag"] == '"1"'
    body = r.json()
    assert body["spec"]["visibility_timeout_seconds"] == 30 and body["status"]["phase"] == "ready"
    # stale If-Match is rejected and nothing changes
    r = client.put(f"{BASE}/dev/queue/orders", json={"spec": {"fifo": True}}, headers={**dev, "If-Match": '"7"'})
    assert r.status_code == 412 and r.json()["error"]["details"]["current_version"] == 1
    r = client.put(f"{BASE}/dev/queue/orders", json={"spec": {"fifo": True}}, headers={**dev, "If-Match": '"1"'})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.get(f"{BASE}/dev/queue/orders", headers=dev).json()["spec"]["fifo"] is True


def test_policy_scopes_prod_to_read_only(client, dev):
    r = client.put(f"{BASE}/prod/kv/sessions", json={"spec": {}}, headers=dev)
    assert r.status_code == 403 and r.json()["error"]["code"] == "access_denied"
    assert client.get(f"{BASE}/prod/kv", headers=dev).status_code == 200


def test_principal_without_bindings_is_denied(client, nobody):
    assert client.get(f"{BASE}/dev/kv", headers=nobody).status_code == 403


def test_invalid_spec_and_names(client, dev):
    r = client.put(f"{BASE}/dev/queue/orders", json={"spec": {"visibility_timeout_seconds": -1}}, headers=dev)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert client.put(f"{BASE}/dev/queue/Bad_Name", json={"spec": {}}, headers=dev).status_code == 400
    assert client.put(f"{BASE}/dev/nonsense/x", json={"spec": {}}, headers=dev).status_code == 404
    assert client.put(f"{BASE}/qa/kv/x", json={"spec": {}}, headers=dev).status_code == 404


def test_project_level_kinds_use_underscore_env(client, admin):
    r = client.put(f"{BASE}/prod/policy/p", json={"spec": {"statements": [
        {"effect": "allow", "actions": ["kv:get"], "resources": ["*"]}]}}, headers=admin)
    assert r.status_code == 400
    r = client.put(f"{BASE}/_/policy/p", json={"spec": {"statements": [
        {"effect": "allow", "actions": ["kv:get"], "resources": ["*"]}]}}, headers=admin)
    assert r.status_code == 200 and "effective_at" in r.json()["status"]


def test_quota(client, admin, dev):
    client.put(f"{BASE}/_/quota/small", json={"spec": {"limits": {"bucket": 1}}}, headers=admin)
    assert client.put(f"{BASE}/dev/bucket/a", json={"spec": {}}, headers=dev).status_code == 200
    r = client.put(f"{BASE}/dev/bucket/b", json={"spec": {}}, headers=dev)
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
    # updating an existing resource is not blocked by the quota
    assert client.put(f"{BASE}/dev/bucket/a", json={"spec": {"versioning": True}}, headers=dev).status_code == 200


def test_delete(client, dev):
    client.put(f"{BASE}/dev/kv/carts", json={"spec": {}}, headers=dev)
    assert client.delete(f"{BASE}/dev/kv/carts", headers=dev).status_code == 204
    assert client.get(f"{BASE}/dev/kv/carts", headers=dev).status_code == 404
    assert client.delete(f"{BASE}/dev/kv/carts", headers=dev).status_code == 404


def test_audit_records_denials_and_chain(client, dev, admin):
    client.put(f"{BASE}/prod/kv/sessions", json={"spec": {}}, headers=dev)
    items = client.get("/v1/projects/shop/audit", headers=dev).json()["items"]
    denied = [i for i in items if i["action"] == "kv:create" and i["outcome"] == "denied"]
    assert denied and denied[0]["principal"] == "user:dev"
    assert client.get("/admin/v1/audit/verify", headers=admin).json() == {"intact": True, "first_bad_seq": None}
    assert client.get("/admin/v1/audit/verify", headers=dev).status_code == 403


def test_only_operator_creates_projects(client, dev, admin):
    assert client.post("/admin/v1/projects", json={"name": "other"}, headers=dev).status_code == 403
    assert client.post("/admin/v1/projects", json={"name": "other"}, headers=admin).status_code == 201


def test_kinds_endpoint_publishes_schemas(client):
    body = client.get("/v1/kinds").json()
    assert body["kinds"]["service"]["scope"] == "env"
    assert "properties" in body["kinds"]["queue"]["schema"]
    assert "service:deploy" in body["actions"]
