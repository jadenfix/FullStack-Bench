# rusty-combined / merge-duplicate-contacts / seed 2 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-combined--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__LgsANjM/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-combined--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__LgsANjM/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #15 possible write: `bash` (failed)

````bash
PGPASSWORD=${RUSTY_SECRET_1} psql "host=simcloud port=5433 dbname=crm__prod__crm user=u_5da3a27bef7b" -v ON_ERROR_STOP=1 << 'SQL'
BEGIN;

WITH dup_groups AS (
    SELECT tenant_id, email_norm, array_agg(id ORDER BY created_at, id) AS ids
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1
),
survivor_map AS (
    SELECT tenant_id, email_norm, ids[1] AS survivor_id, ids[2:] AS duplicate_ids
    FROM dup_groups
)
UPDATE contacts
SET status = 'merged', merged_into = survivor_map.survivor_id
FROM survivor_map
WHERE contacts.tenant_id = survivor_map.tenant_id
  AND contacts.email_norm = survivor_map.email_norm
  AND contacts.id = ANY(survivor_map.duplicate_ids);

UPDATE activities
SET contact_id = survivor_map.survivor_id
FROM survivor_map
WHERE activities.contact_id = ANY(survivor_map.duplicate_ids);

UPDATE deals
SET contact_id = survivor_map.survivor_id
FROM survivor_map
WHERE deals.contact_id = ANY(survivor_map.duplicate_ids);

COMMIT;
SQL
````

## #16 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_1} psql "host=simcloud port=5433 dbname=crm__prod__crm user=u_5da3a27bef7b" -v ON_ERROR_STOP=1 << 'SQL'
BEGIN;

WITH dup_groups AS (
    SELECT tenant_id, email_norm, array_agg(id ORDER BY created_at, id) AS ids
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1
),
survivor_map AS (
    SELECT tenant_id, email_norm, ids[1] AS survivor_id, ids[2:] AS duplicate_ids
    FROM dup_groups
)
UPDATE contacts
SET status = 'merged', merged_into = survivor_map.survivor_id
FROM survivor_map
WHERE contacts.tenant_id = survivor_map.tenant_id
  AND contacts.email_norm = survivor_map.email_norm
  AND contacts.id = ANY(survivor_map.duplicate_ids);

WITH dup_groups AS (
    SELECT tenant_id, email_norm, array_agg(id ORDER BY created_at, id) AS ids
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1
),
survivor_map AS (
    SELECT tenant_id, email_norm, ids[1] AS survivor_id, ids[2:] AS duplicate_ids
    FROM dup_groups
)
UPDATE activities
SET contact_id = survivor_map.survivor_id
FROM survivor_map
WHERE activities.contact_id = ANY(survivor_map.duplicate_ids);

WITH dup_groups AS (
    SELECT tenant_id, email_norm, array_agg(id ORDER BY created_at, id) AS ids
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1
),
survivor_map AS (
    SELECT tenant_id, email_norm, ids[1] AS survivor_id, ids[2:] AS duplicate_ids
    FROM dup_groups
)
UPDATE deals
SET contact_id = survivor_map.survivor_id
FROM survivor_map
WHERE deals.contact_id = ANY(survivor_map.duplicate_ids);

COMMIT;
SQL
````

## #18 edit: `edit_file` → `contacts/app.py`

````json
{
  "path": "contacts/app.py",
  "old_string": "@route(\"POST\", \"/contacts\")\ndef create_contact(body, *args):\n    try:\n        tenant, email, name = body[\"tenant_id\"], body[\"email\"], body.get(\"name\") or \"\"\n    except (KeyError, TypeError):\n        return 400, {\"error\": \"tenant_id and email are required\"}\n    if not isinstance(email, str) or \"@\" not in email:\n        return 400, {\"error\": \"email must be an address\"}\n    key = email_key(email)\n    existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT\n    if existing:\n        return 200, existing\n    cid = db.create(tenant, email, key, name)\n    _log(event=\"contact_created\", id=cid, tenant=tenant)\n    return 201, db.get(cid)",
  "new_string": "@route(\"POST\", \"/contacts\")\ndef create_contact(body, *args):\n    try:\n        tenant, email, name = body[\"tenant_id\"], body[\"email\"], body.get(\"name\") or \"\"\n    except (KeyError, TypeError):\n        return 400, {\"error\": \"tenant_id and email are required\"}\n    if not isinstance(email, str) or \"@\" not in email:\n        return 400, {\"error\": \"email must be an address\"}\n    key = email_key(email)\n    existing = db.find_active(tenant, key)\n    if existing:\n        return 200, existing\n    cid = db.create(tenant, email, key, name)\n    _log(event=\"contact_created\", id=cid, tenant=tenant)\n    return 201, db.get(cid)"
}
````

## #19 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "\"\"\"Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.\n\n    python -m contacts.importer fixtures/partner-batch.csv\n\nRuns as the `contacts-import` job. Rows are inserted in batches; a row whose address is already\npresent for the tenant is skipped. Predates the API's key handling (see docs/DATA_CONTRACT.md) and\nstill lower-cases on its own; nobody got round to sharing the function after the contract landed.\n\"\"\"\nimport csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom .settings import S\n\n\ndef lowered(email):\n    return email.lower()\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = lowered(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s\",\n                                 (r[\"tenant_id\"], r[\"email\"])).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0\n\n\nif __name__ == \"__main__\":\n    sys.exit(main(sys.argv[1]))",
  "new_string": "\"\"\"Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.\n\n    python -m contacts.importer fixtures/partner-batch.csv\n\nRuns as the `contacts-import` job. Rows are inserted in batches; a row whose address is already\npresent for the tenant is skipped. Uses the same key normalization as the API.\n\"\"\"\nimport csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom . import normalize\nfrom .settings import S\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = normalize.email_key(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'\",\n                                 (r[\"tenant_id\"], key)).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0\n\n\nif __name__ == \"__main__\":\n    sys.exit(main(sys.argv[1]))"
}
````

## #147 edit: `bash` → `/tmp/creds.json` (failed)

````bash
mcp__simcloud__db_credentials --env prod --name crm --ttl-seconds 300 > /tmp/creds.json 2>&1 && HOST=$(python3 -c "import json; print(json.load(open('/tmp/creds.json'))['host'])" 2>/dev/null) && PORT=$(python3 -c "import json; print(json.load(open('/tmp/creds.json'))['port'])" 2>/dev/null) && DBNAME=$(python3 -c "import json; print(json.load(open('/tmp/creds.json'))['dbname'])" 2>/dev/null) && USER=$(python3 -c "import json; print(json.load(open('/tmp/creds.json'))['user'])" 2>/dev/null) && PASSWORD=$(python3 -c "import json; print(json.load(open('/tmp/creds.json'))['password'])" 2>/dev/null) && export PGPASSWORD=$PASSWORD && psql "host=$HOST port=$PORT dbname=$DBNAME user=$USER" -c "REINDEX INDEX CONCURRENTLY contacts_active_key_old;" 2>&1
````

## #164 edit: `bash` → `/tmp/creds.json` (failed)

````bash
mcp__simcloud__db_credentials --env prod --name crm --ttl-seconds 300 > /tmp/creds.json && python3 -c "import json,os; d=json.load(open('/tmp/creds.json')); os.environ['PGHOST']=d['host']; os.environ['PGPORT']=str(d['port']); os.environ['PGDATABASE']=d['dbname']; os.environ['PGUSER']=d['user']; os.environ['PGPASSWORD']=d['password']" && psql -c "REINDEX INDEX CONCURRENTLY contacts_active_key_old;"
````

