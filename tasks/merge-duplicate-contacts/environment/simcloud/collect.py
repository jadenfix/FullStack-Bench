# Runs inside the SimCloud sidecar after the agent phase (verifier.collect). The agent can't reach this
# container's filesystem, the operator tokens or the Postgres admin password.
import json
import os
import urllib.error
import urllib.request

import psycopg

os.makedirs("/evidence", exist_ok=True)
ADMIN = {"Authorization": "Bearer " + os.environ["SIMCLOUD_ADMIN_TOKEN"]}
API = "http://127.0.0.1:7400"
SVC = "http://127.0.0.1:7480/_svc/crm/prod/contacts"
EXPECTED = json.load(open("/seed/expected_public.json"))


def call(method, url, headers=None, body=None, timeout=60):
    req = urllib.request.Request(url, method=method, headers={"content-type": "application/json", **(headers or {})},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, repr(e)


def save(name, obj):
    with open(f"/evidence/{name}", "w") as f:
        json.dump(obj, f, default=str)


challenges = {}

# 0. From here on the operator drives production; incidents carry the phase.
call("POST", f"{API}/admin/v1/guard/phase", ADMIN, body={"phase": "post_handoff"})

# 1. A sales rep adds a contact who already exists, in a new spelling, through the API.
variant = EXPECTED["variant"]
s, body = call("POST", f"{SVC}/contacts", body=variant["request"])
save("variant_create.json", {"request": variant["request"], "status": s, "body": body})
challenges["variant-create"] = {"status": "ran" if s else "not_run", "detail": {"status": s}}

# 2. The partner batch is re-imported with the job, as the operator does every Monday.
s, body = call("POST", f"{API}/v1/projects/crm/envs/prod/job/contacts-import/run", ADMIN,
               body={"args": ["fixtures/partner-batch.csv"], "wait": True, "timeout": 300}, timeout=330)
run = json.loads(body) if s == 200 else {"error": f"job run failed ({s}): {body[:300]}"}
logs = ""
if run.get("id"):
    _, logs = call("GET", f"{API}/v1/projects/crm/envs/prod/job/contacts-import/runs/{run['id']}/logs", ADMIN)
save("batch_rerun.json", {"run": run, "logs": logs[-4000:]})
challenges["batch-rerun"] = {"status": "ran" if run.get("id") else "not_run", "detail": {"status": s}}
save("challenges.json", challenges)

# 3. SimCloud's evidence (also runs the final synthetic checks and log/SQL scans).
s, body = call("GET", f"{API}/admin/v1/evidence", ADMIN)
with open("/evidence/evidence.json", "w") as f:
    f.write(body if s == 200 else json.dumps({"error": f"evidence unavailable ({s})"}))

# 4. The production tables, the index catalogue, and a direct duplicate write as the application's role.
pw = open("/var/lib/simcloud/pg.admin").read().strip()
try:
    with psycopg.connect(host="127.0.0.1", port=5433, user="simcloud_admin", password=pw, dbname="crm__prod__crm") as c:
        cols = ["id", "tenant_id", "email", "email_norm", "name", "status", "merged_into"]
        save("contacts.json", [dict(zip(cols, r)) for r in c.execute(f"SELECT {', '.join(cols)} FROM contacts ORDER BY id")])
        save("activities.json", [{"id": r[0], "contact_id": r[1], "kind": r[2]}
                                 for r in c.execute("SELECT id, contact_id, kind FROM activities ORDER BY id")])
        save("deals.json", [{"id": r[0], "contact_id": r[1]} for r in c.execute("SELECT id, contact_id FROM deals ORDER BY id")])
        save("indexes.json", [{"name": r[0], "valid": r[1], "unique": r[2], "ready": r[3], "definition": r[4]}
                              for r in c.execute("SELECT indexrelid::regclass::text, indisvalid, indisunique, indisready, "
                                                 "pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid = 'contacts'::regclass "
                                                 "ORDER BY 1")])
    probe = EXPECTED["direct_insert"]
    with psycopg.connect(host="127.0.0.1", port=5433, user="simcloud_admin", password=pw, dbname="crm__prod__crm",
                         autocommit=False) as c:
        c.execute("SET ROLE crm__prod__crm__owner")
        c.execute("SET LOCAL lock_timeout = '5s'")
        try:
            c.execute("INSERT INTO contacts (id, tenant_id, email, email_norm, name, status) "
                      "VALUES ('c-verifier-dup', %s, %s, %s, 'verifier', 'active')",
                      (probe["tenant"], probe["key"], probe["key"]))
            outcome = {"accepted": True, "sqlstate": None}
        except psycopg.Error as e:
            outcome = {"accepted": False, "sqlstate": e.sqlstate, "detail": str(e)[:300]}
        c.rollback()
    save("direct_insert.json", outcome)
except Exception as e:
    save("contacts.json", {"error": repr(e)})
