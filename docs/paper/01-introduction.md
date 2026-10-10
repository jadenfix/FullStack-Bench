# 1. Introduction

Coding-agent benchmarks grade the state an agent leaves behind: a patch that makes hidden
tests pass, a repository that builds. A change to a running system is graded differently by
the people who operate it. What served during the change, what the customers experienced,
whether an interrupted operation was retried into a duplicate, whether a credential was
disclosed before it was rotated: none of this is visible in the final filesystem, and a later
rollback does not erase it. This paper asks:

> When coding agents modify a running stateful system, how often does final-state correctness
> conceal customer-visible or irreversible violations, and which execution mechanisms reduce
> those violations without merely avoiding useful work?

The contribution is narrow and we state it as such. We do not claim to introduce end-to-end
operations evaluation, fault injection, live services or safety-aware scoring, each of which
exists (section 8). We contribute:

1. **A measurement.** SafeSuccess over an observed episode history, decomposed into four
   evaluation views (final artifact, deployed at handoff, whole episode, recovery) with
   measurement eligibility kept separate from success, so that a missing observation interval
   is reported as missing and never as a held invariant (section 2).
2. **A harm attribution that reports what it could not see.** Outages are attributed to the
   agent, to the task's own fault scenario, to both or to neither, segment by segment at every
   fault transition; observation coverage is part of the evidence; recovery is not the absence
   of harm (section 3).
3. **A fail-closed evidence path.** A manifest frozen before any model call, grader
   qualification in both directions, an executed isolation receipt, and one terminal record per
   attempt with failure classes that keep operator, coverage, harness and solver failures apart
   (section 4).
4. **A controlled mechanism study on one open harness.** Fixed public verification and careful
   execution are varied in a 2x2 inside Rusty with memory and delegation off; the same
   verification gate is carried to the standardized comparator (mini-swe-agent) as a transfer
   arm, and Rusty's tool surface is removed as an ablation, so a mechanism effect can be told
   from a Rusty effect (section 6).

Four research questions organise the evidence (`docs/PLAN.md`, "Research questions and
evidence"):

- **RQ1.** What does whole-episode grading reveal that final-state grading misses?
- **RQ2.** Do fixed verification and careful execution improve outcomes at equal budget?
- **RQ3.** Which cross-layer failure mechanisms defeat plausible repairs?
- **RQ4.** Do improvements generalize to tasks the harness's developers never inspected?

Every number in this paper carries one of five labels: *executed* (fresh eligible runs through
the admission path), *reproduced*, *externally reported*, *estimated* or *planned*. RQ1 to RQ3
are answered on one executed reporting cohort (section 6). RQ4 is answered by direction only,
on the tasks held out from development (section 5), and is labelled accordingly.

What we do not claim: a ranking of harnesses, that any harness is best, or that performance on
these controlled live-system environments predicts production readiness. The tasks run on a
fictional, provider-neutral platform whose open-standard components are real and whose cloud
control plane is simulated; section 7 states per primitive what is real, simulated, simplified
or omitted.
