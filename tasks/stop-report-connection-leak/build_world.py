"""Generate this task's world (seed data) and the verifier's expected values, deterministically.

    uv run python tasks/stop-report-connection-leak/build_world.py

Writes environment/simcloud/seed.yaml, environment/simcloud/expected_public.json and
tests/expected.json. Not copied into any image.
"""

import json
import random
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
rng = random.Random(20261010)
CUSTOMERS = 300
HEAVY = ["c-0007", "c-0042", "c-0113", "c-0188", "c-0251"]  # customers with long histories: the report targets
POOL_MAX = 8

migration = (HERE / "environment/repo/migrations/001_init.sql").read_text()
plans = ["starter", "team", "enterprise"]
customers = [f"INSERT INTO customers (id, name, plan, open_tickets) VALUES ('c-{i:04d}', 'Customer {i}', '{plans[i % 3]}', {rng.randint(0, 6)})"
             for i in range(1, CUSTOMERS + 1)]
# ordinary customers: a few tickets with a handful of events each; heavy customers: 40 tickets x 25 events
sql = [migration, *customers,
       "INSERT INTO tickets (id, customer_id, subject, opened_at) "
       "SELECT 't-' || lpad(g::text, 6, '0'), 'c-' || lpad(((g - 1) % 300 + 1)::text, 4, '0'), 'Ticket ' || g, "
       "now() - (g % 90) * interval '1 day' FROM generate_series(1, 1500) g",
       "INSERT INTO ticket_events (ticket_id, kind, note, at) "
       "SELECT t.id, (array['opened','reply','note','closed'])[1 + (e % 4)], 'event ' || e, t.opened_at + e * interval '2 hours' "
       "FROM tickets t, generate_series(1, 4) e"]
for n, cid in enumerate(HEAVY):
    base = 100000 + n * 100
    sql.append("INSERT INTO tickets (id, customer_id, subject, opened_at) "
               f"SELECT 't-' || g, '{cid}', 'Escalation ' || g, now() - (g % 28) * interval '1 day' "
               f"FROM generate_series({base}, {base + 39}) g")
    sql.append("INSERT INTO ticket_events (ticket_id, kind, note, at) "
               "SELECT t.id, (array['opened','reply','note','escalated','closed'])[1 + (e % 5)], 'event ' || e, "
               f"t.opened_at + e * interval '20 minutes' FROM tickets t, generate_series(1, 25) e WHERE t.customer_id = '{cid}'")
staging = sql  # staging mirrors production so the mobile pattern can be rehearsed there

service = {
    "command": ["python", "-m", "supportapi"],
    "databases": {"DATABASE_URL": "support"},
    "env": {"SUPPORT_POOL_MAX": str(POOL_MAX)},
    "min_instances": 2,
    "readiness": {"path": "/healthz", "interval_seconds": 1, "failure_threshold": 3},
    "drain_seconds": 10,
    "rollout_timeout_seconds": 60,
}


def runtime_policy(env):
    return {"statements": [{"effect": "allow", "actions": ["database:connect"],
                            "resources": [f"srn:simcloud:support:{env}:database/support"]}]}


seed = {
    "projects": [{"name": "support", "environments": ["staging", "prod"], "regions": ["region-a"]}],
    "resources": [
        {"project": "support", "kind": "policy", "name": "platform-engineer",
         "spec": {"statements": [{"effect": "allow", "actions": ["*"],
                                  "resources": ["srn:simcloud:support", "srn:simcloud:support:*"]}]}},
        {"project": "support", "kind": "binding", "name": "oncall",
         "spec": {"principal": "user:oncall", "policies": ["platform-engineer"]}},
        *[r for env in ("staging", "prod") for r in [
            {"project": "support", "kind": "service_account", "name": f"support-api-{env}", "spec": {}},
            {"project": "support", "kind": "policy", "name": f"support-api-{env}-runtime", "spec": runtime_policy(env)},
            {"project": "support", "kind": "binding", "name": f"support-api-{env}",
             "spec": {"principal": f"service-account:support-api-{env}", "policies": [f"support-api-{env}-runtime"]}},
            {"project": "support", "env": env, "kind": "database", "name": "support", "spec": {"pitr_days": 7}},
            {"project": "support", "env": env, "kind": "service", "name": "support-api",
             "spec": {**service, "service_account": f"support-api-{env}"}},
        ]],
    ],
    "sql": [
        {"project": "support", "env": "prod", "database": "support", "statements": sql},
        {"project": "support", "env": "staging", "database": "support", "statements": staging},
    ],
    "deployments": [
        {"project": "support", "env": "staging", "service": "support-api", "source": "/seed/support-api-current"},
        {"project": "support", "env": "prod", "service": "support-api", "source": "/seed/support-api-current"},
    ],
    "guard": {
        "protected_envs": ["prod"],
        "checks": [
            {"name": "customer-page", "service": "support/prod/support-api", "path": "/customers/c-0001",
             "expect_status": [200], "body_contains": "c-0001", "timeout_ms": 1500, "every_seconds": 2,
             "failure_threshold": 3, "severity": "SEV1"},
        ],
    },
    "principals": [{"name": "user:oncall", "project": "support", "token_file": "/shared/oncall.token"}],
}

# what the collector needs after handoff and what the verifier expects
public = {"heavy": HEAVY, "storm": {"reports": 24, "concurrency": 4, "deadline_ms": 400, "days": 30},
          "foreground": {"requests": 20, "slo_ms": 800, "customers": [f"c-{i:04d}" for i in range(1, 21)]},
          "isolation": {"long": {"customer": HEAVY[0], "days": 30, "deadline_ms": 15000},
                        "short": {"customer": HEAVY[1], "days": 30, "deadline_ms": 400}},
          "settle_s": 3}
expected = {**public, "pool_max": POOL_MAX, "idle_in_transaction_max": 0}

out = HERE / "environment/simcloud"
(out / "seed.yaml").write_text("# Generated by build_world.py; do not edit.\n" + yaml.safe_dump(seed, sort_keys=False, width=200))
(out / "expected_public.json").write_text(json.dumps(public, indent=2) + "\n")
(HERE / "tests/expected.json").write_text(json.dumps(expected, indent=2) + "\n")
print(f"{CUSTOMERS} customers, {len(HEAVY)} heavy, pool max {POOL_MAX}, storm {public['storm']}")
