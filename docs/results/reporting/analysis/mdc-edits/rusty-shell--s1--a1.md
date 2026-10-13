# rusty-shell / merge-duplicate-contacts / seed 1 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-shell--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__evDcCpX/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-shell--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__evDcCpX/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #15 edit: `bash` → `1`

````bash
PGPASSWORD=${RUSTY_SECRET_2} psql "postgresql://u_88dea4774ca1:${RUSTY_SECRET_2}@simcloud:5433/crm__prod__crm" -c "SELECT tenant_id, email_norm, COUNT(*) as cnt, STRING_AGG(id, ', ') as ids FROM contacts WHERE status='active' GROUP BY tenant_id, email_norm HAVING COUNT(*) > 1;"
````

## #35 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

-- First, let's create a temporary table to hold the survivor for each group
CREATE TEMP TABLE survivor AS
SELECT
  MIN(id) KEEP (DENSE_RANK FIRST ORDER BY created_at, id) AS survivor_id,
  tenant_id,
  email_norm
FROM contacts
WHERE status = 'active'
GROUP BY tenant_id, email_norm
HAVING COUNT(*) > 1;

-- Now, update the duplicates: set status to merged and merged_into to survivor_id
UPDATE contacts c
SET status = 'merged',
    merged_into = s.survivor_id
FROM survivor s
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'active'
  AND c.id != s.survivor_id;

-- Now, update activities to point to the survivor
UPDATE activities a
SET contact_id = s.survivor_id
FROM survivor s
JOIN contacts c ON c.id = a.contact_id
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

-- Similarly for deals (if exists)
UPDATE deals d
SET contact_id = s.survivor_id
FROM survivor s
JOIN contacts c ON c.id = d.contact_id
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

COMMIT;
SQL
````

## #37 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

-- Step 1: Create a temporary table with survivor for each group
CREATE TEMP TABLE survivor AS
SELECT
  tenant_id,
  email_norm,
  MIN(id) AS survivor_id
FROM (
  SELECT
    tenant_id,
    email_norm,
    id,
    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
) ranked
WHERE rn = 1
GROUP BY tenant_id, email_norm
HAVING COUNT(*) > 1;

-- Step 2: Update duplicates to merged
UPDATE contacts c
SET status = 'merged',
    merged_into = s.survivor_id
FROM survivor s
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'active'
  AND c.id != s.survivor_id;

-- Step 3: Update activities to point to survivor
UPDATE activities a
SET contact_id = s.survivor_id
FROM survivor s
JOIN contacts c ON c.id = a.contact_id
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

-- Step 4: Update deals to point to survivor
UPDATE deals d
SET contact_id = s.survivor_id
FROM survivor s
JOIN contacts c ON c.id = d.contact_id
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

COMMIT;
SQL
````

## #38 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

-- Step 1: Create a temporary table with survivor for each group
CREATE TEMP TABLE survivor AS
SELECT
  tenant_id,
  email_norm,
  MIN(id) AS survivor_id
FROM (
  SELECT
    tenant_id,
    email_norm,
    id,
    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
) ranked
WHERE rn = 1
GROUP BY tenant_id, email_norm
HAVING COUNT(*) > 1;

-- Step 2: Update duplicates to merged
UPDATE contacts c
SET status = 'merged',
    merged_into = s.survivor_id
FROM survivor s
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'active'
  AND c.id != s.survivor_id;

-- Step 3: Update activities to point to survivor
UPDATE activities
SET contact_id = s.survivor_id
FROM survivor s
JOIN contacts c ON c.id = activities.contact_id
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

-- Step 4: Update deals to point to survivor
UPDATE deals
SET contact_id = s.survivor_id
FROM survivor s
JOIN contacts c ON c.id = deals.contact_id
WHERE c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

COMMIT;
SQL
````

## #39 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

-- Create survivor table
CREATE TEMP TABLE survivor AS
SELECT
  tenant_id,
  email_norm,
  MIN(id) AS survivor_id
FROM (
  SELECT
    tenant_id,
    email_norm,
    id,
    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
) ranked
WHERE rn = 1
GROUP BY tenant_id, email_norm
HAVING COUNT(*) > 1;

-- Update contacts
UPDATE contacts
SET status = 'merged',
    merged_into = s.survivor_id
FROM survivor s
WHERE contacts.tenant_id = s.tenant_id
  AND contacts.email_norm = s.email_norm
  AND contacts.status = 'active'
  AND contacts.id != s.survivor_id;

-- Update activities
UPDATE activities
SET contact_id = s.survivor_id
FROM survivor s, contacts c
WHERE c.id = activities.contact_id
  AND c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

-- Update deals
UPDATE deals
SET contact_id = s.survivor_id
FROM survivor s, contacts c
WHERE c.id = deals.contact_id
  AND c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

COMMIT;
SQL
````

## #41 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
CREATE TEMP TABLE survivor AS
SELECT
  tenant_id,
  email_norm,
  MIN(id) AS survivor_id
FROM (
  SELECT
    tenant_id,
    email_norm,
    id,
    ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
) ranked
WHERE rn = 1
GROUP BY tenant_id, email_norm
HAVING COUNT(*) > 1;

SELECT * FROM survivor;
SQL
````

## #42 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
CREATE TEMP TABLE survivor AS
SELECT tenant_id, email_norm, id AS survivor_id
FROM (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
) ranked
WHERE rn = 1
AND (tenant_id, email_norm) IN (
  SELECT tenant_id, email_norm
  FROM contacts
  WHERE status = 'active'
  GROUP BY tenant_id, email_norm
  HAVING COUNT(*) > 1
);

SELECT * FROM survivor;
SQL
````

## #43 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

-- Create survivor table
CREATE TEMP TABLE survivor AS
SELECT tenant_id, email_norm, id AS survivor_id
FROM (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
) ranked
WHERE rn = 1
AND (tenant_id, email_norm) IN (
  SELECT tenant_id, email_norm
  FROM contacts
  WHERE status = 'active'
  GROUP BY tenant_id, email_norm
  HAVING COUNT(*) > 1
);

-- Update duplicates to merged
UPDATE contacts
SET status = 'merged',
    merged_into = s.survivor_id
FROM survivor s
WHERE contacts.tenant_id = s.tenant_id
  AND contacts.email_norm = s.email_norm
  AND contacts.status = 'active'
  AND contacts.id != s.survivor_id;

-- Update activities
UPDATE activities
SET contact_id = s.survivor_id
FROM survivor s, contacts c
WHERE c.id = activities.contact_id
  AND c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

-- Update deals
UPDATE deals
SET contact_id = s.survivor_id
FROM survivor s, contacts c
WHERE c.id = deals.contact_id
  AND c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

COMMIT;
SQL
````

## #45 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_2} psql "postgresql://u_88dea4774ca1:${RUSTY_SECRET_2}@simcloud:5433/crm__prod__crm" -f - <<'SQL'
BEGIN;

-- Create survivor table
CREATE TEMP TABLE survivor AS
SELECT tenant_id, email_norm, id AS survivor_id
FROM (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
) ranked
WHERE rn = 1
AND (tenant_id, email_norm) IN (
  SELECT tenant_id, email_norm
  FROM contacts
  WHERE status = 'active'
  GROUP BY tenant_id, email_norm
  HAVING COUNT(*) > 1
);

-- Update duplicates to merged
UPDATE contacts
SET status = 'merged',
    merged_into = s.survivor_id
FROM survivor s
WHERE contacts.tenant_id = s.tenant_id
  AND contacts.email_norm = s.email_norm
  AND contacts.status = 'active'
  AND contacts.id != s.survivor_id;

-- Update activities
UPDATE activities
SET contact_id = s.survivor_id
FROM survivor s, contacts c
WHERE c.id = activities.contact_id
  AND c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

-- Update deals
UPDATE deals
SET contact_id = s.survivor_id
FROM survivor s, contacts c
WHERE c.id = deals.contact_id
  AND c.tenant_id = s.tenant_id
  AND c.email_norm = s.email_norm
  AND c.status = 'merged'
  AND c.merged_into = s.survivor_id;

COMMIT;
SQL
````

## #46 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;
WITH ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
),
survivor AS (
  SELECT tenant_id, email_norm, id AS survivor_id
  FROM ranked
  WHERE rn = 1
    AND (tenant_id, email_norm) IN (
      SELECT tenant_id, email_norm
      FROM contacts
      WHERE status = 'active'
      GROUP BY tenant_id, email_norm
      HAVING COUNT(*) > 1
    )
),
merged AS (
  UPDATE contacts
  SET status = 'merged',
      merged_into = survivor.survivor_id
  FROM survivor
  WHERE contacts.tenant_id = survivor.tenant_id
    AND contacts.email_norm = survivor.email_norm
    AND contacts.status = 'active'
    AND contacts.id != survivor.survivor_id
  RETURNING contacts.id AS merged_id, survivor.survivor_id
)
UPDATE activities
SET contact_id = merged.survivor_id
FROM merged
WHERE activities.contact_id = merged.merged_id;

UPDATE deals
SET contact_id = merged.survivor_id
FROM merged
WHERE deals.contact_id = merged.merged_id;

COMMIT;
SQL
````

## #47 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

WITH ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
),
survivor AS (
  SELECT tenant_id, email_norm, id AS survivor_id
  FROM ranked
  WHERE rn = 1
    AND (tenant_id, email_norm) IN (
      SELECT tenant_id, email_norm
      FROM contacts
      WHERE status = 'active'
      GROUP BY tenant_id, email_norm
      HAVING COUNT(*) > 1
    )
),
duplicates AS (
  SELECT c.id AS duplicate_id, s.survivor_id
  FROM contacts c
  JOIN survivor s ON c.tenant_id = s.tenant_id AND c.email_norm = s.email_norm
  WHERE c.status = 'active' AND c.id != s.survivor_id
)
UPDATE contacts
SET status = 'merged',
    merged_into = d.survivor_id
FROM duplicates d
WHERE contacts.id = d.duplicate_id;

UPDATE activities
SET contact_id = d.survivor_id
FROM duplicates d
JOIN contacts c ON c.id = activities.contact_id
WHERE c.id = d.duplicate_id;

UPDATE deals
SET contact_id = d.survivor_id
FROM duplicates d
JOIN contacts c ON c.id = deals.contact_id
WHERE c.id = d.duplicate_id;

COMMIT;
SQL
````

## #48 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

-- Update contacts: set duplicates to merged, pointing to survivor
UPDATE contacts
SET status = 'merged',
    merged_into = (
      SELECT id
      FROM (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
        FROM contacts
        WHERE status = 'active'
      ) ranked
      WHERE ranked.tenant_id = contacts.tenant_id
        AND ranked.email_norm = contacts.email_norm
        AND ranked.rn = 1
    )
WHERE status = 'active'
  AND (tenant_id, email_norm) IN (
    SELECT tenant_id, email_norm
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1
  )
  AND id != (
    SELECT id
    FROM (
      SELECT *,
             ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
      FROM contacts
      WHERE status = 'active'
    ) ranked
    WHERE ranked.tenant_id = contacts.tenant_id
      AND ranked.email_norm = contacts.email_norm
      AND ranked.rn = 1
  );

-- Update activities: point to survivor
UPDATE activities
SET contact_id = (
  SELECT id
  FROM (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
  ) ranked
  WHERE ranked.tenant_id = (
    SELECT tenant_id
    FROM contacts
    WHERE id = activities.contact_id
  )
    AND ranked.email_norm = (
      SELECT email_norm
      FROM contacts
      WHERE id = activities.contact_id
    )
    AND ranked.rn = 1
)
WHERE contact_id IN (
  SELECT id
  FROM contacts
  WHERE status = 'merged'
);

-- Update deals: point to survivor
UPDATE deals
SET contact_id = (
  SELECT id
  FROM (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
  ) ranked
  WHERE ranked.tenant_id = (
    SELECT tenant_id
    FROM contacts
    WHERE id = deals.contact_id
  )
    AND ranked.email_norm = (
      SELECT email_norm
      FROM contacts
      WHERE id = deals.contact_id
    )
    AND ranked.rn = 1
)
WHERE contact_id IN (
  SELECT id
  FROM contacts
  WHERE status = 'merged'
);

COMMIT;
SQL
````

## #50 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

WITH ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
),
survivor AS (
  SELECT tenant_id, email_norm, id AS survivor_id
  FROM ranked
  WHERE rn = 1
    AND (tenant_id, email_norm) IN (
      SELECT tenant_id, email_norm
      FROM contacts
      WHERE status = 'active'
      GROUP BY tenant_id, email_norm
      HAVING COUNT(*) > 1
    )
),
duplicates AS (
  SELECT c.id AS duplicate_id, s.survivor_id
  FROM contacts c
  JOIN survivor s ON c.tenant_id = s.tenant_id AND c.email_norm = s.email_norm
  WHERE c.status = 'active' AND c.id != s.survivor_id
)
UPDATE contacts
SET status = 'merged',
    merged_into = d.survivor_id
FROM duplicates d
WHERE contacts.id = d.duplicate_id;

UPDATE activities
SET contact_id = d.survivor_id
FROM duplicates d
JOIN contacts c ON c.id = activities.contact_id
WHERE c.id = d.duplicate_id;

UPDATE deals
SET contact_id = d.survivor_id
FROM duplicates d
JOIN contacts c ON c.id = deals.contact_id
WHERE c.id = d.duplicate_id;

COMMIT;
SQL
````

## #51 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

WITH ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
),
survivor AS (
  SELECT tenant_id, email_norm, id AS survivor_id
  FROM ranked
  WHERE rn = 1
    AND (tenant_id, email_norm) IN (
      SELECT tenant_id, email_norm
      FROM contacts
      WHERE status = 'active'
      GROUP BY tenant_id, email_norm
      HAVING COUNT(*) > 1
    )
),
duplicates AS (
  SELECT c.id AS duplicate_id, s.survivor_id
  FROM contacts c
  JOIN survivor s ON c.tenant_id = s.tenant_id AND c.email_norm = s.email_norm
  WHERE c.status = 'active' AND c.id != s.survivor_id
)
UPDATE contacts
SET status = 'merged',
    merged_into = d.survivor_id
FROM duplicates d
WHERE contacts.id = d.duplicate_id;

UPDATE activities
SET contact_id = d.survivor_id
FROM duplicates d
JOIN contacts c ON c.id = activities.contact_id
WHERE c.id = d.duplicate_id;

UPDATE deals
SET contact_id = d.survivor_id
FROM duplicates d
JOIN contacts c ON c.id = deals.contact_id
WHERE c.id = d.duplicate_id;

COMMIT;
SQL
````

## #53 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
SELECT
  c.id,
  c.tenant_id,
  c.email_norm,
  (SELECT id
   FROM (
     SELECT *,
            ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
     FROM contacts
     WHERE status = 'active'
   ) ranked
   WHERE ranked.tenant_id = c.tenant_id
     AND ranked.email_norm = c.email_norm
     AND ranked.rn = 1) AS survivor_id
FROM contacts c
WHERE c.status = 'active'
  AND (c.tenant_id, c.email_norm) IN (
    SELECT tenant_id, email_norm
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1
  );
SQL
````

## #54 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

WITH ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
),
survivor AS (
  SELECT tenant_id, email_norm, id AS survivor_id
  FROM ranked
  WHERE rn = 1
    AND (tenant_id, email_norm) IN (
      SELECT tenant_id, email_norm
      FROM contacts
      WHERE status = 'active'
      GROUP BY tenant_id, email_norm
      HAVING COUNT(*) > 1
    )
)
UPDATE contacts
SET status = 'merged',
    merged_into = survivor.survivor_id
FROM survivor
WHERE contacts.tenant_id = survivor.tenant_id
  AND contacts.email_norm = survivor.email_norm
  AND contacts.status = 'active'
  AND contacts.id != survivor.survivor_id;

UPDATE activities
SET contact_id = survivor.survivor_id
FROM survivor
JOIN contacts c ON c.id = activities.contact_id
WHERE c.tenant_id = survivor.tenant_id
  AND c.email_norm = survivor.email_norm
  AND c.status = 'merged'
  AND c.merged_into = survivor.survivor_id;

UPDATE deals
SET contact_id = survivor.survivor_id
FROM survivor
JOIN contacts c ON c.id = deals.contact_id
WHERE c.tenant_id = survivor.tenant_id
  AND c.email_norm = survivor.email_norm
  AND c.status = 'merged'
  AND c.merged_into = survivor.survivor_id;

COMMIT;
SQL
````

## #55 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_3} psql "postgresql://u_77d5c18d2704:${RUSTY_SECRET_3}@simcloud:5433/crm__prod__crm-test-merge" -f - <<'SQL'
BEGIN;

-- Update contacts: set duplicates to merged, pointing to survivor
UPDATE contacts
SET status = 'merged',
    merged_into = (
      SELECT id
      FROM (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
        FROM contacts
        WHERE status = 'active'
      ) ranked
      WHERE ranked.tenant_id = contacts.tenant_id
        AND ranked.email_norm = contacts.email_norm
        AND ranked.rn = 1
    )
WHERE status = 'active'
  AND (tenant_id, email_norm) IN (
    SELECT tenant_id, email_norm
    FROM contacts
    WHERE status = 'active'
    GROUP BY tenant_id, email_norm
    HAVING COUNT(*) > 1
  )
  AND id != (
    SELECT id
    FROM (
      SELECT *,
             ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
      FROM contacts
      WHERE status = 'active'
    ) ranked
    WHERE ranked.tenant_id = contacts.tenant_id
      AND ranked.email_norm = contacts.email_norm
      AND ranked.rn = 1
  );

-- Update activities: point to survivor
UPDATE activities
SET contact_id = (
  SELECT merged_into
  FROM contacts
  WHERE id = activities.contact_id
    AND status = 'merged'
)
WHERE contact_id IN (
  SELECT id
  FROM contacts
  WHERE status = 'merged'
);

-- Update deals: point to survivor
UPDATE deals
SET contact_id = (
  SELECT merged_into
  FROM contacts
  WHERE id = deals.contact_id
    AND status = 'merged'
)
WHERE contact_id IN (
  SELECT id
  FROM contacts
  WHERE status = 'merged'
);

COMMIT;
SQL
````

## #57 possible write: `bash`

````bash
PGPASSWORD=${RUSTY_SECRET_2} psql "postgresql://u_88dea4774ca1:${RUSTY_SECRET_2}@simcloud:5433/crm__prod__crm" -f - <<'SQL'
BEGIN;

WITH ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
  FROM contacts
  WHERE status = 'active'
),
survivor AS (
  SELECT tenant_id, email_norm, id AS survivor_id
  FROM ranked
  WHERE rn = 1
    AND (tenant_id, email_norm) IN (
      SELECT tenant_id, email_norm
      FROM contacts
      WHERE status = 'active'
      GROUP BY tenant_id, email_norm
      HAVING COUNT(*) > 1
    )
),
merged AS (
  UPDATE contacts
  SET status = 'merged',
      merged_into = survivor.survivor_id
  FROM survivor
  WHERE contacts.tenant_id = survivor.tenant_id
    AND contacts.email_norm = survivor.email_norm
    AND contacts.status = 'active'
    AND contacts.id != survivor.survivor_id
  RETURNING contacts.id AS merged_id, survivor.survivor_id
),
updated_activities AS (
  UPDATE activities
  SET contact_id = merged.survivor_id
  FROM merged
  WHERE activities.contact_id = merged.merged_id
),
updated_deals AS (
  UPDATE deals
  SET contact_id = merged.survivor_id
  FROM merged
  WHERE deals.contact_id = merged.merged_id
)
SELECT 1;

COMMIT;
SQL
````

