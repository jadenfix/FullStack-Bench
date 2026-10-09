# support-api

Customer pages and activity reports for the support desk. Python stdlib HTTP server + Postgres.

    python -m supportapi            # needs the database connection in the environment

## Endpoints

- `GET /healthz`: liveness only (no database).
- `GET /customers/<id>`: the customer page's data; 503 when no pooled connection is free.
- `GET /reports/activity?customer=<id>&days=<n>`: ticket events as JSON lines, one per row, with a
  trailing `{"done": true, "rows": n}` line. The client may send `X-Deadline-Ms`; the report stops
  with 504 once the deadline passes.
- `GET /admin/pool`: pool size, permits in use and who holds them (operators only; the load
  balancer does not expose it to the public).

## Pool

`SUPPORT_POOL_MAX` permits (8 in production; the managed Postgres budget is shared with billing).
A request waits `SUPPORT_POOL_WAIT_S` for a permit, then fails fast. See `docs/INCIDENT-2026-10-03.md`.
