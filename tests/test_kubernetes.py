import base64
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import auth
from simcloud.api import create_app
from simcloud.errors import SimCloudError
from simcloud.identity import Principal
from simcloud.incidents import Guard
from simcloud.kubernetes import Clusters, object_path, parse_bindings

ADMIN = Principal("admin")
KEY = ("shop", "prod", "main")


class FakeAPI:
    def __init__(self, kubeconfig):
        self.kubeconfig = kubeconfig
        self.objects: dict[str, dict] = {}

    def ready(self):
        return True

    def apply(self, obj):
        self.objects[object_path(obj)] = obj
        return obj

    def delete(self, path):
        self.objects.pop(path, None)

    def list(self, path, **params):
        kind = path.rsplit("/", 1)[1]
        return [o for p, o in self.objects.items() if f"/{kind}/" in p
                and o["metadata"].get("labels", {}).get("simcloud.dev/managed") == "true"]


@pytest.fixture
def k8s(cloud, tmp_path):
    d = tmp_path / "k8s"
    d.mkdir()
    b64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731
    (d / "kubeconfig.yaml").write_text(yaml.safe_dump({
        "clusters": [{"name": "default", "cluster": {"server": "https://127.0.0.1:6443",
                                                     "certificate-authority-data": b64("CA")}}],
        "users": [{"name": "default", "user": {"client-certificate-data": b64("ADMIN-CERT"),
                                               "client-key-data": b64("ADMIN-KEY")}}]}))
    clusters = Clusters(cloud, {KEY: d})
    clusters.apis[KEY] = FakeAPI(d / "kubeconfig.yaml")
    guard = Guard(cloud, clusters=clusters)
    cloud.put(ADMIN, "shop", "_", "policy", "oncall", {"statements": [
        {"effect": "allow", "actions": ["cluster:connect", "cluster:read"], "resources": ["srn:simcloud:shop:prod:cluster/main"]}]})
    cloud.put(ADMIN, "shop", "_", "binding", "oncall", {"principal": "user:oncall", "policies": ["oncall"]})
    cloud.put(ADMIN, "shop", "prod", "cluster", "main", {"access": [
        {"principal": "user:oncall", "cluster_role": "edit", "namespaces": ["shop", "jobs"]},
        {"principal": "user:sre", "cluster_role": "cluster-admin"}]})
    clusters.guard, clusters.dir = guard, d
    return clusters


def test_parse_bindings():
    assert parse_bindings("shop/prod/main=/k8s, x/dev/c=/d") == {("shop", "prod", "main"): Path("/k8s"),
                                                                ("x", "dev", "c"): Path("/d")}
    with pytest.raises(ValueError):
        parse_bindings("shop/main=/k8s")


def test_access_entries_become_managed_bindings(k8s, cloud):
    objs = k8s.apis[KEY].objects
    assert sorted(p.split("/v1/")[1].rsplit("/", 1)[0] for p in objs) == [
        "clusterrolebindings", "namespaces/jobs/rolebindings", "namespaces/shop/rolebindings"]
    crb = next(o for p, o in objs.items() if "clusterrolebindings" in p)
    assert crb["subjects"] == [{"kind": "User", "name": "user:sre", "apiGroup": "rbac.authorization.k8s.io"}]
    assert crb["roleRef"]["name"] == "cluster-admin"
    cloud.put(ADMIN, "shop", "prod", "cluster", "main", {"access": [
        {"principal": "user:oncall", "cluster_role": "edit", "namespaces": ["shop"]}]})
    assert len(objs) == 1 and "/namespaces/shop/" in next(iter(objs))  # stale managed bindings removed
    status = cloud.store.get("shop", "prod", "cluster", "main")["status"]
    assert status["phase"] == "ready" and status["endpoint"] == "https://k8s:6443"


def test_token_review_maps_simcloud_identity(k8s, cloud, clock):
    clock.advance(30)  # IAM propagation
    oncall, _ = cloud.tokens.issue("user:oncall", "shop")
    nobody = cloud.issued["user:nobody"]
    ok = k8s.token_review(*KEY, {"apiVersion": "authentication.k8s.io/v1", "spec": {"token": oncall}})
    assert ok["status"]["authenticated"] and ok["status"]["user"]["username"] == "user:oncall"
    assert "simcloud:authenticated" in ok["status"]["user"]["groups"]
    denied = k8s.token_review(*KEY, {"spec": {"token": nobody}})
    assert denied["status"]["authenticated"] is False
    assert k8s.token_review(*KEY, {"spec": {"token": "garbage"}})["status"]["authenticated"] is False


def test_cluster_tokens_are_short_lived_and_bound(k8s, cloud, clock):
    clock.advance(30)
    oncall, _ = cloud.tokens.issue("user:oncall", "shop")
    cred = k8s.token(cloud.tokens.authenticate(oncall), *KEY)
    tok = cred["status"]["token"]
    assert cred["kind"] == "ExecCredential"
    assert k8s.token_review(*KEY, {"spec": {"token": tok}})["status"]["authenticated"]
    assert not k8s.token_review("shop", "prod", "other", {"spec": {"token": tok}})["status"]["authenticated"]
    c = TestClient(create_app(cloud, clusters=k8s))
    r = c.get("/v1/whoami", headers=auth(tok))
    assert r.status_code == 401 and "Kubernetes API" in r.json()["error"]["message"]
    clock.advance(901)
    k8s._cache.clear()
    assert not k8s.token_review(*KEY, {"spec": {"token": tok}})["status"]["authenticated"]


def test_kubeconfig_never_contains_admin_credentials(k8s, cloud, clock):
    clock.advance(30)
    oncall, _ = cloud.tokens.issue("user:oncall", "shop")
    out = k8s.kubeconfig(cloud.tokens.authenticate(oncall), *KEY)
    doc = yaml.safe_load(out["kubeconfig"])
    assert doc["users"][0]["user"]["exec"]["command"] == "sc"
    assert doc["users"][0]["user"]["exec"]["args"] == ["k8s", "token", "--env", "prod", "main"]
    assert "ADMIN" not in base64.b64decode(doc["clusters"][0]["cluster"]["certificate-authority-data"]).decode()
    assert "client-key-data" not in out["kubeconfig"]
    with pytest.raises(SimCloudError):
        k8s.kubeconfig(cloud.tokens.authenticate(cloud.issued["user:nobody"]), *KEY)


def _ev(user, verb, resource, name="", ns="shop", code=200, sub=None):
    return {"stage": "ResponseComplete", "user": {"username": user}, "verb": verb, "auditID": "a1",
            "objectRef": {"resource": resource, "namespace": ns, "name": name, **({"subresource": sub} if sub else {})},
            "responseStatus": {"code": code}, "requestURI": f"/api/v1/namespaces/{ns}/{resource}/{name}"}


def test_cluster_audit_log_is_ingested_and_guarded(k8s, cloud):
    lines = [_ev("system:kube-controller-manager", "update", "deployments", "web"),
             _ev("user:oncall", "get", "pods", "web-1"),
             _ev("user:oncall", "patch", "deployments", "web"),
             _ev("user:oncall", "delete", "persistentvolumeclaims", "data-db-0"),
             _ev("user:oncall", "delete", "pods", "web-1", code=403),
             _ev("system:serviceaccount:shop:deployer", "create", "jobs", "migrate")]
    assert k8s.ingest_events(*KEY, lines) == 4
    recs = [r for r in cloud.store.audit_records(0, 1000) if r["action"].startswith("k8s:")]
    assert [(r["principal"], r["action"], r["outcome"]) for r in recs] == [
        ("user:oncall", "k8s:patch", "allowed"), ("user:oncall", "k8s:delete", "allowed"),
        ("user:oncall", "k8s:delete", "denied"), ("system:serviceaccount:shop:deployer", "k8s:create", "allowed")]
    assert recs[0]["srn"] == "srn:simcloud:shop:prod:cluster/main/shop/deployments/web"
    incs = k8s.guard.incidents()
    assert [(i["type"], i["actor"]) for i in incs] == [("data_destruction", "user:oncall")]


def test_audit_webhook_needs_its_token(k8s, cloud):
    k8s.audit_token = "audit-secret"
    c = TestClient(create_app(cloud, clusters=k8s))
    body = {"kind": "EventList", "items": [_ev("user:oncall", "patch", "deployments", "web")]}
    url = "/k8s/v1/clusters/shop/prod/main/audit/"
    assert c.post(url + "wrong", json=body).status_code == 401
    assert c.post(url + "audit-secret", content=b"not json").status_code == 400
    assert c.post(url + "audit-secret", json=body).json() == {"recorded": 1}
    assert c.post("/k8s/v1/clusters/shop/prod/nope/audit/audit-secret", json=body).status_code == 404
