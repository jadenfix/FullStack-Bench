# Development cohort: pilot-2b

Exploratory evidence from the development pilot, rendered by `scripts/report_cohort.py` from the
records under `pilot-records/pilot-2b/` with `--rejudge --records`. Its claim label is
*exploratory (development cohort; not admitted)*: the tasks were development tasks for Rusty, the
cohort ran under no admission manifest, and nothing here is a reporting result. It sized the
reporting cohort's envelope and cap (`docs/PAPER_PLAN.md`, "Budget") and appears in the paper
only as Appendix A.

What was run: blocks 1 to 3 of the pilot plan, 15 episodes on ship-checkout-v2 (seed 1) and
stop-double-charges (seeds 0 and 1), five tracks (mini-swe-agent 2.4.6 and the Rusty 2x2) under
one gateway envelope, no Rusty-side limits. Block 0 (ship-checkout-v2 seed 0) ran earlier as
pilot-2 under Rusty-side limits that made its Rusty arms bounded by their own defaults; it is not
budget-matched and is excluded, which is why every track shows one *missing* planned episode.
The host restarted twice during pilot-2b; the four attempts it killed are recorded as interrupted
and replaced, and their calls are counted in the spend table.

Re-judging: the ledger was written before the admission rule for proven budget exhaustion
(`CHANGELOG.md`, 2026-10-10). One line, rusty-careful on stop-double-charges seed 0, exhausted
the input-token envelope with a passing final artifact and was recorded as invalid evidence; under
the current rule it is an eligible solver failure with its functional outcome kept, which is how
this report counts it. Every other line classifies as it did.

Isolation: the gap fixed in `simcloud/privsep.py` (submitted code could inherit the operator's
environment and identity) existed while this pilot ran. No agent trace in it mentions the
operator's admin token, `state.db`, `pg.admin`, `/evidence` or `expected.json`, so the gap was
not used; the pilot's outcomes stand as development evidence with that caveat.

Outage-seconds attribution in these records predates the fault-end segmentation fix (e92c3f3).

Files: `report.json` (every number with its denominator and interval), `report.md` (the tables),
`pairs/<treatment>-vs-<control>/` (the same for each paired comparison on shared task-seed slots;
descriptive only, two tasks). The spend table is `pilot-records/pilot-2b/spend_table.md`.
