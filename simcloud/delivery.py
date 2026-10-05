"""Delivery: releases, deploys, promotion, rollback and traffic.

- A deploy uploads a source tarball. It becomes an immutable release with a
  content digest; the release runs the service's `build` command once and
  then its `command`.
- Promotion deploys the *same artifact* (same digest) to another environment,
  with that environment's service spec (its env vars, secrets, sizes).
- Rollback moves all traffic to an earlier ready release.
- Secrets in the spec are resolved at instance start as the service's service
  account; if that account may not `secret:access` a secret, the deploy fails.
"""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from .core import SimCloud
from .dataplane import DataPlane
from .errors import SimCloudError
from .federation import Federation
from .identity import Principal
from .kinds import PROJECT_SCOPE
from .router import Router
from .runtime import Key, ReleaseRun, Supervisor
from .store import srn

RELEASES = "releases"
MAX_ARTIFACT = 50 * 1024 * 1024
BUILD_TIMEOUT = 600


class Delivery:
    def __init__(self, cloud: SimCloud, data: DataPlane, federation: Federation, supervisor: Supervisor,
                 data_dir: Path, public_url: str = "http://127.0.0.1:7400", router_url: str = "http://127.0.0.1:7480"):
        self.cloud, self.data, self.federation, self.supervisor = cloud, data, federation, supervisor
        self.store = cloud.store
        self.dir = data_dir
        self.public_url, self.router_url = public_url, router_url
        self.router = Router(supervisor, self.traffic, faults=self._lb_faults)
        self.databases = None  # set by the server when managed Postgres is available
        cloud.on_delete.append(self._on_delete)

    # ---- release records -------------------------------------------------------

    def _prefix(self, key: Key) -> str:
        return "/".join(key) + "/"

    def releases(self, key: Key) -> list[dict]:
        return [r for _, r in self.store.kv_items(RELEASES, self._prefix(key))]

    def _release(self, key: Key, release_id: str) -> dict:
        for r in self.releases(key):
            if r["id"] == release_id:
                return r
        raise SimCloudError("not_found", f"release {release_id} not found for {'/'.join(key)}")

    def _save_release(self, key: Key, rel: dict) -> None:
        self.store.kv_put(RELEASES, self._prefix(key) + f"{rel['number']:06d}", rel)

    def _lb_faults(self, key: Key) -> tuple[float, int | None]:
        if not self.cloud.faults.faults:
            return 0.0, None
        svc = self.store.get(key[0], key[1], "service", key[2])
        return self.cloud.faults.load_balancer("/".join(key), (svc or {}).get("spec", {}).get("regions", []))

    def traffic(self, key: Key) -> dict[str, int]:
        svc = self.store.get(key[0], key[1], "service", key[2])
        return (svc or {}).get("status", {}).get("traffic", {}) if svc else {}

    def _set_status(self, key: Key, **fields) -> dict:
        svc = self.store.get(key[0], key[1], "service", key[2])
        status = {**svc["status"], **fields}
        self.store.set_status(key[0], key[1], "service", key[2], status)
        return status

    def _service(self, actor: Principal, key: Key, verb: str) -> dict:
        project, env, name = key
        self.cloud._scope(project, env, "service")
        svc = self.store.get(project, env, "service", name)
        regions = svc["spec"].get("regions") if svc else None
        self.cloud.authorize(actor, f"service:{verb}", srn(project, env, "service", name), project, env, regions)
        if not svc:
            raise SimCloudError("not_found", f"service/{name} not found in {project}/{env}")
        return svc

    # ---- artifacts ---------------------------------------------------------------

    def _store_artifact(self, data: bytes) -> str:
        if len(data) > MAX_ARTIFACT:
            raise SimCloudError("invalid_request", "source archive exceeds 50 MiB")
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        path = self.dir / "artifacts" / (digest.split(":")[1] + ".tar.gz")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        return digest

    def _unpack(self, digest: str, dest: Path) -> None:
        src = self.dir / "artifacts" / (digest.split(":")[1] + ".tar.gz")
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(src, "r:gz") as tar:
                for m in tar.getmembers():
                    target = (dest / m.name).resolve()
                    if not str(target).startswith(str(dest.resolve()) + os.sep) and target != dest.resolve():
                        raise SimCloudError("invalid_request", f"archive member escapes the source root: {m.name}")
                    if m.issym() or m.islnk() or m.isdev():
                        raise SimCloudError("invalid_request", f"links and devices are not allowed: {m.name}")
                tar.extractall(dest, filter="data")
        except tarfile.TarError as e:
            raise SimCloudError("invalid_request", f"not a gzip tar archive: {e}")

    # ---- running a release -----------------------------------------------------------

    def _run_for(self, key: Key, rel: dict) -> ReleaseRun:
        project, env, name = key
        spec = rel["spec"]
        sa = spec.get("service_account")
        run_env = {**spec["env"], "SIMCLOUD_URL": self.public_url, "SIMCLOUD_ROUTER_URL": self.router_url,
                   "SIMCLOUD_PROJECT": project, "SIMCLOUD_ENV": env, "SIMCLOUD_SERVICE": name}
        if sa:
            tok = self.federation.short_lived_token(Principal("admin"), project, sa, 3600)
            run_env["SIMCLOUD_TOKEN"] = tok["access_token"]
        if spec["secrets"]:
            if not sa:
                raise SimCloudError("unprocessable", "a service that uses secrets needs a service_account")
            who = Principal(f"service-account:{sa}", project)
            for var, secret_name in spec["secrets"].items():
                try:
                    run_env[var] = self.data.access_secret(who, project, env, secret_name)["value"]
                except SimCloudError as e:
                    raise SimCloudError(e.code, f"service account {sa} cannot read secret {secret_name}: {e.message}",
                                        e.details)
        if spec.get("databases"):
            if not sa:
                raise SimCloudError("unprocessable", "a service that uses databases needs a service_account")
            if self.databases is None:
                raise SimCloudError("unavailable", "this SimCloud instance has no managed Postgres")
            who = Principal(f"service-account:{sa}", project)
            for var, db in spec["databases"].items():
                try:
                    run_env[var] = self.databases.credentials(who, project, env, db, 43200)["dsn"]
                except SimCloudError as e:
                    raise SimCloudError(e.code, f"service account {sa} cannot connect to database {db}: {e.message}",
                                        e.details)
        probe = spec["readiness"]
        return ReleaseRun(release_id=rel["id"], workdir=rel["workdir"], command=spec["command"], env=run_env,
                          probe_path=probe["path"], probe_interval=probe["interval_seconds"],
                          probe_timeout=probe["timeout_seconds"], failure_threshold=probe["failure_threshold"],
                          drain_seconds=spec["drain_seconds"])

    def _build(self, key: Key, rel: dict) -> bool:
        cmd = rel["spec"].get("build") or []
        if not cmd:
            return True
        self.supervisor.logs.write(key, f"build/{rel['id']}", f"$ {' '.join(cmd)}")
        try:
            proc = subprocess.run(cmd, cwd=rel["workdir"], capture_output=True, text=True, timeout=BUILD_TIMEOUT,
                                  env={**os.environ, **rel["spec"]["env"]})
        except (OSError, subprocess.TimeoutExpired) as e:
            self.supervisor.logs.write(key, f"build/{rel['id']}", f"build failed: {e}")
            return False
        for line in (proc.stdout + proc.stderr).splitlines():
            self.supervisor.logs.write(key, f"build/{rel['id']}", line)
        return proc.returncode == 0

    def _create_release(self, actor: Principal, key: Key, spec: dict, digest: str, promoted_from: str | None) -> dict:
        number = len(self.releases(key)) + 1
        rel_id = f"r{number}"
        workdir = self.dir / "releases" / key[0] / key[1] / key[2] / rel_id
        if workdir.exists():
            shutil.rmtree(workdir)
        self._unpack(digest, workdir)
        rel = {"id": rel_id, "number": number, "digest": digest, "spec": spec, "state": "building",
               "created_by": actor.name, "created_at": self.cloud.clock.now(), "promoted_from": promoted_from,
               "workdir": str(workdir)}
        self._save_release(key, rel)
        if not self._build(key, rel):
            rel["state"] = "build_failed"
            self._save_release(key, rel)
        return rel

    def _go_live(self, key: Key, rel: dict, strategy: str, canary_weight: int, timeout: float) -> dict:
        if not rel["spec"]["command"]:
            rel["state"] = "failed"
            self._save_release(key, rel)
            raise SimCloudError("unprocessable", "the service spec has no command; set `command` to start the app on $PORT")
        if rel["state"] == "build_failed":
            return self._result(key, rel, "build failed; see build logs")
        try:
            run = self._run_for(key, rel)
        except SimCloudError as e:
            rel["state"] = "failed"
            self._save_release(key, rel)
            raise e
        spec = rel["spec"]
        current = self.traffic(key)
        if strategy == "none":
            rel["state"] = "ready_not_serving"
            self._save_release(key, rel)
            return self._result(key, rel, None)
        if strategy == "canary" and current:
            out = self.supervisor.scale_release(key, run, 1, timeout)
            if out["ready"]:
                weights = {r: w for r, w in current.items() if w > 0}
                scale = (100 - canary_weight) / sum(weights.values())
                new_traffic = {r: round(w * scale) for r, w in weights.items()}
                new_traffic[rel["id"]] = 100 - sum(new_traffic.values())
        else:
            out = self.supervisor.rollout(key, run, max(1, spec["min_instances"]), timeout, replace=True)
            new_traffic = {rel["id"]: 100}
        if not out["ready"]:
            rel["state"] = "failed"
            self._save_release(key, rel)
            return self._result(key, rel, out["reason"])
        rel["state"] = "ready"
        self._save_release(key, rel)
        self._set_status(key, phase="ready", traffic=new_traffic, url=self.url(key))
        return self._result(key, rel, None)

    def _result(self, key: Key, rel: dict, error: str | None) -> dict:
        return {"release": rel["id"], "state": rel["state"], "digest": rel["digest"], "traffic": self.traffic(key),
                "error": error}

    def url(self, key: Key) -> str:
        project, env, name = key
        return f"{self.router_url}/_svc/{project}/{env}/{name}/"

    # ---- public operations ----------------------------------------------------------

    def deploy(self, actor: Principal, key: Key, archive: bytes | None, strategy: str = "rolling",
               canary_weight: int = 10) -> dict:
        svc = self._service(actor, key, "deploy")
        if strategy not in ("rolling", "canary", "none") or not 1 <= canary_weight <= 99:
            raise SimCloudError("invalid_request", "strategy is rolling|canary|none; canary_weight 1..99")
        if archive is None:
            # Redeploy the serving artifact with the current spec (e.g. after a config change).
            serving = max(self.traffic(key).items(), key=lambda kv: kv[1], default=(None, 0))[0]
            if not serving:
                raise SimCloudError("unprocessable", "nothing is serving yet; deploy a source archive first")
            digest = self._release(key, serving)["digest"]
        else:
            digest = self._store_artifact(archive)
        rel = self._create_release(actor, key, svc["spec"], digest, None)
        return self._go_live(key, rel, strategy, canary_weight, svc["spec"]["rollout_timeout_seconds"])

    def promote(self, actor: Principal, project: str, name: str, from_env: str, to_env: str,
                release_id: str | None = None) -> dict:
        src_key, dst_key = (project, from_env, name), (project, to_env, name)
        self._service(actor, src_key, "read")
        dst = self._service(actor, dst_key, "promote")
        if release_id is None:
            release_id = max(self.traffic(src_key).items(), key=lambda kv: kv[1], default=(None, 0))[0]
            if not release_id:
                raise SimCloudError("unprocessable", f"nothing is serving in {from_env}")
        src = self._release(src_key, release_id)
        if src["state"] not in ("ready", "ready_not_serving"):
            raise SimCloudError("unprocessable", f"release {release_id} in {from_env} is {src['state']}, not ready")
        rel = self._create_release(actor, dst_key, dst["spec"], src["digest"], f"{from_env}/{release_id}")
        return self._go_live(dst_key, rel, "rolling", 10, dst["spec"]["rollout_timeout_seconds"])

    def rollback(self, actor: Principal, key: Key, to_release: str | None = None) -> dict:
        self._service(actor, key, "rollback")
        current = self.traffic(key)
        if to_release is None:
            serving = [r for r in self.releases(key) if r["id"] in current]
            if len(serving) > 1:
                # A canary is in progress: abort it, back to the oldest serving (stable) release.
                target = serving[0]
            else:
                candidates = [r for r in self.releases(key) if r["state"] == "ready" and r["id"] not in current]
                if not candidates:
                    raise SimCloudError("unprocessable", "no earlier ready release to roll back to")
                target = candidates[-1]
        else:
            target = self._release(key, to_release)
            if target["state"] != "ready":
                raise SimCloudError("unprocessable", f"release {to_release} is {target['state']}")
        out = self.supervisor.rollout(key, self._run_for(key, target), max(1, target["spec"]["min_instances"]),
                                      target["spec"]["rollout_timeout_seconds"], replace=True)
        if not out["ready"]:
            return self._result(key, target, out["reason"])
        self._set_status(key, phase="ready", traffic={target["id"]: 100}, url=self.url(key))
        return self._result(key, target, None)

    def set_traffic(self, actor: Principal, key: Key, weights: dict[str, int], timeout: float = 120.0) -> dict:
        self._service(actor, key, "set_traffic")
        if sum(weights.values()) != 100 or any(w < 0 for w in weights.values()):
            raise SimCloudError("invalid_request", "weights must be non-negative and sum to 100")
        for rid, w in weights.items():
            rel = self._release(key, rid)
            if w > 0 and rel["state"] not in ("ready", "ready_not_serving"):
                raise SimCloudError("unprocessable", f"release {rid} is {rel['state']}")
            if w > 0 and not self.supervisor.ready_instances(key, rid):
                out = self.supervisor.scale_release(key, self._run_for(key, rel), 1, timeout)
                if not out["ready"]:
                    raise SimCloudError("unprocessable", f"release {rid} did not become ready: {out['reason']}")
                rel["state"] = "ready"
                self._save_release(key, rel)
        for rid in [r for r, w in weights.items() if w == 0]:
            self.supervisor.stop_release(key, rid, self._release(key, rid)["spec"]["drain_seconds"])
        self._set_status(key, phase="ready", traffic={r: w for r, w in weights.items() if w > 0}, url=self.url(key))
        return {"traffic": self.traffic(key)}

    def restart(self, actor: Principal, key: Key, timeout: float = 120.0) -> dict:
        self._service(actor, key, "restart")
        runs = {r: self._run_for(key, self._release(key, r)) for r in self.traffic(key)}
        return self.supervisor.restart(key, runs, timeout)

    def status(self, actor: Principal, key: Key) -> dict:
        svc = self._service(actor, key, "read")
        return {"service": svc["name"], "env": key[1], "url": self.url(key), "traffic": self.traffic(key),
                "phase": svc["status"].get("phase"),
                "instances": [i.public() for i in self.supervisor.instances(key)],
                "releases": [{k: r[k] for k in ("id", "state", "digest", "created_by", "created_at", "promoted_from")}
                             for r in self.releases(key)]}

    def logs(self, actor: Principal, key: Key, since: float = 0.0, limit: int = 500, source: str = "") -> list[dict]:
        self._service(actor, key, "logs")
        return self.supervisor.logs.read(key, since, min(limit, 5000), source)

    def metrics(self, actor: Principal, key: Key, release: str | None = None, since: float = 0.0) -> dict:
        project, env, name = key
        self.cloud.authorize(actor, "metrics:read", srn(project, env, "service", name), project, env)
        return self.router.metrics.summary(key, release, since)

    def recover(self) -> None:
        """After a control-plane restart, bring serving releases back up."""
        for project in [k for k, _ in self.store.kv_items("projects")]:
            for svc in self.store.list_resources(project, kind="service"):
                key = (project, svc["env"], svc["name"])
                for rid, w in self.traffic(key).items():
                    if w > 0:
                        rel = self._release(key, rid)
                        try:
                            self.supervisor.scale_release(key, self._run_for(key, rel), max(1, rel["spec"]["min_instances"]))
                        except SimCloudError:
                            pass

    def _on_delete(self, actor: Principal, project: str, env: str, kind: str, name: str) -> None:
        if kind == "service":
            self.supervisor.stop_service((project, env, name), 5.0)


def pack_directory(root: Path, ignore: list[str] | None = None) -> bytes:
    """What `sc deploy` uploads: a gzip tar of the directory, skipping VCS and caches."""
    import fnmatch
    patterns = [".git", "__pycache__", "*.pyc", "node_modules", ".venv", ".simcloud"] + (ignore or [])
    ign_file = root / ".simcloudignore"
    if ign_file.exists():
        patterns += [l.strip() for l in ign_file.read_text().splitlines() if l.strip() and not l.startswith("#")]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if any(fnmatch.fnmatch(part, p) for part in rel.parts for p in patterns):
                continue
            if path.is_symlink():
                continue
            tar.add(path, arcname=str(rel), recursive=False)
    return buf.getvalue()
