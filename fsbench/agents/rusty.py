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
- `memory`: Rusty's memory level, `off` (default) or any level the installed binary
  accepts. Always set explicitly, because Rusty's own default changes between releases and
  a paired cohort must not change with it.
- `verify`: an operator-selected public acceptance command (goal mode only), passed as
  Rusty's `--verify`; `verify_timeout` (1-600 s, default 120). It must be a check every
  compared harness may also run. The hidden grader is never passed here.
- `max_requests`, `max_budget_tokens`, `budget_secs`: Rusty's own client-side model budget
  (lead, reviewers, workers and compaction). Rusty counts attempts by its own rules, which
  are not the gateway's admitted calls, so the gateway receipt is the episode's budget
  record. Unset means unbounded; once one is set, Rusty applies its own defaults to the
  others, so set all three for a paired cohort.
- `allow_destructive`: `false` (default). Rusty refuses destructive steps nobody can approve,
  and that refusal is part of what a study measures. `true` sets `RUSTY_ALLOW_DESTRUCTIVE`,
  a separate experimental condition that must be pinned and reported (Rusty's safety docs
  treat it the same way). With it set, destructive calls run unattended. The trial metadata
  records the setting, every RUSTY_* variable the run was given, and Rusty's `safety` record.
- `allow_missing_mcp`: `false` (default). A task MCP server Rusty cannot use (anything but
  stdio) is a coverage limitation: the run stops before any model call with
  `RustyCoverageLimitation`, which must be reported as such and never replaced as
  infrastructure. With `true`, the run continues without those servers and the metadata
  says `coverage: restricted`, so it can only count toward an explicitly restricted
  comparison.

Before the run, the installed binary's `--capabilities` (or, for binaries that predate it,
its `--help`) is read, and every pinned option (memory, execution, agents, verify) must be
one it accepts. A setting it rejects is the operator's
configuration error (`RustyConfigurationError`), not a solver or harness result.

Trial metadata keeps three completion events apart (see `read_completion`): how often
the model proposed completion, how often Rusty's runtime rejected a proposal, and whether
it finally accepted one. The independent verdict is the verifier's reward, never these.

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
import re
import shlex
from pathlib import Path
from typing import Any

from harbor.agents.installed.base import (
    ApiInternalServerError,
    ApiRateLimitError,
    BaseInstalledAgent,
    ErrorPattern,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

class RustyConfigurationError(ValueError):
    """The operator pinned a setting the installed binary does not accept. Fix the
    configuration and rerun; the episode measured nothing about Rusty or the model."""


class RustyCoverageLimitation(RuntimeError):
    """The task requires a capability Rusty explicitly lacks. Report it as a coverage
    limitation of the harness; it is neither infrastructure nor a solver failure."""


REMOTE_BIN = "/usr/local/bin/rusty"
LOG = "/logs/agent/rusty.txt"
TRAJECTORY = "/logs/agent/rusty.trajectory.json"
MCP_CONFIG = "/logs/agent/rusty-mcp.json"


def build_command(instruction: str, *, mode: str, agents: str, verify: str | None = None,
                  verify_timeout: int = 120) -> str:
    """The shell command run inside the task container."""
    if mode not in ("goal", "prompt"):
        raise ValueError(f"mode must be goal or prompt, not {mode!r}")
    if agents not in ("off", "sub", "swarm", "auto"):
        raise ValueError(f"agents must be off, sub, swarm or auto, not {agents!r}")
    task = ["--goal", instruction] if mode == "goal" else [instruction]
    check = []
    if verify is not None:
        if mode != "goal" or not verify.strip():
            raise ValueError("verify needs goal mode and a non-empty command")
        if not 1 <= verify_timeout <= 600:
            raise ValueError("verify_timeout must be between 1 and 600 seconds")
        check = ["--verify", verify, "--verify-timeout", str(verify_timeout)]
    args = [REMOTE_BIN, "--yolo", "--stats", "--agents", agents, "--trajectory", TRAJECTORY, *check, *task]
    pipeline = f"{shlex.join(args)} </dev/null 2>&1 | tee {shlex.quote(LOG)}"
    return "bash -o pipefail -c " + shlex.quote(pipeline)


LIMITS = {"max_requests": "RUSTY_MAX_REQUESTS", "max_budget_tokens": "RUSTY_MAX_BUDGET_TOKENS",
          "budget_secs": "RUSTY_BUDGET_SECS"}


def build_env(model: str, keys: dict[str, str], base_url: str | None, max_turns: int,
              execution: str = "standard", limits: dict[str, int] | None = None,
              memory: str = "off", allow_destructive: bool = False) -> dict[str, str]:
    """Environment for the run: model, keys for rotation, and quiet output."""
    if execution not in ("standard", "careful", "vibe"):
        raise ValueError("execution must be standard, careful or vibe")
    if not memory.isalpha() or memory != memory.lower():
        raise ValueError(f"memory must be a lowercase level name, not {memory!r}")
    env = {
        "RUSTY_MODEL": model,
        "RUSTY_HOME": "/logs/agent/rusty-home",
        "RUSTY_GOAL_MAX_TURNS": str(max_turns),
        "RUSTY_MODE": execution,
        "RUSTY_MEMORY": memory,
        "RUSTY_NO_DOTENV": "1",
        "NO_COLOR": "1",
        **{k: v for k, v in keys.items() if k == "NVIDIA_API_KEY" or k.startswith("NVIDIA_API_KEY_")},
    }
    if base_url:
        env["RUSTY_BASE_URL"] = base_url
    # Rusty refuses destructive steps when nobody can approve them; that refusal is part of the
    # system under test. Turning it off is a separate, pinned treatment, never a default.
    if allow_destructive:
        env["RUSTY_ALLOW_DESTRUCTIVE"] = "1"
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

    Rusty speaks only the stdio transport, so URL servers are left out (see `mcp_dropped`)."""
    stdio = {s.name: {"command": s.command, "args": list(s.args)}
             for s in servers if s.transport == "stdio" and s.command}
    return {"mcpServers": stdio} if stdio else None


def mcp_dropped(servers: list[Any], transports: tuple[str, ...] = ("stdio",)) -> list[str]:
    """Task MCP servers Rusty cannot be given, as `name (transport)`. Rusty's config file
    carries only command-launched servers, so a transport counts only with a command."""
    return sorted(f"{s.name} ({s.transport})" for s in servers if not (s.transport in transports and s.command))


def help_values(help_text: str, flag: str) -> set[str] | None:
    """The words in one option's `--help` paragraph, with parentheticals and clap's
    `[env: ...]`/`[default: ...]` notes removed: the values that option names. None when
    the binary has no such option."""
    lines = help_text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if re.match(rf"\s*(-\w, )?{re.escape(flag)}\b", line)), None)
    if start is None:
        return None
    para = [lines[start]]
    for line in lines[start + 1:]:
        if re.match(r"\s*-", line) or not line.strip():
            break
        para.append(line)
    text = " ".join(para[1:]) if len(para) > 1 else para[0].split(flag, 1)[1]
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
    return set(re.findall(r"[a-z][a-z-]*", text.lower()))


def read_capabilities(text: str) -> dict | None:
    """`rusty --capabilities` output (contract 1), or None if the binary predates it."""
    try:
        caps = json.loads(text)
    except ValueError:
        return None
    return caps if isinstance(caps, dict) and isinstance(caps.get("contract"), int) else None


def unsupported_by(caps: dict, *, memory: str, execution: str, agents: str, verify: bool) -> list[str]:
    """Pinned settings the binary's own capability listing does not accept."""
    problems = []
    for key, value in (("memory", memory), ("mode", execution), ("agents", agents)):
        if value not in (caps.get(key) or []):
            problems.append(f"the binary's {key} values {caps.get(key)} do not include {value!r}")
    if verify and not (caps.get("verify") or {}).get("supported"):
        problems.append("the binary does not support --verify")
    return problems


def unsupported(help_text: str, *, memory: str, execution: str, agents: str, verify: bool) -> list[str]:
    """Pinned settings the installed binary does not accept, read from its `--help`."""
    problems = []
    for flag, value in (("--memory", memory), ("--mode", execution), ("--agents", agents)):
        words = help_values(help_text, flag)
        if words is None:
            problems.append(f"the binary has no {flag} option")
        elif value not in words:
            problems.append(f"the binary's {flag} does not list {value!r}")
    if verify and help_values(help_text, "--verify") is None:
        problems.append("the binary has no --verify option")
    return problems


def read_totals(trajectory: Path) -> tuple[int | None, int | None]:
    """Prompt and completion tokens from a trajectory file, if it exists."""
    try:
        totals = json.loads(trajectory.read_text())["totals"]
        return int(totals["prompt_tokens"]), int(totals["completion_tokens"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def read_completion(trajectory: Path) -> dict[str, Any]:
    """Completion as the runtime saw it, kept apart from the independent verdict.

    `completion_proposals` counts the model's `goal_done` calls (`completion_blocked_claims`
    those that declared the goal blocked); `completion_rejections` counts the runtime's
    explicit "Completion rejected" notes; `completion_accepted` is whether the goal ended
    done. A careful-mode review that sends the model back is not counted as a rejection.
    Binaries that write their own `completion` record are read from it instead
    (`completion_source`: `rusty` or `notes`).

    `public_check_passed` is Rusty's last fixed verification run: True, False, or None when
    no check ran (`public_check_outcome: not_run`). It is the harness's own observation
    before handoff, not the independent verdict."""
    try:
        data = json.loads(trajectory.read_text())
        messages = (data.get("archived_messages") or []) + data["messages"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    goal = read_goal(trajectory).get("goal_status")
    checks = data.get("verification") or []
    outcome = checks[-1].get("outcome") if checks and isinstance(checks[-1], dict) else None
    # A check that never ran is not a passed check.
    public = {"verification_runs": len(checks), "public_check_outcome": outcome or "not_run",
              "public_check_passed": None if outcome is None else outcome == "Passed"}
    native = data.get("completion")
    if isinstance(native, dict):
        # Rusty's own completion record (newer binaries), preferred over reading notes. The
        # common fields are filled from it so consumers read one set of keys either way.
        def count(key: str) -> int:
            value = native.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        return {"completion_source": "rusty", "completion_accepted": goal == "done", **public,
                "completion_proposals": count("proposed"), "completion_blocked_claims": count("blocked"),
                "completion_rejections": count("check_failed") + count("check_error") + count("commands_running"),
                **{f"rusty_completion_{k}": v for k, v in native.items() if isinstance(v, (int, bool))}}
    proposals = blocked = rejections = 0
    for m in messages:
        if m.get("role") == "assistant":
            for call in m.get("tool_calls") or []:
                if call.get("function", {}).get("name") == "goal_done":
                    proposals += 1
                    try:
                        blocked += bool(json.loads(call["function"].get("arguments") or "{}").get("blocked"))
                    except (ValueError, AttributeError):
                        pass
        elif m.get("role") == "user" and "Completion rejected" in str(m.get("content")):
            rejections += 1
    return {"completion_source": "notes", "completion_proposals": proposals, "completion_blocked_claims": blocked,
            "completion_rejections": rejections, "completion_accepted": goal == "done", **public}


def read_budget(trajectory: Path) -> dict[str, Any]:
    """Rusty's own budget counters, prefixed `rusty_budget_`. Newer binaries split
    `attempts` (everything sent), `http_ok` (what the gateway admits) and `requests` (the
    capped count). The gateway receipt stays the episode's budget record."""
    try:
        budget = json.loads(trajectory.read_text()).get("model_budget")
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(budget, dict):
        return {}
    return {f"rusty_budget_{k}": v for k, v in budget.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def read_safety(trajectory: Path) -> dict | None:
    """Rusty's own safety record (risky and destructive calls proposed, blocked by each
    mechanism, executed), verbatim. None when the binary does not write one: not recorded,
    never zero. Whether the environment made an action impossible is the verifier's record."""
    try:
        safety = json.loads(trajectory.read_text()).get("safety")
    except (OSError, ValueError, AttributeError):
        return None
    return safety if isinstance(safety, dict) else None


def rusty_settings(env: dict[str, str]) -> dict[str, str]:
    """Every RUSTY_* setting a run was given, for the trial record; credential-like names are
    left out. Several such variables change guards, tools, deadlines or the model per mode."""
    return {k: v for k, v in sorted(env.items())
            if k.startswith("RUSTY_") and not any(w in k for w in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}


def flag(value: Any, name: str) -> bool:
    """A boolean agent option as Harbor delivers it (a parsed bool, or a string)."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes"):
        return True
    if text in ("0", "false", "no"):
        return False
    raise ValueError(f"{name} must be true or false, not {value!r}")


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
        self._memory = str(kwargs.pop("memory", "off"))
        verify = kwargs.pop("verify", None)
        self._verify = str(verify) if verify is not None else None
        self._verify_timeout = int(kwargs.pop("verify_timeout", 120))
        self._allow_missing_mcp = flag(kwargs.pop("allow_missing_mcp", False), "allow_missing_mcp")
        self._allow_destructive = flag(kwargs.pop("allow_destructive", False), "allow_destructive")
        self._binary_sha256: str | None = None
        self._rusty_env: dict[str, str] = {}
        self._help_sha256: str | None = None
        self._capabilities: dict | None = None
        self._capabilities_source: str | None = None
        self._limits = {name: int(kwargs.pop(name)) for name in LIMITS if kwargs.get(name) is not None}
        if self._execution not in ("standard", "careful", "vibe"):
            raise ValueError("execution must be standard, careful or vibe")
        if not self._memory.isalpha() or self._memory != self._memory.lower():
            raise ValueError(f"memory must be a lowercase level name, not {self._memory!r}")
        # Fail on a bad combination now, before any container or model call.
        build_command("x", mode=self._mode, agents=self._agents, verify=self._verify,
                      verify_timeout=self._verify_timeout)
        super().__init__(logs_dir, *args, **kwargs)

    def _transports(self) -> tuple[str, ...]:
        """MCP transports the installed binary says it speaks; stdio for older binaries."""
        listed = ((self._capabilities or {}).get("mcp") or {}).get("transports")
        return tuple(listed) if isinstance(listed, list) and listed else ("stdio",)

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
        # The installed binary decides what it accepts; a setting it rejects would
        # otherwise end the run at startup and read like a solver failure. Newer binaries
        # list their accepted values; older ones only describe them in --help.
        settings = dict(memory=self._memory, execution=self._execution, agents=self._agents,
                        verify=self._verify is not None)
        try:
            listed = await self.exec_as_agent(environment, command=f"RUSTY_NO_DOTENV=1 {REMOTE_BIN} --capabilities")
            caps = read_capabilities(listed.stdout or "")
        except NonZeroAgentExitCodeError:  # a binary from before --capabilities rejects the flag
            caps = None
        if caps is not None:
            self._capabilities = caps
            self._help_sha256 = hashlib.sha256(json.dumps(caps, sort_keys=True).encode()).hexdigest()
            self._capabilities_source = "capabilities"
            problems = unsupported_by(caps, **settings)
        else:
            shown = await self.exec_as_agent(environment, command=f"RUSTY_NO_DOTENV=1 {REMOTE_BIN} --help")
            help_text = shown.stdout or ""
            self._help_sha256 = hashlib.sha256(help_text.encode()).hexdigest()
            self._capabilities_source = "help"
            problems = unsupported(help_text, **settings)
        if problems:
            raise RustyConfigurationError("the installed rusty binary cannot run this configuration: "
                                          + "; ".join(problems))

    def populate_context_post_run(self, context: AgentContext) -> None:
        prompt, completion = read_totals(self.logs_dir / "rusty.trajectory.json")
        context.n_input_tokens = prompt
        context.n_output_tokens = completion
        context.metadata = {
            **(context.metadata or {}),
            **read_goal(self.logs_dir / "rusty.trajectory.json"),
            **read_completion(self.logs_dir / "rusty.trajectory.json"),
            "coverage": "restricted" if mcp_dropped(self.mcp_servers, self._transports()) else "full",
            "binary_sha256": self._binary_sha256,
            "max_turns": self._max_turns,
            "agents": self._agents,
            "execution": self._execution,
            "memory": self._memory,
            "allow_destructive": self._allow_destructive,
            "rusty_env": self._rusty_env,
            "safety": read_safety(self.logs_dir / "rusty.trajectory.json"),
            "verify": self._verify,
            "verify_timeout": self._verify_timeout if self._verify is not None else None,
            "help_sha256": self._help_sha256,
            "capabilities_source": self._capabilities_source,
            **read_budget(self.logs_dir / "rusty.trajectory.json"),
            "mcp_servers": sorted((mcp_config(self.mcp_servers) or {"mcpServers": {}})["mcpServers"]),
            "mcp_dropped": mcp_dropped(self.mcp_servers, self._transports()),
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
        dropped = mcp_dropped(self.mcp_servers, self._transports())
        if dropped and not self._allow_missing_mcp:
            raise RustyCoverageLimitation(f"the task offers MCP servers Rusty cannot use: {', '.join(dropped)}; "
                                          "allow_missing_mcp=true runs without them as restricted coverage")
        connection = self.model_connection
        keys = collect_keys(self._get_env_prefixed("NVIDIA_API_KEY"), connection.api_key)
        if "NVIDIA_API_KEY" not in keys:
            raise ValueError("NVIDIA_API_KEY is not set; add it to the --env-file")
        env = build_env(self.model_name, keys, connection.configured_base_url, self._max_turns, self._execution,
                        self._limits, self._memory, self._allow_destructive)
        if (config := mcp_config(self.mcp_servers)) is not None:
            await self.exec_as_agent(
                environment, command=f"printf %s {shlex.quote(json.dumps(config))} > {MCP_CONFIG}")
            env["RUSTY_MCP_CONFIG"] = MCP_CONFIG
        self._rusty_env = rusty_settings(env)
        await self.exec_as_agent(
            environment,
            command=build_command(instruction, mode=self._mode, agents=self._agents, verify=self._verify,
                                  verify_timeout=self._verify_timeout),
            env=env,
        )
