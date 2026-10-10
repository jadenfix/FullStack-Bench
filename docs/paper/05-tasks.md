# 5. Tasks

Four tasks form the reporting cohort. Each is a production change on SimCloud with a causal
defect, a customer journey that fails for that reason, a reference strategy, a public
acceptance check the brief names, predeclared post-handoff challenges, and outcome checks
mapped one-to-one onto the four views. Tasks are written from scratch on a fictional platform,
carry a canary string, and are authored by one model family and reviewed by another, then by a
person. Two were development tasks for Rusty; two were built after the harness pin and never
inspected by the Rusty session (section 7, adaptation).

**Table 4. The cohort tasks.** Lineage from `task.toml [metadata.lineage]`; workload from
`[metadata.workload]`; checks per view from `tests/views.json`; receipt columns from
`receipts/<task>/` once M1 has executed them.

| Task | Family | Causal defect | Public check covers | Checks per view (artifact / handoff / episode / recovery) | Post-handoff challenges | Workload | Side of the split |
|---|---|---|---|---|---|---|---|
| ship-checkout-v2 | causal incident repair / safe promotion | production lacks the runtime grants, flag and rollout allowance the new release needs; IAM changes propagate with a delay | the shape of one "Done means" item (the designated partial-check case) | 2 / 4 / 2 / 1 | customer-quotes | observer only | development (familiar family) |
| stop-double-charges | financial reconciliation / durable state | a client retry re-runs checkout and charges the same cart again | one item: a retried checkout returns one order | 2 / 2 / 2 / 2 | retried-checkout | observer only | development (familiar family) |
| merge-duplicate-contacts | schema invariant under live writes | the importer keys contacts by its own lower-casing and the API path has no database constraint, so new spellings and racing creates both make duplicates | one item: a variant spelling returns the existing contact | 1 / 4 / 2 / 3 | variant-create, batch-rerun | observer only | reporting (new mechanism) |
| stop-report-connection-leak | resource lifecycle under cancellation | a report cut at its deadline exits through a path that never returns the pooled connection or ends the transaction, so cancelled reports exhaust the pool while liveness stays green | one item, for a single cancelled report | 1 / 1 / 2 / 5 | cancellation-storm, foreground-after-storm, isolation, deadline | observer, plus an operator-driven workload after handoff | reporting (new mechanism) |

**Table 4b. Qualification executed per task** (filled from the receipts; every cell is a count
of executed runs, never a declaration): reference x10, independent solution, baseline x3,
wrong solutions (each on its own invariant), verifier x3, isolation. `{{receipts.<task>.*}}`.

## What the tasks have in common

The obvious path to "done" causes harm, and the safe path takes judgement: a concurrent index
build instead of a table lock, a canary instead of a full promotion, a scoped policy beside the
existing one instead of a wildcard grant, a refund reconciled against the provider's books
instead of a row deleted. Every condition deciding reward is in the brief; nothing deciding is
hidden, and the brief never gives implementation steps. Each brief names the public check and
says which "Done means" items it covers: the check is necessary, never sufficient, and
ship-checkout-v2 is the designated partial-check case whose check passes as soon as production
quotes from the new engine while least privilege, the artifact digest, staging and the ledger
remain ungraded by it.

The public check is reachable identically from every track: it is a script in the agent's
container, the brief names it, and the only difference verification makes is whether the
harness enforces it before accepting completion (section 6).

## What they do not establish

Four tasks are a mechanism-study set, not a benchmark. They were kept because they have
measurable successes and failures under the pilot's budgets; a frontier challenge set is a
separate artifact. Their difficulty is causal coupling (an earlier decision changes what is
correct later: a dropped invalid index before a concurrent build, a transaction left open by a
cancelled stream), not length; the compositionality diagnostics the plan describes
(component, chained and coupled variants) are development evidence and are not reported here.
Two of the four are observer-only during the agent phase, so sustained-arrival claims are out
of scope for them (section 3). Held-out tasks of a future cohort are named by digest only.
