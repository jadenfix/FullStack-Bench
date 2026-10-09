"""Run an experiment plan: every episode through the operator's budget gateway, wave by wave.

    uv run python -m fsbench.runner PLAN --manifest MANIFEST --out DIR --max-total-calls N
    uv run python -m fsbench.runner PLAN --manifest MANIFEST --out DIR --max-total-calls N --dry-run

The plan comes from `fsbench.experiment plan`; this module executes it and nothing else.

Credentials: provider keys are read from the environment or the checkout's `.env` and stay in
this process. Each key slot gets its own gateway (`fsbench.budget_proxy`) holding one upstream
key, bound to the Docker bridge. Each attempt gets a throwaway session token on its planned
slot, with the manifest's envelope. The solver's environment is stripped of every other
credential-looking variable.

Refused before any model call (preflight):
- the plan does not belong to this manifest, or says it was already executed;
- a task's content hash differs from the manifest's checksum;
- a task's declared container limits differ from the manifest's runtime limits (Harbor applies
  the task's `[environment] cpus` / `memory_mb` as the hard limit);
- the manifest declares a cold build cache: this runner shares Docker's layer cache, so only
  `cache: warm` describes what it does;
- a base image a task builds `FROM` is missing locally (a stale or absent base produced invalid
  gate receipts before), differs from the manifest's pinned `base_images`, or a Rusty
  binary's hash differs from its pin;
- containers are already running (another trial would share the host);
- the worst case (remaining episodes x envelope calls x attempts) exceeds `--max-total-calls`.

Each attempt appends one line to `ledger.jsonl` with its plan position, key slot, job, timing,
the outcome class below, the verifier's full reward record, the agent's metadata (completion
events, budget counters) and the gateway's accounting. Outcome classes:
- `scored`: the verifier produced a reward. A solver that ran out of its budget or exited
  nonzero is scored, not replaced.
- `coverage_limitation`: the task needs a capability the harness lacks. Kept and reported.
- `configuration_error`: the operator pinned an unsupported setting. Invalid; the run stops.
- `provider_error`, `infra_error`, `verifier_error`, `no_trial`, `outer_timeout`,
  `interrupted`: not caused by the solver. Recorded and replaced, up to `--max-attempts`.
- `task_mismatch`: the trial ran a different task revision than the manifest pins. The run stops.
A scored attempt whose Rusty retry wait is at least half its agent time is flagged
`throttle_confounded`; it is kept, and the analysis decides how to treat it.

Ledger lines are never regraded. A changed verifier, task or base image means a new manifest and
a rerun, unless the evidence the attempt retained supports the new check on its own.

The ledger makes runs resumable: an episode whose last attempt is final is skipped, and a job
directory with no ledger line (the host died mid-attempt) is recorded as `interrupted`.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .budget_proxy import BudgetProxy, Envelope

SCHEMA = "fsb-run-v1"
REPLACEABLE = {"provider_error", "infra_error", "verifier_error", "no_trial", "outer_timeout", "interrupted"}
STOPS_RUN = {"configuration_error", "task_mismatch"}
# Anything that looks like a credential is removed from the solver's environment.
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)
PROVIDER_PREFIXES = ("NVIDIA_", "OPENAI_", "RUSTY_", "MSWEA_", "LITELLM_", "ANTHROPIC_")
FROM_LINE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.M | re.I)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def manifest_sha256(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


def task_checksum(task_dir: Path) -> str:
    """The content hash Harbor records as `task_checksum` in every trial result."""
    from dirhash import dirhash
    return dirhash(task_dir, "sha256")


def load_env(path: Path) -> dict[str, str]:
    out = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def provider_keys(slots: list[int], env: dict[str, str]) -> dict[int, str]:
    """Slot 1 is NVIDIA_API_KEY, slot n is NVIDIA_API_KEY_n."""
    keys = {}
    for slot in slots:
        name = "NVIDIA_API_KEY" if slot == 1 else f"NVIDIA_API_KEY_{slot}"
        if not env.get(name):
            raise ValueError(f"key slot {slot} needs {name} in the environment or .env")
        keys[slot] = env[name]
    if len(set(keys.values())) != len(keys):
        raise ValueError("key slots must hold different keys; one key shared by two slots is one slot")
    return keys


def solver_env(base: dict[str, str], harness: str, token: str, base_url: str, fsb_dir: Path) -> dict[str, str]:
    """The Harbor process's environment: no provider key, only this attempt's throwaway token."""
    env = {k: v for k, v in base.items() if not SECRET_NAME.search(k) and not k.startswith(PROVIDER_PREFIXES)}
    # fsbench is installed editable; pin imports to the checkout the plan was made from.
    env["PYTHONPATH"] = str(fsb_dir)
    if harness == "rusty":
        env |= {"NVIDIA_API_KEY": token, "RUSTY_BASE_URL": base_url}
    else:
        env |= {"OPENAI_API_KEY": token, "OPENAI_BASE_URL": base_url}
    return env


def envelope_for(m: dict) -> Envelope:
    e, inf = m["envelope"], m["inference"]
    return Envelope(calls=e["calls"], input_tokens=e["input_tokens"], output_tokens=e["output_tokens"],
                    wall_seconds=e["wall_seconds"], max_reply=inf["max_reply"], temperature=inf["temperature"],
                    top_p=inf["top_p"], reasoning_effort=inf.get("reasoning_effort"))


def outer_timeout(task_dir: Path) -> int:
    """Build + agent + verifier timeouts from the task, plus slack. Harbor's own timeouts fire first;
    this only catches a hung Harbor process."""
    cfg = tomllib.loads((task_dir / "task.toml").read_text())
    total = (cfg.get("environment", {}).get("build_timeout_sec", 900) + cfg.get("agent", {}).get("timeout_sec", 3600)
             + cfg.get("verifier", {}).get("timeout_sec", 600))
    return int(total) + 900


class Docker:
    """The few Docker facts the runner checks. Replaced by a fake in tests."""

    def image_id(self, name: str) -> str | None:
        r = subprocess.run(["docker", "image", "inspect", name, "--format", "{{.Id}}"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def running(self) -> list[str]:
        r = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, check=True)
        return r.stdout.split()


def base_images(task_dir: Path) -> list[str]:
    """Local images the task's Dockerfiles build FROM (anything not pinned by digest): the
    solver's environment and the verifier's image, which carries evaluator code too."""
    names = set()
    for dockerfile in [*(task_dir / "environment").rglob("Dockerfile"), *(task_dir / "tests").rglob("Dockerfile")]:
        for name in FROM_LINE.findall(dockerfile.read_text()):
            if "@sha256:" not in name and not name.startswith("$"):
                names.add(name)
    return sorted(names)


def classify(trial: dict | None, *, timed_out: bool, expected_checksum: str) -> str:
    if timed_out:
        return "outer_timeout"
    if trial is None:
        return "no_trial"
    exc = (trial.get("exception_info") or {}).get("exception_type")
    rewards = (trial.get("verifier_result") or {}).get("rewards") or {}
    if trial.get("task_checksum") and trial["task_checksum"] != expected_checksum:
        return "task_mismatch"
    if exc == "RustyConfigurationError":
        return "configuration_error"
    if exc == "RustyCoverageLimitation":
        return "coverage_limitation"
    if exc in ("ApiRateLimitError", "ApiInternalServerError"):
        return "provider_error"
    if "reward" in rewards:
        return "scored"
    return "verifier_error" if exc is None else "infra_error"


def throttle_confounded(trial: dict) -> bool:
    meta = (trial.get("agent_result") or {}).get("metadata") or {}
    wait = meta.get("rusty_budget_retry_wait_seconds")
    run = trial.get("agent_execution") or {}
    try:
        seconds = (datetime.fromisoformat(run["finished_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return False
    return isinstance(wait, (int, float)) and seconds > 0 and wait / seconds >= 0.5


def find_trial(job_dir: Path) -> tuple[Path | None, dict | None]:
    for result in sorted(job_dir.glob("*/result.json")):
        data = json.loads(result.read_text())
        if "trial_name" in data:
            return result.parent, data
    return None, None


class Gateways:
    """One gateway per key slot, each serving on its own port in a background thread."""

    def __init__(self, keys: dict[int, str], host: str, base_port: int, *, transport=None):
        import uvicorn

        self.host = host
        self.proxies = {slot: BudgetProxy([key], transport=transport) for slot, key in keys.items()}
        self.ports = {slot: base_port + i for i, slot in enumerate(sorted(keys))}
        self.servers = {slot: uvicorn.Server(uvicorn.Config(self.proxies[slot], host=host, port=self.ports[slot],
                                                            log_level="warning"))
                        for slot in keys}
        self.threads: list[threading.Thread] = []

    def __enter__(self) -> Gateways:
        for server in self.servers.values():
            t = threading.Thread(target=server.run, daemon=True)
            t.start()
            self.threads.append(t)
        deadline = time.monotonic() + 30
        while not all(s.started for s in self.servers.values()):
            if time.monotonic() > deadline:
                raise RuntimeError("a gateway did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *_):
        for server in self.servers.values():
            server.should_exit = True
        for t in self.threads:
            t.join(timeout=10)

    def session(self, slot: int, name: str, model: str, receipt: Path, envelope: Envelope) -> tuple[str, str]:
        s = self.proxies[slot].register(name, model, receipt, envelope)
        return s.token, f"http://{self.host}:{self.ports[slot]}/v1"


class Runner:
    def __init__(self, plan: dict, manifest: dict, *, fsb_dir: Path, out: Path, max_total_calls: int,
                 max_attempts: int = 2, only: list[str] | None = None, docker: Docker | None = None,
                 harbor: str | None = None, env: dict[str, str] | None = None):
        self.plan, self.m = plan, manifest
        self.fsb_dir, self.out = fsb_dir.resolve(), out.resolve()
        self.max_total_calls, self.max_attempts = max_total_calls, max_attempts
        self.only = set(only or [])
        self.docker = docker or Docker()
        self.harbor = harbor or str(Path(sys.executable).with_name("harbor"))
        self.env = dict(os.environ if env is None else env)
        self.tracks = {t["name"]: t for t in manifest["tracks"]}
        self.tasks = {t["name"]: t for t in manifest["tasks"]}
        self.ledger = self.out / "ledger.jsonl"
        self.images: dict[str, str | None] = {}
        self._lock = threading.Lock()

    # -- bookkeeping -------------------------------------------------------------------------
    def records(self) -> list[dict]:
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in self.ledger.read_text().splitlines() if line.strip()]

    def append(self, record: dict) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        with self._lock, self.ledger.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def selected(self) -> list[dict]:
        return [e for e in self.plan["episodes"] if not self.only or e["episode"] in self.only]

    def attempts(self, episode: str) -> list[dict]:
        return [r for r in self.records() if r.get("kind") == "attempt" and r["episode"] == episode]

    def pending(self) -> list[dict]:
        """Episodes still to run, with the attempt number each would use next."""
        todo = []
        for e in self.selected():
            done = self.attempts(e["episode"])
            if done and done[-1]["status"] not in REPLACEABLE:
                continue
            if len(done) >= self.max_attempts:
                continue
            todo.append(e)
        return todo

    def admitted_calls(self) -> int:
        return sum((r.get("gateway") or {}).get("admitted_calls", 0) for r in self.records() if r.get("kind") == "attempt")

    # -- checks ------------------------------------------------------------------------------
    def preflight(self) -> list[str]:
        m, plan, errors = self.m, self.plan, []
        if plan.get("manifest_sha256") != manifest_sha256(m):
            errors.append("the plan was not made from this manifest (manifest_sha256 differs)")
        if plan.get("executed"):
            errors.append("the plan says it was already executed")
        if m["runtime"]["cache"] != "warm":
            errors.append("this runner shares Docker's build cache across episodes; declare runtime.cache \"warm\"")
        unknown = self.only - {e["episode"] for e in plan["episodes"]}
        if unknown:
            errors.append(f"--only names episodes not in the plan: {sorted(unknown)}")
        for name in {e["task"] for e in self.selected()}:
            task_dir = self.fsb_dir / "tasks" / name
            if not task_dir.is_dir():
                errors.append(f"task {name} is not under {self.fsb_dir / 'tasks'}")
                continue
            if task_checksum(task_dir) != self.tasks[name]["checksum"]:
                errors.append(f"task {name}: content differs from the manifest's checksum")
            env = tomllib.loads((task_dir / "task.toml").read_text()).get("environment", {})
            if (env.get("cpus"), env.get("memory_mb")) != (m["runtime"]["cpus_limit"], m["runtime"]["memory_limit_mb"]):
                errors.append(f"task {name}: container limits {env.get('cpus')} cpu / {env.get('memory_mb')} MB "
                              f"differ from the runtime's {m['runtime']['cpus_limit']} / {m['runtime']['memory_limit_mb']}")
            pinned = m.get("base_images") or {}
            for image in base_images(task_dir):
                self.images[image] = self.docker.image_id(image)
                if self.images[image] is None:
                    errors.append(f"task {name}: base image {image} is not built locally")
                elif pinned and image not in pinned:
                    errors.append(f"task {name}: base image {image} is not pinned in the manifest's base_images")
                elif pinned and self.images[image] != pinned[image]:
                    errors.append(f"task {name}: base image {image} is {self.images[image]}, "
                                  f"the manifest pins {pinned[image]}")
        for name in {e["track"] for e in self.selected()}:
            track = self.tracks[name]
            if track["harness"] == "rusty":
                binary = Path(track["binary"])
                if not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest() != track["binary_sha256"]:
                    errors.append(f"track {name}: {binary} is missing or does not match binary_sha256")
        if self.docker.running():
            errors.append("containers are already running; another trial would share the host")
        todo = self.pending()
        worst = len(todo) * self.m["envelope"]["calls"] * self.max_attempts + self.admitted_calls()
        if worst > self.max_total_calls:
            errors.append(f"worst case {worst} calls (spent plus {len(todo)} episodes x {self.m['envelope']['calls']} "
                          f"calls x {self.max_attempts} attempts) exceeds --max-total-calls {self.max_total_calls}")
        return errors

    # -- execution ---------------------------------------------------------------------------
    def command(self, episode: dict, job: str) -> list[str]:
        cmd = list(episode["command"])
        if cmd[0] != "harbor":
            raise ValueError("plan commands must start with harbor")
        cmd[0] = self.harbor
        cmd[cmd.index("--job-name") + 1] = job
        return cmd + ["-o", str(self.out / "jobs")]

    def reconcile(self) -> None:
        """Record job directories the ledger never heard of (the host died mid-attempt) as interrupted."""
        logged = {r["job"] for r in self.records() if r.get("kind") == "attempt"}
        jobs = self.out / "jobs"
        for e in self.selected():
            for job_dir in sorted(jobs.glob(f"{e['episode']}--a*")) if jobs.exists() else []:
                if job_dir.name not in logged and re.fullmatch(r"a\d+", job_dir.name.rsplit("--", 1)[1]):
                    self.append({"kind": "attempt", "schema": SCHEMA, "episode": e["episode"], "track": e["track"],
                                 "task": e["task"], "attempt": int(job_dir.name.rsplit("--a", 1)[1]),
                                 "job": job_dir.name, "status": "interrupted", "replaceable": True,
                                 "recorded_at": now(), "note": "job directory without a ledger line"})

    def next_attempt(self, episode: str) -> int:
        n = max((r["attempt"] for r in self.attempts(episode)), default=0) + 1
        if (self.out / "jobs" / f"{episode}--a{n}").exists():
            raise RuntimeError(f"job {episode}--a{n} already exists but is not in the ledger; reconcile first")
        return n

    def run_one(self, gateways: Gateways | None, episode: dict) -> dict:
        track, task = self.tracks[episode["track"]], self.tasks[episode["task"]]
        attempt = self.next_attempt(episode["episode"])
        job = f"{episode['episode']}--a{attempt}"
        receipt = self.out / "receipts" / f"{job}.json"
        record = {"kind": "attempt", "schema": SCHEMA, "manifest_sha256": self.plan["manifest_sha256"],
                  **{k: episode[k] for k in ("episode", "track", "task", "seed", "block", "position", "wave", "key_slot")},
                  "attempt": attempt, "job": job, "harness": track["harness"], "started_at": now()}
        task_dir = self.fsb_dir / "tasks" / task["name"]
        declared = tomllib.loads((task_dir / "task.toml").read_text()).get("environment", {})
        record["resources"] = {
            "cpus_limit": declared.get("cpus"), "memory_limit_mb": declared.get("memory_mb"),
            "limit_source": "task.toml [environment], applied by Harbor as the container limit",
            "cpus_reserved": self.m["runtime"]["cpus_reserved"],
            "memory_reserved_mb": self.m["runtime"]["memory_reserved_mb"],
            "reservation": "declared in the manifest; not separately enforced",
            "concurrent_trials": len([e for e in self.plan["episodes"] if e["wave"] == episode["wave"]]),
        }
        token, base_url = gateways.session(episode["key_slot"], job, self.m["model"]["id"], receipt,
                                           envelope_for(self.m))
        log = self.out / "logs" / f"{job}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        timed_out = False
        with log.open("w") as f:
            proc = subprocess.Popen(self.command(episode, job), cwd=self.fsb_dir, stdout=f, stderr=subprocess.STDOUT,
                                    env=solver_env(self.env, track["harness"], token, base_url, self.fsb_dir),
                                    start_new_session=True)
            try:
                proc.wait(timeout=outer_timeout(task_dir))
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGINT)
                try:
                    proc.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        trial_dir, trial = find_trial(self.out / "jobs" / job)
        status = classify(trial, timed_out=timed_out, expected_checksum=task["checksum"])
        gateway = json.loads(receipt.read_text()) if receipt.exists() else {}
        trial = trial or {}
        agent = trial.get("agent_result") or {}
        record |= {
            "finished_at": now(), "harbor_exit": proc.returncode, "status": status,
            "replaceable": status in REPLACEABLE, "trial_dir": str(trial_dir) if trial_dir else None,
            "task_checksum": trial.get("task_checksum"),
            "exception_type": (trial.get("exception_info") or {}).get("exception_type"),
            "exception_message": ((trial.get("exception_info") or {}).get("exception_message") or "")[:1000] or None,
            "rewards": (trial.get("verifier_result") or {}).get("rewards"),
            "agent_metadata": agent.get("metadata"), "n_input_tokens": agent.get("n_input_tokens"),
            "n_output_tokens": agent.get("n_output_tokens"),
            "throttle_confounded": status == "scored" and throttle_confounded(trial),
            "gateway": gateway.get("accounting"), "gateway_exhausted": gateway.get("exhausted"),
            "receipt": str(receipt),
        }
        self.append(record)
        return record

    def wait_for_quiet_host(self, seconds: int = 300) -> bool:
        deadline = time.monotonic() + seconds
        while self.docker.running():
            if time.monotonic() > deadline:
                return False
            time.sleep(5)
        return True

    def run(self, gateways: Gateways) -> str:
        """Execute pending episodes wave by wave. Returns why it stopped."""
        self.append({"kind": "run", "schema": SCHEMA, "manifest_sha256": self.plan["manifest_sha256"],
                     "plan_sha256": hashlib.sha256(json.dumps(self.plan, sort_keys=True).encode()).hexdigest(),
                     "fsb_dir": str(self.fsb_dir), "fsb_revision": git_revision(self.fsb_dir),
                     "base_images": self.images, "max_attempts": self.max_attempts,
                     "max_total_calls": self.max_total_calls, "only": sorted(self.only),
                     "key_slots": sorted(gateways.ports), "started_at": now()})
        self.reconcile()
        while todo := self.pending():
            wave = min(e["wave"] for e in todo)
            members = [e for e in todo if e["wave"] == wave]
            if not self.wait_for_quiet_host():
                return self.stop("containers from an earlier attempt are still running")
            for image, expected in self.images.items():
                if self.docker.image_id(image) != expected:
                    return self.stop(f"base image {image} changed during the run")
            spent = self.admitted_calls()
            if spent + len(members) * self.m["envelope"]["calls"] > self.max_total_calls:
                return self.stop(f"the next wave could exceed --max-total-calls ({spent} spent)")
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(members)) as pool:
                done = list(pool.map(lambda e: self.run_one(gateways, e), members))
            if bad := [r for r in done if r["status"] in STOPS_RUN]:
                return self.stop(f"{bad[0]['status']} in {bad[0]['job']}")
        return self.stop("complete")

    def stop(self, reason: str) -> str:
        self.append({"kind": "stop", "schema": SCHEMA, "reason": reason, "admitted_calls": self.admitted_calls(),
                     "at": now()})
        return reason


def git_revision(path: Path) -> dict:
    head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True)
    return {"head": head.stdout.strip() or "unknown", "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan", type=Path)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--fsb-dir", type=Path, default=Path.cwd(), help="checkout holding tasks/ (Harbor's cwd)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-total-calls", type=int, required=True, help="the approved cohort budget in admitted calls")
    ap.add_argument("--max-attempts", type=int, default=2)
    ap.add_argument("--only", action="append", help="run only these episodes (repeatable)")
    ap.add_argument("--bind", default="172.17.0.1", help="gateway address reachable from trial containers")
    ap.add_argument("--base-port", type=int, default=8930)
    ap.add_argument("--dry-run", action="store_true", help="preflight and list commands; no gateway, no model call")
    a = ap.parse_args()
    plan, manifest = json.loads(a.plan.read_text()), json.loads(a.manifest.read_text())
    runner = Runner(plan, manifest, fsb_dir=a.fsb_dir, out=a.out, max_total_calls=a.max_total_calls,
                    max_attempts=a.max_attempts, only=a.only)
    errors = runner.preflight()
    if errors:
        print("refused:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 1
    todo = runner.pending()
    print(f"{len(todo)} episodes pending; base images {runner.images}")
    if a.dry_run:
        for e in todo:
            print(f"  wave {e['wave']} key {e['key_slot']}: {' '.join(runner.command(e, e['episode'] + '--aN'))}")
        return 0
    env = {**load_env(runner.fsb_dir / ".env"), **os.environ}
    keys = provider_keys(manifest["runtime"]["key_slots"], env)
    with Gateways(keys, a.bind, a.base_port) as gateways:
        reason = runner.run(gateways)
    print(f"stopped: {reason}")
    return 0 if reason == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
