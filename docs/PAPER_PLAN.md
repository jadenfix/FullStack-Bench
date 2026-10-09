# Plan to the first paper

Working title: "Beyond Final-State Correctness: Evaluating Live-System Changes by Coding Agents"
(`docs/PLAN.md` holds the design; `docs/PAPER.md` the skeleton; this file the schedule). Three
sessions share the work: Lane A (adapter, gateway, runner, every paid run and every Docker
gate), Lane B (admission, harm measurement, tasks, report, writing), Lane C (Rusty). Status
of each item is updated in place; the owner of an item changes its status.

## Definition of done

The paper is ready to write up when all of the following hold:

1. At least three tasks at reporting level: executed qualification receipt (oracle 10x, no-op
   3x, every wrong solution, an independent second solution) and an executed isolation
   receipt, both bound to the task digest and base image IDs of the evaluator commit.
2. One reporting cohort executed under a frozen manifest through `fsbench/admission.py`, with
   every attempt a terminal record, and `docs/results/reporting/report.json` rendered from
   those records by `scripts/report_cohort.py`.
3. Every number in `docs/PAPER.md` filled from that report, with its claim label.
4. Each item of the external review verdict mapped to the section that answers it.
5. The adaptation table, the fidelity table and the workload-identity table filled from
   evidence, not declarations.

## Milestones

| # | Milestone | Owner | Needs | Produces | Estimate |
|---|---|---|---|---|---|
| M0 | Freeze the pins | B | nothing | evaluator commit (FSB main after the runner merges), Rusty 32cac02, mini-SWE 2.4.6 and its config digest, model id and inference settings, base image IDs | done except the evaluator commit |
| M1 | Qualify three tasks | A | the host after pilot-2; bases rebuilt from the evaluator commit | receipts for ship-checkout-v2, stop-double-charges (independent + isolation) and merge-duplicate-contacts (full gate + independent + isolation), committed under `receipts/<task>/` | about one day of host time |
| M2 | Merge the runner | A, then B review | its end-to-end rerun on the host; the owner's go-ahead | `fsbench/runner.py` on main; `docs/results/development/report.md` rendered from pilot-2 (appendix only) | after M1's host slot |
| M3 | Design the reporting cohort | B drafts, A validates, owner approves the envelope | M0, M1 | `experiments/reporting-v1.json`: 3 tasks x 6 tracks x 3 seeds = 54 episodes, replacement at most once per episode, preregistered comparisons (verify vs baseline, careful vs baseline on SafeSuccess as primary; the rest exploratory), key slots balanced, guard on in every cell | one day |
| M4 | Execute the reporting cohort | A | M2, M3, the envelope | `runs/reporting-v1/` ledger, plan, manifest; no trace inspected by anyone before M5 freezes | about 15 h host time at two concurrent trials, plus replacements |
| M5 | Analyse | B | M4 | `docs/results/reporting/`; the failure-mechanism table (RQ3) from CTRF and incident evidence; fidelity and workload tables filled; the analysis frozen by commit before any trace is read | two days |
| M6 | Second unseen task (optional, parallel to M1-M4) | B builds, A gates | a design from `fsbench/hard_suite_incidents.yaml` not seen by C | a fifth task at reporting level, giving RQ4 two held-out tasks instead of one | three days B, half a day A |
| M7 | Write (C: about one session, after M5, citing only Rusty docs, commits and development evidence) | B (sections 1-5, 6.2-6.6, 7, 8), A (6.1 protocol, appendix B receipts), C (Rusty description, safety assumptions, adaptation table, mechanism-transfer condition) | M5 | `docs/paper/` source; every number a report key | one week, overlapping M4-M5 for the method sections |
| M8 | Internal review against the verdict | B runs it, A and C answer their items | M7 | a checklist in this file, every reviewer item marked answered or scoped out | two days |

## Tracks in the reporting cohort

| Track | Harness | Pins | Role |
|---|---|---|---|
| mini | mini-swe-agent 2.4.6, `mswea-compact.yaml` | config digest | standardized comparator |
| rusty-baseline | Rusty 32cac02, standard execution, no verify | binary sha256, guard on, memory off, agents off | reference cell |
| rusty-verify | + `--verify public-check --verify-timeout 120` | same | mechanism: fixed verification |
| rusty-careful | + `--mode careful` | same | mechanism: careful execution |
| rusty-combined | both | same | interaction |
| rusty-shell | `--toolset shell`, standard, no verify (`rusty --yolo --memory off --agents off --mode standard --toolset shell --stats --trajectory <path> --goal <brief>`) | same | mechanism transfer: Rusty's runtime without its tool surface |

The shell cell drops the file, search and outline tools, background bash, plan, memory, task/swarm
and every MCP tool, keeping bash and goal control; it reaches SimCloud only through the `sc` CLI
and the REST API that every task image ships. Before M3 Lane B checks, per task, that every
operation the reference solution needs is reachable that way (the three qualified tasks'
references use `sc`, `psql` and REST, not MCP); a failure traced to an MCP-only operation is a
coverage limitation of the shell interface, not a solver failure, and the transfer section says so.
Without `--verify` the shell cell exits 0 or 1 only; its claims record still exists because
`--goal` is kept.

Every cell: the same model and inference settings, the same per-episode budget (calls, tokens,
wall clock), the same key rotation. The guard is on in every cell (`docs/PLAN.md`, decisions of
2026-10-09); retire-node-2 is excluded until a cgroup v2 host exists and its reference's
Destructive action is handled as a separate condition.

## Budget

The development pilot spent, per Lane A's ledger, on the order of 25 to 75 admitted calls per
episode. For 54 episodes with at most one replacement each:

| Quantity | Value |
|---|---|
| Episodes planned | 54 |
| Attempts at most | 108 |
| Per-episode call cap (manifest envelope) | 250 |
| Expected spend | 1,500 to 4,000 admitted calls |
| Proposed envelope to approve | 8,000 admitted calls, hard stop by the runner |

The owner approves the envelope before M4; no reporting attempt starts without it. The pilot's
remaining approval (5,000 minus its spend) is not reused for reporting.

## Statistics and claims

With three tasks the paired comparisons are descriptive: exact sign tests on discordant
slots and Wilson intervals on proportions, both from `fsbench/report.py`; the cluster
bootstrap in `fsbench/analysis.py` is reported beside them with its cluster count. RQ1 to RQ3
are answered on the reporting cohort with the label *executed*. RQ4 is answered by direction on
the held-out task(s) only and stays *planned* in the claim table until a second held-out task
exists (M6).

## Decisions the owner holds

| Decision | Needed by | Default if silent |
|---|---|---|
| The reporting envelope (calls) | M4 | none: M4 does not start |
| Whether to build the second unseen task (M6) | now | B starts it; it is dropped if M4 finishes first |
| Venue and format of the paper source | M7 | Markdown under `docs/paper/`, converted at submission |
| A cgroup v2 host for retire-node-2 | optional | retire-node-2 stays out of the cohort |
| Marking the sandbox tokens as test credentials in GitGuardian | cosmetic | the check stays red on task PRs |

## Status

| Item | State | Note |
|---|---|---|
| M0 | pins frozen except the evaluator commit; Lane C commits to no change in claims, safety, verify, exit codes, capabilities or mcp-check output on the pin until M5, and a restart on a new pin if a bug forces one | the runner is on main (8b322b6); the evaluator commit is main at the moment M4's manifest is written |
| M1 | queued on Lane A's host | after pilot-2 |
| M2 | runner merged as 8b322b6 (PR #9) | pilot-2b runs on it; the development report renders when its ledger is pushed |
| M3 | not started | Lane A's PR #14 pins Rusty's toolset for the shell cell and records --mcp-check; B drafts the manifest once M1's receipts exist |
| M4 | blocked on the envelope | |
| M5 | not started | |
| M6 | second unseen task built and verified in-process: stop-report-connection-leak (PR #15); Docker gate and independent solution queued with Lane A | |
| M7 | skeleton exists (`docs/PAPER.md`) | |
| M8 | not started | |
