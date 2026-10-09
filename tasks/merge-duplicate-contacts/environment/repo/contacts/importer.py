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


def lowered(email):
    return email.lower()


def main(path):
    batch = int(S.import_batch)
    inserted = skipped = 0
    with open(path, newline="") as f, psycopg.connect(S.dsn(), autocommit=True) as c:
        rows = list(csv.DictReader(f))
        for start in range(0, len(rows), batch):
            for r in rows[start:start + batch]:
                key = lowered(r["email"])
                seen = c.execute(f"SELECT 1 FROM {S.table('contacts')} WHERE tenant_id = %s AND email = %s",
                                 (r["tenant_id"], r["email"])).fetchone()
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
