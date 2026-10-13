# mini / merge-duplicate-contacts / seed 0 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--mini--merge-duplicate-contacts--s0--a1/merge-duplicate-contacts__s5RkqP6/agent/mini-swe-agent.trajectory.json`
- Final data state: `jobs/reporting-v1--mini--merge-duplicate-contacts--s0--a1/merge-duplicate-contacts__s5RkqP6/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #21 possible write: `bash`

````bash
cp contacts/app.py contacts/app.py.bak
````

## #23 edit: `bash` → `=`

````bash
sed -i \"s/existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT/existing = db.find_active(tenant, key)/\" contacts/app.py
````

## #25 edit: `bash` → `contacts/app.py`

````bash
sed -i '46s/.*/    existing = db.find_active(tenant, key)/' contacts/app.py
````

## #27 possible write: `bash`

````bash
cp contacts/importer.py contacts/importer.py.bak
````

## #29 edit: `bash` → `from`

````bash
sed -i \"5i from .normalize import email_key\" contacts/importer.py
````

## #32 edit: `bash` → `contacts/importer.py`

````bash
sed -i '11i from .normalize import email_key' contacts/importer.py
````

## #34 edit: `bash` → `contacts/importer.py`

````bash
sed -i '30s/.*/                key = email_key(r[\"email\"])/' contacts/importer.py
````

## #37 edit: `bash` → `seen`

````bash
sed -i '31,32c\\                seen = c.execute(f\"SELECT 1 FROM {S.table(\"contacts\")} WHERE tenant_id = %s AND email_norm = %s AND status = \\'active\\'\", (r[\"tenant_id\"], key)).fetchone()' contacts/importer.py
````

## #39 possible write: `bash`

````bash
python3 -c "
import sys
with open('contacts/importer.py', 'r') as f:
    lines = f.readlines()

# Find the line with 'seen = c.execute' and replace it and the next line
for i, line in enumerate(lines):
    if 'seen = c.execute' in line and 'WHERE tenant_id = %s AND email = %s' in line:
        # Replace this line
        lines[i] = '                seen = c.execute(f\"SELECT 1 FROM {S.table(\\\"contacts\\\")} WHERE tenant_id = %s AND email_norm = %s AND status = \\'active\\'\", (r[\\\"tenant_id\\\"], key)).fetchone()\\n'
        # Remove the next line
        if i+1 < len(lines):
            lines[i+1] = ''
        break

with open('contacts/importer.py', 'w') as f:
    f.writelines(lines)
"
````

## #43 possible write: `bash`

````bash
python3 -c "
import sys
with open('contacts/importer.py', 'r') as f:
    lines = f.readlines()

# line numbers are 0-indexed
# line 30 is the key line
# line 31 is the seen line we want to replace
lines[31] = '                seen = c.execute(f\"SELECT 1 FROM {S.table(\\'contacts\\')} WHERE tenant_id = %s AND email_norm = %s AND status = \\'active\\'\", (r[\\\"tenant_id\\\"], key)).fetchone()\\n'

with open('contacts/importer.py', 'w') as f:
    f.writelines(lines)
"
````

## #45 possible write: `bash`

````bash
python3 -c "
import sys
with open('contacts/importer.py', 'r') as f:
    lines = f.readlines()

lines[31] = '''                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'\", (r[\"tenant_id\"], key)).fetchone()\\n'''

with open('contacts/importer.py', 'w') as f:
    f.writelines(lines)
"
````

## #48 possible write: `bash`

````bash
python3 -c "
import sys
with open('contacts/importer.py', 'r') as f:
    lines = f.readlines()

lines[31] = '''                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'\", (r[\"tenant_id\"], key)).fetchone()\\n'''

with open('contacts/importer.py', 'w') as f:
    f.writelines(lines)
"
````

## #51 possible write: `bash`

````bash
python3 -c "
import sys
with open('contacts/importer.py', 'r') as f:
    lines = f.readlines()

lines[31] = '''                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'\", (r[\"tenant_id\"], key)).fetchone()\\n'''

with open('contacts/importer.py', 'w') as f:
    f.writelines(lines)
"
````

## #54 edit: `bash` → `/tmp/importer_fixed.py`

````bash
cat > /tmp/importer_fixed.py << 'EOF'
"""Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.

    python -m contacts.importer fixtures/partner-batch.csv

Runs as the `contacts-import` job. Rows are inserted in batches; a row whose address is already
present for the tenant is skipped. Predates the API's key handling (see docs/DATA_CONTRACT.md) and
still lower-cases on its own; nobody got round to sharing the function after the contract landed.
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

## #56 possible write: `bash`

````bash
cp /tmp/importer_fixed.py contacts/importer.py
````

## #66 edit: `bash` → `/app/merge_duplicates.py`

````bash
cat > /app/merge_duplicates.py << 'EOF'
#!/usr/bin/env python3
"""
Merge duplicate active contacts per tenant and key.
"""
import os
import sys
from collections import defaultdict

# Add the contacts module to the path
sys.path.insert(0, os.path.dirname(__file__))

from contacts import db
from contacts.settings import S


def merge_duplicates():
    conn = db.conn()
    try:
        # Find all active contacts, grouped by tenant_id and email_norm
        # We'll fetch the necessary columns: id, tenant_id, email_norm, created_at
        query = f"""
            SELECT id, tenant_id, email_norm, created_at
            FROM {S.table('contacts')}
            WHERE status = 'active'
            ORDER BY tenant_id, email_norm, created_at, id
        """
        rows = conn.execute(query).fetchall()
        
        # Group by (tenant_id, email_norm)
        groups = defaultdict(list)
        for row in rows:
            cid, tenant_id, email_norm, created_at = row
            groups[(tenant_id, email_norm)].append((cid, created_at))
        
        # For each group with more than one active contact, merge duplicates
        for (tenant_id, email_norm), contacts_in_group in groups.items():
            if len(contacts_in_group) <= 1:
                continue
            # Determine survivor: earliest created_at, then smallest id
            # We already sorted by created_at, id in the query, so the first is the survivor
            survivor_id, survivor_created = contacts_in_group[0]
            duplicates = contacts_in_group[1:]
            
            for dup_id, dup_created in duplicates:
                # Update the duplicate contact to merged
                conn.execute(
                    f"UPDATE {S.table('contacts')} SET status = 'merged', merged_into = %s WHERE id = %s",
                    (survivor_id, dup_id)
                )
                # Move activities
                conn.execute(
                    f"UPDATE {S.table('activities')} SET contact_id = %s WHERE contact_id = %s",
                    (survivor_id, dup_id)
                )
                # Move deals
                conn.execute(
                    f"UPDATE {S.table('deals')} SET contact_id = %s WHERE contact_id = %s",
                    (survivor_id, dup_id)
                )
                # Log
                print(f"Merged contact {dup_id} into {survivor_id} for tenant {tenant_id} key {email_norm}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


if __name__ == "__main__":
    merge_duplicates()
EOF
````

## #91 edit: `bash` → `1"`

````bash
PGPASSWORD=IWLwBcqatIgBkNgB4ks5l58u psql -h simcloud -p 5433 -U u_39fb812f67e8 -d crm__prod__crm -Atc "SELECT tenant_id, email_norm, COUNT(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING COUNT(*) > 1"
````

## #125 edit: `bash` → `1"`

````bash
PGPASSWORD=IWLwBcqatIgBkNgB4ks5l58u psql -h simcloud -p 5433 -U u_39fb812f67e8 -d crm__prod__crm -Atc "SELECT tenant_id, email_norm, COUNT(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING COUNT(*) > 1"
````

