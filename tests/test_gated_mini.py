import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest

from fsbench.agents import gated_mini
from fsbench.agents.gated_mini import GatedMini, check_command, read_check


def agent(tmp_path, monkeypatch, outputs, **kw):
    """A gated agent whose mini-swe-agent rounds and container commands are recorded, not run."""
    a = GatedMini(logs_dir=tmp_path, model_name="openai/x", **kw)
    tasks, checks = [], iter(outputs)

    async def solve(task, environment, context):
        tasks.append(task)

    async def exec_as_agent(environment, command, env=None, cwd=None, timeout_sec=None):
        if command.startswith("out=$("):
            return SimpleNamespace(stdout=next(checks))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(a, "_solve", solve)
    monkeypatch.setattr(a, "exec_as_agent", exec_as_agent)
    return a, tasks


def run(a, instruction="ship it"):
    context = gated_mini.AgentContext()
    asyncio.run(a.run(instruction, environment=None, context=context))
    a.populate_context_post_run(context)
    return context.metadata


def test_a_failed_check_sends_mini_back_with_the_same_information_rusty_gets(tmp_path, monkeypatch):
    a, tasks = agent(tmp_path, monkeypatch, ["3 tests failed\n__public_check_exit=1\n", "ok\n__public_check_exit=0\n"],
                     verify="public-check")
    meta = run(a)
    assert tasks[0] == "ship it"
    assert "`public-check` exited with status 1" in tasks[1] and "3 tests failed" in tasks[1]
    assert tasks[1].endswith("Original task:\nship it")
    assert meta["completion_proposals"] == 2 and meta["completion_rejections"] == 1
    assert meta["completion_accepted"] is True and meta["public_check_outcome"] == "passed"
    assert meta["gate_context"] == "fresh_per_round" and meta["verification_runs"] == 2


def test_rounds_are_capped_and_an_unaccepted_run_is_recorded_as_such(tmp_path, monkeypatch):
    a, tasks = agent(tmp_path, monkeypatch, ["no\n__public_check_exit=1\n"] * 2 + ["slow\n__public_check_exit=124\n"],
                     verify="public-check", max_rounds=3)
    meta = run(a)
    assert len(tasks) == 3
    assert meta["completion_accepted"] is False and meta["public_check_passed"] is False
    assert meta["public_check_outcome"] == "timed_out" and meta["completion_rejections"] == 3


def test_without_a_check_it_is_plain_mini_swe_agent(tmp_path, monkeypatch):
    a, tasks = agent(tmp_path, monkeypatch, [])
    meta = run(a)
    assert tasks == ["ship it"]
    assert meta["gate"] == "none" and meta["public_check_outcome"] == "not_run" and meta["verification_runs"] == 0


def test_the_prompt_template_is_applied_once(tmp_path, monkeypatch):
    template = tmp_path / "t.j2"
    template.write_text("PRE {{ instruction }}")
    a, tasks = agent(tmp_path, monkeypatch, ["x\n__public_check_exit=1\n", "__public_check_exit=0\n"],
                     verify="public-check", prompt_template_path=str(template))
    run(a, "do it")
    assert tasks[0] == "PRE do it"
    assert tasks[1].count("PRE") == 1 and tasks[1].endswith("Original task:\nPRE do it")


def test_tokens_add_up_over_every_round(tmp_path, monkeypatch):
    a, _ = agent(tmp_path, monkeypatch, ["__public_check_exit=0\n"], verify="public-check")
    for n, (p, c) in enumerate([(100, 10), (50, 5)], start=1):
        (tmp_path / f"mini-swe-agent.round{n}.trajectory.json").write_text(json.dumps(
            {"messages": [{"role": "assistant", "content": "x",
                           "extra": {"response": {"usage": {"prompt_tokens": p, "completion_tokens": c}}}}]}))
    context = gated_mini.AgentContext()
    asyncio.run(a.run("t", environment=None, context=context))
    a.populate_context_post_run(context)
    assert (context.n_input_tokens, context.n_output_tokens) == (150, 15)


def test_check_command_reports_exit_and_output_in_a_real_shell():
    out = subprocess.run(["bash", "-c", check_command("echo hi; echo there >&2; exit 3", 5)],
                         capture_output=True, text=True)
    assert out.returncode == 0, "the exec itself never fails"
    assert read_check(out.stdout) == ("failed", 3, "hi\nthere")
    slow = subprocess.run(["bash", "-c", check_command("sleep 5", 1)], capture_output=True, text=True)
    assert read_check(slow.stdout)[:2] == ("timed_out", 124)
    assert read_check("garbage")[0] == "error"


def test_settings_are_validated(tmp_path):
    for bad in ({"verify": "a\nb"}, {"verify": " "}, {"verify_timeout": 0}, {"max_rounds": 0}):
        with pytest.raises(ValueError):
            GatedMini(logs_dir=tmp_path, model_name="openai/x", **bad)


def test_harbor_accepts_the_gate_options_before_building_the_agent():
    # Harbor checks `--ak` options against the agent's options model in a preflight, before the
    # constructor runs; undeclared options are refused there ("Unknown option 'verify'").
    opts = GatedMini.parse_options({"verify": "public-check", "max_rounds": 2, "version": "2.4.6",
                                    "config_file": None})
    assert (opts.verify, opts.max_rounds, opts.verify_timeout) == ("public-check", 2, 120)
    with pytest.raises(ValueError, match="max_rounds"):
        GatedMini.parse_options({"max_rounds": 0})
    with pytest.raises(ValueError, match="Unknown option 'nonsense'"):
        GatedMini.parse_options({"nonsense": 1})
