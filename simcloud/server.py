"""Run the SimCloud control plane.

Environment:
  SIMCLOUD_DB           SQLite path (default /var/lib/simcloud/state.db)
  SIMCLOUD_ADMIN_TOKEN  verifier-only operator token (required)
  SIMCLOUD_SEED         optional seed file applied at first start
  SIMCLOUD_HOST/PORT    bind address (default 127.0.0.1:7400)
"""

import os
import sys
from pathlib import Path

import uvicorn

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .api import create_app
from .clock import Clock
from .core import SimCloud, load_seed
from .dataplane import DataPlane
from .federation import Federation, load_or_create_signing_key
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


def seed_issuers(federation: Federation, seed: dict) -> None:
    for entry in seed.get("issuers", []):
        federation.register_issuer(Principal("admin"), entry["issuer"], entry["jwks"])


def main() -> int:
    admin = os.environ.get("SIMCLOUD_ADMIN_TOKEN")
    if not admin:
        print("SIMCLOUD_ADMIN_TOKEN must be set", file=sys.stderr)
        return 2
    db_path = os.environ.get("SIMCLOUD_DB", "/var/lib/simcloud/state.db")
    seed_path = os.environ.get("SIMCLOUD_SEED")
    fresh = not Path(db_path).exists()
    cloud = build(db_path, admin, seed_path)
    federation = build_federation(cloud, db_path)
    if seed_path and fresh:
        seed_issuers(federation, load_seed(seed_path))
    uvicorn.run(create_app(cloud, build_data(cloud, db_path), federation), host=os.environ.get("SIMCLOUD_HOST", "127.0.0.1"),
                port=int(os.environ.get("SIMCLOUD_PORT", "7400")), log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
