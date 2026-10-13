# rusty-baseline / merge-duplicate-contacts / seed 0 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-baseline--merge-duplicate-contacts--s0--a1/merge-duplicate-contacts__A82bEsG/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-baseline--merge-duplicate-contacts--s0--a1/merge-duplicate-contacts__A82bEsG/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #39 edit: `edit_file` → `contacts/app.py`

````json
{
  "path": "contacts/app.py",
  "old_string": "    key = email_key(email)\n    existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT",
  "new_string": "    key = email_key(email)\n    existing = db.find_active(tenant, key)"
}
````

## #40 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "\"\"\"Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.\n\n    python -m contacts.importer fixtures/partner-batch.csv\n\nRuns as the `contacts-import` job. Rows are inserted in batches; a row whose address is already\npresent for the tenant is skipped. Predates the API's key handling (see docs/DATA_CONTRACT.md) and\nstill lower-cases on its own; nobody got round to sharing the function after the contract landed.\n\"\"\"\nimport csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom .settings import S\n\n\ndef lowered(email):\n    return email.lower()\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = lowered(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s\",\n                                 (r[\"tenant_id\"], r[\"email\"])).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0\n\n\nif __name__ == \"__main__\":\n    sys.exit(main(sys.argv[1]))",
  "new_string": "\"\"\"Partner batch import: one CSV (tenant_id,email,name) per run, written straight to the table.\n\n    python -m contacts.importer fixtures/partner-batch.csv\n\nRuns as the `contacts-import` job. Rows are inserted in batches; a row whose address is already\npresent for the tenant is skipped. Uses the same key normalization as the API.\n\"\"\"\nimport csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom . import normalize\nfrom .settings import S\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = normalize.email_key(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s\",\n                                 (r[\"tenant_id\"], key)).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0\n\n\nif __name__ == \"__main__\":\n    sys.exit(main(sys.argv[1]))"
}
````

