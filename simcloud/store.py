"""Authoritative state: resources and the audit log, in SQLite.

The audit log is append-only and hash-chained, so the verifier can tell if
anything rewrote history. Only the control plane process opens the database;
agents reach SimCloud through the API, never the file.
"""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from .clock import Clock
from .errors import SimCloudError

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    project TEXT NOT NULL,
    env TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    spec TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (project, env, kind, name)
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    principal TEXT NOT NULL,
    action TEXT NOT NULL,
    srn TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
"""

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def srn(project: str, env: str, kind: str, name: str) -> str:
    """SimCloud resource name, the identifier policies and the audit log use."""
    return f"srn:simcloud:{project}:{env}:{kind}/{name}"


class Store:
    def __init__(self, path: str, clock: Clock):
        self.clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    # ---- resources -------------------------------------------------------

    def get(self, project: str, env: str, kind: str, name: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM resources WHERE project=? AND env=? AND kind=? AND name=?",
                (project, env, kind, name),
            ).fetchone()
        return _resource(row) if row else None

    def list_resources(self, project: str, env: str | None = None, kind: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM resources WHERE project=?", [project]
        if env is not None:
            sql, args = sql + " AND env=?", args + [env]
        if kind is not None:
            sql, args = sql + " AND kind=?", args + [kind]
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY env, kind, name", args).fetchall()
        return [_resource(r) for r in rows]

    def put(self, project: str, env: str, kind: str, name: str, spec: dict,
            status: dict | None = None, expect_version: int | None = None) -> dict:
        """Create or replace. `expect_version` = 0 means must not exist;
        a positive value must match the current version (optimistic concurrency)."""
        now = self.clock.now()
        with self.tx() as db:
            row = db.execute(
                "SELECT version, created_at, status FROM resources WHERE project=? AND env=? AND kind=? AND name=?",
                (project, env, kind, name),
            ).fetchone()
            current = row["version"] if row else 0
            if expect_version is not None and expect_version != current:
                code = "conflict" if expect_version == 0 else "precondition_failed"
                raise SimCloudError(code, f"{kind}/{name} is at version {current}, not {expect_version}",
                                    {"current_version": current})
            version = current + 1
            created = row["created_at"] if row else now
            new_status = status if status is not None else (json.loads(row["status"]) if row else {})
            db.execute(
                "INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?,?,?,?,?)",
                (project, env, kind, name, canonical(spec), canonical(new_status), version, created, now),
            )
        return self.get(project, env, kind, name)

    def set_status(self, project: str, env: str, kind: str, name: str, status: dict) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE resources SET status=?, updated_at=? WHERE project=? AND env=? AND kind=? AND name=?",
                (canonical(status), self.clock.now(), project, env, kind, name),
            )

    def delete(self, project: str, env: str, kind: str, name: str) -> bool:
        with self.tx() as db:
            cur = db.execute(
                "DELETE FROM resources WHERE project=? AND env=? AND kind=? AND name=?",
                (project, env, kind, name),
            )
        return cur.rowcount > 0

    # ---- small key-value tables for subsystems ---------------------------

    def kv_get(self, namespace: str, key: str) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE namespace=? AND key=?", (namespace, key)).fetchone()
        return json.loads(row["value"]) if row else None

    def kv_put(self, namespace: str, key: str, value: Any) -> None:
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (namespace, key, canonical(value)))

    def kv_delete(self, namespace: str, key: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM kv WHERE namespace=? AND key=?", (namespace, key))

    def kv_items(self, namespace: str, prefix: str = "") -> list[tuple[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT key, value FROM kv WHERE namespace=? AND key LIKE ? ESCAPE '\\' ORDER BY key",
                (namespace, _like_prefix(prefix)),
            ).fetchall()
        return [(r["key"], json.loads(r["value"])) for r in rows]

    # ---- audit log -------------------------------------------------------

    def audit(self, principal: str, action: str, resource: str, outcome: str, detail: dict | None = None) -> dict:
        with self.tx() as db:
            last = db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (last["seq"] + 1) if last else 1
            prev = last["hash"] if last else GENESIS
            record = {
                "seq": seq, "ts": self.clock.now(), "principal": principal, "action": action,
                "srn": resource, "outcome": outcome, "detail": detail or {},
            }
            digest = _chain_hash(prev, record)
            db.execute(
                "INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?)",
                (seq, record["ts"], principal, action, resource, outcome, canonical(record["detail"]), prev, digest),
            )
        return {**record, "prev_hash": prev, "hash": digest}

    def audit_records(self, since_seq: int = 0, limit: int = 1000) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM audit WHERE seq > ? ORDER BY seq LIMIT ?", (since_seq, limit)
            ).fetchall()
        return [_audit(r) for r in rows]

    def verify_audit_chain(self) -> int | None:
        """Return None if the chain is intact, otherwise the first bad seq."""
        prev = GENESIS
        with self._lock:
            rows = self._db.execute("SELECT * FROM audit ORDER BY seq").fetchall()
        for expected_seq, row in enumerate(rows, start=1):
            rec = _audit(row)
            body = {k: rec[k] for k in ("seq", "ts", "principal", "action", "srn", "outcome", "detail")}
            if rec["seq"] != expected_seq or rec["prev_hash"] != prev or _chain_hash(prev, body) != rec["hash"]:
                return rec["seq"]
            prev = rec["hash"]
        return None


def _chain_hash(prev: str, record: dict) -> str:
    return hashlib.sha256((prev + canonical(record)).encode()).hexdigest()


def _like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _resource(row: sqlite3.Row) -> dict:
    return {
        "project": row["project"], "env": row["env"], "kind": row["kind"], "name": row["name"],
        "srn": srn(row["project"], row["env"], row["kind"], row["name"]),
        "spec": json.loads(row["spec"]), "status": json.loads(row["status"]),
        "version": row["version"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _audit(row: sqlite3.Row) -> dict:
    return {
        "seq": row["seq"], "ts": row["ts"], "principal": row["principal"], "action": row["action"],
        "srn": row["srn"], "outcome": row["outcome"], "detail": json.loads(row["detail"]),
        "prev_hash": row["prev_hash"], "hash": row["hash"],
    }
