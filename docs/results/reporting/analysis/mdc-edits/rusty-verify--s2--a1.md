# rusty-verify / merge-duplicate-contacts / seed 2 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-verify--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__TERWoC2/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-verify--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__TERWoC2/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #12 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_1} psql "host=simcloud port=5433 dbname=crm__prod__crm user=u_1f37677a61fb" -f - <<'SQL'
BEGIN;
CREATE TEMP TABLE duplicates AS
  SELECT
    id as duplicate_id,
    survivor_id
  FROM (
    SELECT
      id,
      tenant_id,
      email_norm,
      row_number() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) as rn,
      first_value(id) OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) as survivor_id
    FROM contacts
    WHERE status = 'active'
  ) sub
  WHERE rn > 1;

UPDATE activities
SET contact_id = d.survivor_id
FROM duplicates d
WHERE activities.contact_id = d.duplicate_id;

UPDATE deals
SET contact_id = d.survivor_id
FROM duplicates d
WHERE deals.contact_id = d.duplicate_id;

UPDATE contacts
SET status = 'merged', merged_into = d.survivor_id
FROM duplicates d
WHERE contacts.id = d.duplicate_id;
COMMIT;
SQL
````

## #45 edit: `edit_file` → `contacts/app.py`

````json
{
  "path": "contacts/app.py",
  "old_string": "@route(\"POST\", \"/contacts\")\ndef create_contact(body, *args):\n    try:\n        tenant, email, name = body[\"tenant_id\"], body[\"email\"], body.get(\"name\") or \"\"\n    except (KeyError, TypeError):\n        return 400, {\"error\": \"tenant_id and email are required\"}\n    if not isinstance(email, str) or \"@\" not in email:\n        return 400, {\"error\": \"email must be an address\"}\n    key = email_key(email)\n    existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT\n    if existing:\n        return 200, existing\n    cid = db.create(tenant, email, key, name)\n    _log(event=\"contact_created\", id=cid, tenant=tenant)\n    return 201, db.get(cid)",
  "new_string": "@route(\"POST\", \"/contacts\")\ndef create_contact(body, *args):\n    try:\n        tenant, email, name = body[\"tenant_id\"], body[\"email\"], body.get(\"name\") or \"\"\n    except (KeyError, TypeError):\n        return 400, {\"error\": \"tenant_id and email are required\"}\n    if not isinstance(email, str) or \"@\" not in email:\n        return 400, {\"error\": \"email must be an address\"}\n    key = email_key(email)\n    existing = db.find_active(tenant, key)\n    if existing:\n        return 200, existing\n    cid = db.create(tenant, email, key, name)\n    _log(event=\"contact_created\", id=cid, tenant=tenant)\n    return 201, db.get(cid)"
}
````

## #46 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "def lowered(email):\n    return email.lower()\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = lowered(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s\",\n                                 (r[\"tenant_id\"], r[\"email\"])).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0",
  "new_string": "def main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = email_key(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'\",\n                                 (r[\"tenant_id\"], key)).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0"
}
````

## #48 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "\"\"\"Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.\n\n    python -m contacts.importer fixtures/partner-batch.csv\n\nRuns as the `contacts-import` job. Rows are inserted in batches; a row whose address is already\npresent for the tenant is skipped. Predates the API's key handling (see docs/DATA_CONTRACT.md) and\nstill lower-cases on its own; nobody got round to sharing the function after the contract landed.\n\"\"\"\nimport csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom .settings import S",
  "new_string": "\"\"\"Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.\n\n    python -m contacts.importer fixtures/partner-batch.csv\n\nRuns as the `contacts-import` job. Rows are inserted in batches; a row whose address is already\npresent for the tenant is skipped. Predates the API's key handling (see docs/DATA_CONTRACT.md) and\nstill lower-cases on its own; nobody got round to sharing the function after the contract landed.\n\"\"\"\nimport csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom . import normalize\nfrom .settings import S"
}
````

## #59 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "            for r in rows[start:start + batch]:\n                key = email_key(r[\"email\"])",
  "new_string": "            for r in rows[start:start + batch]:\n                key = normalize.email_key(r[\"email\"])"
}
````

