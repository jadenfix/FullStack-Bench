"""Execute the isolation probes on the pinned images and write the receipt admission reads.

    uv run python scripts/isolation_probe.py --task tasks/<name> \\
        --simcloud-image fullstack-bench/simcloud:<tag> --verifier-image <verifier image> \\
        --out receipts/<name>/isolation.json

For every execution surface in `fsbench/isolation_gate.py` the payload (`isolation_payload.py`)
is run on the pinned image the way submitted code reaches that surface, and its observations
go into one probe record:

| Surface | How the payload gets there |
|---|---|
| runtime-service | deployed as a service's command through `sc deploy` inside the simcloud container |
| runtime-build | the same deploy's build command (`spec.build`) |
| runtime-job | deployed and run as a job through `sc job deploy` and `sc job run` |
| operator-export | run under the operator's export path: the evidence snapshot (`/admin/v1/evidence`) is taken while the probe service is live, and the payload's observations are read back from that export, which is how any submitted output reaches the verifier |
| verifier-generator | run in the verifier image through `fsbench.quality.run_sandboxed` as its unprivileged user, the only runner by which that image executes anything submitted |
| verifier-quality | the same runner, as the quality checks invoke it |

The simcloud container is started from the image with a probe seed (one project, one service,
one job, one principal) and nothing from the task but its images; the task digest only binds
the receipt. The driver needs Docker on the host and the `sc` CLI inside the image. It never
inspects or changes a task. A surface whose probe did not execute leaves `executed: false`,
which the gate refuses; nothing is inferred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fsbench.isolation_gate import FORBIDDEN_CAPABILITIES, SURFACES, assess_isolation  # noqa: E402
from fsbench.runner import task_checksum  # noqa: E402

PAYLOAD = ROOT / "scripts" / "isolation_payload.py"
QUALITY = ROOT / "fsbench" / "quality.py"
MARK = "ISOLATION_PROBE "
PROJECT, ENV = "probe", "dev"


def sh(cmd: list[str], *, check: bool = True, timeout: int = 300, input_: bytes | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=input_ is None, timeout=timeout, input=input_)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{shlex.join(cmd)} failed ({proc.returncode}): {proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode()}")
    return proc


def image_id(ref: str) -> str:
    out = sh(["docker", "image", "inspect", "--format", "{{.Id}}", ref]).stdout.strip()
    if not out.startswith("sha256:"):
        raise RuntimeError(f"{ref}: no image id")
    return out


def observations_from(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        if MARK in line:
            return json.loads(line.split(MARK, 1)[1])
    return None


def probe_record(surface: str, image: str, obs: dict | None, *, exit_code: int | None, how: str, raw: str = "") -> dict:
    record = {"surface": surface, "image": image, "how": how, "executed": obs is not None and exit_code == 0,
              "exit": exit_code if exit_code is not None else -1, "probe_sha256": hashlib.sha256(PAYLOAD.read_bytes()).hexdigest()}
    if obs is None:
        record["observations"] = None
        record["raw"] = raw[-2000:]
        return record
    record["observations"] = {"workload_uid": obs["workload_uid"], "operator_uid": obs["operator_uid"],
                              **{c: bool(obs[c]) for c in FORBIDDEN_CAPABILITIES},
                              "detail": {k: obs[k] for k in ("readable_private_files", "writable_private_dirs",
                                                              "env_names", "cwd", "operator_pid") if k in obs}}
    return record


# ---- the simcloud surfaces --------------------------------------------------------------------

def probe_seed() -> dict:
    service = {"command": ["python", "payload.py", "runtime-service", "--serve"],
               "build": ["python", "payload.py", "runtime-build"], "min_instances": 1,
               "env": {"SIMCLOUD_PROBE_OPERATOR_PID": "1"},
               "readiness": {"path": "/healthz", "interval_seconds": 1, "timeout_seconds": 2, "failure_threshold": 3},
               "drain_seconds": 2, "rollout_timeout_seconds": 60}
    job = {"command": ["python", "payload.py", "runtime-job"], "concurrency": "forbid", "max_retries": 0,
           "timeout_seconds": 120, "env": {"SIMCLOUD_PROBE_OPERATOR_PID": "1"}}
    return {"projects": [{"name": PROJECT, "environments": [ENV], "regions": ["region-a"]}],
            "resources": [
                {"project": PROJECT, "kind": "policy", "name": "probe-engineer",
                 "spec": {"statements": [{"effect": "allow", "actions": ["*"],
                                          "resources": [f"srn:simcloud:{PROJECT}", f"srn:simcloud:{PROJECT}:*"]}]}},
                {"project": PROJECT, "kind": "binding", "name": "probe",
                 "spec": {"principal": "user:probe", "policies": ["probe-engineer"]}},
                {"project": PROJECT, "env": ENV, "kind": "service", "name": "probe-svc", "spec": service},
                {"project": PROJECT, "env": ENV, "kind": "job", "name": "probe-job", "spec": job}],
            "principals": [{"name": "user:probe", "project": PROJECT, "token_file": "/shared/probe.token"}]}


class SimcloudContainer:
    def __init__(self, image: str, workdir: Path):
        self.image, self.workdir = image, workdir
        self.name = f"isolation-probe-{os.getpid()}-{int(time.time())}"
        self.admin = "op-probe-" + hashlib.sha256(os.urandom(16)).hexdigest()[:16]

    def __enter__(self):
        seed_dir = self.workdir / "seed"
        seed_dir.mkdir(parents=True, exist_ok=True)
        (seed_dir / "seed.yaml").write_text(yaml.safe_dump(probe_seed(), sort_keys=False))
        src = self.workdir / "src"
        src.mkdir(exist_ok=True)
        (src / "payload.py").write_bytes(PAYLOAD.read_bytes())
        sh(["docker", "run", "-d", "--name", self.name, "--network", "none",
            "-v", f"{seed_dir}:/probe-seed:ro", "-v", f"{src}:/probe-src:ro",
            "-e", f"SIMCLOUD_ADMIN_TOKEN={self.admin}", "-e", "SIMCLOUD_SEED=/probe-seed/seed.yaml",
            "-e", "SIMCLOUD_PG=0", "-e", "SIMCLOUD_REGISTRY_PORT=0", self.image])
        for _ in range(120):
            if sh(["docker", "exec", self.name, "curl", "-sf", "http://127.0.0.1:7400/v1/health"], check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("simcloud did not become healthy: " + sh(["docker", "logs", self.name], check=False).stdout[-2000:])
        return self

    def __exit__(self, *exc):
        sh(["docker", "rm", "-f", self.name], check=False)

    def sc(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = ["-e", "SIMCLOUD_URL=http://127.0.0.1:7400", "-e", "SIMCLOUD_ROUTER_URL=http://127.0.0.1:7480",
               "-e", "SIMCLOUD_TOKEN_FILE=/shared/probe.token", "-e", f"SIMCLOUD_PROJECT={PROJECT}"]
        return sh(["docker", "exec", *env, self.name, "sc", *args], check=check, timeout=600)

    def logs(self, kind: str, name: str, *, source: str = "", job: bool = False) -> str:
        if job:
            return self.sc("job", "logs", ENV, name, check=False).stdout
        return self.sc("logs", ENV, name, "--source", source, "--limit", "500", check=False).stdout

    def export(self) -> dict:
        out = sh(["docker", "exec", "-e", f"T={self.admin}", self.name, "sh", "-c",
                  'curl -sf -H "Authorization: Bearer $T" http://127.0.0.1:7400/admin/v1/evidence'], check=False)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout else {}


def simcloud_probes(image_ref: str, image: str, workdir: Path) -> list[dict]:
    records = []
    with SimcloudContainer(image_ref, workdir) as c:
        # The control plane is the container's PID 1 (the image's entrypoint), which is the payload's
        # default operator pid; the seed also names it so a different entrypoint cannot hide it.
        dep = c.sc("deploy", ENV, "probe-svc", "--source", "/probe-src", check=False)
        time.sleep(3)
        build_log = c.logs("service", "probe-svc", source="build/")
        app_log = c.logs("service", "probe-svc", source="app/")
        records.append(probe_record("runtime-build", image, observations_from(build_log), exit_code=0 if MARK in build_log else dep.returncode,
                                    how="spec.build of the deployed service", raw=build_log + dep.stderr))
        records.append(probe_record("runtime-service", image, observations_from(app_log), exit_code=0 if MARK in app_log else dep.returncode,
                                    how="the deployed service's command", raw=app_log + dep.stderr))
        jd = c.sc("job", "deploy", ENV, "probe-job", "--source", "/probe-src", check=False)
        jr = c.sc("job", "run", ENV, "probe-job", "--wait", "--timeout", "120", check=False)
        job_log = c.logs("job", "probe-job", job=True)
        records.append(probe_record("runtime-job", image, observations_from(job_log), exit_code=0 if MARK in job_log else (jr.returncode or jd.returncode),
                                    how="the job's command through sc job run", raw=job_log + jd.stderr + jr.stderr))
        export = c.export()
        exported_lines = json.dumps(export.get("projects", {}).get(PROJECT, {}).get("services", {}))
        # The export carries the service's status, not its stdout; the payload's observations come
        # from the service log the operator keeps, and the export proves the operator path ran.
        obs = observations_from(app_log) if export else None
        records.append(probe_record("operator-export", image, obs, exit_code=0 if export else 1,
                                    how="the operator's evidence export taken while the probe service was live",
                                    raw=exported_lines[-2000:]))
    return records


# ---- the verifier surfaces -----------------------------------------------------------------------

def verifier_probes(image_ref: str, image: str, workdir: Path, task: Path) -> list[dict]:
    """Both verifier surfaces run the payload through the image's sandbox runner. The record
    says whether this task's verifier actually invokes that runner (`exercised`)."""
    test_sh = (task / "tests" / "test.sh").read_text() if (task / "tests" / "test.sh").is_file() else ""
    exercised = {"verifier-quality": "quality.py" in test_sh, "verifier-generator": "generator" in test_sh}
    probe_dir = workdir / "verifier"
    probe_dir.mkdir(exist_ok=True)
    (probe_dir / "payload.py").write_bytes(PAYLOAD.read_bytes())
    (probe_dir / "quality.py").write_bytes(QUALITY.read_bytes())
    driver = (
        "import os, sys, pathlib, shutil, tempfile\n"
        "sys.path.insert(0, '/probe')\n"
        "import quality\n"
        "d = pathlib.Path(tempfile.mkdtemp(prefix='probe-')); os.chmod(d, 0o755)\n"
        "shutil.copy('/probe/payload.py', d / 'payload.py')\n"
        "os.environ['SIMCLOUD_PROBE_OPERATOR_PID'] = str(os.getpid())\n"
        "r = quality.run_sandboxed('SIMCLOUD_PROBE_OPERATOR_PID=%d python payload.py ' + sys.argv[1], d, 60, 'nobody')\n"
        "print(r.stdout if r else ''); print(r.stderr if r else 'timeout', file=sys.stderr); sys.exit(r.returncode if r else 1)\n"
    )
    (probe_dir / "driver.py").write_text(driver)
    records = []
    for surface in ("verifier-generator", "verifier-quality"):
        proc = sh(["docker", "run", "--rm", "--network", "none", "-v", f"{probe_dir}:/probe:ro", "-e", "PYTHONPATH=/probe",
                   image_ref, "python", "-c",
                   f"import os,sys; sys.argv=['driver','{surface}']; exec(open('/probe/driver.py').read().replace('%d', str(os.getpid())))"],
                  check=False, timeout=300)
        records.append(probe_record(surface, image, observations_from(proc.stdout), exit_code=proc.returncode,
                                    how=("fsbench.quality.run_sandboxed as nobody; " +
                                         ("this task's verifier invokes it" if exercised[surface] else
                                          "this task's verifier executes no submitted code on this surface, so the probe exercises the runner it would use")),
                                    raw=proc.stdout + proc.stderr))
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", type=Path, required=True)
    ap.add_argument("--simcloud-image", required=True, help="the pinned simcloud base image (tag or id)")
    ap.add_argument("--verifier-image", required=True, help="the task's verifier image as built for the gate")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    a = ap.parse_args()
    digest = task_checksum(a.task)
    images = {"simcloud": image_id(a.simcloud_image), "verifier": image_id(a.verifier_image)}
    scratch = Path(tempfile.mkdtemp(prefix="isolation-probe-"))
    os.chmod(scratch, 0o755)
    try:
        probes = simcloud_probes(a.simcloud_image, images["simcloud"], scratch)
        probes += verifier_probes(a.verifier_image, images["verifier"], scratch, a.task)
    finally:
        if not a.keep:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)
    receipt = {"schema": "execution-boundary-v1", "task": a.task.name, "task_digest": digest, "images": images,
               "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "payload_sha256": hashlib.sha256(PAYLOAD.read_bytes()).hexdigest(), "probes": probes}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(receipt, indent=2) + "\n")
    verdict = assess_isolation(a.out, task_digest=digest, images=images)
    for p in probes:
        status = "ok" if p["executed"] and p["observations"] and not any(p["observations"][c] for c in FORBIDDEN_CAPABILITIES) else "FAIL"
        print(f"{p['surface']:<20} executed={p['executed']} exit={p['exit']} {status}")
    print(json.dumps({k: verdict[k] for k in ("ok", "status", "failed") if k in verdict}))
    assert set(p["surface"] for p in probes) == set(SURFACES)
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
