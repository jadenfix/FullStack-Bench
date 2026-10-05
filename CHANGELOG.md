# Changelog

## 2026-10-04: Declarative stacks (plan/apply/drift/import) and the `sc` CLI

- What:
  - `stacks.py`: `simcloud.yaml` documents (an `iam:` section for project-level kinds, `environments:` for the rest).
  - Server-side commands:
    - `plan`: a field-level diff with a plan hash
    - `apply`: dependency-ordered; `--plan-hash` refuses a stale plan; a partial apply keeps what succeeded
    - `drift`: what was modified or deleted outside the stack
    - `import`
  - Stack state records exactly which resources a stack manages. Resources outside every stack are never deleted.
  - `cli.py`: `sc` with whoami, kinds, get, put (`--if-match`), delete, plan, apply, drift, import and audit. Exit codes: 0 success, 1 API error, 2 usage.
- Why:
  - Drift and "someone hand-edited prod" are core fault types for the Ship-to-prod and IaC areas.
  - Planning on the server keeps the CLI, the API and the coming MCP server identical, and every underlying write is authorised and audited.
- Tradeoffs:
  - Apply isn't transactional across resources, like real IaC tools: a mid-apply denial leaves earlier changes applied and recorded in state.
  - The OpenTofu provider comes later and will drive the same endpoints.

## 2026-10-04: SimCloud core, REST API and seed files

- What:
  - `core.py`: every operation authorises, audits (allowed and denied), validates the spec, checks quotas and checks project and environment scope.
  - `api.py`: REST v1 with ETag / If-Match optimistic concurrency, a `/v1/kinds` endpoint publishing JSON schemas, the audit log, and operator-only `/admin` endpoints.
  - `server.py`: runs the control plane from env vars and applies a task's seed file (projects, resources, principals). Each token is written to a mode-600 file.
- Why:
  - The CLI and MCP server will sit on the same core, so all three behave identically and every call is audited.
  - Seeds let each task define its starting world declaratively.
- Tradeoffs: project-level kinds share the resource path through the `_` environment rather than having their own routes. That keeps one URL shape, at the cost of a slightly odd path.

## 2026-10-04: SimCloud identity: kinds, tokens and policy evaluation

- What:
  - `kinds.py`: 22 resource kinds with pydantic spec schemas, and their verbs, which form the action catalogue.
  - `identity.py`: tokens with a public id and a hash-only secret; expiry and revocation; an admin token reserved for the verifier.
  - `policy.py`: a policy evaluator. Deny by default, explicit deny wins, glob patterns, conditions on context and token claims.
  - Propagation delay: changes take effect `propagation_seconds` after they are written, revocations included.
  - `effective_permissions` for grading least privilege.
- Why:
  - The least-privilege and federated-CI taste entries are graded from what the evaluator actually allows, not from how the policy text looks.
  - IAM propagation delay is a real failure mode agents should handle (retry, wait).
- Tradeoffs:
  - The condition language is small (string_equals, string_not_equals, string_like, bool).
  - A missing context key fails the condition, which is safer and simpler than real clouds' per-operator rules. The skill docs state this.

## 2026-10-04: SimCloud scaffold: state store and audit log

- What:
  - The `simcloud` package (Python 3.12, uv) and the `sc` / `simcloud` entry points.
  - A controllable clock.
  - The error catalogue.
  - A SQLite store for resources: versioned, with optimistic concurrency.
  - A hash-chained, append-only audit log.
  - CI runs pytest.
- Why: every later capability (policies, federation, delivery, faults) reads
  the clock and writes state and audit records. The verifier grades from the
  audit log, so tampering must be detectable: a changed or deleted record
  breaks the chain.
- Tradeoffs:
  - SQLite keeps the control plane to one process and one file, which is
    simple to reset per trial. It doesn't support multiple writers, which a
    single control plane per trial doesn't need.
  - The store isn't the security boundary. The control-plane container is,
    because agents only reach the API.

## 2026-10-04: Model smoke test and default author

- What: `scripts/smoke_models.py` makes one bounded call per candidate model
  (120 s timeout, no retries; keys read from `.env`). The prompt is a tiny
  structured-output instruction with a known answer.
- Result:
  - `z-ai/glm-5.3`: correct JSON, about 44 s. Now the default author.
  - `nvidia/nemotron-3-ultra-550b-a55b`: correct JSON, about 6 s. Second author and QA family.
  - `moonshotai/kimi-k3`: empty content twice (finish `stop`, about 33 reasoning tokens).
  - `deepseek-ai/deepseek-v4.1-flash`: timed out twice.
- Tradeoffs: the determinacy panel needs three model families, and only two
  work today. Kimi-K3 and DeepSeek are re-checked before each batch rather
  than worked around.

## 2026-10-04: Bootstrap

- What: repo skeleton. `.gitignore` (secrets, run state, private split),
  `.env.example`, `AGENTS.md` / `CLAUDE.md` rules, and the design in
  `docs/PLAN.md`.
- Why: the benchmark needs its rules in place before any paid run. The repo
  is public, so secrets and the private held-out split are excluded from the
  first commit onward.
- Tradeoffs:
  - SimCloud, a simulated provider-neutral platform, is used instead of real
    vendor clouds or emulators. It tests judgement and engineering rather than
    vendor-API recall, and it resists contamination. The cost is the
    engineering to build it, plus a transfer risk that a later study measures.
