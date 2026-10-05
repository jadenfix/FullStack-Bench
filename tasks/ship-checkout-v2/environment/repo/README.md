# shopsrv

The storefront backend: carts, quotes, checkout. One process, no framework.

## Running locally

    SIMCLOUD_ENV=dev python shopsrv.py      # listens on $PORT (default 8080)
    python -m unittest discover tests

## Endpoints

- `GET /checkout/quote?cart=<id>` returns the priced cart, signed for the payments partner
- `GET /healthz` readiness
- `GET /v0/price` legacy, kept for the mobile app (do not remove until the 3.x app is gone)

## Configuration

`conf/app.json`, overridden per environment by `conf/app.<env>.json`, then by
`SHOP__SECTION__KEY` environment variables.

The payments signing key is read from `PAYMENT_KEY` (see `legacy/old_checkout.py`).
Feature flags live in `conf/flags.json`.

## Pricing engines

`pricing.py` is the original engine. The new engine is `pricing_v2.py`.
