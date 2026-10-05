# orders

Checkout and order lookup for the shop. Python stdlib HTTP server + Postgres.

    python -m ordersvc            # needs DATABASE_URL and the payments key in the environment

## Checkout

`POST /checkout {"cart_id", "customer_id", "amount_cents"}` creates the order, charges it with the
payments provider and returns `{"order_id", "charge_id", "status"}`.

Retries are safe: the gateway reuses the request id when a client retries, and we pass it to the
payments provider as the idempotency key.

## Data

`orders` (one row per checkout) and `order_events` (audit trail). Migrations in `migrations/`.
`refunded_cents` is synced nightly from the payments provider.
