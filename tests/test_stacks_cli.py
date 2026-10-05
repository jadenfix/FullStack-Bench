import json

import pytest
import yaml

from simcloud.cli import main as sc

DOC = {
    "project": "shop",
    "environments": {
        "dev": {
            "queue": {
                "orders": {"max_receives": 5, "dead_letter_queue": "orders-dlq"},
                "orders-dlq": {},
            },
            "kv": {"sessions": {"default_ttl_seconds": 3600}},
        }
    },
}


def plan(client, headers, doc=DOC, stack="default"):
    r = client.post(f"/v1/projects/shop/stacks/{stack}/plan", json={"document": doc}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def apply(client, headers, doc=DOC, stack="default", plan_hash=None):
    body = {"document": doc, **({"plan_hash": plan_hash} if plan_hash else {})}
    return client.post(f"/v1/projects/shop/stacks/{stack}/apply", json=body, headers=headers)


def test_plan_apply_converge(client, dev):
    p = plan(client, dev)
    assert p["summary"]["create"] == 3
    r = apply(client, dev, plan_hash=p["plan_hash"])
    assert r.status_code == 200 and r.json()["applied"] == 3
    again = plan(client, dev)
    assert again["summary"] == {"create": 0, "update": 0, "delete": 0, "noop": 3, "drifted": 0}


def test_apply_order_puts_dlq_and_kv_before(client, dev):
    kinds = [c["kind"] for c in plan(client, dev)["changes"]]
    assert kinds == sorted(kinds, key=["kv", "queue"].index)


def test_stale_plan_hash_refused(client, dev):
    p = plan(client, dev)
    client.put("/v1/projects/shop/envs/dev/kv/sessions", json={"spec": {}}, headers=dev)
    r = apply(client, dev, plan_hash=p["plan_hash"])
    assert r.status_code == 409 and "plan changed" in r.json()["error"]["message"]


def test_update_and_delete_only_managed(client, dev):
    apply(client, dev)
    client.put("/v1/projects/shop/envs/dev/kv/unmanaged", json={"spec": {}}, headers=dev)
    doc = json.loads(json.dumps(DOC))
    doc["environments"]["dev"]["kv"]["sessions"]["default_ttl_seconds"] = 60
    del doc["environments"]["dev"]["queue"]["orders-dlq"]
    p = plan(client, dev, doc)
    actions = {(c["kind"], c["name"]): c["action"] for c in p["changes"]}
    assert actions[("kv", "sessions")] == "update"
    assert actions[("queue", "orders-dlq")] == "delete"
    assert ("kv", "unmanaged") not in actions  # never touched: not in the stack
    apply(client, dev, doc)
    assert client.get("/v1/projects/shop/envs/dev/queue/orders-dlq", headers=dev).status_code == 404
    assert client.get("/v1/projects/shop/envs/dev/kv/unmanaged", headers=dev).status_code == 200


def test_drift_detected_and_reconciled(client, dev):
    apply(client, dev)
    client.put("/v1/projects/shop/envs/dev/kv/sessions", json={"spec": {"default_ttl_seconds": 1}}, headers=dev)
    client.delete("/v1/projects/shop/envs/dev/queue/orders-dlq", headers=dev)
    drift = client.get("/v1/projects/shop/stacks/default/drift", headers=dev).json()["drift"]
    assert {(d["name"], d["drift"]) for d in drift} == {("sessions", "modified_outside_stack"),
                                                         ("orders-dlq", "deleted_outside_stack")}
    p = plan(client, dev)
    assert p["summary"]["drifted"] == 2
    apply(client, dev)
    assert client.get("/v1/projects/shop/stacks/default/drift", headers=dev).json()["drift"] == []


def test_import_then_manage(client, dev):
    client.put("/v1/projects/shop/envs/dev/bucket/assets", json={"spec": {"versioning": True}}, headers=dev)
    r = client.post("/v1/projects/shop/stacks/default/import", json={"env": "dev", "kind": "bucket", "name": "assets"},
                    headers=dev)
    assert r.status_code == 200
    # the document doesn't list it, so the stack now plans to delete it
    actions = {(c["kind"], c["name"]): c["action"] for c in plan(client, dev)["changes"]}
    assert actions[("bucket", "assets")] == "delete"
    # a second stack can't import a resource another stack manages
    r = client.post("/v1/projects/shop/stacks/other/import", json={"env": "dev", "kind": "bucket", "name": "assets"},
                    headers=dev)
    assert r.status_code == 409


def test_invalid_documents(client, dev):
    bad = {"project": "shop", "environments": {"dev": {"policy": {"p": {"statements": []}}}}}
    r = client.post("/v1/projects/shop/stacks/default/plan", json={"document": bad}, headers=dev)
    assert r.status_code == 422 and "iam" in json.dumps(r.json())
    r = client.post("/v1/projects/shop/stacks/default/plan", json={"document": {**DOC, "project": "other"}}, headers=dev)
    assert r.status_code == 422


def test_apply_respects_policies(client, dev):
    prod_doc = {"project": "shop", "environments": {"prod": {"kv": {"x": {}}}}}
    r = apply(client, dev, prod_doc)
    assert r.status_code == 403


# ---- the sc CLI against the same app ----------------------------------------

@pytest.fixture
def run_sc(client, cloud, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SIMCLOUD_TOKEN", cloud.issued["user:dev"])

    def _run(*argv):
        code = sc(["--url", "http://testserver", *argv], http=client)
        out = capsys.readouterr()
        return code, out.out, out.err
    return _run


def test_cli_whoami_and_get(run_sc):
    code, out, _ = run_sc("whoami")
    assert code == 0 and yaml.safe_load(out)["principal"] == "user:dev"
    code, out, _ = run_sc("-o", "json", "get", "dev", "kv")
    assert code == 0 and json.loads(out) == {"items": []}


def test_cli_plan_apply_drift(run_sc, tmp_path):
    f = tmp_path / "simcloud.yaml"
    f.write_text(yaml.safe_dump(DOC))
    code, out, _ = run_sc("plan", "-f", str(f))
    assert code == 0 and "3 to create" in out and "+ dev/queue/orders" in out
    code, out, _ = run_sc("apply", "-f", str(f))
    assert code == 0 and "applied 3 change(s)" in out
    code, out, _ = run_sc("drift")
    assert code == 0 and yaml.safe_load(out)["drift"] == []


def test_cli_errors_have_codes_and_exit_1(run_sc, tmp_path):
    spec = tmp_path / "kv.yaml"
    spec.write_text("{}")
    code, _, err = run_sc("put", "prod", "kv", "x", "-f", str(spec))
    assert code == 1 and "access_denied" in err
    code, _, err = run_sc("get", "dev", "kv", "missing")
    assert code == 1 and "not_found" in err


def test_cli_missing_file_exits_2(run_sc):
    code, _, err = run_sc("plan", "-f", "/nonexistent.yaml")
    assert code == 2
