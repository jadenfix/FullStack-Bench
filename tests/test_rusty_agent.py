import json
import shlex

import pytest

from fsbench.agents.rusty import TRAJECTORY, build_command, build_env, read_totals


def test_goal_command_quotes_the_instruction():
    cmd = build_command("fix it; rm -rf / && echo 'pwned'", mode="goal", agents="off")
    argv = shlex.split(cmd.split(" </dev/null")[0])
    assert argv[:4] == ["/usr/local/bin/rusty", "--yolo", "--stats", "--agents"]
    assert argv[-2:] == ["--goal", "fix it; rm -rf / && echo 'pwned'"]
    assert ["--trajectory", TRAJECTORY] == argv[argv.index("--trajectory"):argv.index("--trajectory") + 2]
    assert cmd.endswith("| tee /logs/agent/rusty.txt")


def test_prompt_mode_and_validation():
    argv = shlex.split(build_command("hi", mode="prompt", agents="swarm").split(" </dev/null")[0])
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
    assert "k1" not in build_command("task", mode="goal", agents="off")


def test_read_totals(tmp_path):
    p = tmp_path / "t.json"
    assert read_totals(p) == (None, None)
    p.write_text(json.dumps({"totals": {"prompt_tokens": 120, "completion_tokens": 7}}))
    assert read_totals(p) == (120, 7)
