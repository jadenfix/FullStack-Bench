# Reporting records (reporting-v1)

The reporting cohort's run as the runner left it. Everything under `docs/results/reporting/` is
rendered from these files. The cohort ran from `a7f08b0` (manifest `experiments/reporting-v1.json`,
evaluator commit `55d185f`), launched 2026-10-11 16:05Z, and stopped `complete` at 2026-10-12
13:17:57Z. Nothing here is regraded.

| Path | What it is |
|---|---|
| `ledger.jsonl` | the runner's ledger: one `run` line, one line per attempt (with its admission record), the `stop` line |
| `plan.json`, `manifest.json` | what the run ran; the manifest is byte-identical to `experiments/reporting-v1.json` |
| `stop.json` | the ledger's last `stop` line |
| `receipts/<job>.json` | each attempt's budget-gateway receipt, which admission reads |
| `evidence/<job>/<trial>/result.json`, `verifier/` | Harbor's trial result and the verifier's output |
| `evidence/<job>/<trial>/agent/*.trajectory.json` | the trajectory files `fsbench.trajectory` reads (Rusty, mini-swe-agent and its gated rounds) |
| `evidence/<job>/<trial>/trajectory_summary.json` | `fsbench.trajectory.write_summary` over those files |

Ledger paths are run-host paths: `.../run/jobs/<job>/<trial>` is `evidence/<job>/<trial>` and
`.../run/receipts/<job>.json` is `receipts/<job>.json`.

Not published: agent terminal logs, Rusty's home directory, the trial's SimCloud artifacts and the
container images (the runner removed each trial's images and volumes after checking them against
the pins). In the trajectory files every API token a trial's own SimCloud minted
(`sct_<id>_<secret>`) is replaced with `sct_REDACTED`; those tokens died with the trial. Nothing
else is edited.

## Checking them

`tests/test_reporting_records.py` runs in CI and checks that:

- every receipt and every evidence file admission hashed (`admission.budget.sha256`,
  `admission.files_sha256`) still has that SHA-256;
- the manifest is the frozen file, and its canonical hash and the plan's hash are the run line's;
- `stop.json` is the ledger's stop line, its admitted calls are the attempts' sum, and every planned
  episode has an attempt;
- each trajectory summary equals `summary_for_trial` over the raw files beside it.

## Rendering the report from them

`docs/PAPER_PLAN.md` ("M5 procedure") has the commands: `scripts/report_cohort.py` with
`--records docs/results/reporting/records --receipts receipts` for the report and each comparison,
`scripts/adjust_pairs.py` for the family adjustment, `python -m fsbench.analysis` for the bootstrap
and `scripts/failure_mechanisms.py` for the mechanism table.
