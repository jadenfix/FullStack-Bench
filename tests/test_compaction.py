import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import context_compaction as fc  # noqa: E402

TEMPLATE = "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}\n</output>"


class FakeModel:
    """Scripted tool-calling model: step i runs commands[i]; records every context it was sent."""

    def __init__(self, commands):
        self.commands = commands
        self.seen = []
        self.config = types.SimpleNamespace(model_name="fake/model", model_kwargs={})

    def get_template_vars(self):
        return {}

    def format_message(self, **kw):
        return dict(kw)

    def query(self, messages):
        self.seen.append(messages)
        i = len(self.seen) - 1
        if i >= len(self.commands):
            return {"role": "assistant", "content": "done", "tool_calls": [],
                    "extra": {"actions": [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                                           "tool_call_id": f"t{i}"}]}}
        return {"role": "assistant", "content": f"thinking about step {i}",
                "tool_calls": [{"id": f"t{i}", "type": "function"}],
                "extra": {"actions": [{"command": self.commands[i], "tool_call_id": f"t{i}"}]}}

    def format_observation_messages(self, message, outputs, template_vars):
        out = []
        for a, o in zip(message["extra"]["actions"], outputs):
            out.append({"role": "tool", "tool_call_id": a["tool_call_id"], "content": o["output"],
                        "extra": {"raw_output": o["output"], "returncode": o["returncode"]}})
        return out

    def serialize(self):
        return {}


class FakeEnv:
    def __init__(self, big=20000):
        self.big = big

    def get_template_vars(self):
        return {}

    def execute(self, action):
        cmd = action["command"]
        if "COMPLETE_TASK" in cmd:
            from minisweagent.exceptions import Submitted
            raise Submitted({"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": ""}})
        return {"output": f"$ {cmd}\n" + ("x" * self.big) + f"\nEND-OF-{cmd[:20]}", "returncode": 0}

    def serialize(self):
        return {}


def run_agent(tmp_path, commands, budget=12000, monkeypatch=None, summary="## Goal and constraints\n- ship it"):
    if monkeypatch is not None:
        fake_litellm = types.SimpleNamespace(completion=lambda **kw: types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=summary))]))
        monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    model, env = FakeModel(commands), FakeEnv()
    agent = fc.CompactingAgent(model, env, system_template="system", instance_template="the task",
                               step_limit=0, cost_limit=0, context_budget_tokens=budget, keep_recent_steps=3,
                               memory_dir=str(tmp_path / "mem"), output_path=tmp_path / "traj.json")
    agent.run("task")
    return agent, model


def tool_pairs_ok(messages):
    open_ids = set()
    for m in messages:
        if m.get("role") == "assistant":
            assert not open_ids, f"tool calls left unanswered: {open_ids}"
            open_ids = {tc["id"] for tc in m.get("tool_calls") or []}
        elif m.get("role") == "tool":
            assert m["tool_call_id"] in open_ids, "tool result without its call"
            open_ids.discard(m["tool_call_id"])
    return True


def test_context_stays_within_budget_and_history_is_complete(tmp_path, monkeypatch):
    cmds = [f"cat file{i}.py" for i in range(30)]
    agent, model = run_agent(tmp_path, cmds, monkeypatch=monkeypatch)
    for ctx in model.seen:
        assert fc.estimate_tokens(ctx) <= 12000 * 1.1
        assert ctx[0]["content"] == "system" and ctx[1]["content"] == "the task"  # pinned
        assert tool_pairs_ok(ctx[2:])
    traj = json.loads((tmp_path / "traj.json").read_text())
    assert len([m for m in traj["messages"] if m.get("role") == "tool"]) == 30  # nothing lost from the record
    assert traj["info"]["compaction"]["events"] and traj["info"]["compaction"]["folded_steps"] > 0


def test_recent_steps_verbatim_older_outputs_on_disk(tmp_path, monkeypatch):
    agent, model = run_agent(tmp_path, [f"cat f{i}" for i in range(12)], budget=40000, monkeypatch=monkeypatch)
    last = model.seen[-1]
    tool_msgs = [m for m in last if m.get("role") == "tool"]
    assert tool_msgs[-1]["content"].endswith("END-OF-cat f11")  # most recent: full
    shortened = [m for m in tool_msgs if "characters elided" in m["content"]]
    assert shortened
    path = shortened[0]["content"].split("full output: ")[1].split(" ...]")[0]
    assert Path(path).read_text().count("x") == 20000  # lossless on disk


def test_change_ledger_survives_folding(tmp_path, monkeypatch):
    cmds = ["sc deploy prod web --source /app"] + [f"cat big{i}" for i in range(25)] + \
           ["curl -X PUT $U/kv/flags/keys/a -d '{\"value\": 1}'"]
    agent, model = run_agent(tmp_path, cmds, monkeypatch=monkeypatch)
    memory = next(m for m in model.seen[-1] if m.get("role") == "user" and "Working memory" in m["content"])
    assert "sc deploy prod web --source /app" in memory["content"]  # from step 1, long since folded
    assert "curl -X PUT" in memory["content"]
    assert "## Summary of earlier work\n## Goal and constraints" in memory["content"]


def test_deterministic_fallback_when_summary_call_fails(tmp_path, monkeypatch):
    def boom(**kw):
        raise RuntimeError("no model")
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=boom))
    agent, model = run_agent(tmp_path, [f"cat x{i}" for i in range(25)])
    assert agent.compaction_events and agent.compaction_events[0]["how"].startswith("deterministic")
    assert "- ran: cat x0" in agent.summary and "(full: " in agent.summary


def test_agent_notes_are_shown(tmp_path, monkeypatch):
    (tmp_path / "mem").mkdir()
    (tmp_path / "mem" / "notes.md").write_text("flag key is checkout.engine")
    agent, model = run_agent(tmp_path, ["ls"], monkeypatch=monkeypatch)
    assert any("flag key is checkout.engine" in m.get("content", "") for m in model.seen[-1])
