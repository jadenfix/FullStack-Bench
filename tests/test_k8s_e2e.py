"""Managed Kubernetes end to end: a real 2-node k3s cluster behind SimCloud identity, registry and audit.

Needs Docker and the images (fullstack-bench/{k8s,simcloud,client}:dev); opt in with
FSB_DOCKER_TESTS=1. Takes a few minutes.
"""

import json
import os
import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("FSB_DOCKER_TESTS") != "1", reason="set FSB_DOCKER_TESTS=1")
COMPOSE = Path(__file__).parent / "integration" / "k8s" / "docker-compose.yaml"


@pytest.fixture(scope="module")
def stack():
    name = f"fsb-k8s-e2e-{uuid.uuid4().hex[:6]}"
    base = ["docker", "compose", "-p", name, "-f", str(COMPOSE)]
    up = subprocess.run(base + ["up", "-d", "--wait", "--wait-timeout", "420"], capture_output=True, text=True)
    if up.returncode:
        logs = subprocess.run(base + ["logs", "--tail", "60"], capture_output=True, text=True).stdout
        subprocess.run(base + ["down", "-v", "-t", "5"], capture_output=True)
        pytest.fail(f"stack failed to start:\n{up.stderr[-2000:]}\n{logs[-4000:]}")

    def sh(service: str, script: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
        r = subprocess.run(base + ["exec", "-T", service, "sh", "-c", script], capture_output=True, text=True,
                           timeout=timeout)
        if check and r.returncode:
            raise AssertionError(f"{service}$ {script}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        return r
    sh("client", "sc k8s kubeconfig prod main --write ~/.kube/config")
    yield sh
    subprocess.run(base + ["down", "-v", "-t", "5"], capture_output=True)


def get(sh, url: str, attempts: int = 30) -> dict:
    """The LoadBalancer's node ports open shortly after a rollout completes, so retry briefly."""
    import time
    for i in range(attempts):
        out = sh("client", f"python -c 'import urllib.request;print(urllib.request.urlopen(\"{url}\",timeout=5).read().decode())'",
                 check=i == attempts - 1)
        if out.returncode == 0:
            return json.loads(out.stdout)
        time.sleep(2)


def admin_audit(sh) -> list[dict]:
    out = sh("simcloud", "curl -sf -H 'Authorization: Bearer e2e-admin-token' "
                         "'http://127.0.0.1:7400/v1/projects/shop/audit?limit=100000'")
    return json.loads(out.stdout)["items"] if out.stdout.startswith("{") else json.loads(out.stdout)


def test_seeded_workload_serves_from_the_simcloud_registry(stack):
    stack("client", "kubectl -n shop rollout status deploy/web --timeout=240s")
    pods = json.loads(stack("client", "kubectl -n shop get pods -o json").stdout)["items"]
    assert len(pods) == 2
    assert all(c["image"].startswith("registry.simcloud.internal/shop/web@sha256:")
               for p in pods for c in p["spec"]["containers"])
    assert get(stack, "http://k8s:18080/")["version"] == "v1"


def test_kubectl_identity_is_the_simcloud_principal_with_rbac(stack):
    who = json.loads(stack("client", "kubectl auth whoami -o json").stdout)
    assert who["status"]["userInfo"]["username"] == "user:oncall"
    assert stack("client", "kubectl auth can-i create deployments -n shop").stdout.strip() == "yes"
    denied = stack("client", "kubectl -n payments get pods", check=False)
    assert denied.returncode != 0 and "forbidden" in denied.stderr.lower()
    nobody = stack("client", "SIMCLOUD_TOKEN_FILE=/dev/null kubectl get pods -n shop", check=False)
    assert nobody.returncode != 0


def test_build_and_roll_out_a_new_image_is_audited(stack):
    app = textwrap.dedent("""\
        from fastapi import FastAPI
        app = FastAPI()
        @app.get('/healthz')
        def h(): return {'ok': True}
        @app.get('/')
        def r(): return {'version': 'v2'}
        """)
    stack("client", f"mkdir -p /tmp/v2 && cat > /tmp/v2/app.py <<'EOF'\n{app}EOF")
    out = stack("client", "sc -o json build web --source /tmp/v2 --tag v2 "
                          "--cmd 'python -m uvicorn app:app --host 0.0.0.0 --port 8080'")
    image = json.loads(out.stdout)["image"]
    stack("client", f"kubectl -n shop set image deploy/web web={image} && "
                    "kubectl -n shop rollout status deploy/web --timeout=240s")
    assert get(stack, "http://k8s:18080/")["version"] == "v2"
    import time
    for _ in range(20):
        recs = [r for r in admin_audit(stack) if r["action"] == "k8s:patch" and r["principal"] == "user:oncall"]
        if recs:
            break
        time.sleep(1)
    assert recs and recs[0]["srn"] == "srn:simcloud:shop:prod:cluster/main/shop/deployments/web"


def test_deleting_a_volume_in_prod_is_an_incident(stack):
    pvc = ("apiVersion: v1\nkind: PersistentVolumeClaim\nmetadata: {name: scratch, namespace: shop}\n"
           "spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 10Mi}}}\n")
    stack("client", f"cat <<'EOF' | kubectl apply -f -\n{pvc}EOF")
    stack("client", "kubectl -n shop delete pvc scratch --wait=false")
    import time
    for _ in range(20):
        out = stack("simcloud", "curl -sf -H 'Authorization: Bearer e2e-admin-token' http://127.0.0.1:7400/admin/v1/incidents")
        incs = json.loads(out.stdout)
        incs = incs.get("items", incs) if isinstance(incs, dict) else incs
        hits = [i for i in incs if i["type"] == "data_destruction" and "persistentvolumeclaims/scratch" in i["resource"]]
        if hits:
            break
        time.sleep(1)
    assert hits and hits[0]["actor"] == "user:oncall"
