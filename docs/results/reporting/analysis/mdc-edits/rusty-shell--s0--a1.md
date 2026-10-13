# rusty-shell / merge-duplicate-contacts / seed 0 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-shell--merge-duplicate-contacts--s0--a1/merge-duplicate-contacts__5RVBJzf/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-shell--merge-duplicate-contacts--s0--a1/merge-duplicate-contacts__5RVBJzf/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #30 edit: `bash` → `merge_duplicates.sql`

````bash
cd /app && cat > merge_duplicates.sql << 'EOF'
BEGIN;

-- First, update the duplicate contacts to merged and set merged_into to the survivor.
WITH ranked AS (
    SELECT
        id,
        tenant_id,
        email_norm,
        ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
)
UPDATE contacts
SET status = 'merged',
    merged_into = survivor.id
FROM ranked
JOIN ranked survivor ON
    ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1
  AND ranked.id = contacts.id;

-- Second, update activities to point to the survivor for merged contacts.
UPDATE activities
SET contact_id = survivor.id
FROM ranked
JOIN ranked survivor ON
    activities.contact_id = ranked.id
    AND ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1;

-- Third, update deals to point to the survivor for merged contacts.
UPDATE deals
SET contact_id = survivor.id
FROM ranked
JOIN ranked survivor ON
    deals.contact_id = ranked.id
    AND ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1;

COMMIT;
EOF
````

## #32 edit: `bash` → `merge_duplicates.sql`

````bash
cd /app && cat > merge_duplicates.sql << 'EOF'
BEGIN;

CREATE TEMP TABLE ranked AS
SELECT id, tenant_id, email_norm,
       ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
FROM contacts
WHERE status = 'active';

-- Update duplicate contacts to merged and set merged_into to the survivor.
UPDATE contacts
SET status = 'merged',
    merged_into = survivor.id
FROM ranked
JOIN ranked survivor ON
    ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1
  AND ranked.id = contacts.id;

-- Update activities to point to the survivor for merged contacts.
UPDATE activities
SET contact_id = survivor.id
FROM ranked
JOIN ranked survivor ON
    activities.contact_id = ranked.id
    AND ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1;

-- Update deals to point to the survivor for merged contacts.
UPDATE deals
SET contact_id = survivor.id
FROM ranked
JOIN ranked survivor ON
    deals.contact_id = ranked.id
    AND ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1;

COMMIT;
EOF
````

## #34 edit: `bash` → `merge_duplicates.sql`

````bash
cd /app && cat > merge_duplicates.sql << 'EOF'
BEGIN;

CREATE TEMP TABLE ranked AS
SELECT id, tenant_id, email_norm,
       ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
FROM contacts
WHERE status = 'active';

-- Update duplicate contacts to merged and set merged_into to the survivor.
UPDATE contacts
SET status = 'merged',
    merged_into = survivor.id
FROM ranked
JOIN ranked survivor ON
    ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1
  AND ranked.id = contacts.id;

-- Update activities to point to the survivor for merged contacts.
UPDATE activities
SET contact_id = survivor.id
FROM ranked
JOIN ranked survivor ON
    activities.contact_id = ranked.id
    AND ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1;

-- Update deals to point to the survivor for merged contacts.
UPDATE deals
SET contact_id = survivor.id
FROM ranked
JOIN ranked survivor ON
    deals.contact_id = ranked.id
    AND ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1;

COMMIT;
EOF
````

## #35 edit: `bash` → `merge_duplicates.sql`

````bash
cd /app && cat > merge_duplicates.sql << 'EOF'
BEGIN;

CREATE TEMP TABLE ranked AS
SELECT id, tenant_id, email_norm,
       ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
FROM contacts
WHERE status = 'active';

CREATE TEMP TABLE contact_map AS
SELECT ranked.id AS old_id, survivor.id AS new_id
FROM ranked
JOIN ranked survivor ON
    ranked.tenant_id = survivor.tenant_id
    AND ranked.email_norm = survivor.email_norm
    AND survivor.rn = 1
WHERE ranked.rn > 1;

-- Update duplicate contacts to merged and set merged_into to the survivor.
UPDATE contacts
SET status = 'merged',
    merged_into = contact_map.new_id
FROM contact_map
WHERE contacts.id = contact_map.old_id;

-- Update activities to point to the survivor for merged contacts.
UPDATE activities
SET contact_id = contact_map.new_id
FROM contact_map
WHERE activities.contact_id = contact_map.old_id;

-- Update deals to point to the survivor for merged contacts.
UPDATE deals
SET contact_id = contact_map.new_id
FROM contact_map
WHERE deals.contact_id = contact_map.old_id;

COMMIT;
EOF
````

## #50 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_2} psql "postgresql://u_b7a145f8a45d:${RUSTY_SECRET_2}@simcloud:5433/crm__prod__crm" <<'EOF'
BEGIN;
ALTER INDEX contacts_active_key_old VALIDATE;
COMMIT;
EOF
````

## #56 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak
````

## #57 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak
````

## #58 edit: `bash` → `contacts/app.py`

````bash
cd /app && sed -i "s/existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT/existing = db.find_active(tenant, email_key(email))  # use normalized key/" contacts/app.py
````

## #60 possible write: `bash`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.bak
````

## #61 edit: `bash` → `contacts/importer.py`

````bash
cd /app && cat > contacts/importer.py << 'EOF'
"""Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.

    python -m contacts.importer fixtures/partner-batch.csv

Runs as the `contacts-import` job. Rows are inserted in batches; a row whose address is already
present for the tenant is skipped. Now uses the normalized key as per DATA_CONTRACT.
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
                # Check if an active contact already exists for this tenant and key
                seen = c.execute(
                    f"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'",
                    (r["tenant_id"], key)
                ).fetchone()
                if seen:
                    skipped += 1
                    continue
                cid = "c-" + secrets.token_hex(5)
                c.execute(
                    f"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) "
                    f"VALUES (%s, %s, %s, %s, %s, 'active')",
                    (cid, r["tenant_id"], r["email"], key, r["name"])
                )
                c.execute(
                    f"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)",
                    (cid, path)
                )
                inserted += 1
            print(f"batch {start // batch + 1}: inserted={inserted} skipped={skipped}", flush=True)
    print(f"done: inserted={inserted} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
EOF
````

