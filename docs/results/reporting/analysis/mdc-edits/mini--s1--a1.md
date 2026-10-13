# mini / merge-duplicate-contacts / seed 1 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--mini--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__vV9bFJV/agent/mini-swe-agent.trajectory.json`
- Final data state: `jobs/reporting-v1--mini--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__vV9bFJV/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #20 edit: `bash` → `/app/merge_duplicates.py`

````bash
cat > /app/merge_duplicates.py << 'EOF'
import os
import psycopg

# Use the same DSN as the service
dsn = os.environ.get('DATABASE_URL')
if not dsn:
    # Fallback to the one we got from sc db credentials (for testing)
    dsn = "postgresql://u_84410927ae6c:3jY_ZWLaglhtB2bFJ6lPku4s@simcloud:5433/crm__prod__crm?sslmode=disable"

def merge_group(conn, tenant_id, email_norm, ids):
    # ids is a list of contact ids in the group
    # Determine survivor: earliest created_at, then smallest id
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, created_at
            FROM contacts
            WHERE id = ANY(%s)
            ORDER BY created_at, id
            LIMIT 1
            """,
            (ids,)
        )
        row = cur.fetchone()
        if not row:
            return
        survivor_id, survivor_created = row
        # The duplicates are the rest
        duplicate_ids = [id for id in ids if id != survivor_id]
        if not duplicate_ids:
            return
        # For each duplicate, move activities and deals, then mark as merged
        for dup_id in duplicate_ids:
            # Update activities
            cur.execute(
                """
                UPDATE activities
                SET contact_id = %s
                WHERE contact_id = %s
                """,
                (survivor_id, dup_id)
            )
            # Update deals
            cur.execute(
                """
                UPDATE deals
                SET contact_id = %s
                WHERE contact_id = %s
                """,
                (survivor_id, dup_id)
            )
            # Update contact
            cur.execute(
                """
                UPDATE contacts
                SET status = 'merged', merged_into = %s
                WHERE id = %s
                """,
                (survivor_id, dup_id)
            )
        # Commit after each group? We'll commit at the end of the function, but we want to commit per group to avoid large transaction.
        conn.commit()

def main():
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            # Find all groups of active contacts with duplicate (tenant_id, email_norm)
            cur.execute(
                """
                SELECT tenant_id, email_norm, array_agg(id) as ids
                FROM contacts
                WHERE status = 'active'
                GROUP BY tenant_id, email_norm
                HAVING COUNT(*) > 1
                ORDER BY tenant_id, email_norm
                """
            )
            groups = cur.fetchall()
            print(f"Found {len(groups)} groups with duplicates")
            for tenant_id, email_norm, ids in groups:
                print(f"Processing group: tenant={tenant_id}, email_norm={email_norm}, ids={ids}")
                merge_group(conn, tenant_id, email_norm, ids)
        # After processing all groups, we can validate the index
        # But first, let's check if there are any remaining duplicates
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT tenant_id, email_norm
                    FROM contacts
                    WHERE status = 'active'
                    GROUP BY tenant_id, email_norm
                    HAVING COUNT(*) > 1
                ) t
                """
            )
            remaining = cur.fetchone()[0]
            if remaining > 0:
                print(f"ERROR: {remaining} duplicate groups remain after merging")
                return 1
            else:
                print("No duplicate active contacts remain.")
                # Now validate the unique index if it exists and is invalid
                # We can try to validate the index by altering it.
                # But we need to check the index state.
                cur.execute(
                    """
                    SELECT indexdef FROM pg_indexes WHERE tablename = 'contacts' AND indexname = 'contacts_active_key'
                    """
                    )
                idx = cur.fetchone()
                if idx:
                    print(f"Index definition: {idx[0]}")
                    # Check if it's valid
                    cur.execute(
                        """
                        SELECT indisvalid FROM pg_index WHERE indexname = 'contacts_active_key'
                        """
                        )
                    valid = cur.fetchone()[0]
                    if not valid:
                        print("Index is invalid, attempting to validate...")
                        try:
                            with conn.cursor() as cur2:
                                cur2.execute("ALTER INDEX contacts_active_key VALIDATE;")
                                conn.commit()
                                print("Index validated.")
                        except Exception as e:
                            print(f"Failed to validate index: {e}")
                            return 1
                    else:
                        print("Index is already valid.")
                else:
                    print("Index contacts_active_key not found.")
        return 0

if __name__ == '__main__':
    exit(main())
EOF
````

## #39 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.backup
````

## #40 edit: `bash` → `contacts/app.py`

````bash
sed -i '/existing = db.find_active(tenant, email.strip().lower())/c\    existing = db.find_active(tenant, email_key(email))  # use the normalized key' contacts/app.py
````

## #43 possible write: `bash`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.backup
````

## #44 edit: `bash` → `/app/contacts/importer.py`

````bash
cat > /app/contacts/importer.py << 'EOF'
"""Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.

    python -m contacts.importer fixtures/partner-batch.csv

Runs as the `contacts-import` job. Rows are inserted in batches; a row whose address is already
present for the tenant is skipped. Now uses the normalized key as per the data contract.
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
                seen = c.execute(
                    f"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s",
                    (r["tenant_id"], key),
                ).fetchone()
                if seen:
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

## #57 edit: `bash` → `contacts/app.py`

````bash
sed -i 's/existing = db.find_active(tenant, email_key(email))  # use the normalized key/existing = db.find_active(tenant, key)/' contacts/app.py
````

## #68 edit: `bash` → `/tmp/test.csv`

````bash
cd /app && cat > /tmp/test.csv << 'EOF'
tenant_id,email,name
t-test-import,test@example.com,Test User
t-test-import,Test+tag@example.com,Test User
EOF
````

