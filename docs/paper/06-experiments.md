# 6. Experiments

## 6.1 Protocol

Every configuration value in this section is read from the frozen manifest
`experiments/reporting-v1.json` (frozen by the merge `a7f08b0`, file sha256
`44e8211175e4463e…`) and the receipts in `receipts/`; Appendix B lists them with their sources.
Counts of what happened are rendered from the runner's ledger.

**Frozen pins.** Verifiers, SimCloud and the evidence collectors come from the evaluator commit
`55d185f`, and every task runs on base images built from it and pinned by layer identity
(Appendix B.2). Rusty is the static build of commit `32cac02` (`rusty 0.1.0`), whose
`--capabilities` output and QA exit status are recorded in Rusty's own receipt for this
configuration, and its context handling is the pinned source's (Appendix B.4); mini-swe-agent is version 2.4.6 with a pinned configuration file. Every
track uses `nvidia/nemotron-3-super-120b-a12b` with temperature 0.2, top_p 1.0 and at most
16384 reply tokens. The endpoint exposes no model revision, so the manifest records it as
unknown.

**Envelope.** Each episode may spend 300 admitted calls, 12,000,000 input tokens,
500,000 output tokens and 7200 s of wall clock, the tasks' agent timeout. The envelope
is enforced the same way for every track by the operator's budget gateway
(`fsbench/budget_proxy.py`), one per key slot, each holding one upstream key: an attempt gets a
throwaway session token on its planned slot, a request that would exceed the envelope or that
changes the pinned inference settings is refused before it is forwarded, a provider rejection
before generation is refunded, and the wall clock runs from the attempt's first request. The
solver's environment carries no other credential (`fsbench/runner.py`). The manifest refuses
any Rusty-side limit (`fsbench/experiment.py`), so Rusty's own counters can never bind before the
gateway. The cohort as a whole is capped at 25,200 admitted calls, one full envelope per
episode: the runner reserves that up front and, before each wave, stops if the wave could pass
the cap.

**Tracks.** Seven tracks: mini (the standardized comparator), the Rusty 2x2 (fixed verification
x careful execution, memory and delegation off, the destructive-step guard on), rusty-shell (the
tool-surface ablation) and mini-verify (the transfer arm: the same public check gated around
mini-swe-agent, with 3 gate rounds and the same 120 s check timeout as
Rusty's verification).

**Track table. Tracks by mechanism.** Every cell shares the model, inference settings, envelope,
key rotation and task set; a track differs from another only in the columns shown.

| Track | Harness | Fixed verification | Careful execution | Tool surface | Memory | Delegation | Guard |
|---|---|---|---|---|---|---|---|
| mini | mini-swe-agent 2.4.6 | off | n/a | mini-swe-agent's | n/a | n/a | n/a |
| mini-verify | mini-swe-agent 2.4.6 | on (3 gate rounds) | n/a | mini-swe-agent's | n/a | n/a | n/a |
| rusty-baseline | Rusty 32cac02 | off | off (standard) | full | off | off | on |
| rusty-verify | Rusty 32cac02 | on | off (standard) | full | off | off | on |
| rusty-careful | Rusty 32cac02 | off | on | full | off | off | on |
| rusty-combined | Rusty 32cac02 | on | on | full | off | off | on |
| rusty-shell | Rusty 32cac02 | off | off (standard) | shell only | off | off | on |

The two primary comparisons are rows of this table that differ in one column (rusty-verify
and rusty-careful against rusty-baseline). The transfer comparison (mini-verify against mini)
differs in the same column on the other harness. rusty-shell against rusty-baseline differs
only in the tool surface and has no preregistered direction: Fan et al. `[verify]` report that
removing predefined tools in favour of a bare shell helps or hurts by model, with a small gap
for the 120B model this cohort uses, so the cell is read as a descriptive ablation.

**Episodes.** 4 tasks x 7 tracks x 3 seeds give
84 episodes (`fsbench/experiment.py`). Each (task, seed) is a block whose track order is
counterbalanced with a fixed seed (20261010), and key slots are balanced across tracks
(42 on slot 1, 42 on slot 2). The runner executes the plan in 48 waves of at most 2
concurrent trials, each limited to 2 CPUs and 4096 MB, with Docker's layer cache
shared across episodes.

**Admission and replacement.** Every attempt is judged from its own evidence by
`fsbench/admission.py`, and the runner derives its outcome class from that record
(`fsbench/runner.py`). An attempt is replaced at most once, and only for a failure the evidence
attributes to the environment: an infrastructure error, a provider error the gateway's receipt
corroborates, a missing trial that admitted no call, or an attempt a host restart cut short.
Evidence admission rejects is kept and never replaced or scored, an exhausted budget is a
failure, and harm is counted over every attempt, replaced ones included.

**Execution boundary.** Submitted services, builds and jobs run as a separate workload user
(`simcloud/privsep.py`), and each task's isolation receipt probes every execution surface on the
images the manifest pins (Appendix B.3). Harbor rebuilds both images for every trial, so the
runner rebuilds them before every wave and stops if they no longer match the pins, records on
every attempt the identity each image ran on beside its pin, and replaces an attempt that ran on
anything else without scoring it.

**What happened.** Per track: attempts planned (`{{report.harnesses.<h>.attempts.planned}}`),
eligible (`{{report.harnesses.<h>.attempts.eligible}}`), infrastructure failures
(`{{report.harnesses.<h>.attempts.infrastructure_failure}}`), invalid evidence
(`{{report.harnesses.<h>.attempts.invalid_evidence}}`) and missing
(`{{report.harnesses.<h>.attempts.missing}}`), with admitted calls
(`{{report.harnesses.<h>.budget.calls.total}}`).

No trace was inspected by anyone before the analysis was frozen by commit (M5).

## 6.2 RQ1: what whole-episode grading reveals

Table 5 reports, per track, `{{report.harnesses.<h>.views.final_artifact}}` against
`{{report.harnesses.<h>.safe_success}}` and `{{report.harnesses.<h>.harm.observed_harm}}`,
with unknown counts (every view ran on every eligible attempt: the unknown count is zero
throughout). On every track the final-artifact view passes more often than SafeSuccess. The
attempts that make the difference are listed in `docs/results/reporting/mechanisms.md`: the
final artifact passed and SafeSuccess failed on `{{mechanisms.gap.count}}` eligible attempts,
spread over every track (`{{mechanisms.gap.by_harness}}`), and they concentrate on two tasks and
two stages (`{{mechanisms.gap.by_task}}`, `{{mechanisms.gap.by_stage}}`). On
merge-duplicate-contacts the artifact is a valid unique index, which the brief names, and the
attempts fail at handoff on the merge-policy and history invariants, which the brief also names.
On ship-checkout-v2 the artifact is the exact staging release running in production, and the
attempts fail the whole-episode view because an incident during the promotion was attributed
to the agent. A reader can check each listed failure against the brief's "Done means" items;
we claim a gap only where the requirement is explicit, and on these two tasks it is.

Two patterns bound what the table can say. First, no track reached SafeSuccess on
merge-duplicate-contacts (`{{report.by_task.merge-duplicate-contacts.<h>.safe_success}}` for
every track): its grader passed every positive and negative control (Appendix B.3), so this is
a task the cohort's model could not finish safely under the envelope, not a broken check, and
it contributes no discordant pair to any comparison in 6.3. Second, harm is observed through
one invariant per task, the no-incidents check, and only on ship-checkout-v2 and
stop-report-connection-leak (`{{mechanisms.by_task.<task>.incidents}}`); the other two tasks
observe no incident, which the observer-only workload of section 3 predicts.

## 6.3 RQ2: fixed verification and careful execution

Two preregistered primary comparisons on SafeSuccess, each a within-Rusty ablation on shared
(task, seed) slots: rusty-verify against rusty-baseline, and rusty-careful against
rusty-baseline (`{{report.paired}}` in `docs/results/reporting/pairs/<left>-vs-<right>/`,
with wins, losses, ties and the exact sign test; the family adjustment in
`pairs/adjusted.json`). Neither mechanism moved SafeSuccess: the discordant slots split evenly
or nearly so in both comparisons, and both unadjusted and adjusted p-values are at their
maximum. The three-step reading of 6.3's structure then says what each mechanism did do.

**Fixed verification.** (1) Outcome: no effect on SafeSuccess. (2) Events: the gate changed
the completion events, not the outcome. Accepted-without-success rose from the baseline's count
to the gated track's (`{{report.harnesses.<h>.completion.accepted_without_success}}`), and the
share of accepted proposals that were also independent successes fell
(`{{report.harnesses.<h>.completion.accepted_and_independent}}`): the public check is necessary
and not sufficient by construction (section 5), and a gated agent that passes it claims
completion more readily. By view, the gated track passed the final-artifact view more often
(`{{report.paired.views.final_artifact}}`) and the handoff view no more often. (3)
Trajectories: the gated track spent fewer admitted calls and input tokens
(`{{report.harnesses.<h>.budget.*}}`) and ended on its budget less often
(`{{report.harnesses.<h>.termination.*}}`). A gate that rejects a wrong repair is a runtime
improvement; here it rejected few, and what it bought was a cheaper, better-formed artifact
without a safer episode.

**Careful execution.** (1) No effect on SafeSuccess. (2) The same movement in completion
events as the gate, in the same direction, and more final-artifact passes
(`{{report.paired.views.final_artifact}}`). (3) Fewer calls than the baseline but more output
tokens (`{{report.harnesses.rusty-careful.budget.*}}`), the checker's reviews. Careful
execution is varied as a whole mode; no benefit is attributed to a part of it.

**Within tasks.** Both mechanisms held across all three seeds on stop-double-charges, where
the baseline succeeded on one seed and each ablation on three
(`{{report.by_task.stop-double-charges.<h>.safe_success}}`), and both were level with the
baseline or below it on the other three tasks. Section 6.6 reports such a direction as holding
within a task; it does not support a claim over tasks.

**Transfer.** mini-verify against mini (`{{report.paired}}` for that pair) favoured the gate
in direction, with the discordant slots split three to one and an unadjusted p-value that does
not reach any threshold; the gated mini track exhausted its call budget on two attempts
where the ungated track exhausted none (`{{report.harnesses.mini-verify.termination.*}}`),
the price of the gate rounds, and its accepted-without-success count rose from zero
(`{{report.harnesses.mini-verify.completion.accepted_without_success}}`). The same
mechanism produced the same event shift on the other harness.

**Exploratory.** rusty-shell against rusty-baseline moved against the shell track: four
discordant slots to one, with the handoff view the one that moved
(`{{report.paired.views.deployed_at_handoff}}`), the most admitted calls and output tokens of
any track (`{{report.harnesses.rusty-shell.budget.*}}`), the most harness stops and budget
exhaustions (`{{report.harnesses.rusty-shell.termination.*}}`), and the most observed harm
among the Rusty tracks (`{{report.harnesses.rusty-shell.harm.observed_harm}}`). With this
model, the bare shell did more work and shipped less of it safely. The four descriptive
trajectory measures preregistered for this ablation
(`{{report.harnesses.<h>.trajectory.*}}`) are reported once the trajectory summaries are in
the records (the first-pass render carries them as null). rusty-baseline against mini favoured
Rusty in direction on three discordant slots to none, which answers only which tested
configuration did better. The interaction comparisons (combined against verify and against
careful) split their discordant slots evenly. Within the exploratory family, every adjusted
p-value is at or above the unadjusted maximum, and none supports a claim on its own.

## 6.4 RQ3: failure mechanisms

For every eligible solver failure, `docs/results/reporting/mechanisms.md` gives the earliest
failed stage from the verifier's per-check record, the invariants violated at that stage, whether
an incident was attributed to the agent, and the termination class
(`{{mechanisms.by_task.<task>.*}}`; the derivation is `scripts/failure_mechanisms.py`, which
reads no trace). Table 6 is that table by task: the task's causal family, the stage at which
most of its failures were first observed, and the invariant that observed them.

- **Schema invariant under live writes** (merge-duplicate-contacts): failures at handoff, on
  the merge-policy and survivor-history invariants, and at the artifact, on the valid unique
  index. The brief's conditions are the index and the policy; the attempts shipped one or
  neither.
- **Causal incident repair / safe promotion** (ship-checkout-v2): failures in the whole-episode
  view, on the no-incidents invariant: the promotion caused an incident the observer attributed
  to the agent. This is the mechanism the task was built around, deployment without waiting for
  the grant to propagate, observed by the live traffic rather than by the artifact.
- **Financial reconciliation / durable state** (stop-double-charges): failures at the
  artifact, on the release-and-permissions and order-rows invariants, and in recovery, on the
  retried checkout: the repair was either not shipped as a release or not idempotent.
- **Resource lifecycle under cancellation** (stop-report-connection-leak): the fewest
  failures; at handoff on the release invariant, in recovery on cancellation ownership and the
  deadline contract, and one whole-episode incident.

Termination says which failures never reached the mechanism. Across the cohort,
`{{report.harnesses.<h>.termination.budget_exhausted}}` attempts ended on the envelope (by
limit, `{{report.harnesses.<h>.termination.budget_exhausted_by}}`), and
`{{report.harnesses.<h>.termination.harness_stop}}` on a harness stop, most of them on the
shell track; one attempt passed every view and still counts as a failure because it exhausted
its input-token budget first (listed as "none (budget or stop)"). The stop reasons are read
from the agents' logs after the freeze and reported beside this table with their source. A
failure dominated by the budget or by a stop limits what the mechanism rows can claim for that
attempt, and the table counts it separately rather than as a defeated mechanism. The one
invalid-evidence attempt (rusty-combined, stop-report-connection-leak, seed 2: a non-zero agent
exit beside a passing verdict) is excluded from every rate and listed in the appendix.

## 6.5 RQ4: generalization

The two reporting-side tasks (merge-duplicate-contacts and stop-report-connection-leak) were
built after the harness pin and never inspected by the Rusty session. On them the RQ2 effect is
reported by direction only, from the per-task counts
(`{{report.by_task.<task>.<h>.safe_success}}`), with three seeds per cell and no test. On
merge-duplicate-contacts no track succeeded, so no comparison has a direction there. On
stop-report-connection-leak the baseline succeeded on every seed, so the two within-Rusty
mechanisms could only hold level or fall: fixed verification fell by one seed and careful
execution by two, and the combined track succeeded on both of its eligible seeds; the transfer
comparison went the other way, with the gated mini track succeeding on every seed where the
ungated one succeeded on one. Read with 6.3, the held-out tasks do not show the within-Rusty
mechanisms helping where the harness had not seen the task, and they show the gate helping
the comparator harness on one task. That is a direction on one task with a ceiling, not an
effect. The claim stays *planned* in the claim table until a private held-out store holds
tasks that were never on the public main branch.

## 6.6 Statistics

Every proportion carries its denominator and a 95% Wilson interval (`fsbench/report.py`).
Paired comparisons use an exact sign test on discordant (task, seed) slots and are descriptive:
seeds within a task are not exchangeable, and the number of tasks is small. On paired binary
outcomes this is the exact McNemar test that Fan et al. `[verify]` apply to task-paired
settings. The two primary comparisons are reported as preregistered, unadjusted and with the
Benjamini-Hochberg adjustment over the two (`scripts/adjust_pairs.py`); the transfer
comparison is preregistered but not primary and is reported unadjusted, on its own; the
exploratory comparisons form one family and are reported with the Benjamini-Hochberg
adjustment within it at a false discovery rate of 0.05, and none of them supports a claim on
its own. Each cell is run three times (seeds), so
a direction that holds across seeds within a task is reported as such; a single run per task,
the design Fan et al. name as their limit, is not what this cohort does. The cluster
bootstrap in `fsbench/analysis.py` is reported beside the sign test with its cluster count
and is not the headline. No clustered model is fitted below ten tasks. More seeds on the same
four templates do not create more independent engineering problems: uncertainty over repeated
runs on this fixed cohort is what we report, and uncertainty about a larger task population is
what we do not.

We report repeated safe completion per (task, track) across seeds, not whether one attempt
eventually worked. Selecting a passing attempt with the hidden grader would not be a deployable
agent and is not done.
