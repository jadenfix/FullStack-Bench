"""Generate this task's world (seed data) and the verifier's expected values, deterministically.

    uv run python tasks/merge-duplicate-contacts/build_world.py

Writes environment/simcloud/seed.yaml, environment/repo/fixtures/partner-batch.csv and
tests/expected.json. Not copied into any image.
"""

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
rng = random.Random(20261009)
T0 = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
TENANTS = ["t-acme", "t-borealis", "t-cobalt", "t-delta"]
DOMAINS = {"t-acme": "acme-example.com", "t-borealis": "borealis-example.net", "t-cobalt": "cobalt-example.org",
           "t-delta": "delta-example.co"}
FIRST = ["priya", "jonas", "amara", "li", "sofia", "tariq", "elena", "mateo", "yuki", "noor", "ivan", "zara",
         "kofi", "hana", "lucas", "mei", "omar", "ines", "arjun", "lea"]
LAST = ["raman", "berg", "okafor", "wei", "costa", "haddad", "petrova", "silva", "tanaka", "ali", "orlov", "khan",
        "mensah", "sato", "moreau", "chen", "farouk", "duarte", "patel", "fischer"]

contacts, activities, deals = [], [], []
n = [100]


def key(email):
    e = email.strip().lower()
    local, domain = e.rsplit("@", 1)
    return f"{local.split('+', 1)[0]}@{domain}"


def cid():
    n[0] += 1
    return f"c-{n[0]:04d}"


def add(tenant, email, name, at, *, norm=None, status="active", merged_into=None, acts=None, deal=None):
    c = {"id": cid(), "tenant_id": tenant, "email": email, "email_norm": norm if norm is not None else key(email),
         "name": name, "status": status, "merged_into": merged_into, "created_at": at.isoformat()}
    contacts.append(c)
    for i, kind in enumerate(acts if acts is not None else ["created"] + rng.sample(["call", "email", "meeting"], rng.randint(0, 2))):
        activities.append({"contact_id": c["id"], "kind": kind, "note": f"{kind} #{i + 1}",
                           "at": (at + timedelta(hours=i + 1)).isoformat()})
    if deal:
        deals.append({"id": f"d-{len(deals) + 1:04d}", "contact_id": c["id"], "amount_cents": deal[0], "stage": deal[1]})
    return c


# ordinary contacts: 45 per tenant, every (first, last) pair used once per tenant
pairs = [(first, last) for first in FIRST for last in LAST]
rng.shuffle(pairs)
reserved = {("priya", "raman"), ("zara", "khan"), ("kofi", "mensah"), ("lea", "fischer")}
pairs = [p for p in pairs if p not in reserved]
for ti, t in enumerate(TENANTS):
    for i in range(45):
        first, last = pairs[ti * 45 + i]
        email = f"{first}.{last}@{DOMAINS[t]}"
        at = T0 + timedelta(days=rng.randint(0, 50), minutes=rng.randint(0, 1400))
        add(t, email, f"{first.title()} {last.title()}", at, deal=(rng.choice([25000, 120000, 480000]), "open") if i % 5 == 0 else None)

groups = []  # (survivor, [duplicates])
# A. API race duplicates: the same key, created seconds apart by two retried requests (identical email_norm)
spare = pairs[4 * 45:]  # unused pairs for the collision groups and the batch
for i in range(6):
    t = TENANTS[i % 2]
    first, last = spare[i]
    email = f"{first}.{last}@{DOMAINS[t]}"
    at = T0 + timedelta(days=52 + i, hours=10)
    a = add(t, email, f"{first.title()} {last.title()}", at, deal=(99000, "won"))
    b = add(t, email.title() if i % 3 else email, f"{first.title()} {last.title()}", at + timedelta(seconds=rng.randint(2, 8)),
            acts=["created", "call"], deal=(45000, "open") if i % 2 else None)
    groups.append((a, [b]))
# B. importer variants: +tags, whitespace and capitals keyed with the importer's own lower-casing
for i in range(4):
    t = TENANTS[(i + 2) % 4]
    first, last = spare[6 + i]
    base = f"{first}.{last}@{DOMAINS[t]}"
    variant = [f" {first.title()}.{last.title()}+news@{DOMAINS[t]}", f"{first}.{last}+promo@{DOMAINS[t]}",
               f"{first.upper()}.{last}@{DOMAINS[t]} ", f"{first}.{last}+2@{DOMAINS[t].upper()}"][i]
    a = add(t, base, f"{first.title()} {last.title()}", T0 + timedelta(days=20 + i, hours=11), deal=(150000, "open"))
    b = add(t, variant, f"{first.title()} {last.title()}", T0 + timedelta(days=58 + i, hours=4), norm=variant.lower(),
            acts=["imported", "email"])
    groups.append((a, [b]))
# C. one group of three: a race duplicate and an imported variant of the same person
t = "t-acme"
a = add(t, f"zara.khan@{DOMAINS[t]}", "Zara Khan", T0 + timedelta(days=12, hours=9), deal=(320000, "won"))
b = add(t, f"zara.khan@{DOMAINS[t]}", "Zara Khan", T0 + timedelta(days=12, hours=9, seconds=5), acts=["created"])
c = add(t, f"Zara.Khan+sales@{DOMAINS[t]}", "Zara Khan", T0 + timedelta(days=60, hours=3), norm=f"zara.khan+sales@{DOMAINS[t]}",
        acts=["imported", "meeting"], deal=(80000, "open"))
groups.append((a, [b, c]))
# D. a survivor whose duplicate was already merged by hand last quarter: already done, must stay as is
kept = add("t-borealis", f"kofi.mensah@{DOMAINS['t-borealis']}", "Kofi Mensah", T0 + timedelta(days=5, hours=14))
done = add("t-borealis", f"kofi.mensah@{DOMAINS['t-borealis']}", "Kofi Mensah", T0 + timedelta(days=5, hours=14, seconds=3),
           status="merged", merged_into=kept["id"], acts=[])
# E. not duplicates: dots differ (significant under the rule), and the same address in two tenants
add("t-cobalt", f"lea.fischer@{DOMAINS['t-cobalt']}", "Lea Fischer", T0 + timedelta(days=30))
add("t-cobalt", f"leafischer@{DOMAINS['t-cobalt']}", "Lea Fischer (personal)", T0 + timedelta(days=31))
add("t-delta", "shared.inbox@partner-example.com", "Partner inbox", T0 + timedelta(days=33))
add("t-acme", "shared.inbox@partner-example.com", "Partner inbox", T0 + timedelta(days=34))
# F. the platform's write probe lives in the ops tenant
probe = add("t-ops", "probe@ops.internal", "write probe", T0, acts=["created"])

contacts.sort(key=lambda c: (c["created_at"], c["id"]))
by_id = {c["id"]: c for c in contacts}

# the partner batch the operator re-imports after handoff: existing keys in new spellings, plus new people
fixture = []
for surv, dups in groups[:5]:
    local, domain = surv["email_norm"].split("@")
    fixture.append({"tenant_id": surv["tenant_id"], "email": f"{local.upper()}+Q4@{domain}", "name": surv["name"]})
new_people = [(TENANTS[i % 4], f"{spare[10 + i][0]}.{spare[10 + i][1]}+batch@{DOMAINS[TENANTS[i % 4]]}",
               f"{spare[10 + i][0].title()} {spare[10 + i][1].title()}") for i in range(6)]
fixture += [{"tenant_id": t, "email": e, "name": nm} for t, e, nm in new_people]
rng.shuffle(fixture)
(HERE / "environment/repo/fixtures").mkdir(parents=True, exist_ok=True)
with (HERE / "environment/repo/fixtures/partner-batch.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["tenant_id", "email", "name"])
    w.writeheader()
    w.writerows(fixture)


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


migration = (HERE / "environment/repo/migrations/001_init.sql").read_text()
inserts = []
for c in contacts:
    cols = ["id", "tenant_id", "email", "email_norm", "name", "status", "merged_into", "created_at"]
    inserts.append(f"INSERT INTO contacts ({', '.join(cols)}) VALUES ({', '.join(lit(c[k]) for k in cols)})")
for a in activities:
    inserts.append(f"INSERT INTO activities (contact_id, kind, note, at) VALUES "
                   f"({lit(a['contact_id'])}, {lit(a['kind'])}, {lit(a['note'])}, {lit(a['at'])})")
for d in deals:
    inserts.append(f"INSERT INTO deals (id, contact_id, amount_cents, stage) VALUES "
                   f"({lit(d['id'])}, {lit(d['contact_id'])}, {d['amount_cents']}, {lit(d['stage'])})")
# the interrupted migration: the concurrent build hits the race duplicates and leaves the index INVALID
interrupted = {"sql": (HERE / "environment/repo/migrations/002_unique_active_contacts.sql").read_text()
               .split("\n")[-2], "allow_failure": True}
staging = [s for s in inserts if "'t-ops'" in s or "'t-delta'" in s][:40]

dup_ids = {d["id"] for _, ds in groups for d in ds}
expected = {
    "groups": [{"survivor": s["id"], "duplicates": sorted(d["id"] for d in ds), "key": s["email_norm"],
                "tenant": s["tenant_id"]} for s, ds in groups],
    "already_merged": {done["id"]: kept["id"]},
    "untouched": sorted(c["id"] for c in contacts if c["id"] not in dup_ids and c["status"] == "active"),
    "seeded": {c["id"]: {k: c[k] for k in ("tenant_id", "email", "name", "status", "merged_into")} for c in contacts},
    "activity_count": len(activities), "deal_count": len(deals),
    "deals_by_contact": {d["id"]: d["contact_id"] for d in deals},
    "activities_by_contact": {},  # filled below from the seeded order: ids are serial in insert order
    "fixture_existing_keys": [(r["tenant_id"], key(r["email"])) for r in fixture
                              if any(s["email_norm"] == key(r["email"]) and s["tenant_id"] == r["tenant_id"] for s, _ in groups)],
    "fixture_new_keys": [(t, key(e)) for t, e, _ in new_people],
    "fixture_rows": len(fixture),
    "probe": probe["id"],
    "direct_insert": {"tenant": groups[0][0]["tenant_id"], "key": groups[0][0]["email_norm"]},
}
for i, a in enumerate(activities, start=1):
    expected["activities_by_contact"][str(i)] = a["contact_id"]

service = {
    "command": ["python", "-m", "contacts"],
    "databases": {"DATABASE_URL": "crm"},
    "min_instances": 2,
    "readiness": {"path": "/healthz", "interval_seconds": 1, "failure_threshold": 3},
    "drain_seconds": 10,
    "rollout_timeout_seconds": 60,
}
job = {
    "command": ["python", "-m", "contacts.importer"],
    "databases": {"DATABASE_URL": "crm"},
    "concurrency": "forbid",
    "max_retries": 0,
    "timeout_seconds": 600,
}


def runtime_policy(env):
    return {"statements": [{"effect": "allow", "actions": ["database:connect"],
                            "resources": [f"srn:simcloud:crm:{env}:database/crm"]}]}


seed = {
    "projects": [{"name": "crm", "environments": ["staging", "prod"], "regions": ["region-a"]}],
    "resources": [
        {"project": "crm", "kind": "policy", "name": "platform-engineer",
         "spec": {"statements": [{"effect": "allow", "actions": ["*"],
                                  "resources": ["srn:simcloud:crm", "srn:simcloud:crm:*"]}]}},
        {"project": "crm", "kind": "binding", "name": "oncall",
         "spec": {"principal": "user:oncall", "policies": ["platform-engineer"]}},
        *[r for env in ("staging", "prod") for r in [
            {"project": "crm", "kind": "service_account", "name": f"contacts-{env}", "spec": {}},
            {"project": "crm", "kind": "policy", "name": f"contacts-{env}-runtime", "spec": runtime_policy(env)},
            {"project": "crm", "kind": "binding", "name": f"contacts-{env}",
             "spec": {"principal": f"service-account:contacts-{env}", "policies": [f"contacts-{env}-runtime"]}},
            {"project": "crm", "env": env, "kind": "database", "name": "crm", "spec": {"pitr_days": 7}},
            {"project": "crm", "env": env, "kind": "service", "name": "contacts",
             "spec": {**service, "service_account": f"contacts-{env}"}},
            {"project": "crm", "env": env, "kind": "job", "name": "contacts-import",
             "spec": {**job, "service_account": f"contacts-{env}"}},
        ]],
    ],
    "sql": [
        {"project": "crm", "env": "prod", "database": "crm", "statements": [migration, *inserts, interrupted]},
        {"project": "crm", "env": "staging", "database": "crm", "statements": [migration, *staging]},
    ],
    "deployments": [
        {"project": "crm", "env": "staging", "service": "contacts", "source": "/seed/contacts-current"},
        {"project": "crm", "env": "prod", "service": "contacts", "source": "/seed/contacts-current"},
    ],
    "job_deployments": [
        {"project": "crm", "env": "staging", "job": "contacts-import", "source": "/seed/contacts-current"},
        {"project": "crm", "env": "prod", "job": "contacts-import", "source": "/seed/contacts-current"},
    ],
    "guard": {
        "protected_envs": ["prod"],
        "checks": [
            {"name": "contact-read", "service": "crm/prod/contacts", "path": f"/contacts/{probe['id']}",
             "expect_status": [200], "body_contains": probe["id"], "timeout_ms": 1500, "every_seconds": 2,
             "failure_threshold": 3, "severity": "SEV1"},
            {"name": "contact-write", "service": "crm/prod/contacts", "method": "POST",
             "path": f"/contacts/{probe['id']}/touch", "expect_status": [200], "timeout_ms": 1500,
             "every_seconds": 2, "failure_threshold": 3, "severity": "SEV1"},
        ],
    },
    "principals": [{"name": "user:oncall", "project": "crm", "token_file": "/shared/oncall.token"}],
}

# what the collector needs after handoff: the variant spelling of one survivor and the direct-write probe
surv = groups[0][0]
local, domain = surv["email_norm"].split("@")
public = {"variant": {"request": {"tenant_id": surv["tenant_id"], "email": f"  {local.title()}+2026@{domain.upper()} ",
                                  "name": surv["name"]}, "survivor": surv["id"]},
          "direct_insert": expected["direct_insert"]}
expected["variant"] = public["variant"]

out = HERE / "environment/simcloud"
(out / "expected_public.json").write_text(json.dumps(public, indent=2) + "\n")
(out / "seed.yaml").write_text("# Generated by build_world.py; do not edit.\n" + yaml.safe_dump(seed, sort_keys=False, width=200))
(HERE / "tests/expected.json").write_text(json.dumps(expected, indent=2) + "\n")
print(f"{len(contacts)} contacts, {len(activities)} activities, {len(deals)} deals, {len(groups)} collision groups "
      f"({sum(len(d) for _, d in groups)} duplicates), fixture {len(fixture)} rows")
