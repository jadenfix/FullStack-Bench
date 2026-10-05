"""Generate this task's world (seed data) and the verifier's expected values, deterministically.

    uv run python tasks/stop-double-charges/build_world.py

Writes environment/simcloud/seed.yaml, environment/simcloud/simsaas.yaml and tests/expected.json.
Not copied into any image.
"""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
rng = random.Random(20261005)
T0 = datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc)
SIMSAAS_ADMIN = "pay-op-51b0e7c2a9d4"
PROD_KEY = "tp_live_shop_8c41f0e2b7d9a35e"
STAGING_KEY = "tp_test_shop_2f7a9c1e04b8d6a3"

orders, charges = [], []
n = [1000]


def oid():
    n[0] += 1
    return f"o-{n[0]}"


def add(cart, customer, amount, at, status="paid", charged=True, refunded_at_provider=0, request_id=None):
    o = {"id": oid(), "cart_id": cart, "customer_id": customer, "amount_cents": amount, "status": status,
         "charge_id": None, "request_id": request_id or f"{rng.getrandbits(64):016x}", "created_at": at.isoformat()}
    if charged:
        ch = f"ch_{rng.getrandbits(48):012x}"
        o["charge_id"] = ch
        charges.append({"account": "shop-live", "id": ch, "amount": amount, "currency": "usd",
                        "metadata": {"order_id": o["id"], "cart_id": cart}, "created": int(at.timestamp()),
                        "amount_refunded": refunded_at_provider})
    orders.append(o)
    return o


# 50 normal orders
for i in range(50):
    add(f"cart-{1100 + i}", f"c-{101 + (i * 7) % 40}", rng.choice([1299, 2450, 4599, 899, 15900, 3200]),
        T0 + timedelta(minutes=37 * i))

duplicates = []  # (kept order, [extra orders])
# 6 double-charged carts: the client retried ~4-9 s later with a new request id
for i in range(6):
    at = T0 + timedelta(hours=3, minutes=53 * i)
    cart, cust, amt = f"cart-{2201 + i}", f"c-{150 + i}", rng.choice([2450, 4599, 7800, 12900])
    first = add(cart, cust, amt, at)
    dup = add(cart, cust, amt, at + timedelta(seconds=rng.randint(4, 9)))
    duplicates.append((first, [dup]))
# 1 triple-charged cart
at = T0 + timedelta(hours=9)
first = add("cart-2301", "c-171", 5600, at)
dups = [add("cart-2301", "c-171", 5600, at + timedelta(seconds=5)),
        add("cart-2301", "c-171", 5600, at + timedelta(seconds=11))]
duplicates.append((first, dups))
# a double charge that support already refunded in the payments dashboard (DB doesn't know yet)
at = T0 + timedelta(hours=11)
first = add("cart-2401", "c-180", 3300, at)
already = add("cart-2401", "c-180", 3300, at + timedelta(seconds=6), refunded_at_provider=3300)
duplicates.append((first, [already]))
# a retry whose first attempt failed without charging: not a double charge
at = T0 + timedelta(hours=12)
add("cart-2501", "c-181", 9900, at, status="failed", charged=False)
add("cart-2501", "c-181", 9900, at + timedelta(seconds=7))
# a customer who legitimately bought the same thing twice on different days
add("cart-3001", "c-117", 4599, T0 + timedelta(days=1, hours=2))
add("cart-3002", "c-117", 4599, T0 + timedelta(days=2, hours=5))

orders.sort(key=lambda o: o["created_at"])


def sql_literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


migrations = [(HERE / "environment/repo/migrations" / f).read_text() for f in
              ("001_init.sql", "002_refunds_and_events.sql")]
inserts = []
for o in orders:
    cols = ["id", "cart_id", "customer_id", "amount_cents", "status", "charge_id", "request_id", "created_at"]
    inserts.append(f"INSERT INTO orders ({', '.join(cols)}) VALUES ({', '.join(sql_literal(o[c]) for c in cols)})")
    inserts.append(f"INSERT INTO order_events (order_id, kind, detail, at) VALUES "
                   f"({sql_literal(o['id'])}, 'created', {sql_literal(o['request_id'])}, {sql_literal(o['created_at'])})")

staging_inserts = [i for i in inserts[:20]]  # a slice of realistic data for staging (charges unknown to staging's Tillpoint)

expected = {
    "to_refund": sorted(d["charge_id"] for _, ds in duplicates for d in ds if not d is already),
    "already_refunded": [already["charge_id"]],
    "duplicate_orders": sorted(d["id"] for _, ds in duplicates for d in ds),
    "kept_orders": sorted(f["id"] for f, _ in duplicates),
    "must_not_refund": sorted(o["charge_id"] for o in orders if o["charge_id"] and
                              o["id"] not in {d["id"] for _, ds in duplicates for d in ds}),
    "order_count": len(orders),
    "refund_total_cents": sum(d["amount_cents"] for _, ds in duplicates for d in ds if d is not already),
    "health_order": orders[0]["id"],
    "seeded_statuses": {o["id"]: o["status"] for o in orders},
}

service = {
    "command": ["python", "-m", "ordersvc"],
    "databases": {"DATABASE_URL": "orders"},
    "secrets": {"TILLPOINT_API_KEY": "tillpoint-api-key"},
    "env": {"TILLPOINT_URL": "http://simsaas:7700"},
    "min_instances": 2,
    "readiness": {"path": "/healthz", "interval_seconds": 1, "failure_threshold": 3},
    "drain_seconds": 10,
    "rollout_timeout_seconds": 60,
}


def runtime_policy(env):
    return {"statements": [
        {"effect": "allow", "actions": ["database:connect"], "resources": [f"srn:simcloud:shop:{env}:database/orders"]},
        {"effect": "allow", "actions": ["secret:access"],
         "resources": [f"srn:simcloud:shop:{env}:secret/tillpoint-api-key"]}]}


seed = {
    "projects": [{"name": "shop", "environments": ["staging", "prod"], "regions": ["region-a"]}],
    "resources": [
        {"project": "shop", "kind": "policy", "name": "platform-engineer",
         "spec": {"statements": [{"effect": "allow", "actions": ["*"],
                                  "resources": ["srn:simcloud:shop", "srn:simcloud:shop:*"]}]}},
        {"project": "shop", "kind": "binding", "name": "oncall",
         "spec": {"principal": "user:oncall", "policies": ["platform-engineer"]}},
        *[r for env in ("staging", "prod") for r in [
            {"project": "shop", "kind": "service_account", "name": f"orders-{env}", "spec": {}},
            {"project": "shop", "kind": "policy", "name": f"orders-{env}-runtime", "spec": runtime_policy(env)},
            {"project": "shop", "kind": "binding", "name": f"orders-{env}",
             "spec": {"principal": f"service-account:orders-{env}", "policies": [f"orders-{env}-runtime"]}},
            {"project": "shop", "env": env, "kind": "database", "name": "orders", "spec": {"pitr_days": 7}},
            {"project": "shop", "env": env, "kind": "secret", "name": "tillpoint-api-key", "spec": {}},
            {"project": "shop", "env": env, "kind": "service", "name": "orders",
             "spec": {**service, "service_account": f"orders-{env}"}},
        ]],
    ],
    "sql": [
        {"project": "shop", "env": "prod", "database": "orders", "statements": migrations + inserts},
        {"project": "shop", "env": "staging", "database": "orders", "statements": migrations + staging_inserts},
    ],
    "secret_values": [
        {"project": "shop", "env": "prod", "name": "tillpoint-api-key", "value": PROD_KEY},
        {"project": "shop", "env": "staging", "name": "tillpoint-api-key", "value": STAGING_KEY},
    ],
    "deployments": [
        {"project": "shop", "env": "staging", "service": "orders", "source": "/seed/orders-current"},
        {"project": "shop", "env": "prod", "service": "orders", "source": "/seed/orders-current"},
    ],
    "guard": {
        "protected_envs": ["prod"],
        "checks": [{"name": "order-lookup", "service": "shop/prod/orders", "path": f"/orders/{expected['health_order']}",
                    "expect_status": [200], "body_contains": expected["health_order"], "timeout_ms": 1500,
                    "every_seconds": 2, "failure_threshold": 3, "severity": "SEV1"}],
    },
    "principals": [{"name": "user:oncall", "project": "shop", "token_file": "/shared/oncall.token"}],
}

simsaas = {"payments": {"port": 7700,
                        "accounts": [{"account": "shop-live", "api_key": PROD_KEY},
                                     {"account": "shop-test", "api_key": STAGING_KEY}],
                        "charges": charges}}

out = HERE / "environment/simcloud"
(out / "seed.yaml").write_text("# Generated by build_world.py; do not edit.\n" + yaml.safe_dump(seed, sort_keys=False,
                                                                                                  width=200))
(out / "simsaas.yaml").write_text("# Generated by build_world.py; do not edit.\n" + yaml.safe_dump(simsaas, sort_keys=False))
(HERE / "tests/expected.json").write_text(json.dumps({**expected, "simsaas_admin": SIMSAAS_ADMIN}, indent=2) + "\n")
print(f"{len(orders)} orders, {len(charges)} charges, refund {len(expected['to_refund'])} charges "
      f"({expected['refund_total_cents']} cents), {len(expected['already_refunded'])} already refunded")
