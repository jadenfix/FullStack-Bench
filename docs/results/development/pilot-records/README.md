# Development pilot records

Development evidence only: these tasks and traces are not held out, and no number here is a
reporting result.

- `pilot-2/`: block 0 (ship-checkout-v2 seed 0, five tracks) under manifest `59eca64d33ce`, Rusty
  009a5c1. Its Rusty tracks set `max_requests`, which made Rusty bounded at its own defaults
  (4M tokens, 3600 s) while mini-swe-agent had the gateway's envelope; they are not budget-matched
  and are excluded from paired comparisons. `NOTES.md` has the details.
- `pilot-2b/`: blocks 1-3 (15 episodes) under the corrected manifest `6cfc53713b65`, no Rusty-side
  limits. The host restarted twice during the run; the four attempts it killed are recorded as
  `interrupted` with `host_restarted` and replaced, and their 123 admitted calls are counted.

Each directory holds the runner's `ledger.jsonl`, the `plan.json` and `manifest.json` it ran,
`spend_table.md` and, for `pilot-2b`, the analysis summary. `evidence/<job>/<trial>/` keeps each
trial's `result.json` and verifier output, and `receipts/` each attempt's gateway receipt, which is
what admission reads. A ledger line's `trial_dir` and `receipt` are paths on the run host:
`.../phase5/<pilot>/run/jobs/<job>/<trial>` maps to `evidence/<job>/<trial>`, and
`.../run/receipts/<job>.json` to `receipts/<job>.json`.

No agent trace in either pilot mentions the operator's admin token, `state.db`, `pg.admin`,
`/evidence` or `expected.json`; the isolation gap later fixed in simcloud was not used.
