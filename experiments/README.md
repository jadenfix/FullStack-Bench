# Experiment manifests

A manifest pins everything a cohort's episodes share and is validated before any model call
(`fsbench/experiment.py`). A reporting manifest is frozen, by commit, before its first episode.

## reporting-v1 (frozen)

`reporting-v1.json` is the paper's reporting cohort (`docs/PAPER_PLAN.md`): 4 tasks x 7 tracks
x 3 seeds = 84 episodes, guard on in every cell, replacement at most once per episode. It was
the M3 draft `reporting-v1.draft.json` until its `pending` block was empty; the commit that
renamed it is the frozen manifest. Any change is a new manifest and a new plan, not an edit.

What it fixes:

- the frozen pins: Rusty `32cac02c…`, mini-swe-agent 2.4.6 with the `mswea-compact.yaml` digest,
  the evaluator commit `55d185f5…`, the task digests from that commit's tree;
- the images: `base_images` and each task's `isolation_images` by rootfs-layers-v1 identity, as
  `receipts/` records them (M1, then the probe refresh in the run checkout);
- the tasks and their generalization labels: ship-checkout-v2 and stop-double-charges are
  `familiar_family` (development ran on them); merge-duplicate-contacts and
  stop-report-connection-leak are `new_mechanism` (held out from development and from the Rusty
  session);
- every task's predeclared challenges and the post-handoff observation requirement;
- the public check: each brief names `public-check`, so every track reads the same brief
  (`public_check_source: brief`, scope `partial` in all four);
- the seven tracks and the preregistered comparisons: verify-vs-baseline and
  careful-vs-baseline are primary (SafeSuccess); mini-verify-vs-mini is the preregistered
  transfer comparison; the rest are exploratory;
- counterbalanced ordering with a fixed seed and key slots balanced across tracks (imbalance 0);
- the wall-clock envelope equal to every task's agent timeout (7200 s), so the gateway and Harbor
  end an episode at the same point, and `cache: warm`, which is what the runner's shared Docker
  layer cache does (it refuses `cold`).

Who filled the draft's `pending` rows: the owner approved the envelope (300 calls, 12M input /
500k output tokens, 7200 s); Lane A supplied the model revision ("unknown": the endpoint exposes
none), the runtime block, and the Rusty binary digest and version; M1 supplied the image pins
and re-checked the task digests against the receipts.

Task digests are computed on a clean checkout of the evaluator commit (`git worktree add --detach
<dir> 55d185f5…`, then `fsbench.runner.task_checksum`). A working tree that has run tasks
in-process carries files the digest sees, and the runner's preflight then refuses every task.

Shell cell reachability (checked before this draft, per `docs/PAPER_PLAN.md`): every reference
and independent solution of the four tasks reaches SimCloud through `sc`, the REST API or
`psql`; none uses an MCP tool. A `rusty-shell` failure is therefore a solver failure unless its
trace shows an operation the shell interface cannot reach.
