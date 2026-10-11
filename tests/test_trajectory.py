"""The trajectory summary: tool calls classified by name, shell commands by their program, both
harness formats read the same way, and a trial without a trajectory yields no summary at all."""

import json

from fsbench import trajectory


def call(name, **arguments):
    return {"type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def rusty_trajectory(calls, tools=None):
    data = {"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "do the thing"},
                         {"role": "assistant", "content": None, "tool_calls": calls[:2]},
                         {"role": "tool", "content": "ok"}, {"role": "tool", "content": "ok"},
                         {"role": "assistant", "content": None, "tool_calls": calls[2:]}],
            "totals": {"prompt_tokens": 10, "completion_tokens": 5}, "goal": {"goal_status": "done"},
            "model_budget": {}, "safety": {}}
    if tools is not None:
        data["tools"] = [{"type": "function", "function": {"name": t}} for t in tools]
    return data


def mini_trajectory(commands):
    messages = [{"role": "system", "content": "..."}, {"role": "user", "content": "do the thing"}]
    for i, command in enumerate(commands):
        messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
                                                                             "function": {"name": "bash", "arguments": {"command": command}}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
    return {"info": {"exit_status": "Submitted", "model_stats": {}}, "messages": messages,
            "trajectory_format": "mini-swe-agent-2"}


def test_tool_names_classify_by_word_and_edits_track_repatches():
    assert [trajectory.phase_of(n) for n in ("read_file", "grep", "write_file", "apply_patch", "run_tests", "bash",
                                              "simcloud_deploy", "frobnicate")] == \
        ["explore", "explore", "edit", "edit", "verify", "run", "run", "other"]
    calls = [call("read_file", path="a.py"), call("write_file", path="a.py", content="x"),
             call("edit_file", path="a.py"), call("write_file", path="b.py"), call("public_check"), call("mystery")]
    summary = trajectory.summarize(trajectory.tool_calls(rusty_trajectory(calls)))
    assert summary["phases"] == {"explore": 1, "edit": 3, "run": 0, "verify": 1, "other": 1}
    assert summary["calls"] == 6 and summary["edits"] == 3 and summary["repatches"] == 1 and summary["shell_calls"] == 0
    assert summary["out_of_interface_calls"] == 0 and summary["ended_on_out_of_interface"] is False  # nothing refused
    offered = {"read_file", "write_file", "edit_file", "public_check"}
    known = trajectory.summarize(trajectory.tool_calls(rusty_trajectory(calls)), offered=offered)
    assert known["out_of_interface_calls"] == 1 and known["ended_on_out_of_interface"] is True


def test_out_of_interface_calls_are_read_from_each_harness_refusal():
    # Rusty answers a name no tool matches in the call's own tool message, matched by id.
    messages = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"x\"}"}},
                    {"id": "b", "type": "function", "function": {"name": "frobnicate", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "b", "content": "error: unknown tool `frobnicate`"},
                {"role": "tool", "tool_call_id": "a", "content": "1: x"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c", "type": "function", "function": {"name": "mcp__simcloud__nope", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c", "content": "error: unknown MCP tool `mcp__simcloud__nope`; use one of the mcp__ tools you were given"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "d", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \"x\"}"}}]},
                {"role": "tool", "tool_call_id": "d", "content": "error: unknown tool `write_file`; this run offers only bash"}]
    calls = trajectory.tool_calls({"messages": messages})
    assert [c["refused"] for c in calls] == [False, True, True, True]
    rusty = trajectory.summarize(calls)
    assert rusty["out_of_interface_calls"] == 3 and rusty["ended_on_out_of_interface"] is True
    # mini-swe-agent accepts bash alone and answers anything else with a user message.
    messages = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function",
                                                                     "function": {"name": "edit_file", "arguments": {"path": "a"}}}]},
                {"role": "user", "content": "Your action could not be parsed: Unknown tool 'edit_file'. Please use bash."},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "2", "type": "function",
                                                                     "function": {"name": "bash", "arguments": {"command": "ls"}}}]},
                {"role": "tool", "tool_call_id": "2", "content": "a.py"}]
    mini = trajectory.summarize(trajectory.tool_calls({"messages": messages}))
    assert mini["out_of_interface_calls"] == 1 and mini["ended_on_out_of_interface"] is False
    # A tool message that merely mentions the words is not a refusal.
    messages = [{"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function",
                                                                       "function": {"name": "bash", "arguments": "{\"command\": \"grep -rn 'unknown tool' src\"}"}}]},
                {"role": "tool", "tool_call_id": "1", "content": "src/tools.rs:171: bail!(\"unknown tool `{other}`\")"}]
    assert trajectory.summarize(trajectory.tool_calls({"messages": messages}))["out_of_interface_calls"] == 0


def test_shell_commands_classify_by_program_and_redirect_targets_are_edits():
    commands = ["ls -la src", "cat src/app.py", "cd /app && grep -rn charge src", "cat > src/new.py <<'EOF'\nprint(1)\nEOF",
                "sed -i 's/a/b/' src/new.py", "echo hi 2>/dev/null >> src/new.py", "pytest -q tests", "uv run python -m app",
                "frobnicate --now"]
    summary = trajectory.summarize(trajectory.tool_calls(mini_trajectory(commands)))
    assert summary["phases"] == {"explore": 3, "edit": 3, "run": 2, "verify": 1, "other": 0}
    assert summary["shell_calls"] == 9 and summary["edits"] == 3 and summary["repatches"] == 2
    assert trajectory.classify_call("bash", {"command": "echo x > /dev/null"}) == ("run", None, True)
    assert trajectory.classify_call("bash", {}) == ("run", None, True)


def test_summary_for_trial_reads_either_harness_file_or_the_gated_rounds(tmp_path):
    assert trajectory.summary_for_trial(tmp_path / "absent") is None
    trial = tmp_path / "trial"
    (trial / "agent").mkdir(parents=True)
    assert trajectory.summary_for_trial(trial) is None  # an agent directory without a trajectory
    (trial / "agent" / "rusty.trajectory.json").write_text(json.dumps(rusty_trajectory(
        [call("read_file", path="a"), call("bash", command="pytest"), call("write_file", path="a"), call("goal_done")],
        tools=["read_file", "bash", "write_file", "goal_done"])))
    rusty = trajectory.summary_for_trial(trial)
    assert rusty["files"] == ["agent/rusty.trajectory.json"] and rusty["calls"] == 4 and rusty["shell_calls"] == 1
    assert rusty["phases"] == {"explore": 1, "edit": 1, "run": 0, "verify": 1, "other": 1}
    assert rusty["out_of_interface_calls"] == 0 and rusty["ended_on_out_of_interface"] is False
    gated = tmp_path / "gated"  # noqa: E501
    (gated / "agent").mkdir(parents=True)
    (gated / "agent" / "mini-swe-agent.trajectory.json").write_text(json.dumps(mini_trajectory(["ls"])))  # the last round's copy
    for n, commands in ((1, ["cat a.py", "echo x > a.py"]), (2, ["pytest", "echo y > a.py"]), (10, ["ls"])):
        (gated / "agent" / f"mini-swe-agent.round{n}.trajectory.json").write_text(json.dumps(mini_trajectory(commands)))
    mini = trajectory.summary_for_trial(gated)
    assert mini["files"] == [f"agent/mini-swe-agent.round{n}.trajectory.json" for n in (1, 2, 10)]  # numeric order
    assert mini["calls"] == 5 and mini["repatches"] == 1 and mini["phases"]["verify"] == 1
    (trial / "agent" / "rusty.trajectory.json").write_text("{not json")
    assert trajectory.summary_for_trial(trial) is None
