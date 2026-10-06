---
name: tillpoint
description: Integrate with Tillpoint, the company's payments provider: charges, refunds, idempotency, and verifying and handling webhooks.
---

# Tillpoint payments

Authenticate with `Authorization: Bearer <api key>`. Keys are per account and live in the platform's secrets manager.

## Charges and refunds

- `POST /v1/charges` `{"amount": <int>, "currency": "usd", "metadata": {...}}`: `amount` is an **integer in minor units** (cents). Floats are rejected.
- `POST /v1/refunds` `{"charge": "ch_…", "amount": <int, optional>}`: without `amount` it refunds the remainder. A refund can't exceed what's left of the charge.
- `GET /v1/charges/<id>` and `GET /v1/charges?limit=N`.

## Payouts

- `POST /v1/payouts` `{"amount": <int>, "currency": "gbp", "destination": "<bank account id>", "metadata": {...}}` sends money out. `amount` is in minor units. It takes an `Idempotency-Key` like charges (see below), and a payout is final.
- `GET /v1/payouts?limit=&starting_after=<po_…>` lists payouts newest first, with `has_more`.
- Event: `payout.paid`.

## Idempotency

Send an `Idempotency-Key` header on every `POST` you might retry.
- Same key and same body: you get the original response again, with `idempotent-replayed: true`, and no second charge.
- Same key, different body: `422 idempotency_key_reused`.
- Same key while the first request is still processing: `409 idempotency_key_in_use`. Retry after a moment.
- A 5xx response doesn't consume the key, so retry with the same key.

## Webhooks

Tillpoint POSTs events (`charge.succeeded`, `charge.refunded`) to your registered endpoint, signed per the **Standard Webhooks** spec:
- Headers: `webhook-id`, `webhook-timestamp` (Unix seconds) and `webhook-signature` (`v1,<base64 signature>`; several space-separated signatures may appear during secret rotation).
- The signature is HMAC-SHA256 over `"<webhook-id>.<webhook-timestamp>.<raw request body>"`. The key is the base64-decoded part of your endpoint secret after `whsec_`.
- Verify against the **raw bytes** of the body, not re-serialised JSON. Compare in constant time, and reject timestamps far from now to stop replays.
- Any non-2xx response is retried with backoff (5 s, 25 s, 125 s, … up to 6 attempts) with the **same** `webhook-id`.
- Deliveries can arrive **more than once** and **out of order**. Deduplicate on `webhook-id`, and don't assume event order (use the object state in `data.object`).
