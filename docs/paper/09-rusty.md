# 9. Rusty

Rusty is the companion harness of this study: a terminal coding agent, one Rust binary that
drives a tool-calling model in a loop over a working copy. This section describes what the
reporting cohort ran, what Rusty's safeguards assume, which of its changes were made after
reading benchmark traces, and the two conditions built around it. It cites only Rusty's source,
documentation and commits. It reports no result: results are in section 6. Files named here are
in the Rusty repository at the reporting pin unless stated otherwise.

## 9.1 What the cohort ran

Every Rusty track runs the static build of commit `32cac02` (`rusty 0.1.0`). Its receipt,
`docs/receipts/fsb-reporting-v1.md` (added to Rusty's main branch after the pin, describing the
pin), records the build, the toolchain and the `--capabilities` output (Appendix B). The harness
contract in Rusty's `docs/VERIFICATION.md` lists the flags, exit statuses and output records a
benchmark may rely on. `rusty --capabilities` (`src/main.rs`) reports the contract's version and
every value the build accepts, and the adapter (`fsbench/agents/rusty.py`) refuses a track whose
pinned values the binary does not list, before any model call.

The adapter runs `rusty --yolo --stats --agents off --trajectory <path> --goal <instruction>`,
adding `--verify <public check> --verify-timeout <seconds>` on the verify tracks. The rest is
pinned through the environment: the execution mode (`RUSTY_MODE`), memory off (`RUSTY_MEMORY`), the
tool surface (`RUSTY_TOOLSET`), the goal's turn limit (`RUSTY_GOAL_MAX_TURNS`) and the model and
keys. The context window was not pinned: with `RUSTY_CONTEXT_TOKENS` unset, the build used its own
assumed window (Appendix B.4). The adapter now pins it explicitly, for builds after the pin. So
long-term memory, subagents and swarms are off, and the only MCP server is the one the task
environment declares. At the pin, `--memory off` still offered the memory tools (`remember`,
`recall`, `forget`), each answering that memory is off; no cohort attempt called one (the published
trajectories), and later builds no longer offer them (Rusty #92). `fsbench/experiment.py` refuses a
Rusty track that pins memory or delegation on.

## 9.2 The runtime under test

- **Agent loop** (`src/agent.rs`). A goal runs until the model proposes completion or the turn
  limit is reached. Each turn is bounded by `MAX_STEPS` tool steps. A stalled turn ends that
  turn, not the goal, but three stalled turns in a row end the goal: at the pin with an error
  exit and the goal still open, the stall guard behind most of the cohort's harness stops
  (section 6.4). An identical read result is not sent twice in a row, and a run of identical
  calls first warns, then stops the turn.
- **Tools.** Shell, file and search tools are in `src/tools.rs`; their output is cut in the
  middle above `MAX_OUTPUT`. Owned background commands (`src/activity.rs`) are cancelled at
  turn end and never replayed. MCP tools come from stdio servers (`src/mcp.rs`): tool lists are
  read page by page, names are kept distinct, and each tool's permission class follows its real
  name and annotations. Secrets are redacted before the model sees them but stay usable by name
  (`src/privacy.rs`).
- **Context** (`src/context.rs`, `Agent::manage_context`). Old bulky tool output is cut to a
  stub past 60% of a history budget, and older turns are summarised past 80%. At the pin both
  thresholds are measured with a character estimate against the window the build assumes
  (Appendix B.4); nothing fetches
  a cut output back except running the tool again.
- **Permission classes** (`src/permissions.rs`). Every call is classed read-only, workspace
  write, risky or destructive, from the command itself (including SQL and cloud command lines)
  or from an MCP tool's name and annotations.
- **Completion gate** (`src/verification.rs`, `src/agent.rs`). The model proposes completion
  with `goal_done`. With `--verify`, the runtime runs the fixed check and accepts the claim only
  on a pass whose inputs did not change while the check ran. The `claims` record keeps every
  proposal, acceptance and refusal by cause, so the runtime's acceptance can be set against the
  independent grader's verdict (the completion events of section 6).
- **Accounting** (`src/budget.rs`, `src/llm.rs`). One ledger per process covers every model call;
  `model_budget` reports attempts, answered attempts and transport errors, token reservations and
  known usage, time spent waiting on provider retries, and calls by role. The benchmark gateway's
  receipt, not Rusty's ledger, is the episode budget of record (section 6.1).
- **Careful mode** (`src/execution.rs`, `src/infra.rs`) changes several things at once: the
  instructions; the output and reasoning budgets; one read-only review of the first completion
  claim, a further model call on the same budget; and for kubectl, helm and terraform, a dry-run
  gate and a post-change health check. Its effect is attributed to the mode as a whole, not to
  any one part.

## 9.3 Safety assumptions

Rusty's safeguards guard an agent; they are not transactions. A transactional system such as
STRATUS (Chen et al., 2025b) assumes writer exclusivity and a faithful undo for each action. Rusty
assumes neither, and claims less.

- **Refused.** Rusty refuses a call a deny rule names, any change in read-only mode, and a
  destructive call nobody is present to approve. The destructive class (`src/permissions.rs`;
  the full list is the test `destructive_things_always_need_a_person`) covers force pushes and
  history rewrites; deletion of the workspace, the home directory or the root; cloud deletes;
  `kubectl drain` and `kubectl delete namespace`; `helm uninstall`, `terraform destroy` and
  `terraform state rm`; SQL `DROP`, `TRUNCATE`, and `DELETE` or `UPDATE` without `WHERE`;
  volume and database resets; and MCP tools whose name or annotation says they delete.
- **The unattended guard is on in every Rusty track.** The track table of section 6.1 pins
  `allow_destructive` false. A correct solution can be destructive by this classification
  (draining a node needs `kubectl drain`), and with the guard on an unattended run cannot
  perform it, which would measure the guard rather than the treatment. `fsbench/experiment.py`
  therefore refuses a guard-on track on any task whose reference solution needs a
  destructive-classed action, and no task in this cohort does. Rusty's `RUSTY_ALLOW_DESTRUCTIVE`
  turns the guard off only as a separately pinned treatment; this cohort does not use it.
- **Made recoverable where possible.** Before a mutating kubectl, helm or terraform command,
  `src/infra.rs` snapshots what the command touches and prints a rollback. A concurrent writer
  can make the snapshot stale, and restoring it is a new action, not an undo.
- **What Rusty records.** The `safety` record (`src/agent.rs`) counts each consequential call as
  proposed, executed or refused, with the refusal's cause: rule, person, unattended, or dry-run
  gate. Whether the environment would have refused a call, and whether it caused harm, are
  measured by the benchmark's observers (section 3), not by Rusty.
- **What the fixed check is and is not.** `--verify` runs a command the benchmark chose, in a
  workspace the agent can write. Its input fingerprint catches only changes made while the check
  runs. It says nothing about deployed state, and the agent can change what the check reads
  before proposing completion. An independent grader outside the workspace is still required,
  and section 6 reads the two separately.
- **Trust boundary.** The guard runs inside the agent process, as the same user, on the same
  host. Cancelling a command's process group is cleanup, not isolation. The trajectory and audit
  log are the agent's own record: they show what Rusty observed, not what happened.

## 9.4 Adaptation

Section 7 tracks three sources of adaptation. This section is the harness source: Rusty changes
made after its developers read FullStack-Bench traces. Table 9 lists every such change; it
mirrors "Benchmark-motivated changes" in Rusty's `docs/VERIFICATION.md` at the pin, and each
motivating trace is the one its commit message describes. All of them predate the pin.

**Table 9. Harness adaptation before the pin.**

| PR | Commit | Change | Motivating trace (per the commit message) |
|---|---|---|---|
| #32 | `d3c7149` | Opt-in for unattended destructive calls | `retire-node-2`'s solution drains a node |
| #35 | `a3fc710` | Stop redacting namespaced names and doc placeholders | A promotion task's required grant name was hidden from the model |
| #35 | `6be4eb4` | Explain how to use a hidden value | A database task re-fetched production credentials repeatedly |
| #35 | `8e56497` | Retry streams dropped mid-reasoning | Dropped streams ended runs |
| #35 | `d535143` | A stall ends the turn, not the goal | A stall discarded most of a goal's turns |
| #37 | `e84aab2` | Stdio MCP client | SimCloud features exist only over MCP |
| #41 | `97aae34` | Report the budget left | A run spent its whole budget with the goal open |
| #41 | `bb82eff` | Check every stated requirement before closing | Retries made idempotent, duplicates never refunded |
| #50 | `36aad47` | Resolve mistyped MCP names | A mistyped SimCloud tool name |
| #51 | `95a3513` | Use a redacted secret by name | `stop-double-charges`: a credential-fetching loop |
| #51 | `32445a9` | Point MCP names run in bash to the tool | Same trial: MCP tool names run as shell commands |
| #51 | `5608f55` | Treat a secret tool's `value` field as secret | Same trial: a live key reached the model |
| #52 | `630dd62` | Don't resend an identical read result | `ship-checkout-v2`: one read repeated |

**Lineage.** The motivating traces come from `ship-checkout-v2` and `stop-double-charges`, the
two tasks section 5 marks as the development side of the split, and from `retire-node-2`, which
is not in this cohort. Their trials, and the families they belong to, are development evidence
for every later Rusty version: the two development tasks support RQ1 to RQ3 with this adaptation
stated, and not RQ4 (section 7).

**What is not adaptation.** Changes after Table 9 and up to the pin came from a code audit of the
MCP client, Rusty's own evaluation runs, gateway accounting and the study protocol (the `claims`
and `safety` records, `--capabilities`, the tool surfaces and the harness contract). None was made
from a task trace. Rusty's `docs/VERIFICATION.md` keeps the list current for every later change
of either kind.

## 9.5 Conditions built around Rusty

**Tool-surface ablation (rusty-shell).** The same binary and runtime with `RUSTY_TOOLSET=shell`,
standard mode and no `--verify`. The model is offered `bash`, `goal_done` and `loop_next` only
(`SHELL_TOOLS` in `src/agent.rs`), and any other tool name is refused before it runs. The condition
removes the file, search and outline tools; owned background commands; the plan and memory tools
and subagents; and every MCP tool. The agent loop, permission classes, guard, accounting and the
`claims` and `safety` records are unchanged, so a difference from rusty-baseline comes from the
tool surface. A service reachable only over SimCloud's MCP server, with no command-line equivalent,
is a coverage limitation of this condition rather than a solver failure, and admission records it
from the pinned toolset (`coverage_limited` in the report). The condition follows the
action-space ablation of Fan et al. (2026), which fixes a harness's execution loop and swaps
its predefined tools for a bare shell; no direction is preregistered for it (section 6.1).

**Mechanism transfer (mini-verify).** `fsbench/agents/gated_mini.py` gives mini-swe-agent the
gate Rusty's `--verify` gives Rusty: when mini-swe-agent submits, the same public check runs in
the task container as the agent user, and on a failure mini-swe-agent is started again with the
check's exit status, its output and the original task, the container keeping every change. Two
differences from Rusty's gate are recorded in each trial's metadata (`gate_context`): each round
starts mini-swe-agent with a fresh context, where Rusty continues the same conversation, and the
number of rounds is capped (section 6.1), where Rusty is capped by its turn limit. Both run under
the same gateway envelope. Completion events use the adapter-wide keys for every track, so a
change in the gated track's completion events can be read against Rusty's. The comparison asks
whether fixed verification helps on another harness, not whether Rusty is better.

## 9.6 After the pin

Rusty has changed since the pin, and none of those changes is in this cohort. They include a
context meter calibrated on the provider's reported token counts, window-proportional budgets and
a history cap, a model-initiated compaction mode with harness-side refusals, per-compaction
statistics in the trajectory and a separate summariser model; a stalled goal that exits 2 with
its end recorded as `goal_end` rather than with an error (harness contract 2, Rusty #91); and no
memory tools offered with memory off (Rusty #92). Each is recorded
in Rusty's `docs/VERIFICATION.md` as a change after the reporting pin, with the flags a harness
must pin to keep a later run comparable. A cohort on a later build is a new cohort with its own
receipt, not an extension of this one.

The cohort gives the compaction changes no evidence either way: no attempt on any track crossed
a compaction boundary (section 6.3), so the pinned build never compacted within the episode
envelope on these tasks.

**Development evidence on memory.** Memory is off in every track, and what follows comes from
Rusty's own evaluation tasks, not from this cohort; it is recorded in Rusty's `docs/MEMORY.md`. Two
tasks seed a lesson into memory that holds a value the task's tests need and that the working copy
does not contain. On a build after the pin (`14fba44`; nemotron-3-super, five repeats per level per
task), no run used the seed: the lesson was added to the system prompt of one request only, and the
model first looked for the value several requests later. On `ae99853`, where a lesson stays in the
prompt for the rest of its turn, with each session isolated from other runs' files and a longer
time limit, every `learn` run passed (ten of ten) and no memory-off run did (none of eight scored,
two invalid). The trajectories separate the delivery change from the other two: the lesson was
offered with the first request and first used at request 5 to 7, the passing runs ended within 21
seconds, and isolation only removes places to find the value. This covers only whether a delivered
lesson is used. It does not show that memory captures a lesson by itself, that it helps on other
tasks, or that it holds for other models; the lesson text was written with the task, not by the
model under test.
