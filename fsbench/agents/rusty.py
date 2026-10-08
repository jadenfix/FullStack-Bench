"""Harbor agent for the open track: rusty, a terminal coding agent written in Rust.

    uv run harbor run -p tasks/<task> -a fsbench.agents.rusty:Rusty \
        -m nvidia/nemotron-3-super-120b-a12b --env-file .env \
        --ak binary=/path/to/rusty-linux --job-name rusty-<task>-001 -o jobs

`binary` is a static Linux build for the container's architecture
(`docker build --target bin -o out .` in the rusty repo). It is uploaded rather
than downloaded, so offline variants work. Keys come from the env file
(`NVIDIA_API_KEY`, plus `NVIDIA_API_KEY_2`... for rotation) and reach the
container as environment variables, never on a command line.

Options (`--ak name=value`):
- `binary`: host path to the Linux binary (or set `RUSTY_LINUX_BIN`)
- `mode`: `goal` (default; works until it closes the goal with evidence) or `prompt` (one turn)
- `agents`: `off` (default), `sub`, `swarm` or `auto`
- `max_turns`: goal-mode turn cap (default 25)
- `execution`: `standard` (default), `careful` or `vibe`; pin this independently of `mode`
- `max_requests`, `max_budget_tokens`, `budget_secs`: Rusty's shared model budget
  (every HTTP attempt by the lead, reviewers, workers and compaction). Unset means
  unbounded; once one is set, Rusty applies its own defaults to the others, so set
  all three for a paired cohort.

The task's stdio MCP servers are written to `/logs/agent/rusty-mcp.json` and handed to
Rusty through `RUSTY_MCP_CONFIG` (Rusty builds that predate MCP ignore it).

The run writes `/logs/agent/rusty.txt` (terminal output) and
`/logs/agent/rusty.trajectory.json` (OpenAI-format messages plus token
totals), which `fsbench.digest` reads like mini-swe-agent's trajectory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path
from typing import Any

from harbor.agents.installed.base import (
    ApiInternalServerError,
    ApiRateLimitError,
    BaseInstalledAgent,
    ErrorPattern,
    with_prompt_template,
)
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

REMOTE_BIN = "/usr/local/bin/rusty"
LOG = "/logs/agent/rusty.txt"
TRAJECTORY = "/logs/agent/rusty.trajectory.json"
MCP_CONFIG = "/logs/agent/rusty-mcp.json"


def build_command(instruction: str, *, mode: str, agents: str) -> str:
    """The shell command run inside the task container."""
    if mode not in ("goal", "prompt"):
        raise ValueError(f"mode must be goal or prompt, not {mode!r}")
    if agents not in ("off", "sub", "swarm", "auto"):
        raise ValueError(f"agents must be off, sub, swarm or auto, not {agents!r}")
    task = ["--goal", instruction] if mode == "goal" else [instruction]
    args = [REMOTE_BIN, "--yolo", "--stats", "--agents", agents, "--trajectory", TRAJECTORY, *task]
    pipeline = f"{shlex.join(args)} </dev/null 2>&1 | tee {shlex.quote(LOG)}"
    return "bash -o pipefail -c " + shlex.quote(pipeline)


LIMITS = {"max_requests": "RUSTY_MAX_REQUESTS", "max_budget_tokens": "RUSTY_MAX_BUDGET_TOKENS",
          "budget_secs": "RUSTY_BUDGET_SECS"}


def build_env(model: str, keys: dict[str, str], base_url: str | None, max_turns: int,
              execution: str = "standard", limits: dict[str, int] | None = None) -> dict[str, str]:
    """Environment for the run: model, keys for rotation, and quiet output."""
    if execution not in ("standard", "careful", "vibe"):
        raise ValueError("execution must be standard, careful or vibe")
    env = {
        "RUSTY_MODEL": model,
        "RUSTY_HOME": "/logs/agent/rusty-home",
        "RUSTY_GOAL_MAX_TURNS": str(max_turns),
        "RUSTY_MODE": execution,
        "RUSTY_NO_DOTENV": "1",
        # The task container is disposable and the verifier scores harm, so
        # destructive steps a task needs must not be refused for want of a person.
        "RUSTY_ALLOW_DESTRUCTIVE": "1",
        "NO_COLOR": "1",
        **{k: v for k, v in keys.items() if k == "NVIDIA_API_KEY" or k.startswith("NVIDIA_API_KEY_")},
    }
    if base_url:
        env["RUSTY_BASE_URL"] = base_url
    for name, value in (limits or {}).items():
        if value < 1:
            raise ValueError(f"{name} must be at least 1")
        env[LIMITS[name]] = str(value)
    return env


def collect_keys(prefixed: dict[str, str], api_key: str | None) -> dict[str, str]:
    """Full key names from Harbor's prefix lookup, which strips the prefix
    (`NVIDIA_API_KEY_2` arrives as `_2`), plus the connection's own key."""
    keys = {f"NVIDIA_API_KEY{suffix}": v for suffix, v in prefixed.items()}
    if api_key:
        keys.setdefault("NVIDIA_API_KEY", api_key)
    return keys


def mcp_config(servers: list[Any]) -> dict | None:
    """The task's stdio MCP servers in the `.mcp.json` format Rusty reads.

    Rusty speaks only the stdio transport, so URL servers are left out."""
    stdio = {s.name: {"command": s.command, "args": list(s.args)}
             for s in servers if s.transport == "stdio" and s.command}
    return {"mcpServers": stdio} if stdio else None


def read_totals(trajectory: Path) -> tuple[int | None, int | None]:
    """Prompt and completion tokens from a trajectory file, if it exists."""
    try:
        totals = json.loads(trajectory.read_text())["totals"]
        return int(totals["prompt_tokens"]), int(totals["completion_tokens"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def read_goal(trajectory: Path) -> dict[str, Any]:
    """How the goal ended: `done`, `blocked`, or `active` when the turn cap or an
    error stopped it first. Rusty exits 0 for all three, so this is the only record."""
    try:
        goal = json.loads(trajectory.read_text())["goal"]
        status = goal["status"]
        name = status if isinstance(status, str) else next(iter(status))
        return {"goal_status": name.lower(), "goal_turns": int(goal["turns"])}
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return {}


def sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Rusty(BaseInstalledAgent):
    # Harbor's defaults search the whole transcript, and rusty's --stats line
    # always ends with `"rate_limited":0`, so every failed run read as a provider
    # rate limit (retryable infrastructure) and skipped verification. Match only
    # rusty's own final error for an exhausted provider retry; anything else is
    # the solver's failure.
    ERROR_PATTERNS = [
        ErrorPattern(r"(?m)^error: giving up after .*: HTTP 429", ApiRateLimitError),
        ErrorPattern(r"(?m)^error: giving up after .*: HTTP 5\d\d", ApiInternalServerError),
    ]
    MODEL_CONNECTION = ModelConnectionSpec(
        default_provider="nvidia",
        api_key_envs=("NVIDIA_API_KEY",),
        base_url_envs=("RUSTY_BASE_URL", "NVIDIA_API_BASE"),
    )

    def __init__(self, logs_dir: Path, *args: Any, **kwargs: Any) -> None:
        self._binary = kwargs.pop("binary", None) or os.environ.get("RUSTY_LINUX_BIN")
        self._mode = str(kwargs.pop("mode", "goal"))
        self._agents = str(kwargs.pop("agents", "off"))
        self._max_turns = int(kwargs.pop("max_turns", 25))
        self._execution = str(kwargs.pop("execution", "standard"))
        self._binary_sha256: str | None = None
        self._limits = {name: int(kwargs.pop(name)) for name in LIMITS if kwargs.get(name) is not None}
        if self._execution not in ("standard", "careful", "vibe"):
            raise ValueError("execution must be standard, careful or vibe")
        super().__init__(logs_dir, *args, **kwargs)

    @staticmethod
    def name() -> str:
        return "rusty"

    def get_version_command(self) -> str | None:
        return f"{REMOTE_BIN} --version"

    def parse_version(self, stdout: str) -> str:
        return stdout.strip().removeprefix("rusty").strip()

    async def install(self, environment: BaseEnvironment) -> None:
        if not self._binary or not Path(self._binary).is_file():
            raise FileNotFoundError(
                "rusty needs a Linux binary: pass --ak binary=/path/to/rusty or set RUSTY_LINUX_BIN"
            )
        self._binary_sha256 = sha256(self._binary)
        await environment.upload_file(self._binary, "/tmp/rusty")
        await self.exec_as_root(environment, command=f"install -m 0755 /tmp/rusty {REMOTE_BIN} && rm /tmp/rusty")
        await self.exec_as_agent(environment, command=f"{REMOTE_BIN} --version")

    def populate_context_post_run(self, context: AgentContext) -> None:
        prompt, completion = read_totals(self.logs_dir / "rusty.trajectory.json")
        context.n_input_tokens = prompt
        context.n_output_tokens = completion
        context.metadata = {
            **(context.metadata or {}),
            **read_goal(self.logs_dir / "rusty.trajectory.json"),
            "binary_sha256": self._binary_sha256,
            "max_turns": self._max_turns,
            "agents": self._agents,
            "execution": self._execution,
            "mcp_servers": sorted((mcp_config(self.mcp_servers) or {"mcpServers": {}})["mcpServers"]),
            **self._limits,
        }

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        if not self.model_name:
            raise ValueError("pass a model with -m, e.g. -m nvidia/nemotron-3-super-120b-a12b")
        if self.skills_dir:
            instruction += (
                f"\n\nReference docs for this environment's tools are under {self.skills_dir}. "
                "Read the relevant ones before you start."
            )
        connection = self.model_connection
        keys = collect_keys(self._get_env_prefixed("NVIDIA_API_KEY"), connection.api_key)
        if "NVIDIA_API_KEY" not in keys:
            raise ValueError("NVIDIA_API_KEY is not set; add it to the --env-file")
        env = build_env(self.model_name, keys, connection.configured_base_url, self._max_turns, self._execution,
                        self._limits)
        if (config := mcp_config(self.mcp_servers)) is not None:
            await self.exec_as_agent(
                environment, command=f"printf %s {shlex.quote(json.dumps(config))} > {MCP_CONFIG}")
            env["RUSTY_MCP_CONFIG"] = MCP_CONFIG
        await self.exec_as_agent(
            environment,
            command=build_command(instruction, mode=self._mode, agents=self._agents),
            env=env,
        )
