import sys
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_TOKEN, SEED, auth
from simcloud import jobs as jobs_mod
from simcloud.api import create_app
from simcloud.clock import Clock
from simcloud.core import SimCloud
from simcloud.dataplane import DataPlane
from simcloud.delivery import Delivery, pack_directory
from simcloud.errors import SimCloudError
from simcloud.federation import Federation
from simcloud.identity import Principal
from simcloud.jobs import Jobs, cron_matches
from simcloud.runtime import Supervisor
from simcloud.store import Store

ADMIN = Principal("admin")
KEY = ("shop", "prod", "nightly")
SCRIPT = '''import os, sys, time, resource
mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
print("args", sys.argv[1:], "job", os.environ.get("SIMCLOUD_JOB"), "attempt", os.environ.get("SIMCLOUD_JOB_ATTEMPT"))
print("nofile", resource.getrlimit(resource.RLIMIT_NOFILE)[0], flush=True)
if mode == "sleep":
    time.sleep(float(sys.argv[2]))
if mode == "flaky" and os.environ.get("SIMCLOUD_JOB_ATTEMPT") == "1":
    sys.exit(3)
if mode == "fail":
    sys.exit(2)
'''


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_mod, "RETRY_DELAY", 0.05)
    monkeypatch.setattr(jobs_mod, "STOP_GRACE", 1.0)
    clock = Clock()
    cloud = SimCloud(Store(":memory:", clock), clock, ADMIN_TOKEN)
    cloud.issued = cloud.apply_seed(SEED, Principal("admin"))
    data, fed = DataPlane(cloud), Federation(cloud)
    sup = Supervisor(tmp_path, port_range=(25000, 25999))
    delivery = Delivery(cloud, data, fed, sup, tmp_path)
    jobs = Jobs(cloud, delivery, tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "job.py").write_text(SCRIPT)
    yield cloud, jobs, src
    jobs.shutdown()
    sup.shutdown()


def put_job(cloud, **over):
    spec = {"command": [sys.executable, "job.py"], "max_retries": 0, "timeout_seconds": 30, "rlimit_nofile": 512,
            **over}
    cloud.put(ADMIN, *KEY[:2], "job", KEY[2], spec)


def wait_done(jobs, run_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = jobs._get_run(KEY, run_id)
        if r["status"] in jobs_mod.TERMINAL:
            return r
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still running")


def test_deploy_and_run_with_args_env_and_rlimit(world):
    cloud, jobs, src = world
    put_job(cloud)
    with pytest.raises(SimCloudError) as e:
        jobs.run(ADMIN, KEY)
    assert e.value.code == "unprocessable"  # no release yet
    rel = jobs.deploy(ADMIN, KEY, pack_directory(src))
    assert rel["state"] == "ready"
    run = jobs.run(ADMIN, KEY, ["ok", "--day", "2026-10-05"], wait=True, timeout=20)
    assert run["status"] == "succeeded" and run["exit_code"] == 0 and run["trigger"] == "manual"
    log = "\n".join(jobs.run_logs(ADMIN, KEY, run["id"]))
    assert "args ['ok', '--day', '2026-10-05'] job nightly attempt 1" in log and "nofile 512" in log


def test_retries_and_failures(world):
    cloud, jobs, src = world
    put_job(cloud, max_retries=2)
    jobs.deploy(ADMIN, KEY, pack_directory(src))
    flaky = jobs.run(ADMIN, KEY, ["flaky"], wait=True, timeout=20)
    assert flaky["status"] == "succeeded" and flaky["attempt"] == 2
    put_job(cloud, max_retries=1)
    failed = jobs.run(ADMIN, KEY, ["fail"], wait=True, timeout=20)
    assert failed["status"] == "failed" and failed["exit_code"] == 2 and failed["attempt"] == 2


def test_timeout_kills_the_run(world):
    cloud, jobs, src = world
    put_job(cloud, timeout_seconds=1)
    jobs.deploy(ADMIN, KEY, pack_directory(src))
    r = jobs.run(ADMIN, KEY, ["sleep", "30"], wait=True, timeout=20)
    assert r["status"] == "timed_out"


def test_forbid_refuses_and_replace_stops_the_active_run(world):
    cloud, jobs, src = world
    put_job(cloud, concurrency="forbid")
    jobs.deploy(ADMIN, KEY, pack_directory(src))
    first = jobs.run(ADMIN, KEY, ["sleep", "30"])
    time.sleep(0.5)
    with pytest.raises(SimCloudError) as e:
        jobs.run(ADMIN, KEY, ["ok"])
    assert e.value.code == "conflict" and e.value.details["running"] == first["id"]
    put_job(cloud, concurrency="replace")
    second = jobs.run(ADMIN, KEY, ["ok"])
    assert wait_done(jobs, first["id"])["status"] == "replaced"
    assert wait_done(jobs, second["id"])["status"] == "succeeded"


def test_allow_runs_side_by_side(world):
    cloud, jobs, src = world
    put_job(cloud, concurrency="allow")
    jobs.deploy(ADMIN, KEY, pack_directory(src))
    a = jobs.run(ADMIN, KEY, ["sleep", "1"])
    b = jobs.run(ADMIN, KEY, ["sleep", "1"])
    assert {wait_done(jobs, a["id"])["status"], wait_done(jobs, b["id"])["status"]} == {"succeeded"}


def test_schedule_triggers_once_per_minute_and_forbid_skips(world):
    cloud, jobs, src = world
    put_job(cloud, schedule="* * * * *", concurrency="forbid")
    jobs.deploy(ADMIN, KEY, pack_directory(src))
    now = time.time()
    started = jobs.tick(now)
    assert len(started) == 1 and started[0]["trigger"] == "schedule"
    assert jobs.tick(now + 1) == []  # same minute
    wait_done(jobs, started[0]["id"])
    put_job(cloud, schedule="* * * * *", concurrency="forbid")
    jobs.run(ADMIN, KEY, ["sleep", "30"])
    time.sleep(0.3)
    nxt = jobs.tick(now + 60)
    assert nxt and nxt[0]["status"] == "skipped"
    with pytest.raises(SimCloudError):
        put_job(cloud, schedule="61 * * * *")


def test_cron_matching():
    t = datetime(2026, 10, 5, 2, 30, tzinfo=timezone.utc).timestamp()  # a Monday
    assert cron_matches("30 2 * * *", t) and cron_matches("*/15 * * * 1", t) and cron_matches("30 2 5 10 *", t)
    assert not cron_matches("0 2 * * *", t) and not cron_matches("30 2 * * 0", t)
    assert cron_matches("30 2 1-7 * 1", t) and cron_matches("30 2 * * 1-5", t)


def test_jobs_over_the_api_and_in_evidence(world):
    cloud, jobs, src = world
    put_job(cloud)
    c = TestClient(create_app(cloud, jobs=jobs))
    admin = auth(ADMIN_TOKEN)
    base = "/v1/projects/shop/envs/prod/job/nightly"
    assert c.post(base + "/deploy", content=pack_directory(src), headers=admin).json()["state"] == "ready"
    run = c.post(base + "/run", json={"args": ["ok"], "wait": True, "timeout": 20}, headers=admin).json()
    assert run["status"] == "succeeded"
    assert [r["id"] for r in c.get(base + "/runs", headers=admin).json()["items"]] == [run["id"]]
    assert any("args ['ok']" in l for l in c.get(base + f"/runs/{run['id']}/logs", headers=admin).json()["lines"])
    ev = c.get("/admin/v1/evidence", headers=admin).json()
    assert ev["projects"]["shop"]["jobs"]["prod/nightly"]["runs"][0]["status"] == "succeeded"
    assert c.post(base + "/run", json={}, headers=auth(cloud.issued["user:nobody"])).status_code == 403


def test_job_run_arguments_may_follow_the_flags_or_a_separator():
    """`sc job run ENV NAME --wait -- ARG` is what the help promises; a plain `--wait ARG` and
    `ARG --wait` are accepted too, and anything unknown is still refused."""
    import pytest
    from simcloud.cli import parse
    for argv in (["job", "run", "prod", "imp", "--wait", "--", "a.csv"], ["job", "run", "prod", "imp", "--wait", "a.csv"],
                 ["job", "run", "prod", "imp", "a.csv", "--wait"]):
        a = parse(argv)
        assert a.args == ["a.csv"] and a.wait, argv
    assert parse(["job", "run", "prod", "imp", "--timeout", "30", "a", "b"]).args == ["a", "b"]
    with pytest.raises(SystemExit):
        parse(["job", "run", "prod", "imp", "--bogus"])
    with pytest.raises(SystemExit):
        parse(["status", "prod", "svc", "--", "x"])
