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
| M0 | Freeze the pins | B, A | the adapter PR (#14: Rusty 32cac02 pin, `toolset`, `--mcp-check`, one-envelope preflight) and the fifth task (#15) merged | evaluator commit = FSB main after #14 and #15 merge; Rusty 32cac02; mini-SWE 2.4.6 and its config digest; model id and inference settings; base image IDs. Any later change under `tasks/`, `simcloud/`, the adapters or the verifier forces a rebuild and a regate before M4 | after #14's review findings are fixed |
| M1 | Qualify four tasks | A | the host after pilot-2b (ends about 04:00-07:00 UTC Oct 10); bases rebuilt from the evaluator commit | the full gate for all four tasks on the new bases (oracle 10x, nop 3x, every wrong solution, the independent solution, verifier 3x) plus the executed isolation receipt from `scripts/isolation_probe.py`, committed under `receipts/<task>/`; `assess_qualification` requires every gate of a task to share one base-image set, so the older tasks' earlier receipts cannot be combined with new ones | about 0.75 of a host day: base rebuild 30-60 min, about 18 trials per task at two in parallel and 5-8 min each (4-5 h), the probe minutes per task, plus retries |
| M2 | Merge the runner | A, then B review | done: e2e rerun 8/8 on 6f122c6, merged as 8b322b6 on the owner's go-ahead | `fsbench/runner.py` on main; `docs/results/development/report.md` rendered from pilot-2b (appendix only) when its ledger is pushed | done |
| M3 | Design the reporting cohort | B drafts, A validates (dry run, preflight output, key balance), owner approves the envelope | M0, M1, pilot-2b's spend table | `experiments/reporting-v1.json`: 4 tasks x 7 tracks x 3 seeds = 84 episodes (63 if a new task misses its gate), replacement at most once per episode, preregistered comparisons (verify vs baseline, careful vs baseline on SafeSuccess as primary; mini-verify vs mini as the transfer comparison; the rest exploratory), key slots balanced, guard on in every cell | one day |
| M4 | Execute the reporting cohort | A | M2, M3, the envelope | `runs/reporting-v1/` ledger, plan, manifest; no trace inspected by anyone before M5 freezes | 16-27 h per 54 episodes at two concurrent trials (measured: 18-58 min per episode, about 32 min per wave, throttling dominating), pro rata for more, plus replacements; revised from pilot-2b |
| M5 | Analyse | B | M4 | `docs/results/reporting/`; the failure-mechanism table (RQ3) from CTRF and incident evidence; fidelity and workload tables filled; the analysis frozen by commit before any trace is read | two days |
| M6 | Second unseen task | B built, A gates | done on B's side: stop-report-connection-leak (PR #15) with its independent solution, verified in-process | a fifth task at reporting level, giving RQ4 two held-out tasks instead of one | gate inside M1 |
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
| rusty-shell | `--toolset shell`, standard, no verify (`rusty --yolo --memory off --agents off --mode standard --toolset shell --stats --trajectory <path> --goal <brief>`) | same | tool-surface ablation: Rusty's runtime without its tool surface |
| mini-verify | mini-swe-agent 2.4.6 wrapped by `fsbench/agents/gated_mini.py`: the same fixed public-check gate around mini-SWE, `verify_timeout` pinned | config digest, gate rounds | mechanism transfer: fixed verification carried to another harness |

The shell cell is a tool-surface ablation, not the transfer arm: the transfer arm is mini-verify, which
carries the fixed-verification mechanism to mini-SWE through the same public check. The shell
cell drops the file, search and outline tools, background bash, plan, memory, task/swarm
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

Measured on the pilot-2 smoke (ship-checkout-v2, seed 0, one attempt each, Lane A's ledger):

| Track | Admitted calls | Forwarded (429 share) | Input tokens | Output tokens | Wall |
|---|---|---|---|---|---|
| rusty-baseline | 72 | 158 (54%) | 2.21M | 33.6k | 18.3 min |
| mini | 209 | 1182 (82%) | 6.91M | 49.3k | 58.4 min |
| rusty-careful | 96 | 181 (47%) | 1.84M | 73.8k | 21.5 min |
| rusty-verify | 110 | 201 (45%) | 3.62M | 92.1k | 38.9 min |
| rusty-combined | 92 | 162 (43%) | 3.70M | 78.5k | 21.6 min |

Mean 116 admitted calls per episode, range 72-209. Caveat: the four Rusty arms ran under a hidden
4M-token cap (Rusty's defaults, triggered by `max_requests`), and two hit it; unconstrained Rusty
spends more. pilot-2b (15 episodes, corrected manifest, no Rusty-side limits) is the distribution
the cap and envelope are set from: admitted calls mean 132, median 118, max 240 (mini, exhausted
at the cap); input tokens 1.2M to 11.7M (rusty-careful exhausted the 12M envelope once); output
48k to 187k; wall mean 20 min, max 48 min; 2,108 calls admitted in total, 123 of them lost to two
host restarts. Approved by the owner on 2026-10-11: a cap of 300 calls per episode with the 12M
input and 500k output token envelope kept; at 84 episodes the expected spend is about 11,000
calls (132 per episode) and the preflight's one-attempt reservation is 25,200, plus 5-10% for
restarts. The draft manifest carries the approved cap.

Implications: (a) the per-episode cap is part of every treatment, since exhaustion is scored as a
failure; it is set above pilot-2b's observed maximum with margin, identical for every track;
(b) at a mean near 120 calls, 84 episodes spend about 10,000 calls before replacements, 63 about
7,500; (c) the runner's preflight reserves one envelope per remaining episode and the per-wave
check is the hard stop, so the approved envelope is the expected spend plus replacements, not the
worst case. The envelope above is the approval M4 runs under; no reporting attempt starts
outside it. The pilot's remaining approval is not reused for reporting.

## Statistics and claims

With three tasks the paired comparisons are descriptive: exact sign tests on discordant
slots and Wilson intervals on proportions, both from `fsbench/report.py`; the cluster
bootstrap in `fsbench/analysis.py` is reported beside them with its cluster count. RQ1 to RQ3
are answered on the reporting cohort with the label *executed*. RQ4 is answered by direction on
the held-out task(s) only and stays *planned* in the claim table until a second held-out task
exists (M6).

## Review checklist (M8)

The external review verdict (2026-10-09, fourteen items and a requirements table) mapped to
the section that answers it. *Answered* means the section states what the code and evidence
do; *needs evidence* means the section is written and waits on an executed artifact;
*scoped out* means the paper says so explicitly. Owners answer their items at M8.

| # | Reviewer item | Where answered | State | Owner |
|---|---|---|---|---|
| 1 | Distinct name and narrowed novelty claim | title; 1 (contribution list); 8 (FullStack-Agent paragraph) | answered | B |
| 2 | Engage the closest literature substantively | 8, one paragraph per cluster; every citation `[verify]` until checked | needs verification pass | B |
| 3 | What the safety claim covers: recovery vs never causing a prohibited consequence; SafeSuccess(tau); eligibility separate; durable-state checks from histories | 2 (all four subsections); 4 (qualification cases) | answered | B |
| 4 | Difficulty: long and conjunctive vs causally coupled; compositionality diagnostics; no all-fail collection | 5 ("What they do not establish") | partly: coupling stated per task; component/chained/coupled diagnostics are development evidence, not reported | B |
| 5 | Construct validity: fictional platform; fidelity statement; interface transfer; native components; an unfamiliar implementation | 7 (first paragraph, Table 3); the shell track as the one executed interface variation | partly: fidelity table filled; transfer and native checks scoped out; the independently authored application scoped out | B |
| 6 | Validate the validator: both directions; materially different solution; targeted and held-out incorrect variants; blind spec review; reviewed solver submissions; model families are not independence | 4 (qualification table); 7 (grader validity) | partly: executed controls at M1; held-out mutants and reviewed submissions named as the open threat | B, A (receipts) |
| 7 | Did the mechanism help or did Rusty get a different experiment: whole-system vs within-Rusty vs transfer; operational comparator; matched budget and cost curves | 6.3; 7 (operational comparator) | answered by design; needs M4 | B, A |
| 8 | Can the live traffic detect the failures: open vs closed; accounting identity; monitor-breaking qualification; observer not the load | 3 (coverage, workload identity); Table 4 workload column | partly: observer-only tasks named as a limitation; monitor-breaking cases in qualification; no customer load generator on the cohort tasks | B |
| 9 | Reliable statistically: ten runs are a gate; pass^k; paired, clustered, preregistered primaries | 4 (qualification counts); 6.6 | answered | B |
| 10 | Customized to Rusty: three adaptation sources; lineage; funnel; neutral question | 7 (adaptation); 5 (split column); 9 (Lane C's adaptation table) | answered; Lane C's table at M7 | B, C |
| 11 | Safety, security or operator restriction: four observations apart; privilege-matched baselines; trust boundary; the audit chain proves integrity not truth | 7 (safety paragraph); 4 (trust boundary); 3 (audit chain) | answered | B |
| 12 | Architecture quality vs maintainability | 5 and 8 (FeatureBench paragraph): practices and style secondary; behavioural follow-on is future work | scoped out | B |
| 13 | The compelling experiment: score the same episodes four ways, review disagreements, then a small intervention | 2 (views), 6.2, 6.3 | answered by design; needs M4 and M5 | B |
| 14 | Acceptance requirements: positioning, episode contract, qualified measurement, meaningful difficulty, controlled intervention, fresh generalization, reproducible release | 1; 4 (lifecycle contract); 4 and 7; 5; 6; 6.5 and 7; 4 (manifest) and 7 (rerun vs regrade) | per row above | all |

Bibliography cleanups the verdict asked for: Terminal-Bench cited at its current release, not
an earlier study presented as current; PROBE cited at the exact version used, not as STING with
later results.

## Decisions the owner holds

| Decision | Needed by | Default if silent |
|---|---|---|
| The reporting envelope (calls) | M4 | approved 2026-10-11: 300 calls per episode, 12M input and 500k output tokens, 7200 s wall |
| Whether to build the second unseen task (M6) | now | B starts it; it is dropped if M4 finishes first |
| Venue and format of the paper source | M7 | Markdown under `docs/paper/`, converted at submission |
| A cgroup v2 host for retire-node-2 | optional | retire-node-2 stays out of the cohort |
| Marking the sandbox tokens as test credentials in GitGuardian | cosmetic | the check stays red on task PRs |

## Status

| Item | State | Note |
|---|---|---|
| M0 | done: the evaluator commit is `55d185f5cc99f4472a305d0f72a2867192881407`, the merge of #29 (the runner holds every attempt to the images its isolation receipt covers). Earlier pins: `6452a07c…` (reopened: submitted code could inherit the operator's token and identity; fixed in #23) and `9e5b094…` (reopened: the image pin is a cache-hit identity and nothing held trials to it; fixed in #29, with #27 taking `fsbench/` out of the images so evaluator-side changes no longer move the bases). Lane C commits to no change in claims, safety, verify, exit codes, capabilities or mcp-check output on Rusty 32cac02 until M5, and a restart on a new pin if a bug forces one | images are pinned by their ordered RootFS layers (`rootfs_identity`), a cache-hit identity the runner checks before every wave and on every attempt; M1 rebuilds every base from this commit and binds receipts to it; the probes are refreshed right before the manifest is frozen for M4; any later change under `tasks/`, `simcloud/`, `simsaas/` or the Dockerfile forces a regate before M4 |
| M1 | rerunning on Lane A's host from the evaluator commit 55d185f: bases, digests, the full gate per task, the isolation probes with their images kept tagged, receipts under `receipts/<task>/` (isolation-m1.json beside isolation.json) | the dry run at 9e5b094 passed every gate and probe on all four tasks (oracle 10, nop 3, independent 1, wrong 3/4/4/4, outcome checks 9/8/10/9); about 1.2 h of host time; no builder prune or uncached build on the host between M1 and M4 |
| M2 | done: runner merged as 8b322b6 (PR #9); pilot-2b complete (15 episodes); `docs/results/development/` rendered from the published records with `--rejudge --records` | spend: mean 132 admitted calls per episode, median 118, max 240; wall mean 20 min, max 48; 123 calls lost to two host restarts |
| M3 | drafted: `experiments/reporting-v1.draft.json` (PR #21) validates and dry-plans 84 episodes with the approved envelope (300 calls); its `pending` block names what Lane A (model revision, wall envelope, runtime, Rusty binary digest and version) and M1 (image IDs, digest re-check) supply | shell-cell reachability checked for all four tasks: every reference and independent solution uses sc, REST or psql, none uses MCP |
| M4 | envelope approved; blocked on M1's receipts and the frozen manifest | cap 300 calls per episode, set from pilot-2b's spend table (max 240) |
| M5 | not started | |
| M6 | second unseen task built and verified in-process: stop-report-connection-leak (PR #15); Docker gate and independent solution queued with Lane A | |
| M7 | method sections drafted under `docs/paper/` (1 to 5, 6.2 to 6.6, 7, 8, appendix stubs; 6.1 outlined for Lane A); Lane C's Rusty sections drafted (about 1,300 words), held until M5 | every number a report key; every citation `[verify]` until checked |
| M8 | checklist written (above): fourteen items mapped; eight answered, four partly, one scoped out, one per row | owners answer their rows after M7 |
