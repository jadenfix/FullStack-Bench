"""A small connection pool with a permit per request. `POOL_MAX` is the database's connection
budget for this service (the managed Postgres has other tenants); `acquire` waits at most
`pool_wait_s` for a permit and raises PoolExhausted after that."""
import threading
import time

import psycopg

from .settings import S


class PoolExhausted(Exception):
    pass


class Pool:
    def __init__(self):
        self.max = int(S.pool_max)
        self._idle: list[psycopg.Connection] = []
        self._in_use: dict[int, dict] = {}
        self._cv = threading.Condition()
        self.waiting = 0

    def acquire(self, owner: str, timeout: float | None = None) -> psycopg.Connection:
        deadline = time.monotonic() + (float(S.pool_wait_s) if timeout is None else timeout)
        with self._cv:
            self.waiting += 1
            try:
                while not self._idle and len(self._in_use) >= self.max:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PoolExhausted(f"no connection within {S.pool_wait_s}s ({len(self._in_use)} in use)")
                    self._cv.wait(remaining)
            finally:
                self.waiting -= 1
            conn = self._idle.pop() if self._idle else psycopg.connect(S.dsn(), autocommit=True)
            if conn.closed:
                conn = psycopg.connect(S.dsn(), autocommit=True)
            self._in_use[id(conn)] = {"owner": owner, "since": time.time()}
            return conn

    def release(self, conn: psycopg.Connection) -> None:
        with self._cv:
            self._in_use.pop(id(conn), None)
            if not conn.closed:
                try:
                    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                        conn.rollback()
                except psycopg.Error:
                    conn.close()
            if not conn.closed:
                self._idle.append(conn)
            self._cv.notify()

    def snapshot(self) -> dict:
        with self._cv:
            now = time.time()
            return {"max": self.max, "in_use": len(self._in_use), "idle": len(self._idle), "waiting": self.waiting,
                    "held": sorted(({"owner": v["owner"], "age_s": round(now - v["since"], 1)} for v in self._in_use.values()),
                                   key=lambda h: -h["age_s"])}


POOL = Pool()


def customer(cid: str) -> dict | None:
    conn = POOL.acquire(f"customer:{cid}")
    try:
        row = conn.execute("SELECT id, name, plan, open_tickets FROM customers WHERE id = %s", (cid,)).fetchone()
        return dict(zip(["id", "name", "plan", "open_tickets"], row)) if row else None
    finally:
        POOL.release(conn)
