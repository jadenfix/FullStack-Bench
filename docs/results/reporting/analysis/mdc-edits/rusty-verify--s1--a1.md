# rusty-verify / merge-duplicate-contacts / seed 1 / attempt 1

- Ledger status: `scored`; reward: 0.0
- Trajectory: `docs/results/reporting/records/evidence/reporting-v1--rusty-verify--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__oDL7bN5/agent/rusty.trajectory.json`
- Final data state: `jobs/reporting-v1--rusty-verify--merge-duplicate-contacts--s1--a1/merge-duplicate-contacts__oDL7bN5/artifacts/evidence/` in the M4 run directory on the run host (not published).

Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.

## #13 edit: `edit_file` → `contacts/app.py`

````json
{
  "path": "contacts/app.py",
  "old_string": "    key = email_key(email)\n    existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT\n    if existing:\n        return 200, existing",
  "new_string": "    key = email_key(email)\n    existing = db.find_active(tenant, key)  # lookups predate the key; see DATA_CONTRACT\n    if existing:\n        return 200, existing"
}
````

## #14 edit: `edit_file` → `contacts/importer.py`

````json
{
  "path": "contacts/importer.py",
  "old_string": "import csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom .settings import S\n\n\ndef lowered(email):\n    return email.lower()\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = lowered(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s\",\n                                 (r[\"tenant_id\"], r[\"email\"])).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0",
  "new_string": "import csv\nimport secrets\nimport sys\n\nimport psycopg\n\nfrom . import normalize\nfrom .settings import S\n\n\ndef main(path):\n    batch = int(S.import_batch)\n    inserted = skipped = 0\n    with open(path, newline=\"\") as f, psycopg.connect(S.dsn(), autocommit=True) as c:\n        rows = list(csv.DictReader(f))\n        for start in range(0, len(rows), batch):\n            for r in rows[start:start + batch]:\n                key = normalize.email_key(r[\"email\"])\n                seen = c.execute(f\"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email_norm = %s AND status = 'active'\",\n                                 (r[\"tenant_id\"], key)).fetchone()\n                if seen:\n                    skipped += 1\n                    continue\n                cid = \"c-\" + secrets.token_hex(5)\n                c.execute(f\"INSERT INTO {S.table('contacts')} (id, tenant_id, email, email_norm, name, status) \"\n                          f\"VALUES (%s, %s, %s, %s, %s, 'active')\", (cid, r[\"tenant_id\"], r[\"email\"], key, r[\"name\"]))\n                c.execute(f\"INSERT INTO {S.table('activities')} (contact_id, kind, note) VALUES (%s, 'imported', %s)\",\n                          (cid, path))\n                inserted += 1\n            print(f\"batch {start // batch + 1}: inserted={inserted} skipped={skipped}\", flush=True)\n    print(f\"done: inserted={inserted} skipped={skipped}\")\n    return 0"
}
````

## #15 edit: `write_file` → `/app/merge_duplicates.py`

````json
{
  "path": "/app/merge_duplicates.py",
  "content": "#!/usr/bin/env python3\nimport sys\nsys.path.insert(0, '/app')\n\nfrom contacts import db\n\ndef main():\n    # Find duplicate groups\n    conn = db.conn()\n    try:\n        with conn.cursor() as cur:\n            # Get groups with more than one active contact\n            cur.execute(\"\"\"\n                SELECT tenant_id, email_norm, array_agg(id ORDER BY created_at, id) as ids\n                FROM contacts\n                WHERE status = 'active'\n                GROUP BY tenant_id, email_norm\n                HAVING count(*) > 1\n                ORDER BY tenant_id, email_norm\n            \"\"\")\n            groups = cur.fetchall()\n            print(f\"Found {len(groups)} duplicate groups\")\n            for tenant_id, email_norm, ids in groups:\n                survivor = ids[0]\n                duplicates = ids[1:]\n                print(f\"Group ({tenant_id}, {email_norm}): survivor {survivor}, duplicates {duplicates}\")\n                # For each duplicate, move activities and deals, then mark as merged\n                for dup_id in duplicates:\n                    # Move activities\n                    cur.execute(\n                        f\"UPDATE {db._t('activities')} SET contact_id = %s WHERE contact_id = %s\",\n                        (survivor, dup_id)\n                    )\n                    print(f\"  Updated activities for {dup_id} -> {survivor}: {cur.rowcount} rows\")\n                    # Move deals\n                    cur.execute(\n                        f\"UPDATE {db._t('deals')} SET contact_id = %s WHERE contact_id = %s\",\n                        (survivor, dup_id)\n                    )\n                    print(f\"  Updated deals for {dup_id} -> {survivor}: {cur.rowcount} rows\")\n                    # Mark duplicate as merged\n                    cur.execute(\n                        f\"UPDATE {db._t('contacts')} SET status = 'merged', merged_into = %s WHERE id = %s\",\n                        (survivor, dup_id)\n                    )\n                    print(f\"  Marked {dup_id} as merged into {survivor}\")\n            # Commit all changes\n            conn.commit()\n            print(\"Merge completed successfully\")\n    except Exception as e:\n        conn.rollback()\n        print(f\"Error: {e}\")\n        raise\n    finally:\n        conn.close()\n\nif __name__ == '__main__':\n    main()"
}
````

