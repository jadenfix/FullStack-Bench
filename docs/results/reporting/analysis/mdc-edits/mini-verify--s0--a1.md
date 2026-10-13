# mini-verify / merge-duplicate-contacts / seed 0 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--mini-verify--merge-duplicate-contacts--s0--a1/merge-duplicate-contacts__2ggNrin/agent/mini-swe-agent.round1.trajectory.json`
- Final data state: `jobs/reporting-v1--mini-verify--merge-duplicate-contacts--s0--a1/merge-duplicate-contacts__2ggNrin/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #33 edit: `bash` → `/app/merge_duplicates.py`

````bash
cat > /app/merge_duplicates.py << 'EOF'
import os
import psycopg
from psycopg.rows import dict_row

from contacts.normalize import email_key

# Use the same DSN as the service
dsn = os.environ.get('DATABASE_URL')
if not dsn:
    # fallback to individual env vars
    dsn = f"postgresql://{os.environ.get('PGUSER')}:{os.environ.get('PGPASSWORD')}@{os.environ.get('PGHOST')}:{os.environ.get('PGPORT')}/{os.environ.get('PGDATABASE')}?sslmode=disable"

def main():
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Find duplicate groups
            cur.execute("""
                SELECT tenant_id, email_norm, array_agg(id ORDER BY created_at, id) as ids
                FROM contacts
                WHERE status = 'active'
                GROUP BY tenant_id, email_norm
                HAVING COUNT(*) > 1
                ORDER BY tenant_id, email_norm
            """)
            groups = cur.fetchall()
            print(f"Found {len(groups)} duplicate groups")
            for group in groups:
                tenant_id = group['tenant_id']
                email_norm = group['email_norm']
                ids = group['ids']
                survivor_id = ids[0]  # earliest created, smallest id due to ORDER BY
                duplicate_ids = ids[1:]
                print(f"Group tenant={tenant_id}, email_norm={email_norm}: survivor {survivor_id}, duplicates {duplicate_ids}")
                # Update duplicates to merged
                for dup_id in duplicate_ids:
                    cur.execute(
                        """
                        UPDATE contacts
                        SET status = 'merged', merged_into = %s
                        WHERE id = %s
                        """,
                        (survivor_id, dup_id)
                    )
                    # Move activities
                    cur.execute(
                        """
                        UPDATE activities
                        SET contact_id = %s
                        WHERE contact_id = %s
                        """,
                        (survivor_id, dup_id)
                    )
                    # Move deals
                    cur.execute(
                        """
                        UPDATE deals
                        SET contact_id = %s
                        WHERE contact_id = %s
                        """,
                        (survivor_id, dup_id)
                    )
            conn.commit()
            print("Merge completed")

if __name__ == '__main__':
    main()
EOF
````

## #44 edit: `bash` → `/app/contacts/app.py`

````bash
sed -i '/existing = db.find_active(tenant, email.strip().lower())/c\    existing = db.find_active(tenant, key)  # use normalized key per DATA_CONTRACT' /app/contacts/app.py
````

## #47 edit: `bash` → `/app/contacts/importer.py`

````bash
sed -i '/from .settings import S/a\\from .normalize import email_key' /app/contacts/importer.py
````

## #50 edit: `bash` → `/app/contacts/importer.py`

````bash
sed -i '19,20d' /app/contacts/importer.py
````

## #52 edit: `bash` → `/app/contacts/importer.py`

````bash
sed -i '28s/key = lowered(r\["email"\])/key = email_key(r["email"])/' /app/contacts/importer.py
````

## #54 edit: `bash` → `active`

````bash
sed -i '29s/.*/                active = c.execute(f\"SELECT 1 FROM {S.table(\"'"'"'contacts'"'"'\")} WHERE tenant_id = %s AND email_norm = %s AND status = \"'"'"'active'"'"'\", (r[\"tenant_id\"], key)).fetchone()/' /app/contacts/importer.py
````

## #56 edit: `bash` → `/app/contacts/importer.py`

````bash
cat > /app/contacts/importer.py << 'EOF'
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
                active = c.execute(
                    f"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'",
                    (r["tenant_id"], key),
                ).fetchone()
                if active:
                    skipped += 1
                    continue
                cid = "c-" + secrets.token_hex(5)
                c.execute(
                    f"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) "
                    f"VALUES (%s, %s, %s, %s, %s, 'active')",
                    (cid, r["tenant_id"], r["email"], key, r["name"]),
                )
                c.execute(
                    f"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)",
                    (cid, path),
                )
                inserted += 1
            print(f"batch {start // batch + 1}: inserted={inserted} skipped={skipped}", flush=True)
    print(f"done: inserted={inserted} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
EOF
````

