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

from .api import create_app
from .clock import Clock
from .core import SimCloud, load_seed
from .identity import Principal
from .store import Store


def build(db_path: str, admin_token: str, seed_path: str | None = None, clock: Clock | None = None) -> SimCloud:
    clock = clock or Clock()
    fresh = db_path == ":memory:" or not Path(db_path).exists()
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    cloud = SimCloud(Store(db_path, clock), clock, admin_token)
    if seed_path and fresh:
        cloud.apply_seed(load_seed(seed_path), Principal("admin"))
    return cloud


def main() -> int:
    admin = os.environ.get("SIMCLOUD_ADMIN_TOKEN")
    if not admin:
        print("SIMCLOUD_ADMIN_TOKEN must be set", file=sys.stderr)
        return 2
    cloud = build(os.environ.get("SIMCLOUD_DB", "/var/lib/simcloud/state.db"), admin, os.environ.get("SIMCLOUD_SEED"))
    uvicorn.run(create_app(cloud), host=os.environ.get("SIMCLOUD_HOST", "127.0.0.1"),
                port=int(os.environ.get("SIMCLOUD_PORT", "7400")), log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
