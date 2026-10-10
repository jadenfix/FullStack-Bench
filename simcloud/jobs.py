"""Jobs: batch work with releases, runs, schedules, concurrency policy, retries and history.

- `deploy` uploads a source tarball and makes an immutable job release (built once with the spec's
  `build`), exactly like a service release, but nothing starts.
- `run` starts the latest release: argv = spec `command` + run `args`, in the release's source root,
  with the same environment a service instance gets (env, platform variables, a short-lived token for
  the service account, secrets, database DSNs), and `RLIMIT_NOFILE` = spec `rlimit_nofile`.
- `concurrency`: `allow` runs side by side; `forbid` refuses a manual run with 409 (a scheduled tick
  is recorded as `skipped`); `replace` stops the active run (SIGTERM, then SIGKILL after 10 s) and starts.
- A non-zero exit is retried up to `max_retries` times, 5 s apart, as new attempts of the same run.
  A run past `timeout_seconds` is killed (`timed_out`).
- `schedule` is a 5-field cron expression in UTC, checked every few seconds; each matching minute
  triggers at most once.

Run records are kept in the store (history survives a control-plane restart; a run that was active
during a restart is marked `failed` with reason `platform restart`). Logs are per run.
"""

import os
import resource
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .core import SimCloud
from . import privsep
from .errors import SimCloudError
from .identity import Principal
from .store import srn

JOB_RELEASES = "job_releases"
JOB_RUNS = "job_runs"
RETRY_DELAY = 5.0
STOP_GRACE = 10.0
TERMINAL = {"succeeded", "failed", "timed_out", "replaced", "skipped"}


# ---- cron --------------------------------------------------------------------------------------

def _field(expr: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in expr.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b or step < 1:
            raise ValueError(f"cron field {expr!r} out of range {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def cron_matches(expr: str, ts: float) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron needs 5 fields: minute hour day-of-month month day-of-week")
    minute, hour, dom, month, dow = fields
    t = datetime.fromtimestamp(ts, timezone.utc)
    dows = _field(dow, 0, 7)
    if 7 in dows:
        dows.add(0)
    day_ok_dom = t.day in _field(dom, 1, 31)
    day_ok_dow = (t.isoweekday() % 7) in dows
    # Standard cron: if both day fields are restricted, either may match.
    day_ok = (day_ok_dom or day_ok_dow) if dom != "*" and dow != "*" else (day_ok_dom and day_ok_dow)
    return t.minute in _field(minute, 0, 59) and t.hour in _field(hour, 0, 23) and day_ok \
        and t.month in _field(month, 1, 12)


def validate_cron(expr: str) -> None:
    try:
        cron_matches(expr, 0)
    except (ValueError, TypeError) as e:
        raise SimCloudError("invalid_request", f"invalid schedule {expr!r}: {e}")


# ---- the runtime -------------------------------------------------------------------------------

class Jobs:
    def __init__(self, cloud: SimCloud, delivery, data_dir: Path):
        self.cloud, self.delivery = cloud, delivery
        self.store = cloud.store
        self.dir = data_dir
        self.logs_dir = data_dir / "job-logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}  # run id -> {"proc": Popen | None, "key": key, "stop": reason | None}
        self._last_tick: dict[tuple, int] = {}
        cloud.on_put.append(self._on_put)
        self._recover()

    # ---- records ---------------------------------------------------------------------------------

    @staticmethod
    def _prefix(key) -> str:
        return "/".join(key) + "/"

    def releases(self, key) -> list[dict]:
        return [r for _, r in self.store.kv_items(JOB_RELEASES, self._prefix(key))]

    def _save_release(self, key, rel) -> None:
        self.store.kv_put(JOB_RELEASES, self._prefix(key) + f"{rel['number']:06d}", rel)

    def runs_of(self, key) -> list[dict]:
        return sorted((r for _, r in self.store.kv_items(JOB_RUNS, self._prefix(key))), key=lambda r: r["number"])

    def _save_run(self, run: dict) -> None:
        self.store.kv_put(JOB_RUNS, self._prefix(tuple(run["job"].split("/"))) + f"{run['number']:06d}", run)

    def _get_run(self, key, run_id: str) -> dict:
        for r in self.runs_of(key):
            if r["id"] == run_id:
                return r
        raise SimCloudError("not_found", f"run {run_id} not found for job {'/'.join(key)}")

    def _job(self, actor: Principal, key, verb: str) -> dict:
        project, env, name = key
        self.cloud._scope(project, env, "job")
        self.cloud.authorize(actor, f"job:{verb}", srn(project, env, "job", name), project, env)
        job = self.store.get(project, env, "job", name)
        if not job:
            raise SimCloudError("not_found", f"job/{name} not found in {project}/{env}")
        return job

    def _on_put(self, actor, project, env, kind, name, spec, previous) -> None:
        if kind == "job" and spec.get("schedule"):
            validate_cron(spec["schedule"])

    def _recover(self) -> None:
        for _, run in self.store.kv_items(JOB_RUNS):
            if run["status"] == "running":
                run.update(status="failed", reason="platform restart", finished_at=self.cloud.clock.now())
                self._save_run(run)

    # ---- deploy --------------------------------------------------------------------------------

    def deploy(self, actor: Principal, key, archive: bytes) -> dict:
        job = self._job(actor, key, "deploy")
        digest = self.delivery._store_artifact(archive)
        number = len(self.releases(key)) + 1
        rel_id = f"r{number}"
        workdir = self.dir / "job-releases" / key[0] / key[1] / key[2] / rel_id
        if workdir.exists():
            import shutil
            shutil.rmtree(workdir)
        self.delivery._unpack(digest, workdir)
        privsep.give(workdir, root=self.dir)
        rel = {"id": rel_id, "number": number, "digest": digest, "spec": job["spec"], "state": "building",
               "created_by": actor.name, "created_at": self.cloud.clock.now(), "workdir": str(workdir)}
        ok, log = True, []
        if job["spec"].get("build"):
            try:
                proc = subprocess.run(privsep.as_user(job["spec"]["build"]), cwd=workdir, capture_output=True,
                                      text=True, timeout=600,
                                      env=privsep.workload_env(job["spec"].get("env", {}), home=workdir))
                ok = proc.returncode == 0
                log = (proc.stdout + proc.stderr).splitlines()[-200:]
            except (OSError, subprocess.TimeoutExpired) as e:
                ok, log = False, [f"build failed: {e}"]
        rel["state"] = "ready" if ok else "build_failed"
        rel["build_log"] = log
        self._save_release(key, rel)
        self.cloud.store.set_status(key[0], key[1], "job", key[2], {**(job.get("status") or {}),
                                                                   "phase": "ready" if ok else "build_failed",
                                                                   "release": rel_id})
        return {k: rel[k] for k in ("id", "state", "digest", "created_at")} | {"build_log": log[-20:]}

    # ---- runs ------------------------------------------------------------------------------------

    def active_runs(self, key) -> list[dict]:
        return [r for r in self.runs_of(key) if r["status"] == "running"]

    def run(self, actor: Principal, key, args: list[str] | None = None, wait: bool = False, timeout: float = 600,
            trigger: str = "manual") -> dict:
        job = self._job(actor, key, "run") if trigger == "manual" else self.store.get(*key[:2], "job", key[2])
        spec = job["spec"]
        rel = next((r for r in reversed(self.releases(key)) if r["state"] == "ready"), None)
        if rel is None:
            raise SimCloudError("unprocessable", f"job {key[2]} has no ready release; `sc job deploy` it first")
        if not spec.get("command"):
            raise SimCloudError("unprocessable", "the job spec has no command")
        with self._lock:
            active = self.active_runs(key)
            number = len(self.runs_of(key)) + 1
            run = {"id": f"{key[2]}-{number:05d}", "number": number, "job": "/".join(key), "status": "running",
                   "exit_code": None, "started_at": self.cloud.clock.now(), "finished_at": None, "trigger": trigger,
                   "args": list(args or []), "release": rel["id"], "attempt": 1, "started_by": actor.name,
                   "reason": None}
            if active and spec["concurrency"] == "forbid":
                if trigger == "manual":
                    raise SimCloudError("conflict", f"job {key[2]} is already running and its concurrency is forbid",
                                        {"running": active[0]["id"]})
                run.update(status="skipped", finished_at=run["started_at"], reason=f"{active[0]['id']} still running")
                self._save_run(run)
                return run
            if active and spec["concurrency"] == "replace":
                for a in active:
                    self._stop(a["id"], "replaced")
            self._save_run(run)
            self._active[run["id"]] = {"proc": None, "stop": None}
        threading.Thread(target=self._execute, args=(key, run, rel, spec), name=f"job-{run['id']}",
                         daemon=True).start()
        if wait:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                cur = self._get_run(key, run["id"])
                if cur["status"] in TERMINAL:
                    return cur
                time.sleep(0.2)
        return self._get_run(key, run["id"])

    def _stop(self, run_id: str, reason: str) -> None:
        a = self._active.get(run_id)
        if not a:
            return
        a["stop"] = reason
        proc = a.get("proc")
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            def kill_later(p=proc):
                try:
                    p.wait(STOP_GRACE)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            threading.Thread(target=kill_later, daemon=True).start()

    def _execute(self, key, run: dict, rel: dict, spec: dict) -> None:
        log_path = self.logs_dir / f"{run['id']}.log"
        nofile = spec.get("rlimit_nofile", 4096)

        def limits():
            os.setsid()
            resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
        status, code, reason = "failed", None, None
        for attempt in range(1, spec.get("max_retries", 0) + 2):
            run.update(attempt=attempt)
            self._save_run(run)
            try:
                env = privsep.workload_env({**self.delivery.workload_env(key, spec, "job"),
                                            "SIMCLOUD_JOB_RUN": run["id"], "SIMCLOUD_JOB_ATTEMPT": str(attempt)},
                                           home=rel["workdir"])
            except SimCloudError as e:
                status, reason = "failed", e.message
                break
            with log_path.open("a") as log:
                log.write(f"--- attempt {attempt}: {' '.join(spec['command'] + run['args'])}\n")
                log.flush()
                try:
                    proc = subprocess.Popen(privsep.as_user(spec["command"] + run["args"]), cwd=rel["workdir"], env=env,
                                            stdout=log,
                                            stderr=subprocess.STDOUT, preexec_fn=limits)
                except OSError as e:
                    status, reason = "failed", f"cannot start: {e}"
                    break
                self._active[run["id"]]["proc"] = proc
                try:
                    code = proc.wait(timeout=spec.get("timeout_seconds", 600))
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                    status, code, reason = "timed_out", None, f"exceeded {spec.get('timeout_seconds')} s"
                    break
            stop = self._active[run["id"]].get("stop")
            if stop:
                status, reason = stop, "stopped by a newer run" if stop == "replaced" else stop
                break
            if code == 0:
                status = "succeeded"
                break
            status, reason = "failed", f"exit {code}"
            if attempt <= spec.get("max_retries", 0):
                time.sleep(RETRY_DELAY)
        run.update(status=status, exit_code=code, reason=reason, finished_at=self.cloud.clock.now())
        self._save_run(run)
        self._active.pop(run["id"], None)

    # ---- reads -----------------------------------------------------------------------------------

    def runs(self, actor: Principal, key) -> list[dict]:
        self._job(actor, key, "read")
        return self.runs_of(key)

    def run_record(self, actor: Principal, key, run_id: str) -> dict:
        self._job(actor, key, "read")
        return self._get_run(key, run_id)

    def run_logs(self, actor: Principal, key, run_id: str, tail: int = 2000) -> list[str]:
        self._job(actor, key, "logs")
        self._get_run(key, run_id)
        p = self.logs_dir / f"{run_id}.log"
        return p.read_text(errors="replace").splitlines()[-tail:] if p.exists() else []

    # ---- schedules ---------------------------------------------------------------------------------

    def tick(self, now: float | None = None) -> list[dict]:
        now = self.cloud.clock.now() if now is None else now
        minute = int(now // 60)
        started = []
        for project in [k for k, _ in self.store.kv_items("projects")]:
            for env in self.cloud.project(project)["environments"]:
                for job in self.store.list_resources(project, env, "job"):
                    sched = job["spec"].get("schedule")
                    key = (project, env, job["name"])
                    if not sched or self._last_tick.get(key) == minute:
                        continue
                    try:
                        due = cron_matches(sched, now)
                    except ValueError:
                        continue
                    if due:
                        self._last_tick[key] = minute
                        try:
                            started.append(self.run(Principal("platform:scheduler"), key, trigger="schedule"))
                        except SimCloudError as e:
                            self.store.audit("platform:scheduler", "job:run", srn(project, env, "job", job["name"]),
                                             "error", {"error": e.message})
        return started

    def run_forever(self, stop: threading.Event, every: float = 5.0) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # never die quietly
                self.store.audit("platform:scheduler", "job:tick_error", "srn:simcloud", "error", {"error": repr(e)[:300]})
            stop.wait(every)

    def shutdown(self) -> None:
        for run_id in list(self._active):
            self._stop(run_id, "failed")
