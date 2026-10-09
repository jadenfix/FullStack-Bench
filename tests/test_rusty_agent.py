import json
import shlex

import pytest

from fsbench.agents.rusty import TRAJECTORY, build_command, build_env, read_totals


def pipeline(command):
    outer = shlex.split(command)
    assert outer[:4] == ["bash", "-o", "pipefail", "-c"]
    return outer[4]


def test_goal_command_quotes_the_instruction():
    cmd = build_command("fix it; rm -rf / && echo 'pwned'", mode="goal", agents="off")
    inner = pipeline(cmd)
    argv = shlex.split(inner.split(" </dev/null")[0])
    assert argv[:4] == ["/usr/local/bin/rusty", "--yolo", "--stats", "--agents"]
    assert argv[-2:] == ["--goal", "fix it; rm -rf / && echo 'pwned'"]
    assert ["--trajectory", TRAJECTORY] == argv[argv.index("--trajectory"):argv.index("--trajectory") + 2]
    assert inner.endswith("| tee /logs/agent/rusty.txt")


def test_prompt_mode_and_validation():
    argv = shlex.split(pipeline(build_command("hi", mode="prompt", agents="swarm")).split(" </dev/null")[0])
    assert "--goal" not in argv and argv[-1] == "hi" and "swarm" in argv
    with pytest.raises(ValueError):
        build_command("hi", mode="chat", agents="off")
    with pytest.raises(ValueError):
        build_command("hi", mode="goal", agents="everything")


def test_env_keeps_keys_out_of_argv_and_drops_unrelated_vars():
    env = build_env(
        "nvidia/x",
        {"NVIDIA_API_KEY": "k1", "NVIDIA_API_KEY_2": "k2", "NVIDIA_API_BASE_EXTRA": "nope"},
        "https://example.test/v1",
        12,
    )
    assert env["NVIDIA_API_KEY"] == "k1" and env["NVIDIA_API_KEY_2"] == "k2"
    assert "NVIDIA_API_BASE_EXTRA" not in env
    assert env["RUSTY_BASE_URL"] == "https://example.test/v1"
    assert env["RUSTY_GOAL_MAX_TURNS"] == "12" and env["RUSTY_NO_DOTENV"] == "1"
    assert env["RUSTY_MODE"] == "standard" and env["RUSTY_ALLOW_DESTRUCTIVE"] == "1"
    assert "k1" not in build_command("task", mode="goal", agents="off")


def test_failed_run_keeps_its_exit_status_and_log(tmp_path, monkeypatch):
    import subprocess
    import fsbench.agents.rusty as adapter

    executable = tmp_path / "rusty"
    executable.write_text("#!/bin/sh\necho failed-run\nexit 7\n")
    executable.chmod(0o755)
    logfile = tmp_path / "run.log"
    monkeypatch.setattr(adapter, "REMOTE_BIN", str(executable))
    monkeypatch.setattr(adapter, "LOG", str(logfile))
    result = subprocess.run(build_command("test", mode="goal", agents="off"), shell=True,
                            capture_output=True, text=True)
    assert result.returncode == 7
    assert logfile.read_text().strip() == "failed-run"


def test_execution_is_explicit():
    assert build_env("x", {}, None, 1, "careful")["RUSTY_MODE"] == "careful"
    with pytest.raises(ValueError):
        build_env("x", {}, None, 1, "auto")


def test_read_totals(tmp_path):
    p = tmp_path / "t.json"
    assert read_totals(p) == (None, None)
    p.write_text(json.dumps({"totals": {"prompt_tokens": 120, "completion_tokens": 7}}))
    assert read_totals(p) == (120, 7)


def test_run_passes_every_rotation_key(tmp_path, monkeypatch):
    import asyncio
    import fsbench.agents.rusty as adapter

    for name in [k for k in __import__("os").environ if k.startswith("NVIDIA_")]:
        monkeypatch.delenv(name)
    agent = adapter.Rusty(tmp_path, model_name="nvidia/x",
                          extra_env={"NVIDIA_API_KEY": "k1", "NVIDIA_API_KEY_2": "k2", "NVIDIA_API_KEY_3": "k3"})
    seen = {}

    async def fake_exec(environment, command, env=None, **_):
        seen.update(env or {})

    monkeypatch.setattr(agent, "exec_as_agent", fake_exec)
    asyncio.run(agent.run("task", environment=None, context=adapter.AgentContext()))
    assert {k: seen[k] for k in seen if k.startswith("NVIDIA_API_KEY")} == {
        "NVIDIA_API_KEY": "k1", "NVIDIA_API_KEY_2": "k2", "NVIDIA_API_KEY_3": "k3"}


def test_goal_outcome_and_pins_reach_the_trial_metadata(tmp_path):
    import fsbench.agents.rusty as adapter

    traj = tmp_path / "rusty.trajectory.json"
    assert adapter.read_goal(traj) == {}
    for status, name in (("Active", "active"), ({"Done": "tests pass"}, "done"), ({"Blocked": "no access"}, "blocked")):
        traj.write_text(json.dumps({"goal": {"status": status, "turns": 25}, "totals": {}}))
        assert adapter.read_goal(traj) == {"goal_status": name, "goal_turns": 25}

    binary = tmp_path / "rusty-bin"
    binary.write_bytes(b"elf")
    agent = adapter.Rusty(tmp_path, model_name="nvidia/x", binary=str(binary), max_turns=9)
    agent._binary_sha256 = adapter.sha256(str(binary))
    traj.write_text(json.dumps({"goal": {"status": "Active", "turns": 9},
                                "totals": {"prompt_tokens": 5, "completion_tokens": 2}}))
    context = adapter.AgentContext()
    agent.populate_context_post_run(context)
    assert context.n_input_tokens == 5 and context.n_output_tokens == 2
    assert context.metadata["goal_status"] == "active" and context.metadata["max_turns"] == 9
    assert context.metadata["memory"] == "off"
    assert context.metadata["binary_sha256"] == __import__("hashlib").sha256(b"elf").hexdigest()


def test_model_budget_limits_are_optional_and_validated(tmp_path):
    import fsbench.agents.rusty as adapter

    assert not any(k in build_env("x", {}, None, 1) for k in adapter.LIMITS.values())
    env = build_env("x", {}, None, 1, limits={"max_requests": 250, "max_budget_tokens": 9_000_000, "budget_secs": 6900})
    assert (env["RUSTY_MAX_REQUESTS"], env["RUSTY_MAX_BUDGET_TOKENS"], env["RUSTY_BUDGET_SECS"]) == (
        "250", "9000000", "6900")
    with pytest.raises(ValueError):
        build_env("x", {}, None, 1, limits={"max_requests": 0})
    agent = adapter.Rusty(tmp_path, model_name="nvidia/x", max_requests="250")
    assert agent._limits == {"max_requests": 250}


def test_failed_runs_are_not_mistaken_for_provider_rate_limits(tmp_path):
    from types import SimpleNamespace

    from harbor.agents.installed.base import ApiRateLimitError, NonZeroAgentExitCodeError
    from fsbench.agents.rusty import Rusty

    agent = Rusty(logs_dir=tmp_path, model_name="nvidia/x")
    stats = '{"goal":"open","model_budget":{"rate_limited":0}}'
    stalled = SimpleNamespace(return_code=1, stderr="",
        stdout=f"tool output mentioning a rate limiter\n{stats}\nerror: the goal stalled three turns in a row")
    assert type(agent._classify_exec_error("rusty", stalled)) is NonZeroAgentExitCodeError
    limited = SimpleNamespace(return_code=1, stderr="",
        stdout=f"{stats}\nerror: giving up after 9 attempts in 300s: HTTP 429 Too Many Requests: slow down")
    assert isinstance(agent._classify_exec_error("rusty", limited), ApiRateLimitError)


def test_task_mcp_servers_reach_rusty(tmp_path, monkeypatch):
    import asyncio
    import shlex as sh
    import fsbench.agents.rusty as adapter
    from harbor.models.task.config import MCPServerConfig

    servers = [MCPServerConfig(name="simcloud", transport="stdio", command="simcloud-mcp"),
               MCPServerConfig(name="remote", transport="sse", url="http://example.test/sse")]
    assert adapter.mcp_config(servers) == {"mcpServers": {"simcloud": {"command": "simcloud-mcp", "args": []}}}
    assert adapter.mcp_config(servers[1:]) is None

    for name in [k for k in __import__("os").environ if k.startswith("NVIDIA_")]:
        monkeypatch.delenv(name)
    agent = adapter.Rusty(tmp_path, model_name="nvidia/x", extra_env={"NVIDIA_API_KEY": "k1"}, mcp_servers=servers)
    calls = []

    async def fake_exec(environment, command, env=None, **_):
        calls.append((command, env or {}))

    monkeypatch.setattr(agent, "exec_as_agent", fake_exec)
    asyncio.run(agent.run("task", environment=None, context=adapter.AgentContext()))
    written = sh.split(calls[0][0])
    assert written[:2] == ["printf", "%s"] and json.loads(written[2])["mcpServers"]["simcloud"]["command"] == "simcloud-mcp"
    assert calls[-1][1]["RUSTY_MCP_CONFIG"] == adapter.MCP_CONFIG
    context = adapter.AgentContext()
    agent.populate_context_post_run(context)
    assert context.metadata["mcp_servers"] == ["simcloud"]


def test_memory_is_always_pinned():
    # Rusty's default memory level changes between releases; a cohort must not change with it.
    assert build_env("x", {}, None, 1)["RUSTY_MEMORY"] == "off"
    assert build_env("x", {}, None, 1, memory="learn")["RUSTY_MEMORY"] == "learn"
    with pytest.raises(ValueError):
        build_env("x", {}, None, 1, memory="on")
