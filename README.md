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

Phase 1, in progress: SimCloud v0. Done so far:
- identity and policies
- declarative stacks
- data plane
- workload identity federation
- runtime and load balancer
- delivery
- fault scenarios

## Development

```bash
uv sync
uv run pytest
```
