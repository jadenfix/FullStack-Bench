# FullStack-Bench

**Can an agent do end-to-end full-stack engineering without causing production outages or critical issues?**

FullStack-Bench gives agents real, long-horizon engineering work on a live system and grades the whole episode:
- **Success:** the work is done.
- **Do no harm:** production stayed up and nothing critical happened on the way. That means no destroyed data, no leaked secrets, no broad permissions, no disabled monitoring.

Tasks run on **SimCloud**, a simulated, provider-neutral cloud. Agents learn it from a built-in skill and use it through the `sc` CLI, a REST API or an MCP server. While the agent works, SimCloud keeps synthetic users on production and records every incident in a tamper-evident ledger.

- Design: [`docs/PLAN.md`](docs/PLAN.md)
- Decisions and tradeoffs: [`CHANGELOG.md`](CHANGELOG.md)
- Rules for contributors: [`AGENTS.md`](AGENTS.md)

## Status

Phase 1, in progress: SimCloud v0 and the first tasks. The first task, `tasks/ship-checkout-v2`, passes every gate under Harbor (oracle 1; nop and three unsafe shortcuts 0); run them with `uv run python scripts/gate_task.py tasks/ship-checkout-v2`. SimCloud done so far:
- identity and policies
- declarative stacks
- data plane
- workload identity federation
- runtime and load balancer
- delivery
- fault scenarios
- the incident ledger
- the MCP server
- the skill and its docs drift
- container images
- world seeds and verifier evidence

## Development

The `very-hard-tasks` work adds thirty-five curated authoring designs covering
SDK/MCP/CLI recovery, native memory ownership, offline product workflows,
database replication, infrastructure adoption, storage durability, refactoring
and performance. The sixteen new incident designs include operational triggers,
safe mitigations, protected state and explicit runtime capability requirements.
Six further designs combine ordered failures across shard relocation, metering,
Kubernetes upgrades, schema rollout, sharing graphs and multi-depot dispatch.
They require recovery on the same evolving system and real mixed-client journeys.
These are **design inputs, not runnable or qualified
Harbor tasks**. Inspect them with `uv run python -m fsbench.author --list-hard-cases`;
the generation and qualification protocol is in [`docs/PLAN.md`](docs/PLAN.md#curated-very-hard-task-designs).
The performance evidence checks require correct complete workloads, scaling and
memory budgets, and all scheduled live-traffic requests.
Observed difficulty screens require original complete paired trial receipts;
`fsbench.difficulty_evidence` keeps harness results separate and never equates
missing infrastructure or a small zero-success sample with inability to solve.

```bash
uv sync
uv run pytest
```

## Required evaluation: mini-SWE and Rusty CLI

Pilot candidates must run with both the pinned mini-SWE scaffold and Rusty CLI.
Results are reported separately; mini-SWE is the primary standardized track.
`fsbench/agents/rusty.py` runs [rusty](https://github.com/jadenfix/rusty)
inside the task container in goal mode:

```bash
docker build --target bin -o out /path/to/rusty     # static Linux binary
uv run harbor run -p tasks/ship-checkout-v2 -a fsbench.agents.rusty:Rusty \
  -m nvidia/nemotron-3-super-120b-a12b --env-file .env \
  --ak binary=out/rusty --ak max_turns=25 --job-name rusty-ship-checkout-001 -o jobs
```

Use `agents=off` for paired reporting. Pin the Rusty source revision and binary
hash alongside the lockfile and mini-SWE configuration. The full paired protocol
is in `docs/PLAN.md`.

Options go through `--ak`: `mode=goal|prompt`, `agents=off|sub|swarm|auto`,
`max_turns`, `execution=standard|careful|vibe`, and the shared model budget
`max_requests`, `max_budget_tokens` and `budget_secs` (set all three for a
paired cohort). Every `NVIDIA_API_KEY_N` in the env file is passed on for key
rotation. The trial metadata records the goal outcome, turn count, binary
SHA-256 and these options. Its trajectory is in the same message format as
mini-swe-agent's, so `fsbench.digest` works on rusty trials unchanged.
