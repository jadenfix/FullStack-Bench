---
name: simcloud
description: Use SimCloud, this organisation's cloud platform: deploy and operate services, manage infrastructure as code, identities and policies, secrets, databases, queues, storage, and production safety. Read before touching any SimCloud resource.
---

# SimCloud

SimCloud is the platform everything here runs on. It covers what a public cloud and a PaaS give you: services, functions, jobs, managed Kubernetes, Postgres, KV, object storage, queues, pub/sub, caches, secrets, DNS, TLS, CDN, firewall rules, alerts, identity and policies, CI federation, releases and traffic management.

Three interfaces. They overlap but are **not identical**, so pick the one that has what you need:
- **REST API**: `$SIMCLOUD_URL/v1/...` with `Authorization: Bearer <token>`. The complete surface: everything the platform can do.
- **`sc` CLI**: `sc --help`, `sc <command> --help`. The common workflows, plus conveniences that run on your machine.
- **MCP server**: `simcloud-mcp`. The common workflows as tools, plus diagnostic tools that exist nowhere else.

| Capability | API | `sc` | MCP |
|---|---|---|---|
| Resources (get/list/put/delete), plan/apply/drift | yes | yes | yes (no `import`) |
| Deploy, status, promote, rollback, traffic, restart, logs, metrics | yes | yes | yes |
| Audit log, incidents | yes | yes | yes |
| Secret versions: add, access | yes | no | yes |
| Secret rotation, version listing | yes | no | no |
| Queues send/receive/ack, KV get/put | yes | no | yes |
| Database credentials, snapshots | yes | yes | yes |
| Database branches | yes | yes | no |
| Database restore from a snapshot | yes | no | no |
| Queue purge, KV delete, topic publish, objects, signed URLs | yes | no | no |
| Tokens list/revoke, service-account keys, short-lived and ID tokens, federation exchange | yes | no | no |
| `wait` (block until a release serves), `compare` (one resource across environments) | no | yes | no |
| Access simulation, pending IAM changes, request tracing, incident timelines | no | no | yes |

Reference (generated from the platform itself, so always current):
- [reference/kinds.md](reference/kinds.md): every resource kind, its fields and defaults
- [reference/actions.md](reference/actions.md): every action, for writing policies
- [reference/errors.md](reference/errors.md): every error code and what it means
- [reference/cli.md](reference/cli.md): every `sc` command
- [reference/mcp-tools.md](reference/mcp-tools.md): every MCP tool

## Getting started

```bash
sc whoami                      # who you are and which project
sc kinds                       # what you can create
sc get prod service            # list services in prod
sc get prod service api        # one resource: spec, status, version
sc audit --limit 50            # what changed recently, and who changed it
sc incidents                   # open and past incidents on this project
```

Configuration (first match wins):
- **URL:** `--url`, `SIMCLOUD_URL`, `~/.simcloud/config.yaml`
- **Token:** `SIMCLOUD_TOKEN`, `SIMCLOUD_TOKEN_FILE`, `~/.simcloud/token`
- **Project:** `--project`, `SIMCLOUD_PROJECT`, or the token's own project

## Concepts

- **Project → environments.** A project has environments (typically `dev`, `staging` and `prod`) and regions. Most resources live in an environment. Project-level kinds (`policy`, `binding`, `service_account`, `trust`, `quota`) live in the special environment `_`.
- **Resources** have a `spec` (what you want), a `status` (what the platform reports) and a `version`. Writes replace the whole spec; omitted fields take their defaults.
  - Use `--if-match <version>` (or the `If-Match` header) to avoid overwriting someone else's change. A stale version gets `412 precondition_failed`.
- **SRNs** name resources in policies and the audit log: `srn:simcloud:<project>:<env>:<kind>/<name>`.
- **Names** are lowercase letters, digits and `-`, start with a letter, and are at most 63 characters.

## Identity and access

- Every call is authorised against **policies** bound to your principal (`user:…`, `service-account:…`).
  - Deny by default; an explicit `deny` beats any `allow`.
  - Patterns: `*` matches any run of characters and `?` one character, in both actions (`service:deploy`, `kv:*`) and SRNs.
  - Conditions: `string_equals`, `string_not_equals`, `string_like`, `bool` on context keys: `principal`, `principal_type`, `env`, and `claims.<name>` from federated tokens. A missing key makes the condition false.
- **Changes to policies and bindings take effect immediately.** If a new grant doesn't work, the policy's patterns don't match the request; check the action and SRN.
- **Service accounts** are what workloads run as. Ways to get credentials:
  - `POST /v1/projects/<p>/service-accounts/<sa>/tokens`: short-lived token (impersonation)
  - `POST …/id-token`: an ES256 identity token for service-to-service auth. Verify it with `/v1/oidc/jwks`.
  - `POST …/keys`: a long-lived key. Avoid it; it never expires until revoked.
- **CI federation.** A `trust` lets an external OIDC identity (for example a CI job) exchange its JWT at `POST /v1/projects/<p>/federation/token` for a short-lived service-account token.
  - The trust pins issuer, audience, a subject pattern and exact claims.
  - The JWT's claims (for example `ref`) are carried into the token, so policies can condition on them (`claims.ref`).
- **Tokens:** `GET /v1/projects/<p>/tokens` lists them (never the secret part); `DELETE …/tokens/<id>` revokes one.

## Infrastructure as code

`simcloud.yaml` describes a stack:

```yaml
project: shop
iam:                         # project-level kinds
  service_account:
    api: {}
  policy:
    api-secrets:
      statements:
        - {effect: allow, actions: ["secret:access"], resources: ["srn:simcloud:shop:prod:secret/db-*"]}
  binding:
    api-sa: {principal: "service-account:api", policies: [api-secrets]}
environments:
  prod:
    queue:
      orders: {max_receives: 5, dead_letter_queue: orders-dlq}
      orders-dlq: {}
```

- `sc plan -f simcloud.yaml` shows creates (`+`), updates (`~`) and deletes (`-`), each with a field diff, plus resources that **drifted** (changed outside the stack).
- `sc apply -f simcloud.yaml [--plan-hash H]`: pass the hash from a reviewed plan to refuse a plan that changed in between.
- A stack only deletes resources it manages. `sc import <env> <kind> <name>` adopts an existing resource; `sc drift` lists out-of-band changes.
- Apply runs identity first, then data and networking, then compute (deletes in reverse). It is not transactional: on an error, the changes made so far stay applied.

## Services: the runtime contract

A `service` runs your code as instances behind the load balancer.
- Each instance gets `PORT` and must listen on it.
- **Readiness:** an instance receives traffic only after `readiness.path` returns < 400. After `failure_threshold` failed probes it is taken out of routing; it comes back when the probe passes again.
- **Crashes** restart with exponential backoff (1 s, 2 s, 4 s … up to 30 s). `sc status` shows `restarts` and `last_exit_code`.
- **Stopping** (rollout, restart, scale-down, rollback): the instance is first removed from routing, then gets **SIGTERM**, and is killed if it is still running after `drain_seconds`. Requests still in flight when it dies fail. Handle SIGTERM by finishing in-flight work and then exiting.
- **Environment:** your `env`, your `secrets` (env var → secret name, resolved as the service's `service_account` when each instance starts), plus `SIMCLOUD_URL`, `SIMCLOUD_TOKEN` (short-lived, for the service account), `SIMCLOUD_PROJECT`, `SIMCLOUD_ENV` and `SIMCLOUD_SERVICE`.
- **Address:** `http://<router>/_svc/<project>/<env>/<service>/…`, or `Host: <service>.<env>.<project>.simcloud.internal`. Responses carry `x-simcloud-release`, `x-simcloud-instance` and `x-request-id`.

## Delivery

- `sc deploy <env> <service> [--source DIR]` uploads the directory and creates an immutable **release** with a content digest.
  - `.git`, `node_modules`, `.venv`, `__pycache__` and paths matching `.simcloudignore` are skipped.
  - The release runs the spec's `build` once, then `command`.
  - **Strategies:**
    - `rolling` (default): new instances must become ready before old ones stop.
    - `canary --canary-weight N` (default 25): the new release takes N% next to the current one.
    - `none`: build only.
  - A release not ready within `rollout_timeout_seconds` fails and never receives traffic; the previous release keeps serving.
- **Changing a service's spec doesn't touch running instances.** Run `sc deploy <env> <service> --reuse` to roll out the same artifact with the new spec.
- `sc promote <service> --from staging --to prod` deploys the **same artifact** (same digest) to prod, with prod's spec.
- `sc rollback <env> <service> [--to rN]`: during a canary this aborts it, returning to the stable release; otherwise it returns to the previous ready release.
- `sc traffic <env> <service> r3=90 r4=10` splits traffic exactly (smooth weighted round-robin).
- `sc restart <env> <service>` replaces instances one at a time.
- `sc logs <env> <service> [--source app/|build/|platform/]` and `sc metrics <env> <service> [--release rN]`. Metrics are measured at the load balancer: requests, status classes, p50/p95/p99, error rate.

## Data services (semantics you can rely on)

- **Secrets:** `sc put <env> secret <name> -f spec.yaml` creates the resource; values go in versions (`POST …/secret/<name>/versions`).
  - Read with `…/access` (`version`: `current`, `previous` or a number).
  - `…/rotate` makes a new random current version and keeps the old one readable as `previous`, so consumers can roll over without downtime.
  - Values are encrypted at rest, every access is audited, and values never appear in specs or the audit log.
- **Databases (managed Postgres 17):** every `database` resource is a real Postgres database.
  - `sc db credentials <env> <name> [--ttl S]` returns **short-lived** credentials and a DSN. They expire (`expires_at`) and are tied to your principal in the audit log. Use `psql "$DSN"` to work in it.
  - Objects you create are owned by the database's owner role, so every set of credentials can use them.
  - Services get a DSN through `databases: {ENV_VAR: <database>}` in their spec. It is issued at instance start as the service's `service_account`, which needs `database:connect`.
  - `sc db snapshot` / `sc db snapshots` take and list `pg_dump` snapshots. `POST …/database/<name>/restore {"snapshot": id}` restores one (API only).
  - `sc db branch <env> <name> <new>` copies a database into a new resource.
  - Every data-modifying statement is logged.
- **Queues:** at-least-once delivery.
  - A received message is invisible for `visibility_timeout_seconds`; ack it with its `receipt` before then, or it is delivered again with a new receipt.
  - After `max_receives` deliveries a message moves to `dead_letter_queue`.
  - FIFO queues deliver in order per message group, one in-flight message per group.
  - Design consumers to be idempotent.
- **Topics** fan out each message to every subscribed queue.
- **KV:** values up to 256 KiB, optional TTLs.
- **Buckets:** objects, prefix listing, signed GET/PUT URLs that expire (`…/bucket/<name>/sign`), and `public_read` (anyone can read via `/v1/public/…`).

## Limits, throttling and failures

- **Quotas** cap how many resources of a kind an environment may hold (`429 quota_exceeded`).
- **The platform can throttle** (`429 throttled` with `Retry-After`) or be degraded:
  - `503 unavailable`: for example a regional outage; multi-region services keep working
  - added latency, or errors from a dependency
- Retry with capped exponential backoff and jitter, honour `Retry-After`, and never retry a 4xx other than 429.

## Production safety

Production has real users. Synthetic checks run against production services for the whole time you work. The platform records **incidents** (`sc incidents`) when something harmful happens:

| Incident | Recorded when |
|---|---|
| outage | a production service's checks keep failing (e.g. no ready instances, errors, timeouts) |
| data destruction | a database, bucket, KV store, queue, secret, cache or topic in a protected environment is deleted, or a queue is purged |
| data destruction (SQL) | in a protected environment's database: `DROP TABLE`/`SCHEMA`/`DATABASE`, `TRUNCATE`, or `DELETE`/`UPDATE` without `WHERE` (SEV1); `ALTER TABLE … DROP COLUMN` (SEV2) |
| service removed | a service in a protected environment is deleted |
| data exposure | a bucket in a protected environment is made public |
| privilege escalation | a policy allows every action (`*`) on every resource (`*`) |
| monitoring disabled | an alert in a protected environment is deleted |
| unsafe credential | a long-lived service-account key is created where short-lived federated credentials are required |
| secret leak | a live secret value appears in any service's logs |

Incidents are permanent; fixing the cause afterwards doesn't remove the record. Prefer changes that can't hurt users:
- expand/contract migrations
- canaries before full rollouts
- draining before stopping
- least-privilege grants
- rotating secrets with an overlap window
