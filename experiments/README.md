# Experiment manifests

A manifest pins everything a cohort's episodes share and is validated before any model call
(`fsbench/experiment.py`). A reporting manifest is frozen, by commit, before its first episode.

## reporting-v1 (draft)

`reporting-v1.draft.json` is the M3 draft of the paper's reporting cohort (`docs/PAPER_PLAN.md`):
4 tasks x 7 tracks x 3 seeds = 84 episodes, guard on in every cell, replacement at most once
per episode. It validates and plans today, but it is a draft until its `pending` block is
empty; the file is renamed to `reporting-v1.json` in the commit that empties it, and that commit
is the frozen manifest. Nobody runs the draft.

What the draft fixes now:

- the frozen pins: Rusty `32cac02c…`, mini-swe-agent 2.4.6 with the `mswea-compact.yaml` digest,
  the evaluator commit `6452a07c…`, the task digests from that commit's tree;
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
- counterbalanced ordering with a fixed seed and key slots balanced across tracks (imbalance 0).

What `pending` lists, and who supplies it: the owner approves `envelope.calls`; Lane A supplies
the model revision, wall-clock envelope, runtime block, Rusty binary digest and version string;
M1 supplies the base image IDs and re-checks the task digests against the receipts. The
`pending` block is documentation for the people filling it; the validator does not read it.

Shell cell reachability (checked before this draft, per `docs/PAPER_PLAN.md`): every reference
and independent solution of the four tasks reaches SimCloud through `sc`, the REST API or
`psql`; none uses an MCP tool. A `rusty-shell` failure is therefore a solver failure unless its
trace shows an operation the shell interface cannot reach.
