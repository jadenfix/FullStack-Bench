# Appendix B. Receipts (Lane A)

Every value below is read from a file in this repository or from the pinned binary by `scripts/render_receipts.py`, which checks them against each other before writing (the run binary against the manifest's digest, `--capabilities` against Rusty's receipt, the mini-swe-agent config against its digest, each base pin against `receipts/README.md`). Full digests are in the files named.

## B.1 Frozen configuration

| Item | Value | Source |
|---|---|---|
| Reporting manifest | `experiments/reporting-v1.json`, added in `a5e3b48` (merged as `a7f08b0`); file sha256 `44e8211175e4463e…`; canonical hash in the plan `35869a333e60af20…` | the file; the plan |
| Evaluator commit | `55d185f5cc99f4472a305d0f72a2867192881407` | `evaluator_frozen_at` |
| Harness commit (Rusty) | `32cac02ccf9f3c94e2f20b413521c711a94213d8` | `harness_frozen_at` |
| Model | `nvidia/nemotron-3-super-120b-a12b`, revision `unknown` (the endpoint's model listing carries no version field) | `model` |
| Inference | temperature 0.2, top_p 1.0, at most 16384 reply tokens, reasoning effort unset | `inference` |
| Envelope per episode | 300 calls, 12,000,000 input and 500,000 output tokens, 7200 s | `envelope` |
| Runtime per trial | 2 CPUs and 4096 MB (reserved = limit), 2 concurrent, key slots [1, 2], warm layer cache, counterbalanced order (seed 20261010) | `runtime` |

## B.2 Base images

The two `fullstack-bench` images are built from the evaluator commit and tagged `:dev`; the verifiers build from `python:3.12-slim`. Pinned by rootfs-layers-v1 identity (`fsbench.isolation_gate.rootfs_identity`); config IDs for traceability.

| Image | rootfs-layers-v1 | Config ID |
|---|---|---|
| `fullstack-bench/simcloud:dev` | `bab5c6ee143e341a…` | `c67ff54b056d4850…` |
| `fullstack-bench/client:dev` | `18d91d9c73ee2a06…` | `e2bd22730e333946…` |
| `python:3.12-slim` | `95e4efdee9b09b83…` | `c7e68c9115d5cee2…` |

## B.3 Tasks

Gate receipts (`receipts/<task>/gate.json`, from `scripts/gate_task.py`) run the reference solution ten times, the empty solution three times, every wrong solution once and an independently written solution once, on the task digest and base images above; `fsbench.admission.assess_qualification` passes each at reporting level. The ten reference runs are also the verifier-stability check: the same digest scored 1 every time, and the empty solution 0 every time. Isolation receipts (`isolation-m1.json` at M1, `isolation.json` as admitted, `isolation-refresh.json` from the run checkout just before the freeze) probe every execution surface and pass `fsbench.isolation_gate.assess_isolation` against the manifest's pins.

| Task | Digest | Reference | Empty | Wrong (each fails) | Independent | Outcome checks | Surfaces | simcloud pin | verifier pin |
|---|---|---|---|---|---|---|---|---|---|
| ship-checkout-v2 | `1f4111f9c502…` | 10/10 | 3/3 | 3: noflag, rebuild, wildcard | 1/1 | 9 | 6 | `80f7f7b7dd8e…` | `8de838c50b42…` |
| stop-double-charges | `ac9dc2c1f2c6…` | 10/10 | 3/3 | 4: by_customer, delete_rows, no_fix, unscoped_update | 1/1 | 8 | 6 | `a6d8698fc8bb…` | `ee5de23f6735…` |
| merge-duplicate-contacts | `8c58a6fbd362…` | 10/10 | 3/3 | 4: api_only, delete_duplicates, index_name_is_done, newest_survivor | 1/1 | 10 | 6 | `228d665e3aa5…` | `56d184f54a0e…` |
| stop-report-connection-leak | `9a8f83afb82d…` | 10/10 | 3/3 | 4: bigger_pool, close_shared_pool, restart_only, swallow_cancel | 1/1 | 9 | 6 | `3ffb6b66e420…` | `217e998be7ec…` |

`receipts/README.md` records why `stop-double-charges` was re-probed after M1 (its verifier's identity differed between the long-lived M1 checkout and a fresh one, by cached layers only) and that the refresh in the run checkout matched every pin.

## B.4 Harnesses

| Item | Value | Source |
|---|---|---|
| Rusty commit | `32cac02ccf9f3c94e2f20b413521c711a94213d8` | Rusty `docs/receipts/fsb-reporting-v1.md` |
| Rusty QA | `cargo xtask qa` exited 0 on that commit, 2026-10-09 19:55 UTC | same |
| Toolchain | rustc 1.97.0 (2d8144b78 2026-07-07); cargo 1.97.0 (c980f4866 2026-06-30); x86_64 Linux | same |
| Cohort binary | static musl build of that commit, sha256 `19f976895e1aff5b…`, `--version` prints `rusty 0.1.0` | manifest `tracks[].binary_sha256`, `version` |
| Context handling | window assumed to be 128,000 tokens (`RUSTY_CONTEXT_TOKENS` unset; the adapter does not set it); history budget = window minus a 16,000-token reply reserve and the system prompt; above 60% of it old tool outputs are shrunk, above 80% older turns are summarised; usage estimated at about four characters per token | Rusty `src/context.rs`, `src/agent.rs` at that commit; `fsbench/agents/rusty.py` |
| `--capabilities` | identical to the receipt's: contract 1; modes auto, careful, standard, vibe; toolsets full, shell | the binary |
| mini-swe-agent | 2.4.6, config `configs/mswea-compact.yaml` sha256 `b0f51aceb8161eb5…` | manifest `tracks[]` |

