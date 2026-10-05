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

```bash
uv sync
uv run pytest
```

## Open track: other agents

Reporting runs use the pinned mini-swe-agent scaffold. The open track takes any
agent. `fsbench/agents/rusty.py` runs [rusty](https://github.com/jadenfix/rusty)
inside the task container in goal mode:

```bash
docker build --target bin -o out /path/to/rusty     # static Linux binary
uv run harbor run -p tasks/ship-checkout-v2 -a fsbench.agents.rusty:Rusty \
  -m nvidia/nemotron-3-super-120b-a12b --env-file .env \
  --ak binary=out/rusty --ak max_turns=25 --job-name rusty-ship-checkout-001 -o jobs
```

Options go through `--ak`: `mode=goal|prompt`, `agents=off|sub|swarm|auto`
and `max_turns`. Its trajectory is in the same message format as
mini-swe-agent's, so `fsbench.digest` works on rusty trials unchanged.
