"""The three interfaces differ on purpose; these tests pin the split the skill documents."""

import json
from types import SimpleNamespace

import pytest
import yaml

from simcloud.cli import Client, build_parser
from simcloud.cli import main as sc
from simcloud.mcp_server import TOOLS, Server

P = "/v1/projects/shop"


def test_cli_has_wait_and_compare_but_no_diagnostics():
    sub = next(a for a in build_parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    assert {"wait", "compare"} <= set(sub.choices)
    assert not {"simulate", "simulate-access", "trace", "pending"} & set(sub.choices)


def test_mcp_has_diagnostics_but_no_cli_conveniences():
    assert {"simulate_access", "pending_changes", "trace_request", "incident_timeline"} <= set(TOOLS)
    assert not {"wait", "compare", "import", "rotate_secret", "sign_url", "federation_exchange"} & set(TOOLS)


def test_diagnostic_endpoints_are_not_in_the_public_api_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "/diagnostics/" in p]
    assert "/v1/projects/{project}/envs/{env}/secret/{name}/rotate" in paths  # API-only, but public


@pytest.fixture
def mcp(client, cloud, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SIMCLOUD_TOKEN", cloud.issued["user:dev"])
    return Server(Client(SimpleNamespace(url="http://testserver", project=None), http=client))


def tool(mcp, name, **args):
    r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r["result"]


def test_simulate_access_explains_now_and_after_propagation(mcp, client, admin, clock):
    client.put("/admin/v1/faults", json={"faults": [{"type": "iam_propagation", "seconds": 30}]}, headers=admin)
    client.put(f"{P}/envs/_/policy/deployer", json={"spec": {"statements": [
        {"effect": "allow", "actions": ["service:deploy"], "resources": ["srn:simcloud:shop:prod:service/*"]}]}},
        headers=admin)
    client.put(f"{P}/envs/_/binding/nobody-deployer", json={"spec": {"principal": "user:nobody",
                                                                    "policies": ["deployer"]}}, headers=admin)
    out = json.loads(tool(mcp, "simulate_access", principal="user:nobody", action="service:deploy",
                          resource="srn:simcloud:shop:prod:service/api")["content"][0]["text"])
    assert out["now"]["allowed"] is False
    assert out["after_pending"]["allowed"] is True and out["after_pending"]["policy"] == "deployer"
    pending = json.loads(tool(mcp, "pending_changes")["content"][0]["text"])["items"]
    assert {(p["kind"], p["name"]) for p in pending} == {("policy", "deployer"), ("binding", "nobody-deployer")}
    clock.advance(30)
    assert json.loads(tool(mcp, "pending_changes")["content"][0]["text"])["items"] == []


def test_diagnostics_need_permission(client, nobody):
    r = client.post(f"{P}/diagnostics/access", json={"principal": "user:dev", "action": "kv:get", "resource": "*"},
                    headers=nobody)
    assert r.status_code == 403


def test_incident_timeline_shows_the_change(mcp, client, admin, cloud):
    from simcloud.incidents import Guard
    client.put(f"{P}/envs/prod/queue/orders", json={"spec": {}}, headers=admin)
    client.put(f"{P}/envs/_/policy/all", json={"spec": {"statements": [
        {"effect": "allow", "actions": ["*"], "resources": ["*"]}]}}, headers=admin)
    client.put(f"{P}/envs/_/binding/dev-all", json={"spec": {"principal": "user:dev", "policies": ["all"]}},
               headers=admin)
    dev = {"Authorization": f"Bearer {cloud.issued['user:dev']}"}
    client.post(f"{P}/envs/prod/queue/orders/purge", headers=dev)
    [inc] = client.get(f"{P}/incidents", headers=dev).json()["items"]
    tl = json.loads(tool(mcp, "incident_timeline", incident_id=inc["id"])["content"][0]["text"])
    assert any(e["action"] == "queue:purge" and e["principal"] == "user:dev" for e in tl["events"])


def test_cli_compare(client, cloud, admin, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SIMCLOUD_TOKEN", cloud.issued["user:dev"])
    client.put(f"{P}/envs/staging/queue/orders", json={"spec": {"max_receives": 3}}, headers=admin)
    client.put(f"{P}/envs/prod/queue/orders", json={"spec": {"max_receives": 5}}, headers=admin)
    assert sc(["--url", "http://testserver", "compare", "queue", "orders", "--from", "staging", "--to", "prod"],
              http=client) == 0
    out = yaml.safe_load(capsys.readouterr().out)
    assert out == {"identical": False, "differences": {"max_receives": {"staging": 3, "prod": 5}}}
