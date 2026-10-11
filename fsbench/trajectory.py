"""A per-attempt trajectory summary from the harness's own trajectory file, at analysis time only.

Both harnesses write OpenAI-format messages: Rusty to `agent/rusty.trajectory.json` (plus totals,
goal, model budget and safety), mini-swe-agent to `agent/mini-swe-agent.trajectory.json`, and the
gated mini track one `agent/mini-swe-agent.round<n>.trajectory.json` per gate round, read in
order. Every tool call is an assistant message's `tool_calls[].function` with a `name` and JSON
`arguments`. Nothing here runs at attempt time: `fsbench.report` calls `summary_for_trial` when a
trial directory resolves (a published records directory, say), and an attempt whose trajectory
is absent is reported as lacking the summary, never as zero.

The summary's schema, the same dict an adapter could write into a terminal record:

    {"calls": int,                       # tool calls the agent made
     "phases": {"explore": int, "edit": int, "run": int, "verify": int, "other": int},
     "shell_calls": int,                 # calls to a shell or command tool (a subset of calls)
     "edits": int,                       # calls that wrote, edited or patched a file
     "repatches": int,                   # edits to a file this attempt had already edited
     "out_of_interface_calls": int,      # calls the harness refused as naming no offered tool
     "ended_on_out_of_interface": bool,  # the attempt's last call was one of those
     "compactions": {                    # what followed each compaction boundary (see below)
         "count": int, "by_trigger": {str: int}, "by_model": {str: int}, "repeated_after": int,
         "first_step_errors": int, "rereads_after": int,
         "tokens_before_mean": float|None, "tokens_after_mean": float|None},
     "files": [str]}                     # the trajectory files read, relative to the trial

Compactions come from the `compactions` list a post-pin Rusty writes at the top level of its
trajectory (one entry per compaction with `trigger`, `tokens_before`, `tokens_after`,
`calls_since_last`, `boundary`, the index in `messages` of the summary that replaced history,
and `resumes_at`, the index of the first message made after the compaction; `messages` keeps
the archived messages, so indices are stable). Between `boundary` and `resumes_at` sit the
summary and the messages the compaction kept verbatim, which ran before it. For each
compaction the first COMPACTION_WINDOW tool calls from `resumes_at` on, and before the next
compaction's boundary, are read: `repeated_after` counts those whose signature (tool name and
canonical arguments) the attempt had already made before `resumes_at` (the kept span
included, since those calls are still in the context verbatim), `first_step_errors` whether
the first of them failed (a result starting `error` or `denied`, a refusal, or a shell exit
code other than 0), and `rereads_after` the reads of a file the attempt had read before
`resumes_at`. A record without `resumes_at` (none is written before rusty#71's 38d4944) is
read from `boundary + 1`. A trajectory without the list reports a count of 0, since the pinned
Rusty and mini-swe-agent record no compactions.

An out-of-interface call is read from the harness's own refusal, which both harnesses write
into the trajectory as the call's result: Rusty answers a name no tool matches with
`error: unknown tool ...` (`src/tools.rs`), an unresolved MCP name with `error: unknown MCP
tool ...` and, under `--toolset shell`, anything but its shell tools with the same `unknown
tool` prefix (`src/agent.rs` at the pinned 32cac02); mini-swe-agent accepts `bash` only and
answers any other name with a user message containing `Unknown tool '<name>'.`
(`minisweagent/models/utils/actions_toolcall.py`). Neither harness writes the offered tool
list into its trajectory, so the metric never needs one; a `tools` list, when present, is
checked as well.

A tool is classified by the words in its name (split on anything but letters and digits) under
TOOL_PHASES; the first phase listed whose words it contains wins, so `run_tests` is verify, not
run. A shell call is classified by its command's first program the same way (`cat`, `grep` and
`ls` explore; `pytest` verifies), a command that redirects into a file, pipes through `tee` or
runs `sed -i` is an edit of that file, and anything else is `run`. The taxonomy is a heuristic
over names, stated here so a reader can judge it; it never reads a tool's output."""

from __future__ import annotations

import json
import re
from pathlib import Path

PHASES = ("explore", "edit", "run", "verify", "other")
TOOL_PHASES = {
    "verify": ("verify", "check", "test", "tests", "pytest", "npm_test", "cargo_test"),
    "edit": ("write", "edit", "patch", "create", "replace", "insert", "touch", "tee"),
    "explore": ("read", "list", "ls", "search", "grep", "rg", "glob", "find", "cat", "head", "tail", "view",
                "get", "describe", "logs", "tree", "stat", "which", "diff"),
    "run": ("shell", "bash", "sh", "exec", "execute", "command", "build", "deploy", "apply", "rollout", "run",
            "make", "docker", "kubectl", "curl", "python", "python3", "uv", "npm", "node", "cargo", "go"),
}
SHELL_TOOLS = frozenset({"shell", "bash", "sh", "exec", "execute", "command", "terminal"})
TRAJECTORY_FILES = ("rusty.trajectory.json", "mini-swe-agent.trajectory.json")
ROUND_FILE = re.compile(r"^mini-swe-agent\.round(\d+)\.trajectory\.json$")
PATH_ARGS = ("path", "file_path", "filename", "file", "target")
REDIRECT = re.compile(r"(?<![<>])>{1,2}\s*([^\s;&|)]+)")
TEE = re.compile(r"\btee\s+(?:-a\s+)?([^\s;&|]+)")
SED_INPLACE = re.compile(r"\bsed\s+-i\S*\s+(?:-e\s+)?(?:'[^']*'|\"[^\"]*\"|\S+)\s+([^\s;&|]+)")
PREFIX = re.compile(r"^(?:\s*(?:cd\s+\S+\s*(?:&&|;)|[A-Za-z_][A-Za-z0-9_]*=\S*|sudo|env|time|nohup)\s*)+")
# The harnesses' own refusals of a call that named no offered tool (see the module docstring).
REFUSALS = (re.compile(r"^\s*error: unknown tool `"),   # Rusty, full and shell toolsets
            re.compile(r"unknown MCP tool `"),            # Rusty, two phrasings, one inside an error chain
            re.compile(r"Unknown tool '[^']*'\."))        # mini-swe-agent


def _words(name: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", str(name).lower()) if w}


def phase_of(name: str) -> str:
    """The taxonomy phase of a tool or program name; `other` when no word matches."""
    words = _words(name)
    return next((p for p in TOOL_PHASES if words & set(TOOL_PHASES[p])), "other")


def _arguments(call: dict) -> dict:
    args = (call.get("function") or {}).get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return args if isinstance(args, dict) else {}


def classify_call(name: str, arguments: dict) -> tuple[str, str | None, bool]:
    """(phase, edited path or None, is a shell call) for one tool call."""
    shell = bool(_words(name) & SHELL_TOOLS)
    if not shell:
        phase = phase_of(name)
        path = next((arguments[k] for k in PATH_ARGS if isinstance(arguments.get(k), str)), None)
        return phase, path if phase == "edit" else None, False
    command = str(arguments.get("command") or arguments.get("cmd") or "")
    first = command.strip().splitlines()[0] if command.strip() else ""
    for pattern in (REDIRECT, TEE, SED_INPLACE):
        target = next((h.group(1) for h in pattern.finditer(first) if h.group(1) != "/dev/null"), None)
        if target is not None:
            return "edit", target, True
    program = PREFIX.sub("", first).split(None, 1)[0] if first.strip() else ""
    phase = phase_of(program.rsplit("/", 1)[-1])
    return (phase if phase != "other" else "run"), None, True


def _text(content) -> str:
    if isinstance(content, list):  # OpenAI content parts
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def tool_calls(data: dict) -> list[dict]:
    """The attempt's tool calls in order as `{"tool", "arguments", "at", "refused", "failed"}` from
    one trajectory file. `at` is the index of the assistant message that made the call. `refused`
    is whether the harness answered the call with its unknown-tool refusal, and `failed` whether
    the result reads as a failure (a refusal, a result starting `error` or `denied`, or a shell
    `exit code:` line other than 0): the `tool` message carrying the call's id, else the first
    `tool` or `user` message after it."""
    out, pending = [], {}
    messages = [m for m in data.get("messages") or [] if isinstance(m, dict)]
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            call = {"tool": str((tc.get("function") or {}).get("name") or ""), "arguments": _arguments(tc),
                    "at": i, "refused": False, "failed": False}
            out.append(call)
            pending[len(out) - 1] = (tc.get("id"), i)
    by_id = {m.get("tool_call_id"): m for m in messages if m.get("role") == "tool" and m.get("tool_call_id")}
    for index, (call_id, at) in pending.items():
        reply = by_id.get(call_id) if call_id else None
        if reply is None:
            reply = next((m for m in messages[at + 1:] if m.get("role") in ("tool", "user")), None)
        text = _text((reply or {}).get("content"))
        out[index]["refused"] = any(p.search(text) for p in REFUSALS)
        out[index]["failed"] = out[index]["refused"] or _failed(text)
    return out


EXIT_CODE = re.compile(r"^\s*exit code:\s*(-?\d+)")


def _failed(result: str) -> bool:
    head = result.lstrip().lower()
    if head.startswith(("error", "denied")):
        return True
    code = EXIT_CODE.match(result)
    return code is not None and code.group(1) != "0"


COMPACTION_WINDOW = 5
READ_PROGRAMS = frozenset({"cat", "head", "tail", "less", "more", "sed", "nl", "view"})


def _signature(call: dict) -> str:
    return (call.get("tool") or "") + " " + json.dumps(call.get("arguments") or {}, sort_keys=True)


def _read_path(call: dict) -> str | None:
    """The file a call reads, if it is a read: an explore tool's path argument, or the first
    path-like token after a reading program in a shell command."""
    name, arguments = call.get("tool") or "", call.get("arguments") or {}
    phase, _, is_shell = classify_call(name, arguments)
    if not is_shell:
        if phase != "explore":
            return None
        return next((arguments[k] for k in PATH_ARGS if isinstance(arguments.get(k), str)), None)
    command = str(arguments.get("command") or arguments.get("cmd") or "").strip().splitlines()
    tokens = PREFIX.sub("", command[0]).split() if command else []
    if not tokens or tokens[0].rsplit("/", 1)[-1] not in READ_PROGRAMS:
        return None
    return next((t for t in tokens[1:] if not t.startswith("-") and ("/" in t or "." in t)), None)


def compaction_effects(data: dict, calls: list[dict]) -> dict:
    """What followed each compaction boundary the trajectory records (schema in the module
    docstring). A trajectory without a `compactions` list reports a count of 0."""
    events = sorted((c for c in data.get("compactions") or []
                     if isinstance(c, dict) and isinstance(c.get("boundary"), int)), key=lambda c: c["boundary"])
    by_trigger: dict[str, int] = {}
    by_model: dict[str, int] = {}  # which model wrote each summary; "unrecorded" before rusty item 7
    repeated = errors = rereads = 0
    before_tokens, after_tokens = [], []
    for n, event in enumerate(events):
        trigger = str(event.get("trigger") or "unknown")
        by_trigger[trigger] = by_trigger.get(trigger, 0) + 1
        model = str(event.get("model") or "unrecorded")
        by_model[model] = by_model.get(model, 0) + 1
        boundary = event["boundary"]
        resumes = event.get("resumes_at")
        resumes = resumes if isinstance(resumes, int) and resumes > boundary else boundary + 1
        end = events[n + 1]["boundary"] if n + 1 < len(events) else None  # the window stops at the next boundary
        earlier = [c for c in calls if c["at"] < resumes]  # the kept span ran before the compaction
        later = [c for c in calls if c["at"] >= resumes and (end is None or c["at"] < end)][:COMPACTION_WINDOW]
        seen = {_signature(c) for c in earlier}
        read = {p for p in map(_read_path, earlier) if p}
        repeated += sum(1 for c in later if _signature(c) in seen)
        errors += bool(later) and bool(later[0].get("failed"))
        rereads += sum(1 for c in later if (p := _read_path(c)) and p in read)
        for key, bucket in (("tokens_before", before_tokens), ("tokens_after", after_tokens)):
            if isinstance(event.get(key), (int, float)) and not isinstance(event.get(key), bool):
                bucket.append(event[key])

    def mean(values):
        return round(sum(values) / len(values), 1) if values else None

    return {"count": len(events), "by_trigger": by_trigger, "by_model": by_model, "repeated_after": repeated,
            "first_step_errors": errors, "rereads_after": rereads,
            "tokens_before_mean": mean(before_tokens), "tokens_after_mean": mean(after_tokens)}


def offered_tools(data: dict) -> set[str] | None:
    """The tool names a trajectory says the model was offered, or None when it lists none."""
    tools = data.get("tools")
    if not isinstance(tools, list):
        return None
    names = {str((t.get("function") or {}).get("name") or t.get("name") or "") for t in tools if isinstance(t, dict)}
    return {n for n in names if n} or None


def summarize(calls: list[dict], *, offered: set[str] | None = None) -> dict:
    """The summary for an ordered list of `{"tool", "arguments"}` calls (schema in the module docstring)."""
    phases, edited, repatches, shell, outside = {p: 0 for p in PHASES}, set(), 0, 0, 0
    last_outside = False
    for c in calls:
        phase, path, is_shell = classify_call(c.get("tool") or "", c.get("arguments") or {})
        phases[phase] += 1
        shell += is_shell
        if path is not None:
            repatches += path in edited
            edited.add(path)
        last_outside = bool(c.get("refused")) or (offered is not None and (c.get("tool") or "") not in offered)
        outside += last_outside
    return {"calls": len(calls), "phases": phases, "shell_calls": shell, "edits": phases["edit"],
            "repatches": repatches, "out_of_interface_calls": outside, "ended_on_out_of_interface": last_outside}


def trajectory_files(trial_dir: Path) -> list[Path]:
    """The trajectory files of one trial: the gated rounds in order, else the harness's single file."""
    agent = trial_dir / "agent"
    rounds = sorted((p for p in agent.glob("mini-swe-agent.round*.trajectory.json") if ROUND_FILE.match(p.name)),
                    key=lambda p: int(ROUND_FILE.match(p.name).group(1)))
    return rounds or [agent / name for name in TRAJECTORY_FILES if (agent / name).is_file()]


def summary_for_trial(trial_dir: Path) -> dict | None:
    """The summary over the trial's trajectory files, or None when none exists or one is unreadable."""
    files = trajectory_files(trial_dir)
    if not files:
        return None
    calls, offered, effects = [], None, []
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        file_calls = tool_calls(data)
        calls.extend(file_calls)
        effects.append(compaction_effects(data, file_calls))  # boundaries index this file's messages
        offered = offered_tools(data) if offered is None else offered | (offered_tools(data) or set())
    return {**summarize(calls, offered=offered), "compactions": _merge_effects(effects),
            "files": [str(p.relative_to(trial_dir)) for p in files]}


def _merge_effects(effects: list[dict]) -> dict:
    """One `compactions` record over several trajectory files (the gated track's rounds)."""
    out = {"count": 0, "by_trigger": {}, "by_model": {}, "repeated_after": 0, "first_step_errors": 0, "rereads_after": 0}
    before, after = [], []
    for e in effects:
        for key in ("count", "repeated_after", "first_step_errors", "rereads_after"):
            out[key] += e[key]
        for key in ("by_trigger", "by_model"):
            for k, n in (e.get(key) or {}).items():
                out[key][k] = out[key].get(k, 0) + n
        for key, bucket in (("tokens_before_mean", before), ("tokens_after_mean", after)):
            if e.get(key) is not None:
                bucket.extend([e[key]] * e["count"])
    out["tokens_before_mean"] = round(sum(before) / len(before), 1) if before else None
    out["tokens_after_mean"] = round(sum(after) / len(after), 1) if after else None
    return out
