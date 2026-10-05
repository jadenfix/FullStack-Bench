import os
import secrets
import threading

import psycopg

from .settings import S

_local = threading.local()


def conn():
    c = getattr(_local, "c", None)
    if c is None or c.closed:
        c = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
        _local.c = c
    return c


def _t(name):
    return S.table(name)


def new_order(cart_id, customer_id, amount_cents, trace):
    oid = "o-" + secrets.token_hex(4)
    conn().execute(
        f"INSERT INTO {_t('orders')} (id, cart_id, customer_id, amount_cents, currency, status, request_id) "
        f"VALUES (%s, %s, %s, %s, %s, 'pending', %s)",
        (oid, cart_id, customer_id, amount_cents, S.currency, trace))
    _event(oid, "created", trace)
    return oid


def mark_paid(oid, charge_id):
    conn().execute(f"UPDATE {_t('orders')} SET status = 'paid', charge_id = %s WHERE id = %s", (charge_id, oid))
    _event(oid, "paid", charge_id)


def set_status(oid, status):
    conn().execute(f"UPDATE {_t('orders')} SET status = %s WHERE id = %s", (status, oid))
    _event(oid, status, None)


def get(oid):
    cur = conn().execute(
        f"SELECT id, cart_id, customer_id, amount_cents, currency, status, charge_id, refunded_cents, "
        f"created_at FROM {_t('orders')} WHERE id = %s", (oid,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ["id", "cart_id", "customer_id", "amount_cents", "currency", "status", "charge_id", "refunded_cents",
            "created_at"]
    out = dict(zip(keys, row))
    out["created_at"] = out["created_at"].isoformat()
    return out


def _event(oid, kind, detail):
    conn().execute(f"INSERT INTO {_t('audit')} (order_id, kind, detail) VALUES (%s, %s, %s)", (oid, kind, detail))
