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
                {"role": "tool", "tool_call_id": "d", "content": "error: unknown tool `write_file`; this run offers only bash"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "e", "type": "function", "function": {"name": "mcp__simcloud__gone", "arguments": "{}"}},
                    {"id": "f", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"rm -rf /\"}"}}]},
                {"role": "tool", "tool_call_id": "e", "content": "error: mcp__simcloud__gone: unknown MCP tool `mcp__simcloud__gone`"},
                {"role": "tool", "tool_call_id": "f", "content": "denied by permissions (destructive)"}]
    calls = trajectory.tool_calls({"messages": messages})
    assert [c["refused"] for c in calls] == [False, True, True, True, True, False]  # a permission denial is not one
    rusty = trajectory.summarize(calls)
    assert rusty["out_of_interface_calls"] == 4 and rusty["ended_on_out_of_interface"] is False
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


def test_compaction_boundaries_measure_repeats_errors_and_rereads():
    tc = lambda i, name, **args: {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}  # noqa: E731
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": None, "tool_calls": [tc(1, "read_file", path="src/app.py")]},
        {"role": "tool", "tool_call_id": "c1", "content": "1: x"},
        {"role": "assistant", "content": None, "tool_calls": [tc(2, "bash", command="pytest -q")]},
        {"role": "tool", "tool_call_id": "c2", "content": "exit code: 1\nFAILED"},
        {"role": "user", "content": "[Summary of the conversation so far]\n..."},          # boundary 5
        {"role": "assistant", "content": None, "tool_calls": [tc(3, "bash", command="pytest -q")]},  # repeated, failed
        {"role": "tool", "tool_call_id": "c3", "content": "exit code: 1\nFAILED"},
        {"role": "assistant", "content": None, "tool_calls": [tc(4, "bash", command="cat src/app.py")]},  # re-read
        {"role": "tool", "tool_call_id": "c4", "content": "exit code: 0\nx"},
        {"role": "assistant", "content": None, "tool_calls": [tc(5, "read_file", path="src/app.py")]},  # re-read
        {"role": "tool", "tool_call_id": "c5", "content": "1: x"},
        {"role": "assistant", "content": None, "tool_calls": [tc(6, "write_file", path="src/app.py", content="y")]},
        {"role": "tool", "tool_call_id": "c6", "content": "written"},
        {"role": "user", "content": "[Summary of the conversation so far]\n..."},          # boundary 14
        {"role": "assistant", "content": None, "tool_calls": [tc(7, "bash", command="pytest -q")]},
        {"role": "tool", "tool_call_id": "c7", "content": "exit code: 0\npassed"},
    ]
    data = {"messages": messages, "compactions": [
        {"trigger": "level", "tokens_before": 90_000, "tokens_after": 30_000, "calls_since_last": 2, "boundary": 5},
        {"trigger": "smart", "tokens_before": 70_000, "tokens_after": 20_000, "calls_since_last": 4, "boundary": 14},
    ]}
    calls = trajectory.tool_calls(data)
    assert [c["at"] for c in calls] == [1, 3, 6, 8, 10, 12, 15]
    assert [c["failed"] for c in calls] == [False, True, True, False, False, False, False]
    effects = trajectory.compaction_effects(data, calls)
    assert effects["count"] == 2 and effects["by_trigger"] == {"level": 1, "smart": 1}
    # After the first boundary (up to the second): pytest repeated and failed, read_file app.py repeated,
    # app.py re-read twice (cat and read_file). After the second: pytest repeated, passed.
    assert effects["repeated_after"] == 3 and effects["first_step_errors"] == 1 and effects["rereads_after"] == 2
    assert effects["tokens_before_mean"] == 80_000.0 and effects["tokens_after_mean"] == 25_000.0
    none = trajectory.compaction_effects({"messages": messages}, calls)
    assert none["count"] == 0 and none["by_trigger"] == {} and none["tokens_before_mean"] is None
    # A boundary before any call has nothing earlier to repeat, and only the first five calls after it count.
    far = {"messages": messages, "compactions": [{"trigger": "level", "boundary": 0}]}
    assert trajectory.compaction_effects(far, calls)["repeated_after"] == 0
    assert trajectory.compaction_effects(far, calls)["rereads_after"] == 0
    # resumes_at: the messages between the boundary and it were kept verbatim and ran before the
    # compaction, so they count as earlier, and the post-compaction window starts there.
    kept = {"messages": messages, "compactions": [
        {"trigger": "smart", "boundary": 5, "resumes_at": 10},   # the kept span holds c3 and c4
        {"trigger": "level", "boundary": 14, "resumes_at": 15}]}
    e = trajectory.compaction_effects(kept, calls)
    # After the first: c5 (read_file app.py: repeated, a re-read) and c6 (new write). After the
    # second: c7 (pytest: repeated). c3's failure is in the kept span, so no first-step error.
    assert e["repeated_after"] == 2 and e["first_step_errors"] == 0 and e["rereads_after"] == 1
    # A resumes_at at or before the boundary is ignored.
    odd = {"messages": messages, "compactions": [{"trigger": "level", "boundary": 5, "resumes_at": 5}]}
    assert trajectory.compaction_effects(odd, calls)["repeated_after"] == 3
