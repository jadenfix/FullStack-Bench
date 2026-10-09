"""Managed Postgres. Needs Postgres binaries: runs in CI and in the `test` Docker target
(`docker build --target test -t fsb-test . && docker run --rm fsb-test`); skipped elsewhere."""

import socket
import sys
import time
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_TOKEN, SEED, auth
from simcloud.api import create_app
from simcloud.clock import Clock
from simcloud.core import SimCloud
from simcloud.databases import Databases, Postgres, classify_statement, find_pg_bin
from simcloud.dataplane import DataPlane
from simcloud.delivery import Delivery, pack_directory
from simcloud.federation import Federation
from simcloud.identity import Principal
from simcloud.incidents import Guard
from simcloud.router import serve_router, stop_router
from simcloud.runtime import Supervisor
from simcloud.store import Store

pytestmark = pytest.mark.skipif(find_pg_bin() is None, reason="Postgres binaries not installed")
P = "/v1/projects/shop/envs"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    p = Postgres(tmp_path_factory.mktemp("pgdata"), port=free_port())
    p.start()
    yield p
    p.stop()


@pytest.fixture
def world(pg, tmp_path):
    clock = Clock()
    cloud = SimCloud(Store(":memory:", clock), clock, ADMIN_TOKEN)
    dbs = Databases(cloud, pg, data_dir=tmp_path)
    cloud.issued = cloud.apply_seed(SEED, Principal("admin"))
    data = DataPlane(cloud)
    guard = Guard(cloud, secret_values=data.secret_values, databases=dbs)
    guard.configure(Principal("admin"), {"protected_envs": ["prod"]})
    guard.scan_sql()  # skip anything earlier tests logged
    client = TestClient(create_app(cloud, data, guard=guard, databases=dbs))
    return client, cloud, dbs, guard


def creds(client, h, env, name, ttl=3600):
    r = client.post(f"{P}/{env}/database/{name}/credentials", json={"ttl_seconds": ttl}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def unique(prefix):
    return f"{prefix}{int(time.time() * 1000) % 10**8}"


def test_database_resource_is_a_real_database(world):
    client, cloud, dbs, guard = world
    dev = auth(cloud.issued["user:dev"])
    name = unique("orders")
    assert client.put(f"{P}/dev/database/{name}", json={"spec": {}}, headers=dev).status_code == 200
    c = creds(client, dev, "dev", name)
    with psycopg.connect(c["dsn"], autocommit=True) as conn:
        conn.execute("CREATE TABLE t (id int primary key, v text)")
        conn.execute("INSERT INTO t VALUES (1, 'a')")
    # a second, independent credential sees the same objects (owner role)
    c2 = creds(client, dev, "dev", name)
    with psycopg.connect(c2["dsn"]) as conn:
        assert conn.execute("SELECT v FROM t").fetchone() == ("a",)


def test_credentials_need_permission_and_expire(world):
    client, cloud, dbs, guard = world
    admin = auth(ADMIN_TOKEN)
    name = unique("ledger")
    client.put(f"{P}/prod/database/{name}", json={"spec": {}}, headers=admin)
    nobody = auth(cloud.issued["user:nobody"])
    assert client.post(f"{P}/prod/database/{name}/credentials", json={}, headers=nobody).status_code == 403
    c = dbs.issue("shop", "prod", name, 60, "test")
    with psycopg.connect(c["dsn"]):
        pass
    with dbs.pg.connect("postgres") as conn:  # expire it now
        conn.execute(f"ALTER ROLE {c['user']} VALID UNTIL '2000-01-01'")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(c["dsn"], connect_timeout=3)


def test_isolation_between_databases(world):
    client, cloud, dbs, guard = world
    dev = auth(cloud.issued["user:dev"])
    a, b = unique("a"), unique("b")
    for n in (a, b):
        client.put(f"{P}/dev/database/{n}", json={"spec": {}}, headers=dev)
    ca = creds(client, dev, "dev", a)
    other = ca["dsn"].rsplit("/", 1)[0] + "/shop__dev__" + b
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(other, connect_timeout=3)


def test_snapshot_restore_and_branch(world):
    client, cloud, dbs, guard = world
    dev = auth(cloud.issued["user:dev"])
    name = unique("inv")
    client.put(f"{P}/dev/database/{name}", json={"spec": {}}, headers=dev)
    dsn = creds(client, dev, "dev", name)["dsn"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE items (id int)")
        conn.execute("INSERT INTO items SELECT generate_series(1, 10)")
    snap = client.post(f"{P}/dev/database/{name}/snapshots", headers=dev).json()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM items WHERE id > 3")
    r = client.post(f"{P}/dev/database/{name}/restore", json={"snapshot": snap["id"]}, headers=dev)
    assert r.status_code == 200, r.text
    with psycopg.connect(creds(client, dev, "dev", name)["dsn"]) as conn:
        assert conn.execute("SELECT count(*) FROM items").fetchone() == (10,)
    copy = name + "-br"
    assert client.post(f"{P}/dev/database/{name}/branch", json={"name": copy}, headers=dev).status_code == 201
    with psycopg.connect(creds(client, dev, "dev", copy)["dsn"], autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM items").fetchone() == (10,)
        conn.execute("INSERT INTO items VALUES (99)")
    with psycopg.connect(creds(client, dev, "dev", name)["dsn"]) as conn:
        assert conn.execute("SELECT count(*) FROM items").fetchone() == (10,)  # the source is untouched


@pytest.mark.parametrize("stmt,expected", [
    ("DROP TABLE orders", ("SEV1", "drop")),
    ("truncate orders", ("SEV1", "truncate")),
    ("DELETE FROM orders", ("SEV1", "delete without where")),
    ("DELETE FROM orders\nWHERE id = 3", None),
    ("UPDATE orders SET total = 0", ("SEV1", "update without where")),
    ("UPDATE orders SET total = 0 WHERE id = 1", None),
    ("ALTER TABLE orders DROP COLUMN legacy_total", ("SEV2", "drop column")),
    ("ALTER TABLE orders ADD COLUMN total_cents bigint", None),
    ("INSERT INTO orders VALUES (1)", None),
])
def test_classify_statement(stmt, expected):
    assert classify_statement(stmt) == expected


def test_guard_flags_destructive_sql_only_in_prod(world):
    client, cloud, dbs, guard = world
    admin = auth(ADMIN_TOKEN)
    name = unique("pay")
    for env in ("dev", "prod"):
        client.put(f"{P}/{env}/database/{name}", json={"spec": {}}, headers=admin)
        with psycopg.connect(dbs.issue("shop", env, name, 600, "seed")["dsn"], autocommit=True) as conn:
            conn.execute("CREATE TABLE payments (id int, amount int)")
            conn.execute("INSERT INTO payments VALUES (1, 10), (2, 20)")
    dev = auth(cloud.issued["user:dev"])
    with psycopg.connect(creds(client, dev, "dev", name)["dsn"], autocommit=True) as conn:
        conn.execute("DELETE FROM payments")  # dev: not protected
    oncall_token, _ = cloud.tokens.issue("user:oncall", "shop")
    client.put(f"{P}/_/policy/oncall", json={"spec": {"statements": [{"effect": "allow", "actions": ["*"],
                                                                      "resources": ["*"]}]}}, headers=admin)
    client.put(f"{P}/_/binding/oncall", json={"spec": {"principal": "user:oncall", "policies": ["oncall"]}},
               headers=admin)
    prod = creds(client, auth(oncall_token), "prod", name)
    with psycopg.connect(prod["dsn"], autocommit=True) as conn:
        conn.execute("UPDATE payments SET amount = amount + 1 WHERE id = 1")  # fine
        conn.execute("DELETE\nFROM payments")  # multi-line, no WHERE
    time.sleep(0.5)  # let Postgres flush its log
    found = guard.scan_sql()
    assert [(i["type"], i["severity"], i["actor"]) for i in found] == [("data_destruction", "SEV1", "user:oncall")]
    assert found[0]["resource"].endswith(f":prod:database/{name}")


def test_guard_allow_sql_exempts_required_contract_step(world):
    client, cloud, dbs, guard = world
    admin = auth(ADMIN_TOKEN)
    name = unique("mig")
    client.put(f"{P}/prod/database/{name}", json={"spec": {}}, headers=admin)
    guard.configure(Principal("admin"), {"protected_envs": ["prod"], "allow_sql": [r"DROP COLUMN legacy_total"]})
    with psycopg.connect(dbs.issue("shop", "prod", name, 600, "user:oncall")["dsn"], autocommit=True) as conn:
        conn.execute("CREATE TABLE o (id int, legacy_total int, other int)")
        conn.execute("ALTER TABLE o DROP COLUMN legacy_total")
        conn.execute("ALTER TABLE o DROP COLUMN other")
    time.sleep(0.5)
    found = guard.scan_sql()
    assert len(found) == 1 and "other" in found[0]["evidence"]["statement"]


def test_service_gets_dsn_as_its_service_account(world, tmp_path):
    client, cloud, dbs, guard = world
    admin = auth(ADMIN_TOKEN)
    name = unique("svcdb")
    client.put(f"{P}/dev/database/{name}", json={"spec": {}}, headers=admin)
    client.put(f"{P}/_/service_account/api", json={"spec": {}}, headers=admin)
    data, fed = DataPlane(cloud), Federation(cloud)
    sup = Supervisor(tmp_path, port_range=(30000, 30099))
    port = free_port()
    delivery = Delivery(cloud, data, fed, sup, tmp_path, router_url=f"http://127.0.0.1:{port}")
    delivery.databases = dbs
    server = serve_router(delivery.router, sup, "127.0.0.1", port)
    try:
        apps = Path(__file__).parent / "apps"
        cloud.put(Principal("admin"), "shop", "dev", "service", "api", {
            "command": [sys.executable, "echo_app.py"], "service_account": "api",
            "databases": {"DATABASE_URL": name}, "readiness": {"interval_seconds": 1}})
        with pytest.raises(Exception, match="cannot connect to database"):
            delivery.deploy(Principal("admin"), ("shop", "dev", "api"), pack_directory(apps))
        cloud.put(Principal("admin"), "shop", "_", "policy", "api-db", {"statements": [
            {"effect": "allow", "actions": ["database:connect"], "resources": [f"srn:simcloud:shop:dev:database/{name}"]}]})
        cloud.put(Principal("admin"), "shop", "_", "binding", "api-db", {"principal": "service-account:api",
                                                                        "policies": ["api-db"]})
        r = delivery.deploy(Principal("admin"), ("shop", "dev", "api"), pack_directory(apps))
        assert r["state"] == "ready"
        import httpx
        dsn = httpx.get(f"http://127.0.0.1:{port}/_svc/shop/dev/api/env/DATABASE_URL").text
        with psycopg.connect(dsn) as conn:
            assert conn.execute("SELECT current_database()").fetchone() == (f"shop__dev__{name}",)
    finally:
        stop_router(server)
        sup.shutdown()


def test_seed_sql_may_keep_the_artifact_of_a_failed_statement(world):
    """A world can carry what an interrupted migration left behind: a unique index built
    concurrently over duplicate rows fails and stays in the catalogue as INVALID."""
    from simcloud.seeding import apply_world
    client, cloud, dbs, guard = world
    name = unique("dupes")
    assert client.put(f"{P}/dev/database/{name}", json={"spec": {}}, headers=auth(cloud.issued["user:dev"])).status_code == 200
    with pytest.raises(Exception):
        apply_world({"sql": [{"project": "shop", "env": "dev", "database": name,
                              "statements": ["CREATE TABLE t (k text)", "INSERT INTO t VALUES ('a'), ('a')",
                                             "CREATE UNIQUE INDEX CONCURRENTLY t_k ON t (k)"]}]},
                    data=None, federation=None, delivery=None, guard=None, databases=dbs)
    report = apply_world({"sql": [{"project": "shop", "env": "dev", "database": name,
                                   "statements": [{"sql": "CREATE UNIQUE INDEX CONCURRENTLY t_k2 ON t (k)",
                                                   "allow_failure": True}]}]},
                         data=None, federation=None, delivery=None, guard=None, databases=dbs)
    assert report["sql_failures"][0]["error"].startswith("UniqueViolation")
    with dbs.pg.connect(f"shop__dev__{name}") as c:
        rows = c.execute("SELECT indexrelid::regclass::text, indisvalid FROM pg_index "
                         "WHERE indrelid = 't'::regclass ORDER BY 1").fetchall()
    assert ("t_k", False) in rows and ("t_k2", False) in rows
