"""mini-swe-agent with a fixed public acceptance check: the mechanism-transfer track.

    harbor run -p tasks/<task> -a fsbench.agents.gated_mini:GatedMini -m openai/<model> \
        --ak version=2.4.6 --ak config_file=configs/mswea-compact.yaml \
        --ak 'verify="public-check"' --job-name <job>

Rusty's `--verify` refuses to accept completion until a fixed check passes. This wrapper gives
the baseline the same gate, so an effect of the gate can be told apart from an effect of Rusty:
when mini-swe-agent submits, the check runs in the task container as the agent user; if it fails,
mini-swe-agent is started again with the check's exit status and output (the same information
Rusty's gate returns) and the original task. The container keeps every change between rounds.

Known differences from Rusty's gate, recorded in the trial metadata (`gate_context`):
- each round starts mini-swe-agent with a fresh context; Rusty continues the same conversation;
- the number of rounds is capped (`max_rounds`, default 3); Rusty is capped by its turn limit.
Both run under the same gateway envelope, which bounds the whole episode either way.

Completion events use the adapter-wide keys (`completion_proposals`, `completion_rejections`,
`completion_accepted`, `verification_runs`, `public_check_outcome`, `public_check_passed`), so
the analysis reads them the same way for every track.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from harbor.agents.installed.base import with_prompt_template
from harbor.agents.installed.mini_swe_agent import MiniSweAgent, MiniSweAgentOptions, _message_usage
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

TRAJECTORY = "/logs/agent/mini-swe-agent.trajectory.json"
EXIT_MARK = "__public_check_exit="


def check_command(check: str, timeout: int) -> str:
    """Run the check, never fail the exec, and report its exit status on the last line."""
    return (f"out=$(timeout {timeout} bash -lc {shlex.quote(check)} 2>&1); code=$?; "
            f"printf '%s\\n{EXIT_MARK}%s\\n' \"$out\" \"$code\"")


def read_check(stdout: str) -> tuple[str, int | None, str]:
    """(outcome, exit status, output) from `check_command`'s stdout."""
    body, _, tail = (stdout or "").rpartition(EXIT_MARK)
    try:
        code = int(tail.strip())
    except ValueError:
        return "error", None, stdout or ""
    outcome = "passed" if code == 0 else "timed_out" if code == 124 else "failed"
    return outcome, code, body.rstrip("\n")


def feedback(check: str, code: int | None, output: str, instruction: str, limit: int = 4000) -> str:
    shown = output if len(output) <= limit else "[earlier output omitted]\n" + output[-limit:]
    return (f"Your previous submission was not accepted: the acceptance check `{check}` exited with status "
            f"{code}. Its output:\n```\n{shown}\n```\nThe environment keeps every change made so far. Continue "
            f"the original task until it is complete and the check passes.\n\nOriginal task:\n{instruction}")


def round_tokens(paths: list[Path]) -> tuple[int, int]:
    prompt = completion = 0
    for path in paths:
        try:
            messages = json.loads(path.read_text()).get("messages") or []
        except (OSError, ValueError):
            continue
        for message in messages:
            usage = _message_usage(message)
            prompt += usage["prompt_tokens"]
            completion += usage["completion_tokens"]
    return prompt, completion


class GatedMiniOptions(MiniSweAgentOptions):
    """mini-swe-agent's options plus the gate's. Harbor validates `--ak` options against this model
    before it constructs the agent, so every option must be declared here."""

    verify: str | None = Field(default=None, description="The fixed public acceptance check (one line).")
    verify_timeout: int = Field(default=120, ge=1, le=600, description="Seconds the check may run.")
    max_rounds: int = Field(default=3, ge=1, description="mini-swe-agent runs at most this many times.")

    @field_validator("verify")
    @classmethod
    def one_line(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\n" in value):
            raise ValueError("verify must be a one-line command")
        return value


class GatedMini(MiniSweAgent):
    options_model = GatedMiniOptions
    options: GatedMiniOptions

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._verify = self.options.verify
        self._verify_timeout, self._max_rounds = self.options.verify_timeout, self.options.max_rounds
        self._rounds: list[dict] = []

    @staticmethod
    def name() -> str:
        return "gated-mini-swe-agent"

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        task = instruction
        for n in range(1, self._max_rounds + 1):
            await self._solve(task, environment, context)
            await self.exec_as_agent(environment, command=(
                f"cp {TRAJECTORY} /logs/agent/mini-swe-agent.round{n}.trajectory.json 2>/dev/null || true"))
            if self._verify is None:
                self._rounds.append({"round": n, "check": "not_run"})
                return
            result = await self.exec_as_agent(environment, command=check_command(self._verify, self._verify_timeout),
                                              timeout_sec=self._verify_timeout + 30)
            outcome, code, output = read_check(result.stdout)
            self._rounds.append({"round": n, "check": outcome, "exit": code})
            if outcome == "passed":
                return
            task = feedback(self._verify, code, output, instruction)

    async def _solve(self, task: str, environment: BaseEnvironment, context: AgentContext) -> None:
        # The parent's undecorated run: the prompt template was applied once, in run().
        await MiniSweAgent.run.__wrapped__(self, task, environment, context)

    def populate_context_post_run(self, context: AgentContext) -> None:
        super().populate_context_post_run(context)
        rounds = sorted(self.logs_dir.glob("mini-swe-agent.round*.trajectory.json"))
        if rounds:
            context.n_input_tokens, context.n_output_tokens = round_tokens(rounds)
        checks = [r["check"] for r in self._rounds if r["check"] != "not_run"]
        context.metadata = {
            **(context.metadata or {}),
            "gate": "public_check" if self._verify else "none",
            "gate_context": "fresh_per_round",
            "verify": self._verify, "verify_timeout": self._verify_timeout, "max_rounds": self._max_rounds,
            "gate_rounds": self._rounds,
            "completion_source": "gated_mini",
            "completion_proposals": len(self._rounds),
            "completion_blocked_claims": 0,
            "completion_rejections": sum(c != "passed" for c in checks),
            "completion_accepted": (checks[-1] == "passed") if checks else (len(self._rounds) > 0),
            "verification_runs": len(checks),
            "public_check_outcome": checks[-1] if checks else "not_run",
            "public_check_passed": (checks[-1] == "passed") if checks else None,
        }
