# rusty-combined / merge-duplicate-contacts / seed 1 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-combined--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__VKGq3UZ/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-combined--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__VKGq3UZ/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #18 edit: `edit_file` → `contacts/app.py`

````json
{
  "path": "contacts/app.py",
  "old_string": "    key = email_key(email)\n    existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT",
  "new_string": "    key = email_key(email)\n    existing = db.find_active(tenant, key)"
}
````

## #19 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "def lowered(email):\n    return email.lower()",
  "new_string": "from .normalize import email_key"
}
````

## #21 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "import csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom .settings import S",
  "new_string": "import csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom . import db\nfrom .settings import S"
}
````

## #23 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "def main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = lowered(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s\",\n                                 (r[\"tenant_id\"], r[\"email\"])).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0",
  "new_string": "def main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = email_key(r[\"email\"])\n                existing = db.find_active(r[\"tenant_id\"], key)\n                if existing:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0"
}
````

## #31 possible write: `bash`

````bash
psql "postgresql://u_4ae7ee8b459c:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm?sslmode=disable" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;

-- First, update the contacts table to mark duplicates as merged and set merged_into
WITH ranked AS (
  SELECT id, tenant_id, email_norm,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
)
UPDATE contacts
SET status = 'merged', merged_into = sub.survivor_id
FROM (
  SELECT tenant_id, email_norm,
         id AS survivor_id
  FROM ranked
  WHERE rn = 1
) sub
WHERE contacts.tenant_id = sub.tenant_id
  AND contacts.email_norm = sub.email_norm
  AND contacts.id != sub.survivor_id
  AND contacts.status = 'active';

-- Second, update activities to point to the survivor
UPDATE activities
SET contact_id = sub.survivor_id
FROM (
  SELECT c.id AS duplicate_id, m.id AS survivor_id
  FROM contacts c
  JOIN contacts m ON m.tenant_id = c.tenant_id
                  AND m.email_norm = c.email_norm
                  AND m.status = 'active'
                  AND m.merged_into IS NULL
  WHERE c.status = 'merged'
    AND c.merged_into = m.id
) sub
WHERE activities.contact_id = sub.duplicate_id;

-- Third, update deals to point to the survivor
UPDATE deals
SET contact_id = sub.survivor_id
FROM (
  SELECT c.id AS duplicate_id, m.id AS survivor_id
  FROM contacts c
  JOIN contacts m ON m.tenant_id = c.tenant_id
                  AND m.email_norm = c.email_norm
                  AND m.status = 'active'
                  AND m.merged_into IS NULL
  WHERE c.status = 'merged'
    AND c.merged_into = m.id
) sub
WHERE deals.contact_id = sub.duplicate_id;

COMMIT;
SQL
````

## #40 edit: `edit_file` → `contacts/db.py`

````json
{
  "path": "contacts/db.py",
  "old_string": "import secrets\nimport threading\n\nimport psycopg\n\nfrom .settings import S",
  "new_string": "import secrets\nimport threading\n\nimport psycopg\nimport psycopg.errors\n\nfrom .settings import S"
}
````

## #41 edit: `edit_file` → `contacts/db.py`

````json
{
  "path": "contacts/db.py",
  "old_string": "def create(tenant_id, email, email_norm, name):\n    cid = \"c-\" + secrets.token_hex(5)\n    conn().execute(\n        f\"INSERT INTO {_t('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n        f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, tenant_id, email, email_norm, name))\n    activity(cid, \"created\", \"api\")\n    return cid",
  "new_string": "def create(tenant_id, email, email_norm, name):\n    cid = \"c-\" + secrets.token_hex(5)\n    try:\n        conn().execute(\n            f\"INSERT INTO {_t('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n            f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, tenant_id, email, email_norm, name))\n    except psycopg.errors.UniqueViolation:\n        # Duplicate key: an active contact with this tenant and email_norm already exists.\n        # Return the existing active contact.\n        return db.find_active(tenant_id, email_norm)\n    activity(cid, \"created\", \"api\")\n    return cid"
}
````

## #42 edit: `edit_file` → `contacts/app.py`

````json
{
  "path": "contacts/app.py",
  "old_string": "import json\nimport os\nimport signal\nimport sys\nimport threading\nimport time\nimport traceback\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n\nfrom . import db\nfrom .normalize import email_key\nfrom .settings import S",
  "new_string": "import json\nimport os\nimport signal\nimport sys\nimport threading\nimport time\nimport traceback\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n\nfrom . import db\nfrom .normalize import email_key\nfrom .settings import S\nimport psycopg.errors"
}
````

## #43 edit: `edit_file` → `contacts/db.py`

````json
{
  "path": "contacts/db.py",
  "old_string": "import secrets\nimport threading\n\nimport psycopg\nimport psycopg.errors\n\nfrom .settings import S",
  "new_string": "import secrets\nimport threading\n\nimport psycopg\n\nfrom .settings import S"
}
````

## #63 possible write: `bash` (failed)

````bash
DATABASE_URL="postgresql://u_4ae7ee8b459c:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm?sslmode=disable" psql -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
-- Try to insert a duplicate active contact for an existing tenant and key
-- We'll use one of the existing keys that we know has an active contact after our merge.
-- For example, tenant t-acme and email_norm arjun.sato@acme-example.com (which has active contact c-0281)
INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
VALUES ('c-test-duplicate', 't-acme', 'ARJUN.SATO+test@acme-example.com', 'arjun.sato@acme-example.com', 'Test Duplicate', 'active');
EXCEPTION WHEN unique_violation THEN
  RAISE NOTICE 'Duplicate key violation expected and caught.';
ROLLBACK;
SQL
````

## #64 possible write: `bash` (failed)

````bash
DATABASE_URL="postgresql://u_4ae7ee8b459c:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm?sslmode=disable" psql "postgresql://u_4ae7ee8b459c:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm?sslmode=disable" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
-- Try to insert a duplicate active contact for an existing tenant and key
-- We'll use one of the existing keys that we know has an active contact after our merge.
-- For example, tenant t-acme and email_norm arjun.sato@acme-example.com (which has active contact c-0281)
INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
VALUES ('c-test-duplicate', 't-acme', 'ARJUN.SATO+test@acme-example.com', 'arjun.sato@acme-example.com', 'Test Duplicate', 'active');
EXCEPTION WHEN unique_violation THEN
  RAISE NOTICE 'Duplicate key violation expected and caught.';
ROLLBACK;
SQL
````

