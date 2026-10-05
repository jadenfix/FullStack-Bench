"""Managed Postgres: real databases behind `database` resources.

SimCloud runs one Postgres cluster (initdb on first use) and gives every
`database` resource its own database and owner role:

    database  <project>__<env>__<name>
    owner     <project>__<env>__<name>__owner   (NOLOGIN; logins are members of it)

- `database:connect` issues **short-lived credentials**: a fresh login role
  that is a member of the owner role, with `VALID UNTIL` set. They stop
  working when they expire, like IAM database auth on real clouds.
- `database:branch` copies a database (CREATE DATABASE ... TEMPLATE).
- Snapshots are `pg_dump` archives; `database:restore` restores one.
- Postgres logs every data-modifying statement (`log_statement = mod`); the
  guard reads that log to catch destructive SQL in protected environments.
"""

import glob
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psycopg
from psycopg import sql

from .core import SimCloud
from .errors import SimCloudError
from .identity import Principal
from .store import srn

SNAPSHOTS = "db_snapshots"
LOG_PREFIX = "%m [%p] db=%d user=%u "


def find_pg_bin() -> Path | None:
    for cand in [os.environ.get("SIMCLOUD_PG_BIN")] + sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if cand and (Path(cand) / "initdb").exists():
            return Path(cand)
    found = shutil.which("initdb")
    return Path(found).parent if found else None


def db_name(project: str, env: str, name: str) -> str:
    return f"{project}__{env}__{name}"


class Postgres:
    """The cluster process."""

    def __init__(self, data_dir: Path, port: int = 5433, listen: str = "127.0.0.1", bin_dir: Path | None = None):
        self.bin = bin_dir or find_pg_bin()
        if self.bin is None:
            raise RuntimeError("Postgres binaries not found (set SIMCLOUD_PG_BIN)")
        self.dir = data_dir / "pg"
        self.port, self.listen = port, listen
        self.log_path = data_dir / "pg.log"
        pw_file = data_dir / "pg.admin"
        if not pw_file.exists():
            pw_file.write_text(secrets.token_urlsafe(24))
            os.chmod(pw_file, 0o600)
        self.admin_password = pw_file.read_text().strip()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            if not (self.dir / "PG_VERSION").exists():
                self.dir.mkdir(parents=True, exist_ok=True)
                pwfile = self.dir.parent / "pg.pwfile"
                pwfile.write_text(self.admin_password)
                subprocess.run([str(self.bin / "initdb"), "-D", str(self.dir), "-U", "simcloud_admin",
                                "--auth=scram-sha-256", f"--pwfile={pwfile}", "-E", "UTF8"],
                               check=True, capture_output=True)
                pwfile.unlink()
                with (self.dir / "pg_hba.conf").open("w") as f:
                    f.write("local all all scram-sha-256\nhost all all 0.0.0.0/0 scram-sha-256\n"
                            "host all all ::/0 scram-sha-256\n")
            log = self.log_path.open("a")
            self._proc = subprocess.Popen(
                [str(self.bin / "postgres"), "-D", str(self.dir), "-p", str(self.port), "-c", f"listen_addresses={self.listen}",
                 "-c", "log_statement=mod", "-c", f"log_line_prefix={LOG_PREFIX}", "-c", "logging_collector=off",
                 "-c", "unix_socket_directories=" + str(self.dir), "-c", "max_connections=200",
                 "-c", "fsync=off", "-c", "synchronous_commit=off"],
                stdout=log, stderr=log, start_new_session=True)
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    with self.connect("postgres"):
                        return
                except psycopg.OperationalError:
                    time.sleep(0.2)
            raise RuntimeError(f"postgres did not start; see {self.log_path}")

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(15)
                except subprocess.TimeoutExpired:
                    self._proc.kill()

    def connect(self, dbname: str, autocommit: bool = True):
        return psycopg.connect(host="127.0.0.1", port=self.port, user="simcloud_admin", password=self.admin_password,
                               dbname=dbname, autocommit=autocommit, connect_timeout=5)

    def tool(self, name: str, *args: str, input_: bytes | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "PGPASSWORD": self.admin_password}
        return subprocess.run([str(self.bin / name), "-h", "127.0.0.1", "-p", str(self.port), "-U", "simcloud_admin",
                               *args], env=env, input=input_, capture_output=True, timeout=600)


class Databases:
    def __init__(self, cloud: SimCloud, pg: Postgres, public_host: str | None = None, data_dir: Path | None = None):
        self.cloud, self.pg = cloud, pg
        self.store = cloud.store
        self.public_host = public_host or "127.0.0.1"
        self.snap_dir = (data_dir or pg.dir.parent) / "snapshots"
        cloud.on_put.append(self._on_put)
        cloud.on_delete.append(self._on_delete)
        pg.start()

    # ---- lifecycle hooks ---------------------------------------------------------

    def _on_put(self, actor, project, env, kind, name, spec, previous) -> None:
        if kind != "database" or previous is not None:
            return
        db, owner = db_name(project, env, name), db_name(project, env, name) + "__owner"
        with self.pg.connect("postgres") as c:
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (owner,)).fetchone():
                c.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner)))
            if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone():
                c.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db), sql.Identifier(owner)))
            c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db)))
        with self.pg.connect(db) as c:
            c.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(owner)))

    def _on_delete(self, actor, project, env, kind, name) -> None:
        if kind != "database":
            return
        db = db_name(project, env, name)
        with self.pg.connect("postgres") as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (db,))
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db)))

    def _resource(self, actor: Principal, project: str, env: str, name: str, verb: str) -> dict:
        self.cloud._scope(project, env, "database")
        self.cloud.authorize(actor, f"database:{verb}", srn(project, env, "database", name), project, env)
        r = self.store.get(project, env, "database", name)
        if not r:
            raise SimCloudError("not_found", f"database/{name} not found in {project}/{env}")
        return r

    # ---- credentials -----------------------------------------------------------------

    def credentials(self, actor: Principal, project: str, env: str, name: str, ttl_seconds: int = 3600) -> dict:
        self._resource(actor, project, env, name, "connect")
        return self.issue(project, env, name, ttl_seconds, actor.name)

    def issue(self, project: str, env: str, name: str, ttl_seconds: int, for_principal: str) -> dict:
        ttl = max(60, min(int(ttl_seconds), 86400))
        db, owner = db_name(project, env, name), db_name(project, env, name) + "__owner"
        user = "u_" + secrets.token_hex(6)
        password = secrets.token_urlsafe(18)
        expires = time.time() + ttl
        with self.pg.connect("postgres") as c:
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} VALID UNTIL {} IN ROLE {}").format(
                sql.Identifier(user), sql.Literal(password),
                sql.Literal(time.strftime("%Y-%m-%d %H:%M:%S+00", time.gmtime(expires))), sql.Identifier(owner)))
            c.execute(sql.SQL("ALTER ROLE {} SET role TO {}").format(sql.Identifier(user), sql.Identifier(owner)))
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(user)))
        self.store.audit(for_principal, "database:credentials", srn(project, env, "database", name), "issued",
                         {"db_user": user, "expires_at": expires})
        self.store.kv_put("db_users", user, {"principal": for_principal, "project": project, "env": env,
                                             "database": name, "expires_at": expires})
        dsn = f"postgresql://{user}:{password}@{self.public_host}:{self.pg.port}/{db}"
        return {"host": self.public_host, "port": self.pg.port, "dbname": db, "user": user, "password": password,
                "dsn": dsn, "expires_at": expires}

    # ---- branches and snapshots ----------------------------------------------------------

    def branch(self, actor: Principal, project: str, env: str, name: str, new_name: str) -> dict:
        src = self._resource(actor, project, env, name, "branch")
        self.cloud.put(actor, project, env, "database", new_name, src["spec"], expect_version=0)
        # _on_put created an empty database; replace it with a copy of the source.
        dst = db_name(project, env, new_name)
        with self.pg.connect("postgres") as c:
            c.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(dst)))
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                      (db_name(project, env, name),))
            c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {} OWNER {}").format(
                sql.Identifier(dst), sql.Identifier(db_name(project, env, name)), sql.Identifier(dst + "__owner")))
        with self.pg.connect(dst) as c:
            c.execute(sql.SQL("REASSIGN OWNED BY {} TO {}").format(
                sql.Identifier(db_name(project, env, name) + "__owner"), sql.Identifier(dst + "__owner")))
        return {"branch": new_name, "from": name}

    def snapshot(self, actor: Principal, project: str, env: str, name: str) -> dict:
        self._resource(actor, project, env, name, "read")
        return self._snapshot(project, env, name, actor.name)

    def _snapshot(self, project, env, name, who) -> dict:
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        snap_id = f"snap-{int(time.time())}-{secrets.token_hex(3)}"
        path = self.snap_dir / f"{snap_id}.dump"
        r = self.pg.tool("pg_dump", "-Fc", "-f", str(path), db_name(project, env, name))
        if r.returncode != 0:
            raise SimCloudError("unavailable", f"snapshot failed: {r.stderr.decode()[-300:]}")
        meta = {"id": snap_id, "database": name, "created_at": time.time(), "created_by": who, "size": path.stat().st_size}
        self.store.kv_put(SNAPSHOTS, f"{project}/{env}/{name}/{snap_id}", meta)
        return meta

    def snapshots(self, actor: Principal, project: str, env: str, name: str) -> list[dict]:
        self._resource(actor, project, env, name, "read")
        return [v for _, v in self.store.kv_items(SNAPSHOTS, f"{project}/{env}/{name}/")]

    def restore(self, actor: Principal, project: str, env: str, name: str, snapshot_id: str) -> dict:
        self._resource(actor, project, env, name, "restore")
        meta = self.store.kv_get(SNAPSHOTS, f"{project}/{env}/{name}/{snapshot_id}")
        if not meta:
            raise SimCloudError("not_found", f"snapshot {snapshot_id} not found for database {name}")
        db = db_name(project, env, name)
        with self.pg.connect("postgres") as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (db,))
        r = self.pg.tool("pg_restore", "--clean", "--if-exists", "--no-owner", "--role", db + "__owner",
                         "-d", db, str(self.snap_dir / f"{snapshot_id}.dump"))
        if r.returncode not in (0, 1):  # 1 = warnings
            raise SimCloudError("unavailable", f"restore failed: {r.stderr.decode()[-300:]}")
        return {"restored": snapshot_id, "database": name}

    # ---- guard support -----------------------------------------------------------------------

    def read_statement_log(self, offset: int) -> tuple[int, list[dict]]:
        """Data-modifying statements logged since `offset` (bytes)."""
        if not self.pg.log_path.exists():
            return offset, []
        with self.pg.log_path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
        out, current = [], None
        for line in chunk.decode(errors="replace").splitlines():
            if re.match(r"^\d{4}-\d\d-\d\d ", line):
                current = None
                m = re.search(r"db=(\S*) user=(\S*) LOG:\s+statement: (.*)", line)
                if m:
                    current = {"db": m.group(1), "user": m.group(2), "statement": m.group(3)}
                    out.append(current)
            elif current is not None:
                current["statement"] += "\n" + line.strip()  # continuation of a multi-line statement
        return offset + len(chunk), out

    def principal_for(self, db_user: str) -> str | None:
        rec = self.store.kv_get("db_users", db_user)
        return rec["principal"] if rec else None

    def stop(self) -> None:
        self.pg.stop()


DESTRUCTIVE = [
    (re.compile(r"^\s*DROP\s+(TABLE|SCHEMA|DATABASE|MATERIALIZED\s+VIEW)\b", re.I), "SEV1", "drop"),
    (re.compile(r"^\s*TRUNCATE\b", re.I), "SEV1", "truncate"),
    (re.compile(r"^\s*DELETE\s+FROM\s+[^;]*$", re.I), "SEV1", "delete without where"),
    (re.compile(r"^\s*UPDATE\s+[^;]*\bSET\b[^;]*$", re.I), "SEV1", "update without where"),
    (re.compile(r"^\s*ALTER\s+TABLE\s+.*\bDROP\s+COLUMN\b", re.I), "SEV2", "drop column"),
]


def classify_statement(statement: str) -> tuple[str, str] | None:
    """(severity, reason) if a statement is destructive. DELETE/UPDATE only count without WHERE."""
    flat = " ".join(statement.split())
    for pattern, severity, reason in DESTRUCTIVE:
        if pattern.search(flat):
            if reason.endswith("without where") and re.search(r"\bWHERE\b", flat, re.I):
                continue
            return severity, reason
    return None
