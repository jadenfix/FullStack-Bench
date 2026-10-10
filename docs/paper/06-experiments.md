# 6. Experiments

## 6.1 Protocol (Lane A)

Outline for the executed section; every value from `experiments/reporting-v1.json` and the
runner's ledger, none from memory.

- The frozen pins: evaluator commit, Rusty commit and binary digest, mini-swe-agent version
  and config digest, model id and revision, inference settings, base image IDs.
- The envelope per episode (calls, input and output tokens, wall clock equal to the tasks'
  agent timeout) and the gateway that enforces it identically for every track; Rusty's own
  limits unset so they can never bind first.
- Seven tracks: mini (the standardized comparator), the Rusty 2x2 (verify x careful, memory
  and delegation off, guard on), rusty-shell (the tool-surface ablation) and mini-verify (the
  transfer arm: the same public check gated around mini-swe-agent, with its gate rounds pinned).
- Episodes: tasks x tracks x seeds, blocks per (task, seed) with counterbalanced track order,
  key slots balanced across tracks, two concurrent trials, warm layer cache, and replacement
  at most once per episode for infrastructure failures only.
- What happened: attempts planned, eligible, replaced, invalid, missing
  (`{{report.harnesses.<h>.attempts.*}}`); host restarts and their cost; the preflight and
  per-wave budget checks.
- No trace was inspected by anyone before the analysis was frozen by commit (M5).

## 6.2 RQ1: what whole-episode grading reveals (planned)

Table 5 reports, per track, `{{report.harnesses.<h>.views.final_artifact}}` against
`{{report.harnesses.<h>.safe_success}}` and `{{report.harnesses.<h>.harm.observed_harm}}`,
with unknown counts. Every episode where `final_artifact` passed and SafeSuccess failed is
listed with the failing view and check. The reader can check each listed failure against the
brief's stated requirement; we claim a gap only where the requirement is explicit.

## 6.3 RQ2: fixed verification and careful execution (planned)

Two preregistered primary comparisons on SafeSuccess, each a within-Rusty ablation on shared
(task, seed) slots: rusty-verify against rusty-baseline, and rusty-careful against
rusty-baseline (`{{report.paired}}` per comparison: wins, losses, ties, exact sign test).
Beside outcome, per cell: the three completion events
(`{{report.harnesses.<h>.completion.*}}`: proposed, accepted, accepted-and-independent,
accepted-without-success, success-without-acceptance) and the public-check outcomes, so a
gate that rejects the same wrong repair is read as a runtime improvement and not as better
engineering; and cost (`{{report.harnesses.<h>.budget.*}}`: calls, tokens, exhaustion), so a
gain bought with budget is visible.

Preregistered but not primary: the transfer comparison mini-verify against mini. If the gate
improves both harnesses, the mechanism is general; if it improves only Rusty, the effect is
Rusty's. Exploratory: combined against verify and against careful (interaction), shell against
baseline (Rusty's runtime without its tool surface), and rusty-baseline against mini (the two
complete systems as configured, which answers only which tested configuration did better).

Careful execution is varied as a whole mode. No benefit is attributed to one of its parts in
this paper.

## 6.4 RQ3: failure mechanisms (planned)

For every eligible solver failure, the earliest failed stage from the per-check record and
the incident evidence, with the first independently observed violated invariant. A
qualitative table by mechanism (retry identity, cancellation ownership, index validity,
propagation delay, deployment without drain), not a rate. Failures dominated by budget,
infrastructure or invalid evidence are reported as that and limit the claim.

## 6.5 RQ4: generalization (planned)

The two reporting-side tasks were built after the harness pin and never inspected by the
Rusty session. On them the RQ2 effect is reported by direction only. The claim stays
*planned* in the claim table until a private held-out store holds tasks that were never on
the public main branch.

## 6.6 Statistics

Every proportion carries its denominator and a 95% Wilson interval (`fsbench/report.py`).
Paired comparisons use an exact sign test on discordant (task, seed) slots and are descriptive:
seeds within a task are not exchangeable, and the number of tasks is small. The cluster
bootstrap in `fsbench/analysis.py` is reported beside the sign test with its cluster count
and is not the headline. No clustered model is fitted below ten tasks. More seeds on the same
four templates do not create more independent engineering problems: uncertainty over repeated
runs on this fixed cohort is what we report, and uncertainty about a larger task population is
what we do not.

We report repeated safe completion per (task, track) across seeds, not whether one attempt
eventually worked. Selecting a passing attempt with the hidden grader would not be a deployable
agent and is not done.
