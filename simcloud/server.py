"""Run the SimCloud control plane.

Environment:
  SIMCLOUD_DB           SQLite path (default /var/lib/simcloud/state.db)
  SIMCLOUD_ADMIN_TOKEN  verifier-only operator token (required)
  SIMCLOUD_SEED         optional seed file applied at first start
  SIMCLOUD_HOST/PORT    bind address (default 127.0.0.1:7400)
  SIMCLOUD_ROUTER_PORT  load balancer port (default 7480)
  SIMCLOUD_INSTANCE_PORTS  port range for service instances (default 21000-21999)
  SIMCLOUD_PUBLIC_URL / SIMCLOUD_PUBLIC_ROUTER_URL  addresses advertised to services and in status
                        (default http://127.0.0.1:<port>, right when containers share a network namespace)
  SIMCLOUD_PG           "0" disables managed Postgres (default: on when Postgres binaries are found)
  SIMCLOUD_PG_PORT / SIMCLOUD_PG_LISTEN / SIMCLOUD_PG_PUBLIC_HOST  cluster port (5433), listen address
                        (127.0.0.1), host put in DSNs (127.0.0.1)
  SIMCLOUD_REGISTRY_PORT  image registry (pull API) port (default 7500; "0" disables the registry)
  SIMCLOUD_BASE_IMAGES  directory of base-image OCI layouts (default /opt/simcloud/base-images)
  SIMCLOUD_K8S          managed Kubernetes bindings, see kubernetes.py (default: none)
  SIMCLOUD_K8S_SERVER / SIMCLOUD_K8S_INGRESS_HOST  addresses advertised for clusters
  SIMCLOUD_K8S_AUDIT_TOKEN  shared token the clusters' audit webhooks present
"""

import os
import sys
import threading
from pathlib import Path

import uvicorn

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import privsep
from .api import create_app
from .clock import Clock
from .core import SimCloud, load_seed
from .databases import Databases, Postgres, find_pg_bin
from .dataplane import DataPlane
from .delivery import Delivery
from .router import serve_router
from .runtime import Supervisor
from .seeding import apply_world
from .federation import Federation, load_or_create_signing_key
from .incidents import Guard
from .jobs import Jobs
from .kubernetes import Clusters, parse_bindings
from .registry import Registry, create_registry_app
from .identity import Principal
from .store import Store


def _key_file(path: Path) -> bytes:
    """Load or create a 256-bit key kept next to the state DB (mode 600), so
    secrets and signed URLs survive a control-plane restart."""
    if path.exists():
        return path.read_bytes()
    key = AESGCM.generate_key(bit_length=256)
    path.write_bytes(key)
    os.chmod(path, 0o600)
    return key


def build_data(cloud: SimCloud, db_path: str) -> DataPlane:
    if db_path == ":memory:":
        return DataPlane(cloud)
    base = Path(db_path)
    return DataPlane(cloud, _key_file(base.with_suffix(".kms")), _key_file(base.with_suffix(".urlkey")))


def build_federation(cloud: SimCloud, db_path: str) -> Federation:
    if db_path == ":memory:":
        return Federation(cloud)
    return Federation(cloud, load_or_create_signing_key(Path(db_path).with_suffix(".oidc.pem")))


def build(db_path: str, admin_token: str, seed_path: str | None = None, clock: Clock | None = None) -> SimCloud:
    clock = clock or Clock()
    fresh = db_path == ":memory:" or not Path(db_path).exists()
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    cloud = SimCloud(Store(db_path, clock), clock, admin_token)
    if seed_path and fresh:
        cloud.apply_seed(load_seed(seed_path), Principal("admin"))
    return cloud


def _lock_down(data_dir: Path) -> None:
    """Operator material is the operator's alone. Workloads get their own directories later
    (`privsep.give`); everything else under the data directory stays root-only."""
    os.umask(0o077)
    data_dir.mkdir(parents=True, exist_ok=True)
    # Traversable, not listable: workloads reach their own workdirs beneath it and nothing else.
    os.chmod(data_dir, 0o711)
    for name in ("state.db", "state.oidc.pem", "state.kms", "state.urlkey", "pg.admin", "pg.log", "logs", "artifacts",
                 "job-logs"):
        privsep.keep_private(data_dir / name)
    for extra in ("/evidence", "/seed"):
        privsep.keep_private(Path(extra))


def main() -> int:
    admin = os.environ.get("SIMCLOUD_ADMIN_TOKEN")
    if not admin:
        print("SIMCLOUD_ADMIN_TOKEN must be set", file=sys.stderr)
        return 2
    db_path = os.environ.get("SIMCLOUD_DB", "/var/lib/simcloud/state.db")
    if db_path != ":memory:":
        _lock_down(Path(db_path).parent)
    seed_path = os.environ.get("SIMCLOUD_SEED")
    fresh = not Path(db_path).exists()
    cloud = build(db_path, admin)  # the seed is applied below, once every component is attached
    federation = build_federation(cloud, db_path)
    host = os.environ.get("SIMCLOUD_HOST", "127.0.0.1")
    port = int(os.environ.get("SIMCLOUD_PORT", "7400"))
    router_port = int(os.environ.get("SIMCLOUD_ROUTER_PORT", "7480"))
    lo, hi = (int(x) for x in os.environ.get("SIMCLOUD_INSTANCE_PORTS", "21000-21999").split("-"))
    data = build_data(cloud, db_path)
    supervisor = Supervisor(Path(db_path).parent, port_range=(lo, hi + 1))
    delivery = Delivery(cloud, data, federation, supervisor, Path(db_path).parent,
                        public_url=os.environ.get("SIMCLOUD_PUBLIC_URL", f"http://127.0.0.1:{port}"),
                        router_url=os.environ.get("SIMCLOUD_PUBLIC_ROUTER_URL", f"http://127.0.0.1:{router_port}"))
    databases = None
    if os.environ.get("SIMCLOUD_PG", "1") != "0" and find_pg_bin():
        pg = Postgres(Path(db_path).parent, port=int(os.environ.get("SIMCLOUD_PG_PORT", "5433")),
                      listen=os.environ.get("SIMCLOUD_PG_LISTEN", "127.0.0.1"))
        databases = Databases(cloud, pg, public_host=os.environ.get("SIMCLOUD_PG_PUBLIC_HOST", "127.0.0.1"))
        delivery.databases = databases
    registry = None
    registry_port = int(os.environ.get("SIMCLOUD_REGISTRY_PORT", "7500"))
    if registry_port:
        registry = Registry(cloud, Path(db_path).parent,
                            Path(os.environ.get("SIMCLOUD_BASE_IMAGES", "/opt/simcloud/base-images")))
        reg_server = uvicorn.Server(uvicorn.Config(create_registry_app(registry), host=host, port=registry_port,
                                                   log_level="warning"))
        threading.Thread(target=reg_server.run, name="simcloud-registry", daemon=True).start()
    clusters = None
    if os.environ.get("SIMCLOUD_K8S"):
        clusters = Clusters(cloud, parse_bindings(os.environ["SIMCLOUD_K8S"]),
                            public_server=os.environ.get("SIMCLOUD_K8S_SERVER", "https://k8s:6443"),
                            ingress_host=os.environ.get("SIMCLOUD_K8S_INGRESS_HOST", "k8s"),
                            api_server=os.environ.get("SIMCLOUD_K8S_SERVER", "https://k8s:6443"),
                            audit_token=os.environ.get("SIMCLOUD_K8S_AUDIT_TOKEN"))
    jobs = Jobs(cloud, delivery, Path(db_path).parent)
    serve_router(delivery.router, supervisor, host, router_port)
    delivery.recover()
    guard = Guard(cloud, secret_values=data.secret_values, service_logs=supervisor.logs.by_service,
                  router_url=f"http://127.0.0.1:{router_port}", databases=databases, clusters=clusters)
    if seed_path and fresh:
        seed = load_seed(seed_path)
        cloud.apply_seed(seed, Principal("admin"))
        apply_world(seed, data=data, federation=federation, delivery=delivery, guard=guard, databases=databases,
                    registry=registry, clusters=clusters, jobs=jobs)
    stop = threading.Event()
    threading.Thread(target=guard.run_forever, args=(stop,), name="simcloud-guard", daemon=True).start()
    if clusters is not None:
        threading.Thread(target=clusters.run_forever, args=(stop,), name="simcloud-k8s-audit", daemon=True).start()
    threading.Thread(target=jobs.run_forever, args=(stop,), name="simcloud-jobs", daemon=True).start()
    try:
        uvicorn.run(create_app(cloud, data, federation, delivery, guard, databases, registry, clusters, jobs),
                    host=host, port=port,
                    log_level="warning")
    finally:
        stop.set()
        jobs.shutdown()
        supervisor.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
