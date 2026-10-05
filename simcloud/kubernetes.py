"""Managed Kubernetes: real k3s clusters behind `cluster` resources.

Each cluster resource is bound to a k3s cluster the environment provides (a server node plus
agent nodes, run as sidecar containers). SimCloud holds the cluster's admin credentials; nobody
else does. Everyone else authenticates the way managed clusters on real clouds work:

- **Identity is SimCloud identity.** The apiserver sends every bearer token to SimCloud's
  TokenReview webhook. A token is accepted when it is a valid SimCloud token whose principal has
  `cluster:connect` on the cluster. The Kubernetes user name is the principal name
  (`user:oncall`, `service-account:deployer`), with the group `simcloud:authenticated`.
- **Authorisation is Kubernetes RBAC.** `cluster.spec.access` entries (principal, role,
  namespaces) are kept in sync as RoleBindings / ClusterRoleBindings labelled
  `simcloud.dev/managed=true`; anything else is ordinary in-cluster RBAC.
- `cluster:connect` returns a kubeconfig whose user runs `sc k8s token`, which exchanges the
  caller's SimCloud token for a short-lived token bound to this cluster.
- **Every change made through the apiserver lands in the SimCloud audit log**, attributed to
  the principal: the apiserver's audit webhook posts events to SimCloud with a shared token. The
  guard watches them in protected environments.

Configuration (server environment): SIMCLOUD_K8S="<project>/<env>/<name>=<dir>[,...]" where <dir>
is the cluster's shared state directory holding its admin kubeconfig (kubeconfig.yaml);
SIMCLOUD_K8S_AUDIT_TOKEN authenticates the audit webhook; SIMCLOUD_K8S_SERVER /
SIMCLOUD_K8S_INGRESS_HOST are the addresses advertised to clients and used by SimCloud
(default https://k8s:6443 and k8s).
"""

import base64
import hashlib
import json
import ssl
import tempfile
import threading
import time
from pathlib import Path

import httpx
import yaml

from .core import SimCloud
from .errors import SimCloudError
from .identity import Principal
from .store import srn

MANAGED = "simcloud.dev/managed"
TOKEN_TTL = 900
CACHE_SECONDS = 10.0
SYSTEM_USERS_RECORDED = ("system:serviceaccount:",)
READ_VERBS = {"get", "list", "watch"}


class KubeAPI:
    """A minimal admin client for one cluster (client certificate from the k3s admin kubeconfig)."""

    def __init__(self, kubeconfig: Path, server: str | None = None):
        self.kubeconfig = kubeconfig
        self.server_override = server
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _connect(self) -> httpx.Client:
        with self._lock:
            if self._client is not None:
                return self._client
            if not self.kubeconfig.exists():
                raise SimCloudError("unavailable", "the cluster is not ready yet", retry_after=5)
            cfg = yaml.safe_load(self.kubeconfig.read_text())
            cluster = cfg["clusters"][0]["cluster"]
            user = cfg["users"][0]["user"]
            tmp = Path(tempfile.mkdtemp(prefix="simcloud-k8s-"))
            ca, crt, key = tmp / "ca.crt", tmp / "client.crt", tmp / "client.key"
            ca.write_bytes(base64.b64decode(cluster["certificate-authority-data"]))
            crt.write_bytes(base64.b64decode(user["client-certificate-data"]))
            key.write_bytes(base64.b64decode(user["client-key-data"]))
            for f in (ca, crt, key):
                f.chmod(0o600)
            server = self.server_override or cluster["server"]
            # An explicit context: httpx 0.28 silently drops `cert=` when `verify` is a path.
            ctx = ssl.create_default_context(cafile=str(ca))
            ctx.load_cert_chain(str(crt), str(key))
            self._client = httpx.Client(base_url=server, verify=ctx, timeout=30.0)
            return self._client

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return self._connect().request(method, path, **kw)
        except httpx.HTTPError as e:
            raise SimCloudError("unavailable", f"cluster API unreachable: {type(e).__name__}", retry_after=5)

    def ready(self) -> bool:
        try:
            return self.request("GET", "/readyz").status_code == 200
        except SimCloudError:
            return False

    def apply(self, obj: dict) -> dict:
        """Server-side apply as field manager 'simcloud'."""
        path = object_path(obj)
        r = self.request("PATCH", path, params={"fieldManager": "simcloud", "force": "true"},
                         content=json.dumps(obj), headers={"Content-Type": "application/apply-patch+yaml"})
        if r.status_code >= 400:
            raise SimCloudError("invalid_request", f"cluster rejected {obj.get('kind')}/{obj['metadata'].get('name')}: "
                                f"{r.json().get('message', r.text)[:300]}")
        return r.json()

    def delete(self, path: str) -> None:
        self.request("DELETE", path)

    def list(self, path: str, **params) -> list[dict]:
        r = self.request("GET", path, params=params)
        if r.status_code >= 400:
            raise SimCloudError("unavailable", f"cluster list {path} failed: {r.status_code}")
        return r.json().get("items", [])


PLURALS = {"Namespace": "namespaces", "ServiceAccount": "serviceaccounts", "ConfigMap": "configmaps",
           "Secret": "secrets", "Service": "services", "Deployment": "deployments", "StatefulSet": "statefulsets",
           "DaemonSet": "daemonsets", "Job": "jobs", "CronJob": "cronjobs", "Role": "roles",
           "RoleBinding": "rolebindings", "ClusterRole": "clusterroles", "ClusterRoleBinding": "clusterrolebindings",
           "PersistentVolumeClaim": "persistentvolumeclaims", "PodDisruptionBudget": "poddisruptionbudgets",
           "HorizontalPodAutoscaler": "horizontalpodautoscalers", "NetworkPolicy": "networkpolicies",
           "Ingress": "ingresses", "Pod": "pods", "ResourceQuota": "resourcequotas", "LimitRange": "limitranges"}
CLUSTER_SCOPED = {"Namespace", "ClusterRole", "ClusterRoleBinding"}


def object_path(obj: dict) -> str:
    api = obj["apiVersion"]
    base = f"/api/{api}" if "/" not in api else f"/apis/{api}"
    kind = obj["kind"]
    plural = PLURALS.get(kind)
    if not plural:
        raise SimCloudError("invalid_request", f"unsupported kind {kind}")
    name = obj["metadata"]["name"]
    if kind in CLUSTER_SCOPED:
        return f"{base}/{plural}/{name}"
    return f"{base}/namespaces/{obj['metadata'].get('namespace', 'default')}/{plural}/{name}"


def parse_bindings(value: str) -> dict[tuple[str, str, str], Path]:
    out = {}
    for item in filter(None, (v.strip() for v in value.split(","))):
        key, _, d = item.partition("=")
        parts = tuple(key.split("/"))
        if len(parts) != 3 or not d:
            raise ValueError(f"bad SIMCLOUD_K8S entry {item!r}: use <project>/<env>/<name>=<dir>")
        out[parts] = Path(d)
    return out


class Clusters:
    def __init__(self, cloud: SimCloud, bindings: dict[tuple[str, str, str], Path],
                 public_server: str = "https://k8s:6443", ingress_host: str = "k8s",
                 api_server: str | None = None, audit_token: str | None = None):
        self.cloud = cloud
        self.store = cloud.store
        self.bindings = bindings
        self.public_server = public_server
        self.ingress_host = ingress_host
        self.audit_token = audit_token
        self.apis = {k: KubeAPI(d / "kubeconfig.yaml", api_server) for k, d in bindings.items()}
        self._cache: dict[str, tuple[float, dict]] = {}
        self.on_event: list = []  # (project, env, cluster, audit_event) for every recorded cluster change
        cloud.on_put.append(self._on_put)

    # ---- binding and status ---------------------------------------------------------------

    def api(self, project: str, env: str, name: str) -> KubeAPI:
        api = self.apis.get((project, env, name))
        if api is None:
            raise SimCloudError("unavailable", f"no cluster capacity is attached for {project}/{env}/{name}")
        return api

    def _on_put(self, actor, project, env, kind, name, spec, previous) -> None:
        if kind != "cluster":
            return
        status = {"phase": "unbound", "message": "no cluster capacity is attached for this environment"}
        if (project, env, name) in self.apis:
            status = {"phase": "ready" if self.apis[(project, env, name)].ready() else "provisioning",
                      "endpoint": self.public_server, "ingress_host": self.ingress_host}
            try:
                self.sync_access(project, env, name, spec)
            except SimCloudError as e:
                status["access_error"] = e.message
        self.store.set_status(project, env, "cluster", name, status)

    def resync(self) -> None:
        """Refresh status and managed RBAC for every bound cluster resource."""
        for project, env, name in self.apis:
            r = self.store.get(project, env, "cluster", name)
            if r:
                self._on_put(None, project, env, "cluster", name, r["spec"], r["spec"])

    def sync_access(self, project: str, env: str, name: str, spec: dict) -> list[str]:
        api = self.api(project, env, name)
        wanted = {}
        for entry in spec.get("access", []):
            digest = hashlib.sha256(f"{entry['principal']}|{entry['cluster_role']}".encode()).hexdigest()[:10]
            subject = [{"kind": "User", "name": entry["principal"], "apiGroup": "rbac.authorization.k8s.io"}]
            role_ref = {"kind": "ClusterRole", "name": entry["cluster_role"], "apiGroup": "rbac.authorization.k8s.io"}
            labels = {MANAGED: "true"}
            annotations = {"simcloud.dev/principal": entry["principal"]}
            if not entry.get("namespaces"):
                obj = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
                       "metadata": {"name": f"simcloud-{digest}", "labels": labels, "annotations": annotations},
                       "subjects": subject, "roleRef": role_ref}
                wanted[object_path(obj)] = obj
            for ns in entry.get("namespaces", []):
                obj = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
                       "metadata": {"name": f"simcloud-{digest}", "namespace": ns, "labels": labels,
                                    "annotations": annotations},
                       "subjects": subject, "roleRef": role_ref}
                wanted[object_path(obj)] = obj
        errors = []
        for obj in wanted.values():
            try:
                api.apply(obj)
            except SimCloudError as e:
                errors.append(e.message)
        sel = {"labelSelector": f"{MANAGED}=true"}
        existing = [("/apis/rbac.authorization.k8s.io/v1/clusterrolebindings/" + b["metadata"]["name"])
                    for b in api.list("/apis/rbac.authorization.k8s.io/v1/clusterrolebindings", **sel)]
        existing += [f"/apis/rbac.authorization.k8s.io/v1/namespaces/{b['metadata']['namespace']}/rolebindings/"
                     f"{b['metadata']['name']}" for b in api.list("/apis/rbac.authorization.k8s.io/v1/rolebindings", **sel)]
        for path in existing:
            if path not in wanted:
                api.delete(path)
        if errors:
            raise SimCloudError("invalid_request", "; ".join(errors)[:500])
        return sorted(wanted)

    # ---- credentials ------------------------------------------------------------------------

    def _authorize_connect(self, actor: Principal, project: str, env: str, name: str) -> dict:
        self.cloud._scope(project, env, "cluster")
        self.cloud.authorize(actor, "cluster:connect", srn(project, env, "cluster", name), project, env)
        r = self.store.get(project, env, "cluster", name)
        if not r:
            raise SimCloudError("not_found", f"cluster/{name} not found in {project}/{env}")
        return r

    def kubeconfig(self, actor: Principal, project: str, env: str, name: str) -> dict:
        self._authorize_connect(actor, project, env, name)
        api = self.api(project, env, name)
        cfg = yaml.safe_load(api.kubeconfig.read_text())
        ca = cfg["clusters"][0]["cluster"]["certificate-authority-data"]
        ctx = f"{project}-{env}-{name}"
        doc = {
            "apiVersion": "v1", "kind": "Config", "current-context": ctx,
            "clusters": [{"name": ctx, "cluster": {"server": self.public_server, "certificate-authority-data": ca}}],
            "users": [{"name": ctx, "user": {"exec": {
                "apiVersion": "client.authentication.k8s.io/v1", "interactiveMode": "Never",
                "command": "sc", "args": ["k8s", "token", "--env", env, name],
                "provideClusterInfo": False}}}],
            "contexts": [{"name": ctx, "context": {"cluster": ctx, "user": ctx, "namespace": "default"}}],
        }
        return {"kubeconfig": yaml.safe_dump(doc, sort_keys=False), "context": ctx, "server": self.public_server}

    def token(self, actor: Principal, project: str, env: str, name: str) -> dict:
        """An ExecCredential with a short-lived token bound to this cluster."""
        self._authorize_connect(actor, project, env, name)
        ttl = TOKEN_TTL
        if actor.claims.get("exp"):
            ttl = max(60, min(ttl, int(actor.claims["exp"] - self.cloud.clock.now())))
        token, rec = self.cloud.tokens.issue(actor.name, project, ttl,
                                             claims={**{k: v for k, v in actor.claims.items() if k != "aud"},
                                                     "aud": f"k8s:{project}/{env}/{name}"}, kind="cluster")
        exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(rec["expires_at"]))
        return {"apiVersion": "client.authentication.k8s.io/v1", "kind": "ExecCredential",
                "status": {"token": token, "expirationTimestamp": exp}}

    def token_review(self, project: str, env: str, name: str, review: dict) -> dict:
        token = (review.get("spec") or {}).get("token", "")
        out = {"apiVersion": review.get("apiVersion", "authentication.k8s.io/v1"), "kind": "TokenReview"}
        key = hashlib.sha256(f"{project}/{env}/{name}|{token}".encode()).hexdigest()
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return {**out, "status": hit[1]}
        try:
            p = self.cloud.tokens.authenticate(token)
            aud = p.claims.get("aud")
            if aud and aud != f"k8s:{project}/{env}/{name}":
                raise SimCloudError("unauthenticated", "token is bound to a different audience")
            if p.is_admin or p.project != project:
                raise SimCloudError("unauthenticated", "token is not for this project")
            self._authorize_connect(p, project, env, name)
            status = {"authenticated": True, "user": {"username": p.name, "uid": p.name,
                                                      "groups": ["simcloud:authenticated"],
                                                      "extra": {"simcloud.dev/token-id": [p.token_id or ""]}}}
        except SimCloudError as e:
            status = {"authenticated": False, "error": e.message}
        self._cache[key] = (time.monotonic(), status)
        return {**out, "status": status}

    # ---- audit -----------------------------------------------------------------------------------

    def ingest_events(self, project: str, env: str, name: str, items: list[dict]) -> int:
        """The apiserver's audit webhook: copy cluster changes into the SimCloud audit log."""
        if (project, env, name) not in self.apis:
            raise SimCloudError("not_found", f"no cluster bound as {project}/{env}/{name}")
        return sum(1 for ev in items if self._record(project, env, name, ev))

    def _record(self, project: str, env: str, name: str, ev: dict) -> bool:
        if ev.get("stage") != "ResponseComplete":
            return False
        user = (ev.get("user") or {}).get("username", "")
        if user.startswith("system:") and not user.startswith(SYSTEM_USERS_RECORDED):
            return False
        if user.startswith("system:serviceaccount:kube-system:"):
            return False
        ref = ev.get("objectRef") or {}
        verb = ev.get("verb", "")
        if verb in READ_VERBS and ref.get("resource") != "secrets":
            return False
        code = (ev.get("responseStatus") or {}).get("code", 0)
        path = "/".join(x for x in (ref.get("namespace") or "_cluster", ref.get("resource", ""), ref.get("name", ""))
                        if x)
        if ref.get("subresource"):
            path += "/" + ref["subresource"]
        resource = srn(project, env, "cluster", name) + "/" + path
        self.store.audit(user, f"k8s:{verb}", resource, "allowed" if code < 400 else "denied",
                         {"code": code, "uri": ev.get("requestURI", "")[:300], "user_agent": ev.get("userAgent", "")[:120],
                          "audit_id": ev.get("auditID")})
        if code < 400:
            for hook in self.on_event:
                hook(project, env, name, {"user": user, "verb": verb, "namespace": ref.get("namespace"),
                                          "resource": ref.get("resource"), "name": ref.get("name"),
                                          "subresource": ref.get("subresource"), "srn": resource})
        return True

    def run_forever(self, stop: threading.Event, resync_seconds: float = 30.0) -> None:
        while not stop.is_set():
            try:
                self.resync()
            except Exception as e:  # never die quietly
                self.store.audit("k8s", "k8s:resync_error", "srn:simcloud", "error", {"error": repr(e)[:300]})
            stop.wait(resync_seconds)

    # ---- world seeding (operator) -------------------------------------------------------------

    def wait_ready(self, project: str, env: str, name: str, timeout: float = 180.0) -> None:
        api = self.api(project, env, name)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if api.ready():
                return
            time.sleep(2)
        raise SimCloudError("unavailable", f"cluster {project}/{env}/{name} not ready after {timeout:.0f}s")

    def apply_manifests(self, project: str, env: str, name: str, docs: list[dict]) -> list[str]:
        api = self.api(project, env, name)
        done = []
        for d in filter(None, docs):
            api.apply(d)
            done.append(object_path(d))
        return done
