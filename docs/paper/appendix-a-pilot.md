# Appendix A. Development pilot (exploratory)

Every number in this appendix is a key of `docs/results/development/report.json`, rendered by
`scripts/report_cohort.py --rejudge --records` from the records under
`docs/results/development/pilot-records/pilot-2b/`, and carries the cohort's claim label,
`{{report.claim_label}}`: exploratory, a development cohort that ran under no admission manifest.
`{{report.reportable}}` is false. Nothing here supports a claim in the body; the pilot's one
product is the reporting envelope of section 6.1.

**What ran.** `{{report.records}}` records on `{{report.tasks}}`, both development tasks that
the Rusty session had seen, across five tracks: mini-swe-agent 2.4.6 and the Rusty 2x2 (fixed
verification x careful execution), under one gateway envelope and no Rusty-side limit. The
block that ran earlier as pilot-2, under Rusty-side limits that bounded its Rusty arms before the
gateway did, is not budget-matched and is excluded; that exclusion is the one *missing* planned
episode every track shows in `{{report.harnesses.<h>.attempts.missing}}`. The host restarted
twice; the attempts it cut short are recorded as interrupted and replaced, and their calls stay
in the spend table.

**What it sized.** The spend table
(`docs/results/development/pilot-records/pilot-2b/spend_table.md`) lists, per final attempt,
admitted calls, tokens in and out, wall clock, the share of forwarded requests the provider
throttled, and which limit, if any, the attempt exhausted. The reporting envelope was set above
the table's maximum admitted calls with margin, identical for every track, and its token and
wall-clock limits above the table's maxima (`docs/PAPER_PLAN.md`, "Budget"). The cohort-wide
call cap of section 6.1 is one full envelope per episode.

**What it shows, descriptively.** Per track, `{{report.harnesses.<h>.safe_success}}`,
`{{report.harnesses.<h>.views.<view>}}`, `{{report.harnesses.<h>.completion.*}}`,
`{{report.harnesses.<h>.harm.*}}`, `{{report.harnesses.<h>.failure_classes.*}}` and
`{{report.harnesses.<h>.budget.*}}`, each with its denominator and Wilson interval, in the
same tables as section 6 (`docs/results/development/report.md`). The paired directories
(`docs/results/development/pairs/<treatment>-vs-<control>/`) give the same for each comparison
on shared (task, seed) slots; with two tasks they are descriptive only. The trajectory,
termination and cost keys of section 6.3 are absent: the pilot's adapters predate them.

**Caveats that keep it exploratory.** (1) The two tasks were Rusty's development tasks, so no
generalization claim survives. (2) The ledger was written before the admission rule for proven
budget exhaustion; one attempt, rusty-careful on stop-double-charges seed 0, exhausted the
input-token envelope with a passing final artifact and was first recorded as invalid evidence.
Under the current rule it is an eligible solver failure with its functional outcome kept, which
is how the re-judged report counts it; every other record classifies as it did. (3) The
execution-boundary gap later closed in `simcloud/privsep.py` existed while the pilot ran. No
trace mentions the operator's token, the state database, the admin role, the evidence directory
or the expected-state file, so the gap was not used; the outcomes stand with that caveat.
(4) Outage-seconds attribution predates the fault-end segmentation fix (`e92c3f3`), so the harm
columns are read as presence, not duration. (5) The cohort's `{{report.full_benchmark_claim}}`
is set by the tool as it is for every cohort and means nothing more than that every planned
track has a record.
