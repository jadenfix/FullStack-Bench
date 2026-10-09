"""One paired screen: Rusty and the pinned mini-SWE on the same task, model and envelope.

    uv run python scripts/paired_screen.py tasks/ship-checkout-v2 --tag p3 \
        --rusty-binary /path/to/rusty --bind 172.17.0.1 [--swap] [--port 8900]

Each track gets its own operator gateway (fsbench.budget_proxy) on its own NVIDIA key, because the
provider rate-limits per key and two agents on one key starve each other. --swap exchanges the keys,
so a cohort can alternate them. Keys come from .env (NVIDIA_API_KEY, NVIDIA_API_KEY_2) and never
leave the gateways; each trial gets a throwaway token. --bind is an address the task containers
can reach (the Docker bridge on Linux). Both tracks run at once with unique job names, and the
receipt in runs/paired/<tag>/receipt.json pins every input and records each track's outcome.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARBOR = ROOT / ".venv" / "bin" / "harbor"
sys.path.insert(0, str(ROOT))
from fsbench.budget_proxy import BudgetProxy, Envelope  # noqa: E402 -- bootstrap imports for standalone execution
from fsbench.llm import load_env  # noqa: E402

MODEL = "nvidia/nemotron-3-super-120b-a12b"
TRACKS = ("rusty", "mini")


def key_slots(swap: bool) -> dict[str, int]:
    """Which .env key (1 or 2) each track's gateway uses."""
    return {"rusty": 2, "mini": 1} if swap else {"rusty": 1, "mini": 2}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def harbor_command(track: str, task: Path, job: str, out: Path, args) -> list[str]:
    common = ["--job-name", job, "-o", str(out), "-n", "1", "-y"]
    if track == "rusty":
        return [str(HARBOR), "run", "-p", str(task), "-a", "fsbench.agents.rusty:Rusty", "-m", args.model,
                "--ak", f"binary={args.rusty_binary}", "--ak", "agents=off", "--ak", f"max_turns={args.max_turns}",
                "--ak", f"max_requests={args.calls}", "--ak", f"max_budget_tokens={args.rusty_tokens}",
                "--ak", f"budget_secs={args.wall - 200}", *common]
    return [str(HARBOR), "run", "-p", str(task), "-a", "mini-swe-agent", "-m", f"openai/{args.model}",
            "--ak", f"version={args.mswea_version}", "--ak", f"config_file={args.mswea_config}", *common]


def track_env(track: str, token: str, url: str) -> dict[str, str]:
    """The harness process sees only the trial token, never a provider key."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NVIDIA_API_KEY", "OPENAI_", "MSWEA_", "RUSTY_")) and k != "NVIDIA_API_BASE"}
    if track == "rusty":
        env.update(NVIDIA_API_KEY=token, RUSTY_BASE_URL=url)
    else:
        env.update(OPENAI_API_KEY=token, OPENAI_BASE_URL=url)
    return env


def outcome(job_dir: Path) -> dict:
    """Reward, per-check results and any exception from a finished Harbor job."""
    trials = [p for p in job_dir.glob("*__*") if p.is_dir()]
    if not trials:
        return {"status": "no_trial"}
    trial = trials[0]
    result = {}
    try:
        result = json.loads((trial / "result.json").read_text())
    except (OSError, ValueError):
        pass
    exc = result.get("exception_info") or {}
    reward = (trial / "verifier" / "reward.txt")
    checks = {}
    try:
        for t in json.loads((trial / "verifier" / "ctrf.json").read_text())["results"]["tests"]:
            checks[t["name"].split("::")[-1]] = t["status"]
    except (OSError, ValueError, KeyError):
        pass
    return {"trial": trial.name, "reward": float(reward.read_text()) if reward.exists() else None,
            "checks": checks, "exception": exc.get("exception_type"),
            "agent_metadata": (result.get("agent_result") or {}).get("metadata")}


def gateway_summary(receipt: Path) -> dict:
    data = json.loads(receipt.read_text())
    statuses: dict[str, int] = {}
    for r in data["usage_records"]:
        key = r["status"] + (f"_{r['http_status']}" if r.get("http_status") not in (None, 200) else "")
        statuses[key] = statuses.get(key, 0) + 1
    return {"calls": data["calls"], "input_charged": data["input_charged"], "output_charged": data["output_charged"],
            "exhausted": data["exhausted"], "attempts": statuses}


def serve(proxy: BudgetProxy, host: str, port: int) -> None:
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(proxy, host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return
        time.sleep(0.1)
    raise RuntimeError(f"gateway did not start on {host}:{port}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", type=Path)
    ap.add_argument("--tag", required=True, help="unique label for this pair; used in job names")
    ap.add_argument("--rusty-binary", type=Path, required=True)
    ap.add_argument("--bind", default="172.17.0.1", help="address the task containers can reach")
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--swap", action="store_true", help="rusty on key 2, mini on key 1")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--calls", type=int, default=250)
    ap.add_argument("--wall", type=int, default=7200, help="gateway wall-clock seconds per trial")
    ap.add_argument("--rusty-tokens", type=int, default=12_500_000)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--mswea-version", default="2.4.6")
    ap.add_argument("--mswea-config", type=Path, default=ROOT / "configs" / "mswea-compact.yaml")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit; no gateway, no paid calls")
    args = ap.parse_args()
    task = args.task.resolve()
    out = ROOT / "runs" / "paired" / args.tag
    if out.exists() and not args.dry_run:
        ap.error(f"{out} exists; tags must be unique")
    slots = key_slots(args.swap)
    rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    pins = {"fsb_rev": rev, "task": task.name, "model": args.model,
            "rusty_binary_sha256": sha256(args.rusty_binary) if args.rusty_binary.is_file() else None,
            "mswea_version": args.mswea_version, "mswea_config_sha256": sha256(args.mswea_config),
            "envelope": {"calls": args.calls, "wall_seconds": args.wall}, "key_slots": slots}
    jobs = {t: f"{t}-{args.tag}" for t in TRACKS}
    if args.dry_run:
        print(json.dumps({"pins": pins, "jobs": jobs,
                          "commands": {t: harbor_command(t, task, jobs[t], out / "jobs", args) for t in TRACKS}},
                         indent=2))
        return 0
    env = load_env()
    keys = {1: env.get("NVIDIA_API_KEY"), 2: env.get("NVIDIA_API_KEY_2")}
    if not all(keys.values()):
        ap.error("both NVIDIA_API_KEY and NVIDIA_API_KEY_2 must be set in .env")
    out.mkdir(parents=True)
    procs, sessions = {}, {}
    for i, track in enumerate(TRACKS):
        proxy = BudgetProxy([keys[slots[track]]])
        sessions[track] = proxy.register(jobs[track], args.model, out / f"gateway-{track}.json",
                                         Envelope(calls=args.calls, wall_seconds=args.wall))
        port = args.port + i
        serve(proxy, args.bind, port)
        url = f"http://{args.bind}:{port}/v1"
        log = open(out / f"{track}.log", "w")
        procs[track] = (subprocess.Popen(harbor_command(track, task, jobs[track], out / "jobs", args), cwd=ROOT,
                                         env=track_env(track, sessions[track].token, url), stdout=log,
                                         stderr=subprocess.STDOUT), log)
    deadline = args.wall + 3600 + 1800  # agent window, verification and environment build
    tracks = {}
    for track, (proc, log) in procs.items():
        try:
            code = proc.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = None
        log.close()
        tracks[track] = {"job": jobs[track], "harbor_exit": code, **outcome(out / "jobs" / jobs[track]),
                         "gateway": gateway_summary(sessions[track].receipt)}
    receipt = {"pins": pins, "tracks": tracks, "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({t: {"reward": v.get("reward"), "exception": v.get("exception")} for t, v in tracks.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
