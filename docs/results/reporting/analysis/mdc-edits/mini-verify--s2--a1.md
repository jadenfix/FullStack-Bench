# mini-verify / merge-duplicate-contacts / seed 2 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--mini-verify--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__ow9aynt/agent/mini-swe-agent.round1.trajectory.json`
- Final data state: `jobs/reporting-v1--mini-verify--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__ow9aynt/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #22 edit: `bash` → `/tmp/check_duplicates.py`

````bash
cat > /tmp/check_duplicates.py << 'EOF'
import os
import psycopg
from contacts.settings import S

# Override DSN to use staging credentials from environment? We'll just use the service's DSN which points to whatever env.
# But we can set environment variable to point to staging.
# Let's just use the service's DSN as is; we need to ensure we are pointing to staging.
# We'll set CONTACTS_dsn_source appropriately? Actually, the DSN is built from dsn_source and env var.
# We'll instead directly connect using the staging credentials we got from sc.
# For simplicity, we'll use the service's DSN but we can change environment.
# Let's just use the staging DSN we obtained.

DSN = "postgresql://u_7a65a344ef56:1vewFH5L9hB_OQ13NU0Az4YA@simcloud:5433/crm__staging__crm?sslmode=disable"

def main():
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Find duplicate groups
            cur.execute("""
                SELECT tenant_id, email_norm, COUNT(*) as cnt, MIN(created_at) as earliest, MIN(id) as min_id
                FROM contacts
                WHERE status = 'active'
                GROUP BY tenant_id, email_norm
                HAVING COUNT(*) > 1
                ORDER BY tenant_id, email_norm
            """)
            duplicates = cur.fetchall()
            print(f"Found {len(duplicates)} duplicate groups")
            for tenant_id, email_norm, cnt, earliest, min_id in duplicates:
                print(f"Tenant: {tenant_id}, norm: {email_norm}, count: {cnt}")
                # Get all active contacts for this group
                cur.execute("""
                    SELECT id, created_at, email, name
                    FROM contacts
                    WHERE tenant_id = %s AND email_norm = %s AND status = 'active'
                    ORDER BY created_at, id
                """, (tenant_id, email_norm))
                rows = cur.fetchall()
                for r in rows:
                    print(f"  id: {r[0]}, created: {r[1]}, email: {r[2]}, name: {r[3]}")
                # Survivor is first row
                survivor = rows[0]
                print(f"  Survivor: {survivor[0]}")
                # Others to merge
                for r in rows[1:]:
                    print(f"  To merge: {r[0]} -> {survivor[0]}")
            # Also check for existing merged contacts
            cur.execute("""
                SELECT COUNT(*) FROM contacts WHERE status = 'merged'
            """)
            merged_count = cur.fetchone()[0]
            print(f"Existing merged contacts: {merged_count}")

if __name__ == '__main__':
    main()
EOF
````

## #25 edit: `bash` → `/tmp/check_duplicates2.py`

````bash
cat > /tmp/check_duplicates2.py << 'EOF'
import psycopg

DSN = "postgresql://u_7a65a344ef56:1vewFH5L9hB_OQ13NU0Az4YA@simcloud:5433/crm__staging__crm?sslmode=disable"

def main():
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Find duplicate groups
            cur.execute("""
                SELECT tenant_id, email_norm, COUNT(*) as cnt, MIN(created_at) as earliest, MIN(id) as min_id
                FROM contacts
                WHERE status = 'active'
                GROUP BY tenant_id, email_norm
                HAVING COUNT(*) > 1
                ORDER BY tenant_id, email_norm
            """)
            duplicates = cur.fetchall()
            print(f"Found {len(duplicates)} duplicate groups")
            for tenant_id, email_norm, cnt, earliest, min_id in duplicates:
                print(f"Tenant: {tenant_id}, norm: {email_norm}, count: {cnt}")
                # Get all active contacts for this group
                cur.execute("""
                    SELECT id, created_at, email, name
                    FROM contacts
                    WHERE tenant_id = %s AND email_norm = %s AND status = 'active'
                    ORDER BY created_at, id
                """, (tenant_id, email_norm))
                rows = cur.fetchall()
                for r in rows:
                    print(f"  id: {r[0]}, created: {r[1]}, email: {r[2]}, name: {r[3]}")
                # Survivor is first row
                survivor = rows[0]
                print(f"  Survivor: {survivor[0]}")
                # Others to merge
                for r in rows[1:]:
                    print(f"  To merge: {r[0]} -> {survivor[0]}")
            # Also check for existing merged contacts
            cur.execute("""
                SELECT COUNT(*) FROM contacts WHERE status = 'merged'
            """)
            merged_count = cur.fetchone()[0]
            print(f"Existing merged contacts: {merged_count}")

if __name__ == '__main__':
    main()
EOF
````

## #27 edit: `bash` → `/tmp/check_duplicates_prod.py`

````bash
cat > /tmp/check_duplicates_prod.py << 'EOF'
import psycopg

DSN = "postgresql://u_827aed0c2977:hKMLChp5HYT_VKby1G3YhbEL@simcloud:5433/crm__prod__crm?sslmode=disable"

def main():
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Find duplicate groups
            cur.execute("""
                SELECT tenant_id, email_norm, COUNT(*) as cnt, MIN(created_at) as earliest, MIN(id) as min_id
                FROM contacts
                WHERE status = 'active'
                GROUP BY tenant_id, email_norm
                HAVING COUNT(*) > 1
                ORDER BY tenant_id, email_norm
            """)
            duplicates = cur.fetchall()
            print(f"Found {len(duplicates)} duplicate groups")
            for tenant_id, email_norm, cnt, earliest, min_id in duplicates:
                print(f"Tenant: {tenant_id}, norm: {email_norm}, count: {cnt}")
                # Get all active contacts for this group
                cur.execute("""
                    SELECT id, created_at, email, name
                    FROM contacts
                    WHERE tenant_id = %s AND email_norm = %s AND status = 'active'
                    ORDER BY created_at, id
                """, (tenant_id, email_norm))
                rows = cur.fetchall()
                for r in rows:
                    print(f"  id: {r[0]}, created: {r[1]}, email: {r[2]}, name: {r[3]}")
                # Survivor is first row
                survivor = rows[0]
                print(f"  Survivor: {survivor[0]}")
                # Others to merge
                for r in rows[1:]:
                    print(f"  To merge: {r[0]} -> {survivor[0]}")
            # Also check for existing merged contacts
            cur.execute("""
                SELECT COUNT(*) FROM contacts WHERE status = 'merged'
            """)
            merged_count = cur.fetchone()[0]
            print(f"Existing merged contacts: {merged_count}")

if __name__ == '__main__':
    main()
EOF
````

## #37 edit: `bash` → `/tmp/test_merge.py`

````bash
cat > /tmp/test_merge.py << 'EOF'
import psycopg
import sys

DSN = "postgresql://u_7a65a344ef56:1vewFH5L9hB_OQ13NU0Az4YA@simcloud:5433/crm__staging__crm?sslmode=disable"

def main():
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # Start a transaction (autocommit is False by default)
            # Insert test tenant
            test_tenant = 't-test'
            email1 = 'test+tag@example.com'
            email2 = 'test@example.com'
            # Both normalize to test@example.com (trim, lower, remove +tag)
            # Insert two active contacts
            cur.execute("""
                INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, ('c-test-1', test_tenant, email1, 'test@example.com', 'Test One'))
            cur.execute("""
                INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, ('c-test-2', test_tenant, email2, 'test@example.com', 'Test Two'))
            # Insert activities
            cur.execute("""
                INSERT INTO activities (contact_id, kind, note)
                VALUES (%s, 'test', 'activity1')
            """, ('c-test-1',))
            cur.execute("""
                INSERT INTO activities (contact_id, kind, note)
                VALUES (%s, 'test', 'activity2')
            """, ('c-test-2',))
            # Insert deals
            cur.execute("""
                INSERT INTO deals (id, contact_id, amount_cents, stage)
                VALUES (%s, %s, 100, 'prospect')
            """, ('d-test-1', 'c-test-1'))
            cur.execute("""
                INSERT INTO deals (id, contact_id, amount_cents, stage)
                VALUES (%s, %s, 200, 'negotiation')
            """, ('d-test-2', 'c-test-2'))
            # Now run the merge logic
            # We'll use the same SQL as planned
            sql = """
                WITH ranked AS (
                  SELECT
                    id,
                    tenant_id,
                    email_norm,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn,
                    COUNT(*) OVER (PARTITION BY tenant_id, email_norm) AS cnt
                  FROM contacts
                  WHERE status = 'active'
                ),
                duplicates AS (
                  SELECT id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn > 1
                ),
                survivors AS (
                  SELECT id AS survivor_id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn = 1
                )
                UPDATE contacts
                SET status = 'merged',
                    merged_into = survivors.survivor_id
                FROM duplicates
                JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                WHERE contacts.id = duplicates.id
                RETURNING contacts.id, contacts.status, contacts.merged_into
            """
            cur.execute(sql)
            updated = cur.fetchall()
            print(f"Updated contacts: {updated}")
            # Check activities and deals updates? We'll do that separately.
            # Update activities
            cur.execute("""
                UPDATE activities
                SET contact_id = survivors.survivor_id
                FROM duplicates
                JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                WHERE activities.contact_id = duplicates.id
            """)
            print(f"Activities updated: {cur.rowcount}")
            # Update deals
            cur.execute("""
                UPDATE deals
                SET contact_id = survivors.survivor_id
                FROM duplicates
                JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                WHERE deals.contact_id = duplicates.id
            """)
            print(f"Deals updated: {cur.rowcount}")
            # Now select to verify
            cur.execute("""
                SELECT id, status, merged_into, email
                FROM contacts
                WHERE tenant_id = %s
                ORDER BY id
            """, (test_tenant,))
            rows = cur.fetchall()
            print("Contacts after merge:")
            for r in rows:
                print(f"  {r}")
            cur.execute("""
                SELECT contact_id, kind, note
                FROM activities
                WHERE contact_id IN ('c-test-1', 'c-test-2')
                ORDER BY contact_id
            """)
            acts = cur.fetchall()
            print("Activities after merge:")
            for a in acts:
                print(f"  {a}")
            cur.execute("""
                SELECT contact_id, amount_cents, stage
                FROM deals
                WHERE contact_id IN ('c-test-1', 'c-test-2')
                ORDER BY contact_id
            """)
            deals = cur.fetchall()
            print("Deals after merge:")
            for d in deals:
                print(f"  {d}")
            # Rollback the transaction to leave staging unchanged
            conn.rollback()
            print("Transaction rolled back.")

if __name__ == '__main__':
    main()
EOF
````

## #39 edit: `bash` → `/tmp/test_merge2.py`

````bash
cat > /tmp/test_merge2.py << 'EOF'
import psycopg
import sys

DSN = "postgresql://u_7a65a344ef56:1vewFH5L9hB_OQ13NU0Az4YA@simcloud:5433/crm__staging__crm?sslmode=disable"

def main():
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # Start a transaction (autocommit is False by default)
            # Insert test tenant
            test_tenant = 't-test'
            email1 = 'test+tag@example.com'
            email2 = 'test@example.com'
            # Both normalize to test@example.com (trim, lower, remove +tag)
            # Insert two active contacts
            cur.execute("""
                INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, ('c-test-1', test_tenant, email1, 'test@example.com', 'Test One'))
            cur.execute("""
                INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, ('c-test-2', test_tenant, email2, 'test@example.com', 'Test Two'))
            # Insert activities
            cur.execute("""
                INSERT INTO activities (contact_id, kind, note)
                VALUES (%s, 'test', 'activity1')
            """, ('c-test-1',))
            cur.execute("""
                INSERT INTO activities (contact_id, kind, note)
                VALUES (%s, 'test', 'activity2')
            """, ('c-test-2',))
            # Insert deals
            cur.execute("""
                INSERT INTO deals (id, contact_id, amount_cents, stage)
                VALUES (%s, %s, 100, 'prospect')
            """, ('d-test-1', 'c-test-1'))
            cur.execute("""
                INSERT INTO deals (id, contact_id, amount_cents, stage)
                VALUES (%s, %s, 200, 'negotiation')
            """, ('d-test-2', 'c-test-2'))
            # Now run the merge logic for contacts
            sql_contacts = """
                WITH ranked AS (
                  SELECT
                    id,
                    tenant_id,
                    email_norm,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn,
                    COUNT(*) OVER (PARTITION BY tenant_id, email_norm) AS cnt
                  FROM contacts
                  WHERE status = 'active'
                ),
                duplicates AS (
                  SELECT id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn > 1
                ),
                survivors AS (
                  SELECT id AS survivor_id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn = 1
                )
                UPDATE contacts
                SET status = 'merged',
                    merged_into = survivors.survivor_id
                FROM duplicates
                JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                WHERE contacts.id = duplicates.id
            """
            cur.execute(sql_contacts)
            print(f"Contacts updated: {cur.rowcount}")
            # Update activities
            sql_activities = """
                WITH ranked AS (
                  SELECT
                    id,
                    tenant_id,
                    email_norm,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn,
                    COUNT(*) OVER (PARTITION BY tenant_id, email_norm) AS cnt
                  FROM contacts
                  WHERE status = 'active'
                ),
                duplicates AS (
                  SELECT id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn > 1
                ),
                survivors AS (
                  SELECT id AS survivor_id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn = 1
                )
                UPDATE activities
                SET contact_id = survivors.survivor_id
                FROM duplicates
                JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                WHERE activities.contact_id = duplicates.id
            """
            cur.execute(sql_activities)
            print(f"Activities updated: {cur.rowcount}")
            # Update deals
            sql_deals = """
                WITH ranked AS (
                  SELECT
                    id,
                    tenant_id,
                    email_norm,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn,
                    COUNT(*) OVER (PARTITION BY tenant_id, email_norm) AS cnt
                  FROM contacts
                  WHERE status = 'active'
                ),
                duplicates AS (
                  SELECT id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn > 1
                ),
                survivors AS (
                  SELECT id AS survivor_id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn = 1
                )
                UPDATE deals
                SET contact_id = survivors.survivor_id
                FROM duplicates
                JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                WHERE deals.contact_id = duplicates.id
            """
            cur.execute(sql_deals)
            print(f"Deals updated: {cur.rowcount}")
            # Now select to verify
            cur.execute("""
                SELECT id, status, merged_into, email
                FROM contacts
                WHERE tenant_id = %s
                ORDER BY id
            """, (test_tenant,))
            rows = cur.fetchall()
            print("Contacts after merge:")
            for r in rows:
                print(f"  {r}")
            cur.execute("""
                SELECT contact_id, kind, note
                FROM activities
                WHERE contact_id IN ('c-test-1', 'c-test-2')
                ORDER BY contact_id
            """)
            acts = cur.fetchall()
            print("Activities after merge:")
            for a in acts:
                print(f"  {a}")
            cur.execute("""
                SELECT contact_id, amount_cents, stage
                FROM deals
                WHERE contact_id IN ('c-test-1', 'c-test-2')
                ORDER BY contact_id
            """)
            deals = cur.fetchall()
            print("Deals after merge:")
            for d in deals:
                print(f"  {d}")
            # Rollback the transaction to leave staging unchanged
            conn.rollback()
            print("Transaction rolled back.")

if __name__ == '__main__':
    main()
EOF
````

## #46 edit: `bash` → `/tmp/test_merge3.py`

````bash
cat > /tmp/test_merge3.py << 'EOF'
import psycopg
import sys

DSN = "postgresql://u_7a65a344ef56:1vewFH5L9hB_OQ13NU0Az4YA@simcloud:5433/crm__staging__crm?sslmode=disable"

def main():
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # Start a transaction (autocommit is False by default)
            # Insert test tenant
            test_tenant = 't-test'
            email1 = 'test+tag@example.com'
            email2 = 'test@example.com'
            # Both normalize to test@example.com (trim, lower, remove +tag)
            # Insert two active contacts
            cur.execute("""
                INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, ('c-test-1', test_tenant, email1, 'test@example.com', 'Test One'))
            cur.execute("""
                INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, ('c-test-2', test_tenant, email2, 'test@example.com', 'Test Two'))
            # Insert activities
            cur.execute("""
                INSERT INTO activities (contact_id, kind, note)
                VALUES (%s, 'test', 'activity1')
            """, ('c-test-1',))
            cur.execute("""
                INSERT INTO activities (contact_id, kind, note)
                VALUES (%s, 'test', 'activity2')
            """, ('c-test-2',))
            # Insert deals
            cur.execute("""
                INSERT INTO deals (id, contact_id, amount_cents, stage)
                VALUES (%s, %s, 100, 'prospect')
            """, ('d-test-1', 'c-test-1'))
            cur.execute("""
                INSERT INTO deals (id, contact_id, amount_cents, stage)
                VALUES (%s, %s, 200, 'negotiation')
            """, ('d-test-2', 'c-test-2'))
            # Now run the merge logic using CTE with returning
            sql = """
                WITH ranked AS (
                  SELECT
                    id,
                    tenant_id,
                    email_norm,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn,
                    COUNT(*) OVER (PARTITION BY tenant_id, email_norm) AS cnt
                  FROM contacts
                  WHERE status = 'active'
                ),
                duplicates AS (
                  SELECT id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn > 1
                ),
                survivors AS (
                  SELECT id AS survivor_id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn = 1
                ),
                updated_contacts AS (
                  UPDATE contacts
                  SET status = 'merged',
                      merged_into = survivors.survivor_id
                  FROM duplicates
                  JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                  WHERE contacts.id = duplicates.id
                  RETURNING duplicates.id AS duplicate_id, survivors.survivor_id
                )
                UPDATE activities
                SET contact_id = updated_contacts.survivor_id
                FROM updated_contacts
                WHERE activities.contact_id = updated_contacts.duplicate_id
            """
            cur.execute(sql)
            print(f"After contacts and activities update: {cur.rowcount} activities updated")
            # Update deals similarly
            sql_deals = """
                WITH ranked AS (
                  SELECT
                    id,
                    tenant_id,
                    email_norm,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn,
                    COUNT(*) OVER (PARTITION BY tenant_id, email_norm) AS cnt
                  FROM contacts
                  WHERE status = 'active'
                ),
                duplicates AS (
                  SELECT id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn > 1
                ),
                survivors AS (
                  SELECT id AS survivor_id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn = 1
                ),
                updated_contacts AS (
                  UPDATE contacts
                  SET status = 'merged',
                      merged_into = survivors.survivor_id
                  FROM duplicates
                  JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                  WHERE contacts.id = duplicates.id
                  RETURNING duplicates.id AS duplicate_id, survivors.survivor_id
                )
                UPDATE deals
                SET contact_id = updated_contacts.survivor_id
                FROM updated_contacts
                WHERE deals.contact_id = updated_contacts.duplicate_id
            """
            cur.execute(sql_deals)
            print(f"Deals updated: {cur.rowcount}")
            # Now select to verify
            cur.execute("""
                SELECT id, status, merged_into, email
                FROM contacts
                WHERE tenant_id = %s
                ORDER BY id
            """, (test_tenant,))
            rows = cur.fetchall()
            print("Contacts after merge:")
            for r in rows:
                print(f"  {r}")
            cur.execute("""
                SELECT contact_id, kind, note
                FROM activities
                WHERE contact_id IN ('c-test-1', 'c-test-2')
                ORDER BY contact_id
            """)
            acts = cur.fetchall()
            print("Activities after merge:")
            for a in acts:
                print(f"  {a}")
            cur.execute("""
                SELECT contact_id, amount_cents, stage
                FROM deals
                WHERE contact_id IN ('c-test-1', 'c-test-2')
                ORDER BY contact_id
            """)
            deals = cur.fetchall()
            print("Deals after merge:")
            for d in deals:
                print(f"  {d}")
            # Rollback the transaction to leave staging unchanged
            conn.rollback()
            print("Transaction rolled back.")

if __name__ == '__main__':
    main()
EOF
````

## #48 edit: `bash` → `/tmp/test_merge4.py`

````bash
cat > /tmp/test_merge4.py << 'EOF'
import psycopg
import sys

DSN = "postgresql://u_7a65a344ef56:1vewFH5L9hB_OQ13NU0Az4YA@simcloud:5433/crm__staging__crm?sslmode=disable"

def main():
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # Start a transaction (autocommit is False by default)
            # Insert test tenant
            test_tenant = 't-test'
            email1 = 'test+tag@example.com'
            email2 = 'test@example.com'
            # Both normalize to test@example.com (trim, lower, remove +tag)
            # Insert two active contacts
            cur.execute("""
                INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, ('c-test-1', test_tenant, email1, 'test@example.com', 'Test One'))
            cur.execute("""
                INSERT INTO contacts (id, tenant_id, email, email_norm, name, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, ('c-test-2', test_tenant, email2, 'test@example.com', 'Test Two'))
            # Insert activities
            cur.execute("""
                INSERT INTO activities (contact_id, kind, note)
                VALUES (%s, 'test', 'activity1')
            """, ('c-test-1',))
            cur.execute("""
                INSERT INTO activities (contact_id, kind, note)
                VALUES (%s, 'test', 'activity2')
            """, ('c-test-2',))
            # Insert deals
            cur.execute("""
                INSERT INTO deals (id, contact_id, amount_cents, stage)
                VALUES (%s, %s, 100, 'prospect')
            """, ('d-test-1', 'c-test-1'))
            cur.execute("""
                INSERT INTO deals (id, contact_id, amount_cents, stage)
                VALUES (%s, %s, 200, 'negotiation')
            """, ('d-test-2', 'c-test-2'))
            # Now run the merge logic for all three tables in one statement
            sql = """
                WITH ranked AS (
                  SELECT
                    id,
                    tenant_id,
                    email_norm,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn,
                    COUNT(*) OVER (PARTITION BY tenant_id, email_norm) AS cnt
                  FROM contacts
                  WHERE status = 'active'
                ),
                duplicates AS (
                  SELECT id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn > 1
                ),
                survivors AS (
                  SELECT id AS survivor_id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn = 1
                ),
                updated_contacts AS (
                  UPDATE contacts
                  SET status = 'merged',
                      merged_into = survivors.survivor_id
                  FROM duplicates
                  JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                  WHERE contacts.id = duplicates.id
                  RETURNING duplicates.id AS duplicate_id, survivors.survivor_id
                ),
                updated_activities AS (
                  UPDATE activities
                  SET contact_id = updated_contacts.survivor_id
                  FROM updated_contacts
                  WHERE activities.contact_id = updated_contacts.duplicate_id
                ),
                updated_deals AS (
                  UPDATE deals
                  SET contact_id = updated_contacts.survivor_id
                  FROM updated_contacts
                  WHERE deals.contact_id = updated_contacts.duplicate_id
                )
                SELECT 1
            """
            cur.execute(sql)
            # The query returns a row, but we don't need it
            # Now select to verify
            cur.execute("""
                SELECT id, status, merged_into, email
                FROM contacts
                WHERE tenant_id = %s
                ORDER BY id
            """, (test_tenant,))
            rows = cur.fetchall()
            print("Contacts after merge:")
            for r in rows:
                print(f"  {r}")
            cur.execute("""
                SELECT contact_id, kind, note
                FROM activities
                WHERE contact_id IN ('c-test-1', 'c-test-2')
                ORDER BY contact_id
            """)
            acts = cur.fetchall()
            print("Activities after merge:")
            for a in acts:
                print(f"  {a}")
            cur.execute("""
                SELECT contact_id, amount_cents, stage
                FROM deals
                WHERE contact_id IN ('c-test-1', 'c-test-2')
                ORDER BY contact_id
            """)
            deals = cur.fetchall()
            print("Deals after merge:")
            for d in deals:
                print(f"  {d}")
            # Rollback the transaction to leave staging unchanged
            conn.rollback()
            print("Transaction rolled back.")

if __name__ == '__main__':
    main()
EOF
````

## #50 edit: `bash` → `/tmp/merge_production.py`

````bash
cat > /tmp/merge_production.py << 'EOF'
import psycopg
import sys

# Production DSN from sc db credentials prod crm
DSN = "postgresql://u_827aed0c2977:hKMLChp5HYT_VKby1G3YhbEL@simcloud:5433/crm__prod__crm?sslmode=disable"

def main():
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # Run the merge for all duplicate groups
            sql = """
                WITH ranked AS (
                  SELECT
                    id,
                    tenant_id,
                    email_norm,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn,
                    COUNT(*) OVER (PARTITION BY tenant_id, email_norm) AS cnt
                  FROM contacts
                  WHERE status = 'active'
                ),
                duplicates AS (
                  SELECT id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn > 1
                ),
                survivors AS (
                  SELECT id AS survivor_id, tenant_id, email_norm
                  FROM ranked
                  WHERE cnt > 1 AND rn = 1
                ),
                updated_contacts AS (
                  UPDATE contacts
                  SET status = 'merged',
                      merged_into = survivors.survivor_id
                  FROM duplicates
                  JOIN survivors ON duplicates.tenant_id = survivors.tenant_id AND duplicates.email_norm = survivors.email_norm
                  WHERE contacts.id = duplicates.id
                  RETURNING duplicates.id AS duplicate_id, survivors.survivor_id
                ),
                updated_activities AS (
                  UPDATE activities
                  SET contact_id = updated_contacts.survivor_id
                  FROM updated_contacts
                  WHERE activities.contact_id = updated_contacts.duplicate_id
                ),
                updated_deals AS (
                  UPDATE deals
                  SET contact_id = updated_contacts.survivor_id
                  FROM updated_contacts
                  WHERE deals.contact_id = updated_contacts.duplicate_id
                )
                SELECT 1
            """
            cur.execute(sql)
            # The query returns a row, but we don't need it
            # Commit the transaction
            conn.commit()
            print(f"Merge completed. Updated contacts: {cur.rowcount} (this is the number of rows returned by the final SELECT, not the update counts)")
            # We can get the rowcounts from the cursor's rowcount after each statement? Not easily.
            # Let's do a quick check to see how many contacts were merged.
            cur.execute("""
                SELECT COUNT(*) FROM contacts WHERE status = 'merged'
            """)
            merged_count = cur.fetchone()[0]
            print(f"Total merged contacts after merge: {merged_count}")

if __name__ == '__main__':
    main()
EOF
````

## #59 possible write: `bash`

````bash
cp contacts/app.py contacts/app.py.backup
````

## #60 edit: `bash` → `contacts/app.py`

````bash
sed -i '46s/email.strip().lower()/key/' contacts/app.py
````

## #62 possible write: `bash`

````bash
cp contacts/importer.py contacts/importer.py.backup
````

## #64 edit: `bash` → `contacts/importer.py`

````bash
sed -i '8i from .normalize import email_key' contacts/importer.py
````

## #65 edit: `bash` → `contacts/importer.py`

````bash
sed -i '10,11d' contacts/importer.py
````

## #66 edit: `bash` → `contacts/importer.py`

````bash
sed -i '9i def lowered(email):\\n    return email_key(email)' contacts/importer.py
````

## #70 possible write: `bash`

````bash
python3 -c "
import sys
lines = open('contacts/importer.py').readlines()
# line numbers are 0-indexed
lines[29] = '                seen = c.execute(f\"SELECT 1 FROM {S.table(\\\"contacts\\\")} WHERE tenant_id = %s AND email_norm = %s\",\\n'
lines[30] = '                                 (r[\\\"tenantic\\\"], key)).fetchone()\\n'
# Wait, we need to get the exact indentation.
# Let's just replace the two lines with the new two lines.
# Actually, the line 29 is the line with 'key = lowered(...)'
# Let's find the exact lines by scanning.
for i, line in enumerate(lines):
    if 'seen = c.execute' in line and 'SELECT 1 FROM' in line:
        lines[i] = '                seen = c.execute(f\"SELECT 1 FROM {S.table(\\\"contacts\\\")} WHERE tenant_id = %s AND email_norm = %s\",\\n'
        lines[i+1] = '                                 (r[\\\"tenant_id\\\"], key)).fetchone()\\n'
        break
open('contacts/importer.py', 'w').writelines(lines)
"
````

## #73 possible write: `bash`

````bash
cp contacts/importer.py.backup contacts/importer.py
````

## #75 edit: `bash` → `contacts/importer.py`

````bash
sed -i '/from .settings import S/a from .normalize import email_key' contacts/importer.py
````

## #77 edit: `bash` → `contacts/importer.py`

````bash
sed -i '20s/.*/    return email_key(email)/' contacts/importer.py
````

## #81 possible write: `bash`

````bash
python3 -c "
import sys
lines = open('contacts/importer.py').readlines()
for i, line in enumerate(lines):
    if 'seen = c.execute' in line and 'SELECT 1 FROM' in line:
        lines[i] = '                seen = c.execute(f\"SELECT 1 FROM {S.table(\\\"contacts\\\")} WHERE tenant_id = %s AND email_norm = %s\",\\n'
        lines[i+1] = '                                 (r[\\\"tenant_id\\\"], key)).fetchone()\\n'
        break
open('contacts/importer.py', 'w').writelines(lines)
"
````

## #87 possible write: `bash`

````bash
rm contacts/app.py.backup contacts/importer.py.backup
````

