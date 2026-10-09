import secrets
import threading

import psycopg

from .settings import S

_local = threading.local()


def conn():
    c = getattr(_local, "c", None)
    if c is None or c.closed:
        c = psycopg.connect(S.dsn(), autocommit=True)
        _local.c = c
    return c


def _t(name):
    return S.table(name)


COLS = ["id", "tenant_id", "email", "email_norm", "name", "status", "merged_into", "created_at"]


def _row(r):
    if not r:
        return None
    out = dict(zip(COLS, r))
    out["created_at"] = out["created_at"].isoformat()
    return out


def get(cid):
    return _row(conn().execute(f"SELECT {', '.join(COLS)} FROM {_t('contacts')} WHERE id = %s", (cid,)).fetchone())


def find_active(tenant_id, email_norm):
    return _row(conn().execute(
        f"SELECT {', '.join(COLS)} FROM {_t('contacts')} WHERE tenant_id = %s AND email_norm = %s "
        f"AND status = 'active' ORDER BY created_at, id LIMIT 1", (tenant_id, email_norm)).fetchone())


def create(tenant_id, email, email_norm, name):
    cid = "c-" + secrets.token_hex(5)
    conn().execute(
        f"INSERT INTO {_t('contacts')} (id, tenant_id, email, email_norm, name, status) "
        f"VALUES (%s, %s, %s, %s, %s, 'active')", (cid, tenant_id, email, email_norm, name))
    activity(cid, "created", "api")
    return cid


def touch(cid):
    cur = conn().execute(f"UPDATE {_t('contacts')} SET last_seen_at = now() WHERE id = %s", (cid,))
    return cur.rowcount == 1


def activity(cid, kind, note):
    conn().execute(f"INSERT INTO {_t('activities')} (contact_id, kind, note) VALUES (%s, %s, %s)", (cid, kind, note))


def activities(cid):
    rows = conn().execute(f"SELECT id, kind, note, at FROM {_t('activities')} WHERE contact_id = %s ORDER BY id",
                          (cid,)).fetchall()
    return [{"id": r[0], "kind": r[1], "note": r[2], "at": r[3].isoformat()} for r in rows]
