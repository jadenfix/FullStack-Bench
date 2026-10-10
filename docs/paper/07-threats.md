# 7. Threats to validity

**Construct: engineering or the fictional platform.** SimCloud is provider-neutral and its
open-standard components are real (Table 3). A solver that has learned the `sc` conventions
has learned a cloud control plane, not a trick; still, success here is performance on these
controlled live-system environments, and we claim nothing about production readiness.
Interface-transfer checks (the same operation through the CLI, the REST API and MCP), native-
component checks (Postgres and Kubernetes claims demonstrated against the real component) and
an independently authored application are planned and not executed for this cohort; the
shell-only track is the one interface variation this cohort runs.

**Table 3. Simulator fidelity per primitive** (from `docs/PLAN.md`; a claim about a primitive
rests on its row).

| Primitive | Fidelity |
|---|---|
| Kubernetes (k3s cluster, RBAC, eviction, disruption budgets) | real |
| Postgres databases, SQL audit | real |
| HTTP load balancer, traffic split, rollouts, readiness | simulated (SimCloud router and supervisor) |
| IAM policies, bindings, propagation delay | simulated |
| Payments provider, identity provider | simplified (fictional SimSaaS) |
| Object storage, queues, KV, secrets | simulated |
| Cluster system images | real, preloaded from the pinned k3s release |
| Node disk pressure and image garbage collection | simplified; disk-pressure eviction is not simulated |
| Cloud billing, regions beyond outage faults, networking policy outside the cluster | omitted |

**Workload identity.** Table 4 names the tasks on which the observer is the only traffic
during the agent phase. On those, harm is detected on the checked business routes and in the
post-handoff window; sustained-arrival behaviour is not measured, and the open-versus-closed
distinction does not arise because there is no customer load generator to be closed.

**Adaptation.** Three sources are tracked separately in `CHANGELOG.md`, each a dated entry
naming its kind: task adaptation (a candidate selected or revised on particular models'
failures), harness adaptation (Rusty changed after its developers inspected failures) and
evaluator adaptation (checks revised after particular submissions). Rusty's benchmark-motivated
changes before its pin are listed in its own documentation and in section 9; they were
motivated by the two development tasks and by a task excluded from this cohort. Changes after
the frozen tag came from code audit, Rusty's own evaluation, gateway accounting and the
harness contract, not from task traces. The two development tasks are therefore evidence for
RQ1 to RQ3 with the adaptation stated, and not for RQ4. The main question is neutral: does the
intervention improve safe completion, not which tasks show Rusty best.

**Grader validity.** Each task passes the positive and negative controls of section 4. Two
required controls do not exist for this cohort and are the main open threat: held-out mutants
not used to tune the checks, and a reviewed sample of real solver submissions. Cross-model QA
and a human review do not establish independence of the grader from the authors' reading of
the task.

**Author-model overlap.** The authoring model and the QA model of each task are recorded;
the cohort model is a different model and was never used to filter tasks
(`task_filtering_models` is empty in the manifest). Scores with and without each author's
tasks are reported when the author rotation covers more than one family on the cohort.

**Safety, security and operator restriction.** Per attempt, four observations stay apart: the
model proposed an unsafe action (from the trajectory), the runtime blocked it (Rusty's guard
record, a pinned condition held on in every cell), the environment made it impossible, and the
action executed with no detected incident (bounded by the detector's coverage). Baselines are
matched on privilege: every track holds the same principal and token, and the shell track
reaches the platform through the same CLI and API. An adversarial security track is separate
from this operational-reliability study.

**Coverage limitations.** A harness that declares it cannot reach an interface a task needs is
a failure with class `coverage_limitation`; the cohort's `{{report.full_benchmark_claim}}` is
false in that case and the compatible subset is named. Rusty's MCP client is stdio-only and no
cohort task uses another transport; no task needs a browser or image input.

**Operational comparator.** The standardized comparator is mini-swe-agent with a pinned
configuration, and the mechanism-transfer comparator is Rusty's shell toolset. No rollback-
based or state-machine operational agent is run; a locally implemented policy of that kind
would be labelled as such and never described as a reproduction of the published system.

**Precision.** With four tasks the paired comparisons are descriptive (section 6.6).
Mechanism effects are direction and magnitude on this cohort, not population estimates.

**Rerun versus regrade.** A finished episode is regraded only when the retained evidence
supports the new check; otherwise it is rerun. Evidence is retained in full. One regrade is
on record for this cohort's development pilot: the admission rule for proven budget exhaustion
(`CHANGELOG.md`, 2026-10-10), applied by re-judging the ledger from the retained trial
directories and gateway receipts, with the lines whose trial directory was gone left as
recorded.
