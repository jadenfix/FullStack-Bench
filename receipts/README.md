# M1 receipts

Executed at the evaluator commit `55d185f5cc99f4472a305d0f72a2867192881407` on base images built from it
(`docker build --network host --target <simcloud|client> .`). Docker only; no model was called.

Each task directory holds:

- `gate.json`: the executed gates from `scripts/gate_task.py` (oracle 10x, nop 3x, every wrong
  solution, the independent solution), which `fsbench.admission.assess_qualification` reads.
- `isolation-m1.json`: the isolation probe from `scripts/isolation_probe.py` as M1 ran it.
- `isolation.json`: the receipt admission reads (`fsbench.isolation_gate.assess_isolation`): M1's
  probe, unless a later probe on the same commit replaced it (see "Re-pinned after M1"). The probes
  are rerun right before the reporting manifest is frozen; if an image identity has changed by then,
  that run replaces this file and `isolation-m1.json` stays.

Images are identified by `fsbench.isolation_gate.rootfs_identity` (rootfs-layers-v1: a digest over
the ordered RootFS layers). The identity reproduces only from Docker's build cache, so the runner
rebuilds each task's images before every wave and checks every attempt against these pins.
Config IDs are listed for traceability only.

## Base images

| Image | rootfs-layers-v1 | Config ID |
|---|---|---|
| `fullstack-bench/simcloud:55d185f` | `sha256:bab5c6ee143e341afacdffc7e7a81880064b374f504f56a817cc50e2776643d5` | `sha256:c67ff54b056d4850bde90ea6f1823961b7fb210aa4d6f17d245473bc70ed557a` |
| `fullstack-bench/client:55d185f` | `sha256:18d91d9c73ee2a06de98e99d6dcfe94a1db4b6c9bbe233b885439cd142043f07` | `sha256:e2bd22730e333946be42d0095cb34cc4519300e28497e77cec8c21c9d5a5b382` |
| `python:3.12-slim` | `sha256:95e4efdee9b09b836d410bc7d7b5a6d849f07f98f94eb9cd968b119eb59b5633` | `sha256:c7e68c9115d5cee250ac73e38fd4e110334ec03fdb2af166ee11122525646d98` |

## Tasks

### ship-checkout-v2

- Task digest: `1f4111f9c502a1e7997501c0cd0231a98ff0c8baf183c632aff17ee5e612ef3e`
- Gates: oracle 10/10, nop 3/3, independent 1/1, wrong 3 (noflag, rebuild, wildcard); 9 outcome checks
- Isolation: 6 surfaces probed, every forbidden capability observed false

| Role | rootfs-layers-v1 (pin) | Config ID |
|---|---|---|
| simcloud | `sha256:80f7f7b7dd8e5a5bf1eacaa8ad59c3c671b3bf9c2e1de1a318f856d7fa8baf29` | `sha256:4fc72856dc630caecfc515e9cfcc23833918ce25e0365078bf5f6159e2e06b70` |
| verifier | `sha256:8de838c50b42a9460ae6b1fd33ba00c20d826287beb31904dae1007718aec63b` | `sha256:c579cb8bd0a96ce8f32dfc6677b31bb3281ae13bb698888648a0d13dd76d64ed` |

### stop-double-charges

- Task digest: `ac9dc2c1f2c606c3d536272e3c22721ff888ab383918cdb39fffbe9e32befef4`
- Gates: oracle 10/10, nop 3/3, independent 1/1, wrong 4 (by_customer, delete_rows, no_fix, unscoped_update); 8 outcome checks
- Isolation: 6 surfaces probed, every forbidden capability observed false

| Role | rootfs-layers-v1 (pin) | Config ID |
|---|---|---|
| simcloud | `sha256:a6d8698fc8bb2b73b51c85f877e8f383448bc91c5c9e0977a607110a8e588fa3` | `sha256:ef329a65a83905f4f9974c5d60aad56dd985a17fa627ed3c6ffa269ac045aff8` |
| verifier | `sha256:ee5de23f673519c23a102656414a6ff2000205164f9b6dd4da3707c1c8f38274` | `sha256:ba0843a0d0a06be51c7b8b9c30ecd0f254696c994a13aa9f1b118b8e6cce6d1d` |

### merge-duplicate-contacts

- Task digest: `8c58a6fbd362fc47aa57060eacd292d10a7c2ac468bbff44b31d2127eb93dce2`
- Gates: oracle 10/10, nop 3/3, independent 1/1, wrong 4 (api_only, delete_duplicates, index_name_is_done, newest_survivor); 10 outcome checks
- Isolation: 6 surfaces probed, every forbidden capability observed false

| Role | rootfs-layers-v1 (pin) | Config ID |
|---|---|---|
| simcloud | `sha256:228d665e3aa513402517f5469f6f9b59a1fd376a950e3253c6be2e8a161418d5` | `sha256:fa556e7fa202e7e37ff6f14087ec1c04918fcd6431db5f8715927860d09417b4` |
| verifier | `sha256:56d184f54a0edce7d09704a8758fcbf251b430f30005a702273987d1fea187cd` | `sha256:7cca3e79cd8a5ee94dde580f6a7ebac7ec4839ec903393a9dbb7a76ab99b0c00` |

### stop-report-connection-leak

- Task digest: `9a8f83afb82d862a9796292f72cb136c8343302e2675822f0a13b89851d391ea`
- Gates: oracle 10/10, nop 3/3, independent 1/1, wrong 4 (bigger_pool, close_shared_pool, restart_only, swallow_cancel); 9 outcome checks
- Isolation: 6 surfaces probed, every forbidden capability observed false

| Role | rootfs-layers-v1 (pin) | Config ID |
|---|---|---|
| simcloud | `sha256:3ffb6b66e420a969e710a3c1ce9c687089587d3f5b2bf74956a81045d30db5f0` | `sha256:0595804d0b857bcc6a0cb95d928703615e99b96d6838b8199b5b8eea1b19ed72` |
| verifier | `sha256:217e998be7ec329bfdb13e445e2bcf5e8918251fe65bf9200fb20ecc69b4cf29` | `sha256:a19dca79841575022c13a312ed68a278606f1662906b81daa8b911fffc2ae80b` |

## Re-pinned after M1

`stop-double-charges`: M1's probe ran in the long-lived M1 checkout (`isolation-m1.json`, verifier
`sha256:60919ef4…`). Built from a fresh checkout of the same commit, that verifier comes out as
`sha256:ee5de23f…`, the identity a fresh checkout also produced at d19ac29. The two differ only in
cached layers (the `COPY . /tests/` layer, and before cleanup the shared `pip install` layer);
files, modes and owners are identical. Docker's build cache held several records for those steps,
one of them left by an earlier `--no-cache` reproduction build, which was removed. The task was
re-probed on the same commit, and every surface passed again. `isolation.json` is that re-probe,
and the manifest pins it. From a fresh checkout the runner's preflight then rebuilt all eight
pinned images and matched every pin, twice. The gates are unaffected, since they bind to the base
images.

Because an identity can differ between checkouts, the probe refresh before the manifest is frozen
runs in the checkout the reporting cohort runs from, and its receipts set the final pins.
