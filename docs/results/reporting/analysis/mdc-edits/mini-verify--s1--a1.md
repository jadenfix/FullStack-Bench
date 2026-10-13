# mini-verify / merge-duplicate-contacts / seed 1 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--mini-verify--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__QapYQzo/agent/mini-swe-agent.round1.trajectory.json`
- Final data state: `jobs/reporting-v1--mini-verify--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__QapYQzo/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #29 edit: `bash` → `/tmp/merge.sql`

````bash
cat > /tmp/merge.sql <<'EOF'
BEGIN;

WITH dup_groups AS (
    SELECT
        tenant_id,
        email_norm,
        MIN(created_at) AS min_created,
        MIN(id) AS min_id  -- tie-breaker
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1
),
survivors AS (
    SELECT
        d.tenant_id,
        d.email_norm,
        c.id AS survivor_id
    FROM dup_groups d
    JOIN contacts c ON c.tenant_id = d.tenant_id
        AND c.email_norm = d.email_norm
        AND c.created_at = d.min_created
        AND c.id = d.min_id
),
duplicates AS (
    SELECT
        c.id AS duplicate_id,
        s.survivor_id
    FROM contacts c
    JOIN survivors s ON c.tenant_id = s.tenant_id
        AND c.email_norm = s.email_norm
    WHERE c.status = 'active'
      AND c.id <> s.survivor_id
)
-- Update duplicates to merged
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;

-- Move activities
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;

-- Move deals
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;

COMMIT;
EOF
````

## #31 edit: `bash` → `/tmp/merge.sql`

````bash
cat > /tmp/merge.sql <<'EOF'
BEGIN;

CREATE TEMP TABLE dup_groups AS
    SELECT
        tenant_id,
        email_norm,
        MIN(created_at) AS min_created,
        MIN(id) AS min_id  -- tie-breaker
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1;

CREATE TEMP TABLE survivors AS
    SELECT
        d.tenant_id,
        d.email_norm,
        c.id AS survivor_id
    FROM dup_groups d
    JOIN contacts c ON c.tenant_id = d.tenant_id
        AND c.email_norm = d.email_norm
        AND c.created_at = d.min_created
        AND c.id = d.min_id;

CREATE TEMP TABLE duplicates AS
    SELECT
        c.id AS duplicate_id,
        s.survivor_id
    FROM contacts c
    JOIN survivors s ON c.tenant_id = s.tenant_id
        AND c.email_norm = s.email_norm
    WHERE c.status = 'active'
      AND c.id <> s.survivor_id;

-- Update duplicates to merged
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;

-- Move activities
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;

-- Move deals
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;

COMMIT;
EOF
````

## #37 edit: `bash` → `contacts/app.py`

````bash
sed -i "s/existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT/existing = db.find_active(tenant, key)/" contacts/app.py
````

## #39 edit: `bash` → `contacts/importer.py`

````bash
cat > contacts/importer.py <<'EOF'
\"\"\"Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.

    python -m contacts.importer fixtures/partner-batch.csv

Runs as the `contacts-import` job. Rows are inserted in batches; a row whose address is already
present for the tenant is skipped. Uses the same key normalization as the API.
\"\"\"\nimport csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom .settings import S\nfrom .normalize import email_key\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = email_key(r[\"email\"])\n                # Check for an active contact with the same tenant and key\n                seen = c.execute(f\"SELECT 1 FROM {S.table(\u0027contacts\u0027)} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'\",\n                                 (r[\"tenant_id\"], key)).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table(\u0027contacts\u0027)} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, \u0027active\u0027)\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table(\u0027activities\u0027)} (contact_id, kind, note) VALUES (%s, \u0027imported\u0027, %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0\n\n\nif __name__ == \"__main__\":\n    sys.exit(main(sys.argv[1]))
EOF
````

## #43 edit: `bash` → `/tmp/test_api.py`

````bash
cat > /tmp/test_api.py <<'EOF'
import json, secrets, sys, urllib.error, urllib.request, os
url = os.environ.get("SIMCLOUD_ROUTER_URL", "http://simcloud:7480") + "/_svc/crm/prod/contacts/contacts"
tag = secrets.token_hex(3)
forms = [f"check.{tag}@check-example.com", f"  Check.{tag}+again@Check-Example.COM "]
results = []
for email in forms:
    body = json.dumps({"tenant_id": "t-check", "email": email, "name": "public check"}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            results.append((r.status, json.loads(r.read().decode() or "{}")))
    except urllib.error.HTTPError as e:
        results.append((e.code, e.read().decode(errors="replace")[:200]))
    except Exception as e:
        results.append((0, repr(e)))
print("Results:", results)
EOF
python3 /tmp/test_api.py
````

## #52 edit: `bash` → `/tmp/debug.py`

````bash
cat > /tmp/debug.py <<'EOF'
import psycopg
from contacts.settings import S
conn = psycopg.connect(S.dsn(), autocommit=True)
tenant_id = 't-check'
email_norm = 'check.3475c3@check-example.com'
print('tenant_id:', repr(tenant_id))
print('email_norm:', repr(email_norm))
cols = ['id', 'tenant_id', 'email', 'email_norm', 'name', 'status', 'merged_into', 'created_at']
query = f'SELECT {", ".join(cols)} FROM {S.table("contacts")} WHERE tenant_id = %s AND email_norm = %s AND status = %s ORDER BY created_at, id LIMIT 1'
print('Query:', query)
cur = conn.execute(query, (tenant_id, email_norm, 'active'))
row = cur.fetchone()
print('Row:', row)
if row:
    out = dict(zip(cols, row))
    out['created_at'] = out['created_at'].isoformat()
    print('Out:', out)
else:
    print('No row')
EOF
python3 /tmp/debug.py
````

## #81 edit: `bash` → `/app/contacts/importer.py`

````bash
cat > /app/contacts/importer.py <<'EOF'
"""Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.

    python -m contacts.importer fixtures/partner-batch.csv

Runs as the `contacts-import` job. Rows are inserted in batches; a row whose address is already
present for the tenant is skipped. Uses the same key normalization as the API.
"""
import csv
import secrets
import sys

import psycopg

from .settings import S
from .normalize import email_key


def main(path):
    batch = int(S.import_batch)
    inserted = skipped = 0
    with open(path, newline="") as f, psycopg.connect(S.dsn(), autocommit=True) as c:
        rows = list(csv.DictReader(f))
        for start in range(0, len(rows), batch):
            for r in rows[start:start + batch]:
                key = email_key(r["email"])
                # Check for an active contact with the same tenant and key
                seen = c.execute(f"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'",
                                 (r["tenant_id"], key)).fetchone()
                if seen:
                    skipped += 1
                    continue
                cid = "c-" + secrets.token_hex(5)
                c.execute(f"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) "
                          f"VALUES (%s, %s, %s, %s, %s, 'active')", (cid, r["tenant_id"], r["email"], key, r["name"]))
                c.execute(f"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)",
                          (cid, path))
                inserted += 1
            print(f"batch {start // batch + 1}: inserted={inserted} skipped={skipped}", flush=True)
    print(f"done: inserted={inserted} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
EOF
````

