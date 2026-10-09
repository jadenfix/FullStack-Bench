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
    assert "| tee /logs/agent/rusty.txt; code=$?" in inner
    assert inner.endswith('> /logs/agent/rusty.exit; exit $code'), "no --verify: every status passes through"


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
    assert env["RUSTY_MODE"] == "standard" and "RUSTY_ALLOW_DESTRUCTIVE" not in env
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
    monkeypatch.setattr(adapter, "EXIT_FILE", str(tmp_path / "rusty.exit"))
    result = subprocess.run(build_command("test", mode="goal", agents="off"), shell=True,
                            capture_output=True, text=True)
    assert result.returncode == 7
    assert logfile.read_text().strip() == "failed-run"
    assert adapter.read_exit(tmp_path / "rusty.exit") == 7


@pytest.mark.parametrize("code, verify, ran, want", [
    (2, "public-check", True, 0),   # the goal ended unverified: an outcome
    (2, "public-check", False, 2),  # clap's usage error (exit 2, no trajectory): still an error
    (2, None, True, 2), (1, "public-check", True, 1), (0, "public-check", True, 0)])
def test_an_unverified_goal_is_an_outcome_not_an_agent_error(tmp_path, monkeypatch, code, verify, ran, want):
    import subprocess
    import fsbench.agents.rusty as adapter

    traj = tmp_path / "rusty.trajectory.json"
    executable = tmp_path / "rusty"
    write = f"echo '{{}}' > {traj}\n" if ran else ""
    executable.write_text(f"#!/bin/sh\n{write}echo run\nexit {code}\n")
    executable.chmod(0o755)
    monkeypatch.setattr(adapter, "REMOTE_BIN", str(executable))
    monkeypatch.setattr(adapter, "TRAJECTORY", str(traj))
    monkeypatch.setattr(adapter, "LOG", str(tmp_path / "run.log"))
    monkeypatch.setattr(adapter, "EXIT_FILE", str(tmp_path / "rusty.exit"))
    result = subprocess.run(build_command("t", mode="goal", agents="off", verify=verify), shell=True)
    assert result.returncode == want
    assert adapter.read_exit(tmp_path / "rusty.exit") == code, "Rusty's own status is kept as data"


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
    calls = []

    async def fake_exec(environment, command, env=None, **_):
        calls.append((command, env or {}))

    # A server Rusty can't use stops the run before any command, unless the operator allows it.
    strict = adapter.Rusty(tmp_path, model_name="nvidia/x", extra_env={"NVIDIA_API_KEY": "k1"}, mcp_servers=servers)
    monkeypatch.setattr(strict, "exec_as_agent", fake_exec)
    with pytest.raises(adapter.RustyCoverageLimitation, match="remote \\(sse\\)") as raised:
        asyncio.run(strict.run("task", environment=None, context=adapter.AgentContext()))
    assert calls == []
    # The message ends with a record of what was missing, for the trial's result.json.
    record = json.loads(str(raised.value)[str(raised.value).index("{"):])
    assert record == {"coverage": "restricted", "mcp_dropped": ["remote (sse)"]}

    agent = adapter.Rusty(tmp_path, model_name="nvidia/x", extra_env={"NVIDIA_API_KEY": "k1"}, mcp_servers=servers,
                          allow_missing_mcp="true")
    monkeypatch.setattr(agent, "exec_as_agent", fake_exec)
    asyncio.run(agent.run("task", environment=None, context=adapter.AgentContext()))
    written = sh.split(calls[0][0])
    assert written[:2] == ["printf", "%s"] and json.loads(written[2])["mcpServers"]["simcloud"]["command"] == "simcloud-mcp"
    assert calls[-1][1]["RUSTY_MCP_CONFIG"] == adapter.MCP_CONFIG
    context = adapter.AgentContext()
    agent.populate_context_post_run(context)
    assert context.metadata["mcp_servers"] == ["simcloud"]
    assert context.metadata["mcp_dropped"] == ["remote (sse)"] and context.metadata["coverage"] == "restricted"


def test_memory_is_always_pinned():
    # Rusty's default memory level changes between releases; a cohort must not change with it.
    assert build_env("x", {}, None, 1)["RUSTY_MEMORY"] == "off"
    assert build_env("x", {}, None, 1, memory="learn")["RUSTY_MEMORY"] == "learn"
    with pytest.raises(ValueError):
        build_env("x", {}, None, 1, memory="Learn!")


# Excerpts of real `rusty --help` output: a build from before the memory levels (9cf1ff7)
# and one after (25a0b6b), wrapped the way clap prints them.
HELP_OLD = """\
      --mode <MODE>
          Execution mode: careful, standard or vibe (independent of permissions) [env: RUSTY_MODE=]
      --memory <MEMORY>
          Memory: legacy (existing store), off, on (local advisor), deep (background model) [env: RUSTY_MEMORY=] [default: legacy]
  -a, --agents <AGENTS>
          Delegation: off, sub, swarm or auto (overrides saved settings) [env: RUSTY_AGENTS=]
      --verify <COMMAND>
          Fixed local acceptance command, run before the goal can close
      --verify-timeout <VERIFY_TIMEOUT>
          Deadline for the fixed acceptance command (1..600 seconds) [default: 120]
"""
HELP_NEW = """\
      --mode <MODE>
          Execution mode: careful, standard or vibe (independent of permissions) [env: RUSTY_MODE=]
      --memory <MEMORY>
          Memory: off, recall (use saved lessons), learn (also tool context, file checks, credit from --verify),
          reflect (also a model review after checked goals) or deep (most aggressive). Benchmark runs should pass
          off [env: RUSTY_MEMORY=] [default: learn]
  -a, --agents <AGENTS>
          Delegation: off, sub, swarm or auto (overrides saved settings) [env: RUSTY_AGENTS=]
"""


def test_pinned_settings_must_be_ones_the_installed_binary_lists():
    from fsbench.agents.rusty import help_values, unsupported
    assert {"legacy", "off", "on", "deep"} <= help_values(HELP_OLD, "--memory")
    assert "store" not in help_values(HELP_OLD, "--memory")  # parentheticals are not values
    assert {"off", "recall", "learn", "reflect", "deep"} <= help_values(HELP_NEW, "--memory")
    assert "legacy" not in help_values(HELP_NEW, "--memory")
    ok = dict(memory="off", execution="standard", agents="off", verify=False)
    assert unsupported(HELP_OLD, **ok) == [] and unsupported(HELP_NEW, **ok) == []
    assert unsupported(HELP_NEW, **{**ok, "memory": "legacy"}) == ["the binary's --memory does not list 'legacy'"]
    assert unsupported(HELP_OLD, **{**ok, "memory": "learn"}) == ["the binary's --memory does not list 'learn'"]
    assert unsupported(HELP_NEW, **{**ok, "verify": True}) == ["the binary has no --verify option"]
    # A binary with --verify but no --verify-timeout would reject the flag the adapter passes.
    no_timeout = HELP_OLD.replace("      --verify-timeout <VERIFY_TIMEOUT>\n", "")
    assert unsupported(no_timeout, **{**ok, "verify": True}) == ["the binary has no --verify-timeout option"]
    assert help_values("      --verify-timeout <S>\n", "--verify") is None, "a flag is matched exactly"
    assert unsupported(HELP_OLD, **{**ok, "verify": True}) == []
    assert unsupported("", **ok) == [f"the binary has no {f} option" for f in ("--memory", "--mode", "--agents")]


def test_public_verify_check_is_passed_only_in_goal_mode(tmp_path):
    import shlex as sh
    import fsbench.agents.rusty as adapter

    words = sh.split(sh.split(build_command("fix it", mode="goal", agents="off", verify="pytest -q tests/public",
                                            verify_timeout=300))[-1])
    i = words.index("--verify")
    assert words[i + 1:i + 4] == ["pytest -q tests/public", "--verify-timeout", "300"]
    assert "--verify" not in build_command("fix it", mode="goal", agents="off")
    for bad in (dict(mode="prompt", verify="pytest"), dict(mode="goal", verify="  "),
                dict(mode="goal", verify="pytest", verify_timeout=0)):
        with pytest.raises(ValueError):
            build_command("x", agents="off", **bad)
    with pytest.raises(ValueError):
        adapter.Rusty(tmp_path, model_name="nvidia/x", mode="prompt", verify="pytest")


def test_install_rejects_a_setting_the_binary_does_not_accept(tmp_path, monkeypatch):
    import asyncio
    import fsbench.agents.rusty as adapter

    binary = tmp_path / "rusty-bin"
    binary.write_bytes(b"elf")

    class Env:
        async def upload_file(self, *_):
            pass

    async def fake_exec(environment, command, **_):
        if "--capabilities" in command:  # a binary from before --capabilities
            raise adapter.NonZeroAgentExitCodeError("unexpected argument '--capabilities'")
        return type("R", (), {"stdout": HELP_NEW if "--help" in command else "rusty 0.1.0", "return_code": 0})()

    for memory, ok in (("off", True), ("legacy", False)):
        agent = adapter.Rusty(tmp_path, model_name="nvidia/x", binary=str(binary), memory=memory)
        monkeypatch.setattr(agent, "exec_as_agent", fake_exec)
        monkeypatch.setattr(agent, "exec_as_root", fake_exec)
        if ok:
            asyncio.run(agent.install(Env()))
            assert len(agent._help_sha256) == 64 and agent._capabilities_source == "help"
        else:
            with pytest.raises(adapter.RustyConfigurationError, match="does not list 'legacy'"):
                asyncio.run(agent.install(Env()))


def test_completion_events_are_kept_apart_from_the_verdict(tmp_path):
    import fsbench.agents.rusty as adapter

    def call(args):
        return {"role": "assistant", "tool_calls": [{"function": {"name": "goal_done", "arguments": json.dumps(args)}}]}

    traj = tmp_path / "rusty.trajectory.json"
    assert adapter.read_completion(traj) == {}
    rejected = {"role": "user", "content": "[from rusty, not the user] Completion rejected: fixed check failed"}
    traj.write_text(json.dumps({
        # Rusty's real shape: `messages` holds everything, archived first; `archived_messages` counts them.
        "archived_messages": 2,
        "messages": [call({"evidence": "tests pass"}), rejected, call({"evidence": "fixed it"}),
                     call({"blocked": True, "evidence": "no access"})],
        "goal": {"status": {"Blocked": "no access"}}, "verification": [{"outcome": "Failed"}], "totals": {}}))
    assert adapter.read_completion(traj) == {
        "completion_source": "notes", "completion_proposals": 3, "completion_blocked_claims": 1,
        "completion_rejections": 1, "completion_accepted": False, "verification_runs": 1,
        "public_check_outcome": "Failed", "public_check_passed": False}

    # Newer binaries write their own claims record; no check ran, so it is not a pass.
    record = {"proposed": 2, "accepted": 1, "check_failed": 1, "note": "x"}
    want = {
        "completion_source": "rusty", "completion_accepted": True, "completion_proposals": 2,
        "completion_blocked_claims": 0, "completion_rejections": 1, "rusty_claims_proposed": 2,
        "rusty_claims_accepted": 1, "rusty_claims_check_failed": 1, "verification_runs": 0,
        "public_check_outcome": "not_run", "public_check_passed": None}
    done = {"messages": [], "goal": {"status": {"Done": "ok"}, "turns": 2}, "totals": {}}
    traj.write_text(json.dumps({**done, "claims": record, "completion": 250}))
    assert adapter.read_completion(traj) == want, "`claims` is the record; `completion` is a token count"
    # Main builds between rusty #57 and #60 wrote the same record under `completion`.
    traj.write_text(json.dumps({**done, "completion": record}))
    assert adapter.read_completion(traj) == want
    # Older binaries have only the token count there: read the notes, never the integer.
    traj.write_text(json.dumps({**done, "completion": 250}))
    assert adapter.read_completion(traj)["completion_source"] == "notes"


# Real `rusty --capabilities` output from rusty#58 (5136191).
CAPS = ('{"agents":["off","sub","swarm","auto"],"budget":["max-requests","max-budget-tokens","budget-secs"],'
        '"contract":1,"mcp":{"transports":["stdio"]},"memory":["off","recall","learn","reflect","deep"],'
        '"mode":["auto","careful","standard","vibe"],"permissions":["read-only","ask","auto","yolo"],'
        '"tools":["local","daytona"],"verify":{"supported":true,"timeout_secs":[1,600]},"version":"0.1.0"}')


def test_capability_listing_is_preferred_when_the_binary_has_one(tmp_path, monkeypatch):
    import asyncio
    import fsbench.agents.rusty as adapter
    from harbor.models.task.config import MCPServerConfig

    caps = adapter.read_capabilities(CAPS)
    assert adapter.read_capabilities("usage: rusty [OPTIONS]") is None
    ok = dict(memory="off", execution="careful", agents="off", verify=True)
    assert adapter.unsupported_by(caps, **ok) == []
    assert adapter.unsupported_by(caps, **{**ok, "memory": "legacy"}) == [
        "the binary's memory values ['off', 'recall', 'learn', 'reflect', 'deep'] do not include 'legacy'"]
    assert adapter.unsupported_by({**caps, "verify": {"supported": False}}, **ok) == [
        "the binary does not support --verify"]

    binary = tmp_path / "rusty-bin"
    binary.write_bytes(b"elf")

    class Env:
        async def upload_file(self, *_):
            pass

    seen = []

    async def fake_exec(environment, command, **_):
        seen.append(command)
        return type("R", (), {"stdout": CAPS if "--capabilities" in command else "rusty 0.1.0", "return_code": 0})()

    servers = [MCPServerConfig(name="sim", transport="stdio", command="sim-mcp"),
               MCPServerConfig(name="web", transport="streamable-http", url="http://x")]
    agent = adapter.Rusty(tmp_path, model_name="nvidia/x", binary=str(binary), mcp_servers=servers)
    monkeypatch.setattr(agent, "exec_as_agent", fake_exec)
    monkeypatch.setattr(agent, "exec_as_root", fake_exec)
    asyncio.run(agent.install(Env()))
    assert agent._capabilities_source == "capabilities" and not any("--help" in c for c in seen)
    assert agent._transports() == ("stdio",)
    assert adapter.mcp_dropped(servers, agent._transports()) == ["web (streamable-http)"]
    assert adapter.mcp_dropped(servers, ("stdio", "streamable-http")) == ["web (streamable-http)"]  # needs a command


def test_rusty_budget_counters_are_recorded_beside_the_gateway_record(tmp_path):
    import fsbench.agents.rusty as adapter
    traj = tmp_path / "rusty.trajectory.json"
    assert adapter.read_budget(traj) == {}
    traj.write_text(json.dumps({"model_budget": {"attempts": 9, "http_ok": 6, "requests": 7, "enabled": True,
                                                 "retry_wait_seconds": 41.5, "by_role": {"lead": 6}}}))
    assert adapter.read_budget(traj) == {"rusty_budget_attempts": 9, "rusty_budget_http_ok": 6,
                                         "rusty_budget_requests": 7, "rusty_budget_retry_wait_seconds": 41.5}


def test_rusty_keeps_its_destructive_step_guard_unless_a_track_turns_it_off(tmp_path):
    import fsbench.agents.rusty as adapter

    assert "RUSTY_ALLOW_DESTRUCTIVE" not in build_env("x", {}, None, 1)
    assert build_env("x", {}, None, 1, allow_destructive=True)["RUSTY_ALLOW_DESTRUCTIVE"] == "1"
    assert adapter.Rusty(tmp_path, model_name="nvidia/x")._allow_destructive is False
    for given, want in ((True, True), ("true", True), ("false", False), (False, False)):
        assert adapter.Rusty(tmp_path, model_name="nvidia/x", allow_destructive=given)._allow_destructive is want
    with pytest.raises(ValueError):
        adapter.Rusty(tmp_path, model_name="nvidia/x", allow_destructive="maybe")


def test_rustys_safety_record_is_kept_verbatim_or_marked_not_recorded(tmp_path):
    import fsbench.agents.rusty as adapter

    traj = tmp_path / "rusty.trajectory.json"
    assert adapter.read_safety(traj) is None
    traj.write_text(json.dumps({"goal": {"status": "Active", "turns": 1}, "totals": {}}))
    assert adapter.read_safety(traj) is None, "an older binary has no record: not zero"
    record = {"destructive": {"proposed": 2, "executed": 0, "unattended": 2}, "risky": {"proposed": 5, "executed": 5}}
    traj.write_text(json.dumps({"goal": {"status": "Active", "turns": 1}, "totals": {}, "safety": record}))
    assert adapter.read_safety(traj) == record
    agent = adapter.Rusty(tmp_path, model_name="nvidia/x")
    context = adapter.AgentContext()
    agent.populate_context_post_run(context)
    assert context.metadata["safety"] == record and context.metadata["allow_destructive"] is False


def test_every_rusty_setting_a_run_gets_is_recorded_without_credentials(tmp_path, monkeypatch):
    import asyncio
    import fsbench.agents.rusty as adapter

    for name in [k for k in __import__("os").environ if k.startswith("NVIDIA_")]:
        monkeypatch.delenv(name)
    agent = adapter.Rusty(tmp_path, model_name="nvidia/x", extra_env={"NVIDIA_API_KEY": "k1"})

    async def fake_exec(environment, command, env=None, **_):
        pass

    monkeypatch.setattr(agent, "exec_as_agent", fake_exec)
    asyncio.run(agent.run("task", environment=None, context=adapter.AgentContext()))
    context = adapter.AgentContext()
    agent.populate_context_post_run(context)
    recorded = context.metadata["rusty_env"]
    assert recorded["RUSTY_MODE"] == "standard" and recorded["RUSTY_MEMORY"] == "off"
    assert "RUSTY_ALLOW_DESTRUCTIVE" not in recorded
    assert adapter.rusty_settings({"RUSTY_TOOL_BRIDGE_TOKEN": "t", "RUSTY_INFRA": "off", "PATH": "/"}) == {
        "RUSTY_INFRA": "off"}


def test_every_declared_option_is_consumed_and_never_dropped_by_harbor(tmp_path, monkeypatch):
    import fsbench.agents.rusty as adapter

    passed_on = {}
    original = adapter.BaseInstalledAgent.__init__

    def record(self, *args, **kwargs):
        passed_on.update(kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(adapter.BaseInstalledAgent, "__init__", record)
    values = {"binary": "/b", "mode": "goal", "agents": "off", "max_turns": 9, "execution": "careful",
              "memory": "off", "verify": "public-check", "verify_timeout": 60, "allow_missing_mcp": False,
              "allow_destructive": False, "max_requests": 250, "max_budget_tokens": 9_000_000, "budget_secs": 600}
    assert set(values) == set(adapter.Rusty.SUPPORTED_OPTIONS)
    agent = adapter.Rusty(tmp_path, model_name="nvidia/x", **values)
    assert not set(values) & set(passed_on), "a declared option must not fall through to Harbor's base agent"
    assert agent._verify == "public-check" and agent._verify_timeout == 60 and agent._execution == "careful"
