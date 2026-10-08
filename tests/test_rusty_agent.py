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
    assert env["RUSTY_MODE"] == "standard"
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
