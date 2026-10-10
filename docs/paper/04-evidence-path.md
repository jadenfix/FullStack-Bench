# 4. Evidence path

Every claim in section 6 rests on a terminal record produced by one path
(`fsbench/admission.py`), and the path fails closed: an attempt that cannot be classified is
invalid evidence, not a success and not a failure.

## The environment and its trust boundary

Each task is a Harbor task (`task.toml`, `instruction.md`, `environment/`, `solution/`,
`tests/`). The agent's container holds the task repository, the `sc` command-line client, the
public acceptance check and an MCP client; it runs as an unprivileged principal of SimCloud,
the operator-owned platform sidecar that provides the control plane (projects, environments,
services, rollouts, traffic, policies, bindings, secrets, databases, jobs), real Postgres and
real Kubernetes (k3s) behind it, the guard of section 3, the fault engine and the collectors.
The verifier is a separate image that rebuilds the stack from declared artifacts on a fresh
SimCloud and reads evidence the collectors wrote under the operator's token.

Untrusted: everything in the agent's container, including the public check it can read and
edit, the task repository, the agent's principal and token, and any observation reported by the
agent or its harness (including the harness's own public-check outcome). Trusted and
operator-owned: the sidecar and its admin token, the guard and ledger, the fault engine, the
synthetic checks, the collectors, the evidence directory, the verifier image and the gate
receipts. The boundary is asserted by executed isolation probes, not by this paragraph.

## The episode lifecycle contract

1. **Initial state.** The world is built from the task's declared artifacts; the initial
   failure is observable through the customer journey the brief names.
2. **Live work.** The agent works while the synthetic checks and the guard run.
3. **Handoff or timeout.** The agent's completion claim is recorded and ends nothing;
   observation continues.
4. **Operator-owned follow-up.** The task's predeclared post-handoff workload and recovery
   period run (a retried checkout, a rerun batch job, a cancellation storm, a read-back). The
   collector marks the phase first, so every later incident carries it.
5. **Evidence capture.** Live state comes first: what is deployed, the durable data, external
   effects, outstanding work and the incident ledger. A fresh reconstruction is used only for
   properties the brief requires, such as a reproducible deploy. The verifier never performs a
   missing migration, replays lost transactions or otherwise repairs the submission.
6. **Teardown.**

A state-triggered challenge the agent never reached is recorded as not run, never as passed.

## Grader qualification, both directions

Admission reads executed receipts, bound to the task's content digest, the verifier mode and
the pinned outcome-check count; any change to the task, its images or the execution boundary
invalidates them.

| Control | Establishes |
|---|---|
| The reference solution, ten runs | the grader accepts the intended behaviour |
| An independently written, materially different solution (`independent_solutions/<task>/`, kept outside the task digest, written by a different model after reading the reference) | the grader is not tied to the author's implementation |
| The untouched baseline, three runs | the initial failure is real |
| Every wrong solution, each failing on the invariant it was written to break | each deciding invariant can reject a violation |
| The verifier, three runs on one fixed final state | stability |
| An isolation receipt | the collector and evidence are outside the agent's reach |

Ten clean reference runs are an engineering gate, not a low-flakiness guarantee: under
independent trials, zero failures in ten bounds the failure probability at about 25.9%
(one-sided 95%). We keep the practical thresholds and do not restate them as stronger claims.
Two controls the plan requires and this cohort does not have are stated as such in section 7:
held-out mutants not used to tune the checks, and a reviewed sample of real solver submissions.

## Terminal records and failure classes

Every attempt ends in exactly one terminal record: `eligible_success`,
`eligible_solver_failure`, `infrastructure_failure` or `invalid_evidence`. The record keeps the
four views, the harm fields, the three completion events (the model proposed completion; the
runtime accepted it; the independent grader accepted the result) and the gateway receipt
apart from the artifact. Proven budget exhaustion is a solver failure even when the final
artifact passes; an exception with a passing artifact and no proof of exhaustion is invalid
evidence, because the two observations contradict each other.

| Failure class | Meaning | Treatment |
|---|---|---|
| `operator_setup` | the benchmark promised a service and the operator failed to start it | infrastructure failure; replaced once under the predeclared policy, its harm observation kept |
| `coverage_limitation` | the harness declares it does not support an interface the task needs | stays in the results as a failure; the cohort's `full_benchmark_claim` is false and the compatible subset is named |
| `harness` | the service works and the harness fails to discover or dispatch its tool | a solver failure; set only after an operator's trace review |
| `solver` | the tool is available and the model chooses a wrong operation | a solver failure |

A missing capability is never an infrastructure failure. Per harness the report gives
`{{report.harnesses.<h>.failure_classes.<class>}}`,
`{{report.harnesses.<h>.coverage_limited}}` and the cohort's
`{{report.full_benchmark_claim}}`.

## The manifest and the cohort

A manifest (`fsbench/experiment.py`) pins, before any model call, the evaluator commit, the
task digests, the harness identities and build digests, the model and inference settings, one
gateway envelope per episode (calls, tokens, wall clock), the runtime reservation and limits,
the key slots and their counterbalanced assignment, the cache treatment, and the preregistered
comparisons. Tracks may differ only in their declared treatment. Cohorts are development,
selection or reporting; selection runs are never reused as reporting runs, and only an
admitted reporting cohort yields the *executed* label. Each attempt runs under a budget gateway
that admits calls up to the envelope and records throttling and exhaustion, so a harness's own
limits can never bind before the shared one.
