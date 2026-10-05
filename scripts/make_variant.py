"""Derive a task's offline variant from the online task (never hand-maintained).

    uv run python scripts/make_variant.py tasks/ship-checkout-v2 [--out variants/offline]

Offline means the agent's container and SimCloud sit on an `internal` compose network with no
route to the internet. The only way out is `llm-proxy`, which forwards to one upstream, the
model endpoint (simcloud/llmproxy.py). Run the agent with its base URL pointed at the proxy:

    OPENAI_API_BASE=http://llm-proxy:8088/v1   (in the env file passed to harbor --env-file)

This works on any Docker host. Harbor's own `allowlist` network mode needs nftables FIB support
in the Docker kernel, which Docker Desktop lacks.

Changes from the online task:
- environment/docker-compose.yaml: main and simcloud only on `inner` (internal); `llm-proxy` on
  `inner` and `outer`.
- The agent image pre-installs mini-swe-agent and the tools Harbor checks for, because agent setup
  can't download anything offline.
- The reference solution starts with an egress canary that prints EGRESS_BLOCKED/EGRESS_OPEN.
"""

import argparse
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = "https://integrate.api.nvidia.com"

PREINSTALL = r'''
# Offline variant: Harbor's mini-swe-agent setup can't download, so install it now.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && export PATH="$HOME/.local/bin:$PATH" && uv python install 3.12 \
    && uv tool install --python 3.12 mini-swe-agent --with litellm --with orjson --with fastapi \
    && echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" && mini-swe-agent --help >/dev/null
'''

CANARY = [
    "for url in https://pypi.org/simple/ https://github.com https://integrate.api.nvidia.com/v1/models "
    "http://llm-proxy:8088/healthz; do",
    '  if curl -s -o /dev/null -m 8 "$url"; then echo "EGRESS_OPEN $url"; else echo "EGRESS_BLOCKED $url"; fi',
    "done",
]


def make_offline(task: Path, out_root: Path, upstream: str = UPSTREAM) -> Path:
    dst = out_root / task.name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(task, dst, ignore=shutil.ignore_patterns("wrong_solutions"))
    toml = (dst / "task.toml").read_text()
    if "network_mode" in toml:
        raise SystemExit("task already sets network_mode; refusing to guess")
    (dst / "task.toml").write_text(toml.replace('name = "fullstack-bench/', 'name = "fullstack-bench-offline/', 1))

    df = dst / "environment" / "Dockerfile"
    text = df.read_text()
    if "WORKDIR /app" not in text:
        raise SystemExit("agent Dockerfile has no WORKDIR /app to anchor the pre-install")
    df.write_text(text.replace("WORKDIR /app", PREINSTALL.strip() + "\nWORKDIR /app"))

    compose_path = dst / "environment" / "docker-compose.yaml"
    compose = yaml.safe_load(compose_path.read_text())
    services = compose["services"]
    for name in ("main", "simcloud"):
        if "networks" in services[name] or "network_mode" in services[name]:
            raise SystemExit(f"service {name} already sets networking; refusing to guess")
        services[name]["networks"] = ["inner"]
    services["llm-proxy"] = {
        "build": services["simcloud"]["build"],
        "entrypoint": ["python", "-m", "simcloud.llmproxy"],
        "environment": {"LLMPROXY_UPSTREAM": upstream},
        "networks": ["inner", "outer"],
        "healthcheck": {"test": ["CMD", "curl", "-sf", "http://127.0.0.1:8088/healthz"], "interval": "5s",
                        "retries": 12},
    }
    services["main"].setdefault("depends_on", {})["llm-proxy"] = {"condition": "service_healthy"}
    compose["networks"] = {"inner": {"internal": True}, "outer": {}}
    compose_path.write_text(yaml.safe_dump(compose, sort_keys=False))

    solve = dst / "solution" / "solve.sh"
    lines = solve.read_text().splitlines()
    at = next(i for i, l in enumerate(lines) if l.startswith("set -"))
    solve.write_text("\n".join(lines[:at + 1] + CANARY + lines[at + 1:]) + "\n")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "variants" / "offline")
    ap.add_argument("--upstream", default=UPSTREAM)
    args = ap.parse_args()
    for t in args.tasks:
        print(make_offline(t.resolve(), args.out, args.upstream))
    return 0


if __name__ == "__main__":
    sys.exit(main())
