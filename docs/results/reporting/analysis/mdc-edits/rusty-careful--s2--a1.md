# rusty-careful / merge-duplicate-contacts / seed 2 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-careful--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__cDkg8LY/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-careful--merge-duplicate-contacts--s2--a1/merge-duplicate-contacts__cDkg8LY/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #17 edit: `edit_file` → `contacts/app.py`

````json
{
  "path": "contacts/app.py",
  "old_string": "    key = email_key(email)\n    existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT",
  "new_string": "    key = email_key(email)\n    existing = db.find_active(tenant, key)"
}
````

## #18 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "import csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom .settings import S\n\n\ndef lowered(email):\n    return email.lower()\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = lowered(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s\",\n                                 (r[\"tenant_id\"], r[\"email\"])).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0",
  "new_string": "import csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom . import normalize\nfrom .settings import S\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = normalize.email_key(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'\",\n                                 (r[\"tenant_id\"], key)).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0"
}
````

## #25 edit: `bash` → `1`

````bash
psql "postgresql://u_47c0e64be211:${RUSTY_SECRET_1}@simcloud:5433/crm__prod__crm?sslmode=disable" -c "WITH ranked AS (SELECT id, tenant_id, email_norm, created_at, ROW_NUMBER() OVER (PARTITION BY tenant_id, email_norm ORDER BY created_at, id) AS rn FROM contacts WHERE status = 'active') SELECT tenant_id, email_norm, array_agg(id ORDER BY created_at, id) as ids FROM ranked GROUP BY tenant_id, email_norm HAVING count(*) > 1;"
````

