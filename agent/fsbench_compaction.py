"""Context compaction for long-horizon runs: a mini-swe-agent DefaultAgent that never loses anything.

Select it in a mini-swe-agent config:

    agent:
      agent_class: fsbench_compaction.CompactingAgent
      context_budget_tokens: 60000

and put this file on PYTHONPATH (the FullStack-Bench agent image does: /opt/fsbench).

How the model's context is built on every call (the full history is still saved to the trajectory):

1. **Pinned**: the system prompt and the task. Never compacted.
2. **Working memory** (one user message, rebuilt every call):
   - *Changes made*: every state-changing command so far, verbatim and in order. Never summarised.
   - *Summary of earlier work*: structured notes the same model wrote when older steps were folded away.
   - *Your notes*: the contents of `<memory_dir>/notes.md`, a file the agent may edit itself.
   - Where to re-read anything: every full tool output is on disk at `<memory_dir>/obs/`.
3. **Recent steps**: the last `keep_recent_steps` steps verbatim.
4. **Older steps**: assistant messages kept, tool outputs shortened to head and tail with a pointer
   to the full output on disk. When even that exceeds the budget, the oldest steps are folded into
   the summary (written by the agent's own model, so the run still measures that model) and dropped
   from the context, never from disk or the trajectory.

A step is an assistant message plus the tool results that answer it, so compaction never splits a
tool call from its result. Every compaction event is recorded in the trajectory under
info.compaction.
"""

import json
import os
import re
import time
from pathlib import Path

from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.exceptions import LimitsExceeded, TimeExceeded

MUTATING = re.compile(
    r"""\bsc\s+(-o\s+\w+\s+)?(put|apply|delete|deploy|promote|rollback|traffic|restart|import|db\s+(branch|snapshot))\b"""
    r"""|\bcurl\b[^\n]*(-X\s*(PUT|POST|DELETE|PATCH)|--request\s+(PUT|POST|DELETE|PATCH)|\s(-d|--data\S*)\s)"""
    r"""|\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|ALTER\s+TABLE|DROP\s+(TABLE|INDEX|SCHEMA|DATABASE|VIEW)"""
    r"""|TRUNCATE\s+\w|CREATE\s+(UNIQUE\s+)?(TABLE|INDEX))\b"""
    r"""|\bgit\s+(commit|push|reset|checkout\s+--)\b|\b(kubectl|helm)\s+(apply|delete|rollout|scale|upgrade)\b""",
    re.I)
# Scripts that call APIs: an explicit write method in a heredoc or inline script.
SCRIPTED_WRITE = re.compile(r"""["'](PUT|POST|DELETE|PATCH)["']|requests\.(post|put|delete|patch)\(""")


SUMMARY_PROMPT = """You are maintaining working notes for an engineer partway through a long task.
Fold the EARLIER STEPS below into the existing notes. Return only the updated notes, in exactly these sections:

## Goal and constraints
(what must be true at the end, and every rule that must not be broken; keep the exact wording of rules)
## Facts learned
(one bullet per fact, each with its source: file path and line, command, or URL; keep exact names, ids,
paths, versions, env var names, error codes)
## Changes made
(every change already made to any environment, in order, with the exact command; say if it is unverified)
## Hypotheses
(current theories, each marked confirmed / refuted / open)
## Done and next
(what is finished, then the next 1-5 steps)

Never invent facts. If a detail is not in the steps or the existing notes, leave it out.

EXISTING NOTES:
{notes}

EARLIER STEPS:
{steps}
"""


class CompactingConfig(AgentConfig):
    context_budget_tokens: int = 60000
    """Compact when the context sent to the model would exceed this many (estimated) tokens."""
    keep_recent_steps: int = 6
    """Steps kept verbatim at the end of the context."""
    observation_head_chars: int = 1200
    observation_tail_chars: int = 800
    """How much of an older tool output stays in context (the rest is on disk)."""
    summary_max_tokens: int = 3000
    memory_dir: str = "/tmp/agent-memory"


def estimate_tokens(messages: list[dict]) -> int:
    # Conservative: ~3.5 characters per token for code and logs.
    return int(sum(len(json.dumps({k: v for k, v in m.items() if k != "extra"}, default=str)) for m in messages) / 3.5)


class CompactingAgent(DefaultAgent):
    def __init__(self, model, env, *, config_class: type = CompactingConfig, **kwargs):
        super().__init__(model, env, config_class=config_class, **kwargs)
        self.memory = Path(self.config.memory_dir)
        (self.memory / "obs").mkdir(parents=True, exist_ok=True)
        self.summary = ""
        self.folded = 0  # number of leading history steps folded into the summary
        self.changes: list[str] = []
        self.compaction_events: list[dict] = []
        self._obs_count = 0

    # ---- recording ----------------------------------------------------------------------

    def add_messages(self, *messages: dict) -> list[dict]:
        for m in messages:
            if m.get("role") == "assistant":
                for action in (m.get("extra") or {}).get("actions") or []:
                    cmd = action.get("command", "")
                    if MUTATING.search(cmd) or SCRIPTED_WRITE.search(cmd):
                        self.changes.append(f"[step {self.n_calls}] {cmd.strip()}")
            elif m.get("role") in ("tool", "user") and (m.get("extra") or {}).get("raw_output") is not None:
                self._obs_count += 1
                path = self.memory / "obs" / f"step-{self.n_calls:04d}-{self._obs_count:05d}.txt"
                path.write_text(str(m["extra"]["raw_output"]))
                m.setdefault("extra", {})["saved_to"] = str(path)
        return super().add_messages(*messages)

    # ---- building the context ----------------------------------------------------------------

    def _steps(self) -> tuple[list[dict], list[list[dict]]]:
        """Split messages into the pinned prefix (system + task) and steps."""
        pinned, rest = self.messages[:2], self.messages[2:]
        steps, current = [], []
        for m in rest:
            if m.get("role") == "assistant" and current:
                steps.append(current)
                current = []
            current.append(m)
        if current:
            steps.append(current)
        return pinned, steps

    def _shorten(self, m: dict, head: int | None = None, tail: int | None = None) -> dict:
        content = m.get("content")
        if not isinstance(content, str):
            return m
        h = self.config.observation_head_chars if head is None else head
        t = self.config.observation_tail_chars if tail is None else tail
        if len(content) <= h + t + 200:
            return m
        saved = (m.get("extra") or {}).get("saved_to", "")
        elided = len(content) - h - t
        out = {k: v for k, v in m.items() if k != "content"}
        out["content"] = (content[:h] + f"\n[... {elided} characters elided from context; full output: {saved} ...]\n"
                          + content[-t:])
        return out

    def _memory_message(self) -> dict | None:
        notes_file = self.memory / "notes.md"
        notes = notes_file.read_text()[:8000] if notes_file.exists() else ""
        parts = ["# Working memory (rebuilt every step; earlier steps may be folded away)",
                 f"Every full command output is saved under {self.memory}/obs/ (named step-NNNN-*.txt). "
                 f"You can keep your own notes in {notes_file}; they are shown here every step."]
        parts.append("## Changes made so far (verbatim, never summarised)\n" +
                     ("\n".join(self.changes) if self.changes else "(none yet)"))
        if self.summary:
            parts.append("## Summary of earlier work\n" + self.summary)
        if notes:
            parts.append("## Your notes\n" + notes)
        if not (self.changes or self.summary or notes) and self.folded == 0:
            return None
        return {"role": "user", "content": "\n\n".join(parts)}

    def context(self) -> list[dict]:
        pinned, steps = self._steps()
        recent_from = max(self.folded, len(steps) - self.config.keep_recent_steps)
        older = [[self._shorten(m) for m in s] for s in steps[self.folded:recent_from]]
        recent = steps[recent_from:]

        def build():
            mem = self._memory_message()
            return pinned + ([mem] if mem else []) + [m for s in older for m in s] + [m for s in recent for m in s]
        view = build()
        if estimate_tokens(view) <= self.config.context_budget_tokens:
            return view
        # Fold the oldest steps into the summary until the context fits (or only recent steps remain).
        while older and estimate_tokens(build()) > self.config.context_budget_tokens:
            n = max(1, len(older) // 2)
            chunk = steps[self.folded:self.folded + n]
            self._fold(chunk)
            self.folded += n
            older = older[n:]
        if estimate_tokens(build()) <= self.config.context_budget_tokens:
            return build()
        # Recent steps alone are too big (huge outputs): shorten all but the newest step's outputs,
        # then, as a last resort, the newest too, sized to fit. Full outputs stay on disk.
        recent = [[self._shorten(m) if m.get("role") != "assistant" else m for m in s] for s in recent[:-1]] + recent[-1:]
        if estimate_tokens(build()) > self.config.context_budget_tokens and recent:
            spare = max(2000, int((self.config.context_budget_tokens - estimate_tokens(build()[:-len(recent[-1])]))
                                  * 3.5) - 2000)
            per = max(1000, spare // max(1, len(recent[-1])))
            recent[-1] = [self._shorten(m, per * 2 // 3, per // 3) if m.get("role") != "assistant" else m
                          for m in recent[-1]]
        return build()

    def _fold(self, chunk: list[list[dict]]) -> None:
        start = time.time()
        text = "\n\n".join(f"{m.get('role')}: {str(self._shorten(m).get('content'))[:4000]}"
                           + (f"\n(commands: {[a.get('command') for a in (m.get('extra') or {}).get('actions', [])]})"
                              if m.get("role") == "assistant" else "")
                           for s in chunk for m in s)
        new_summary, how = None, "model"
        try:
            import litellm
            cfg = self.model.config
            kwargs = {k: v for k, v in (getattr(cfg, "model_kwargs", {}) or {}).items()
                      if k not in ("tools", "tool_choice", "parallel_tool_calls")}
            kwargs["max_tokens"] = self.config.summary_max_tokens
            resp = litellm.completion(model=cfg.model_name, messages=[
                {"role": "user", "content": SUMMARY_PROMPT.format(notes=self.summary or "(none)", steps=text)}],
                **kwargs)
            new_summary = (resp.choices[0].message.content or "").strip() or None
        except Exception as e:  # never let compaction kill the run
            how = f"deterministic ({type(e).__name__})"
        if not new_summary:
            # Deterministic fallback: keep each folded step's commands and the first line of its result.
            lines = [self.summary] if self.summary else []
            for s in chunk:
                for m in s:
                    if m.get("role") == "assistant":
                        for a in (m.get("extra") or {}).get("actions", []):
                            lines.append(f"- ran: {a.get('command', '')[:300]}")
                    elif m.get("role") in ("tool", "user"):
                        first = str(m.get("content", "")).strip().splitlines()[:1]
                        saved = (m.get("extra") or {}).get("saved_to", "")
                        lines.append(f"  -> {first[0][:200] if first else ''} (full: {saved})")
            new_summary = "\n".join(lines)[-12000:]
        self.summary = new_summary
        self.compaction_events.append({"at_step": self.n_calls, "folded_steps": len(chunk), "how": how,
                                       "seconds": round(time.time() - start, 1), "summary_chars": len(new_summary)})

    # ---- the loop ---------------------------------------------------------------------------

    def query(self) -> dict:
        if 0 < self.config.step_limit <= self.n_calls or 0 < self.config.cost_limit <= self.cost:
            raise LimitsExceeded({"role": "exit", "content": "LimitsExceeded",
                                  "extra": {"exit_status": "LimitsExceeded", "submission": ""}})
        if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):
            raise TimeExceeded({"role": "exit", "content": "TimeExceeded",
                                "extra": {"exit_status": "TimeExceeded", "submission": ""}})
        self.n_calls += 1
        message = self.model.query(self.context())
        self.cost += message.get("extra", {}).get("cost", 0.0)
        self.add_messages(message)
        return message

    def serialize(self, *extra_dicts) -> dict:
        return super().serialize({"info": {"compaction": {
            "events": self.compaction_events, "folded_steps": self.folded, "changes": self.changes,
            "summary": self.summary, "memory_dir": str(self.memory)}}}, *extra_dicts)
