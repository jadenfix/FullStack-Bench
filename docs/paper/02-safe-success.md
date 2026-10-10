# 2. SafeSuccess and the four evaluation views

An episode is described by its initial state, the requested change, the protected invariants,
the workload, the fault process, the observation boundary and the budget. Its observed history
is tau: every synthetic check, control-plane action, incident, challenge and collector read,
with the stage of the episode each belongs to (section 4). Success is a predicate on tau, not
on the final filesystem:

    SafeSuccess(tau) = RequestedChangeCompleted(tau)
                     and RequiredRecoveryPassed(tau)
                     and not ProhibitedHistoryEvent(tau)

Each conjunct comes from independent outcome checks. None reads the agent's claims, and none
asks the application whether it reconciled correctly: balances, effects and placements are
reconstructed from the operation history (the payments provider's books, the orders table read
back through the API, cluster snapshots, the hash-chained audit log). The checks are written
in each task's `tests/test_outputs.py` and run in a separate verifier that rebuilds the stack
from declared artifacts.

## Four views on the same episode

The verifier emits the binary reward and four views beside it (`tests/views.json` per task,
assembled by `tests/conftest.py` into `reward.json`). Every outcome check belongs to exactly
one view (`tests/test_tasks.py` enforces the mapping).

| View | Question | Fails when |
|---|---|---|
| `final_artifact` | Does the submitted implementation satisfy the checked functionality? | the code or configuration the agent left behind is wrong |
| `deployed_at_handoff` | Does the running service satisfy the required behaviour when the agent stopped? | the artifact is right but was never deployed, or the permissions serving it are wrong |
| `whole_episode` | Were prohibited consequences observed during the transition? | an incident attributed to the agent, or evidence integrity broken |
| `recovery` | Do the guarantees survive the operator's predeclared follow-up workload? | the retried checkout double-charges, the rerun batch duplicates, the cancelled report leaks |

A view is `passed: true`, `false` or `null`; null means a check in the view did not run, and
is never counted as a pass or a failure. The disagreements between views are the subject of
RQ1 (section 6.2): an episode whose `final_artifact` passes while `whole_episode` or
`recovery` fails is listed with the failing check, so a reader can judge whether the history
failure corresponds to an explicit requirement in the brief. We do not manufacture the gap with
rules the brief did not state.

## Measurement eligibility is not success

An episode is measurement-eligible when four conditions hold, each recorded by the operator's
own collector: observation of production was complete (no gap longer than the task's declared
maximum), the post-handoff window was observed, every predeclared challenge ran, and the audit
chain is intact. `measurement_eligible` is reported beside `safe_success`, with
`safe_success_among_measured` as the proportion a reader can trust for harm claims. A missing
traffic interval means the history is incompletely observed; it is not proof that the
invariant held.

Per harness the report gives `{{report.harnesses.<h>.safe_success}}`,
`{{report.harnesses.<h>.measurement_eligible}}`,
`{{report.harnesses.<h>.safe_success_among_measured}}`, the four views
`{{report.harnesses.<h>.views.<view>}}` with their unknown counts, and the eligibility flags
`{{report.harnesses.<h>.eligibility.<flag>}}` (Table 1).

## Recovery is not the absence of harm

The grader qualification suite (section 4) includes cases whose final state is correct and
whose history is not: charge twice then refund (stop-double-charges's own public check, run
before the fix is live, creates exactly this), expose a secret or a bucket then fix it, break
checkout then roll back. Incidents are permanent in the operator's ledger. This is the boundary
between our contract and transactional no-regression (section 8): the latter concerns a
specified sequence of externally visible states under writer exclusivity and faithful undo; we
ask what happened to customers in between.

## Durable-state checks from client-observed histories

For tasks whose invariant is about durable state, the consistency contract is stated per task
and violations are found in the client-observed history: acknowledged operations that later
vanish, duplicated effects for one logical operation, reads that contradict earlier
acknowledgements. The checker names the violating operations. It validates the task's stated
contract, not arbitrary application invariants.
