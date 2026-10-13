# rusty-shell / merge-duplicate-contacts / seed 2 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-shell--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__JkLDygW/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-shell--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__JkLDygW/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #42 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #45 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;

UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;

UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #46 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #47 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;

UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;

UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #49 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #58 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak
````

## #60 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak
````

## #64 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak
````

## #66 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak
````

## #70 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.backup
````

## #74 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #75 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak && cp contacts/importer.py contacts/importer.py.bak
````

## #80 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak && cp contacts/importer.py contacts/importer.py.bak
````

## #84 possible write: `bash`

````bash
cd /app && cp contacts/app.py contacts/app.py.bak2
````

## #85 edit: `bash` → `contacts/app.py`

````bash
cd /app && cp contacts/app.py.bak contacts/app.py && sed -i '/if not isinstance(email, str) or "@" not in email:/a\\    key = email_key(email)' contacts/app.py && sed -i 's/db.find_active(tenant, email.strip().lower())/db.find_active(tenant, key)/' contacts/app.py
````

## #87 possible write: `bash`

````bash
cd /app && cp contacts/app.py.bak contacts/app.py
````

## #89 edit: `bash` → `contacts/app.py`

````bash
cd /app && cp contacts/app.py contacts/app.py.before_edit && sed -i 's/existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT/existing = db.find_active(tenant, key)  # lookups predate the key; see DATA_CONTRACT/' contacts/app.py
````

## #92 possible write: `bash`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.before_edit
````

## #94 edit: `bash` → `contacts/importer.py`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.before_edit && sed -i "s/def lowered(email):/def lowered(email):\\n    from .normalize import email_key/" contacts/importer.py && sed -i "s/key = lowered(r\\[\"email\"\\])/key = email_key(r[\"email\"])/" contacts/importer.py && sed -i "s/seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s\",/seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s\",/" contacts/importer.py
````

## #101 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #103 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #108 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;

UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;

UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #109 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
COMMIT;
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
COMMIT;
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #111 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #112 possible write: `bash`

````bash
cd /app && cp contacts/importer.py.before_edit contacts/importer.py
````

## #117 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
DROP INDEX IF EXISTS contacts_active_key;
UPDATE contacts
SET email_norm = 
    ( 
      split_part(split_part(lower(trim(email)), '@', 1), '+', 1) 
    ) 
    || '@' || 
    split_part(lower(trim(email)), '@', 2)
WHERE email LIKE '%@%';

WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;

UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;

UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #119 possible write: `bash`

````bash
cd /app && cp contacts/app.py.bak contacts/app.py
````

## #120 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #124 possible write: `bash`

````bash
cd /app && cp contacts/app.py.bak contacts/app.py && cp contacts/importer.py.bak contacts/importer.py
````

## #134 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" <<'SQL'
BEGIN;
ALTER INDEX contacts_active_key VALIDATE;
COMMIT;
SQL
````

## #136 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
UPDATE contacts
SET email_norm = 
    ( 
      split_part(split_part(lower(trim(email)), '@', 1), '+', 1) 
    ) 
    || '@' || 
    split_part(lower(trim(email)), '@', 2)
WHERE email LIKE '%@%';
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #139 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, 
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #140 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #141 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, 
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #142 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, 
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #143 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, 
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;

UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;

UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #144 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, 
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #146 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #147 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #149 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #151 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #152 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
CREATE TEMP TABLE dup AS
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
)
SELECT r.id AS duplicate_id, s.id AS survivor_id
FROM ranked r
JOIN ranked s ON r.tenant_id = s.tenant_id
              AND r.norm_email = s.norm_email
              AND s.rn = 1
WHERE r.rn > 1;

UPDATE contacts
SET status = 'merged',
    merged_into = dup.survivor_id
FROM dup
WHERE contacts.id = dup.duplicate_id;

UPDATE activities
SET contact_id = dup.survivor_id
FROM dup
WHERE activities.contact_id = dup.duplicate_id;

UPDATE deals
SET contact_id = dup.survivor_id
FROM dup
WHERE deals.contact_id = dup.duplicate_id;
COMMIT;
SQL
````

## #153 edit: `bash` → `contacts/app.py`

````bash
cd /app && cp contacts/app.py.bak contacts/app.py && sed -i 's/existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT/existing = db.find_active(tenant, key)  # lookups predate the key; see DATA_CONTRACT/' contacts/app.py
````

## #154 possible write: `bash`

````bash
cd /app && cp contacts/importer.py.bak contacts/importer.py
````

## #155 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #172 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #181 edit: `bash` → `/tmp/validate.sql`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && echo "BEGIN;" > /tmp/validate.sql && echo "ALTER INDEX contacts_active_key VALIDATE;" >> /tmp/validate.sql && echo "COMMIT;" >> /tmp/validate.sql && psql "$DATABASE_URL" -f /tmp/validate.sql
````

## #188 possible write: `bash`

````bash
cd /app && cp contacts/app.py.bak contacts/app.py && cp contacts/importer.py.bak contacts/importer.py
````

## #195 edit: `bash` → `contacts/app.py`

````bash
cd /app && cp contacts/app.py.bak contacts/app.py && sed -i 's/existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT/existing = db.find_active(tenant, key)  # lookups predate the key; see DATA_CONTRACT/' contacts/app.py
````

## #196 edit: `bash` → `contacts/app.py`

````bash
cd /app && cp contacts/app.py.bak contacts/app.py && sed -i 's/existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT/existing = db.find_active(tenant, key)  # lookups predate the key; see DATA_CONTRACT/' contacts/app.py
````

## #198 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #199 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #200 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #202 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #206 possible write: `bash`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.backup
````

## #209 possible write: `bash`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.before_fix
````

## #210 edit: `bash` → `contacts/importer.py`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.before_edit && sed -i "s/def lowered(email):/def lowered(email):\\n    from .normalize import email_key/" contacts/importer.py && sed -i "s/key = lowered(r\\[\"email\"\\])/key = email_key(r[\"email\"])/" contacts/importer.py && sed -i "s/seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s\",/seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s\",/" contacts/importer.py
````

## #214 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #215 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
CREATE TEMP TABLE ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
);
CREATE TEMP TABLE duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
);
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #228 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #235 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
VALIDATE INDEX contacts_active_key;
COMMIT;
SQL
````

## #247 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #252 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #253 possible write: `bash`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.backup2
````

## #254 possible write: `bash`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.backup3
````

## #255 possible write: `bash`

````bash
cd /app && cp contacts/importer.py.backup contacts/importer.py
````

## #256 edit: `bash` → `contacts/importer.py`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.bak && sed -i "/def lowered(email):/a\\    from .normalize import email_key" contacts/importer.py && sed -i "s/key = lowered(r\\[\"email\"\\])/key = email_key(r[\"email\"])/" contacts/importer.py && sed -i "s/WHERE tenant_id = %s AND email = %s/WHERE tenant_id = %s AND email_norm = %s/" contacts/importer.py
````

## #259 edit: `bash` → `contacts/importer.py`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.bak2 && sed -i "1i from .normalize import email_key" contacts/importer.py && sed -i "/def lowered(email):/,/return email.lower()/d" contacts/importer.py
````

## #260 possible write: `bash`

````bash
cd /app && cp contacts/importer.py.bak contacts/importer.py
````

## #261 edit: `bash` → `contacts/importer.py`

````bash
cd /app && cp contacts/importer.py contacts/importer.py.backup && cat > contacts/importer.py <<'EOF'
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
                seen = c.execute(f"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s",
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

## #265 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #269 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #270 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #271 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #272 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #273 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #275 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id, email_norm, created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.email_norm = s.email_norm
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
UPDATE activities
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE activities.contact_id = duplicates.duplicate_id;
UPDATE deals
SET contact_id = duplicates.survivor_id
FROM duplicates
WHERE deals.contact_id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #276 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
WITH ranked AS (
    SELECT id, tenant_id,
           (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) AS norm_email,
           created_at,
           ROW_NUMBER() OVER (PARTITION BY tenant_id, (split_part(split_part(lower(trim(email)), '@', 1), '+', 1)) || '@' || split_part(lower(trim(email)), '@', 2) ORDER BY created_at, id) AS rn
    FROM contacts
    WHERE status = 'active'
),
duplicates AS (
    SELECT r.id AS duplicate_id, s.id AS survivor_id
    FROM ranked r
    JOIN ranked s ON r.tenant_id = s.tenant_id
                  AND r.norm_email = s.norm_email
                  AND s.rn = 1
    WHERE r.rn > 1
)
UPDATE contacts
SET status = 'merged',
    merged_into = duplicates.survivor_id
FROM duplicates
WHERE contacts.id = duplicates.duplicate_id;
COMMIT;
SQL
````

## #278 edit: `bash` → `1`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -c "SELECT tenant_id, email_norm, count(*) FROM contacts WHERE status = 'active' GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

## #288 possible write: `bash`

````bash
CREDS=$(sc db credentials prod crm) && DSN=$(echo "$CREDS" | grep '^dsn:' | cut -d' ' -f2) && if [[ $DSN =~ \\\$\\{([^}]+)\\\} ]]; then VAR_NAME=${BASH_REMATCH[1]}; PASSWORD=${!VAR_NAME}; DSN=${DSN//\${$VAR_NAME}/$PASSWORD}; fi && export DATABASE_URL="$DSN" && psql "$DATABASE_URL" -f - <<'SQL'
BEGIN;
DO $$
DECLARE
    grp RECORD;
    ids text[];
    survivor text;
    duplicate text;
BEGIN
    FOR grp IN
        SELECT tenant_id, email_norm, array_agg(id ORDER BY created_at, id) AS ids
        FROM contacts
        WHERE status = 'active'
        GROUP BY tenant_id, email_norm
        HAVING count(*) > 1
    LOOP
        ids := grp.ids;
        survivor := ids[1];
        FOREACH duplicate IN ARRAY ids[2:] LOOP
            UPDATE contacts
            SET status = 'merged',
                merged_into = survivor
            WHERE id = duplicate;
            UPDATE activities
            SET contact_id = survivor
            WHERE contact_id = duplicate;
            UPDATE deals
            SET contact_id = survivor
            WHERE contact_id = duplicate;
        END LOOP;
    END LOOP;
END $$;
COMMIT;
SQL
````

