"""One paired screen: Rusty and the pinned mini-SWE on the same task, model and envelope.

    uv run python scripts/paired_screen.py tasks/ship-checkout-v2 --tag p3 --cohort selection \
        --rusty-binary /path/to/rusty --bind 172.17.0.1 \
        --qualification-receipt jobs/gate-receipt-ship-checkout-v2-<stamp>.json \
        --isolation-receipt runs/boundary/ship-checkout-v2.json \
        --image simcloud=sha256:... --image verifier=sha256:... [--swap] [--port 8900]

Each track gets its own operator gateway (fsbench.budget_proxy) on its own NVIDIA key, because the
provider rate-limits per key and two agents on one key starve each other. --swap exchanges the keys,
so a cohort can alternate them. Keys come from .env (NVIDIA_API_KEY, NVIDIA_API_KEY_2) and never
leave the gateways; each trial gets a throwaway token. --bind is an address the task containers
can reach (the Docker bridge on Linux). Both tracks run at once with unique job names.

Before any gateway starts, the screen writes runs/paired/<tag>/manifest.json through
fsbench.admission: task digest, executed qualification and isolation receipts, image digests,
harness identities, model settings and budgets, and the cohort. A selection or reporting pair
whose evidence is missing or stale is not launched; a development pair runs but can never be
reported. Every launched attempt gets a terminal record in runs/paired/<tag>/attempts/<job>.json
(eligible success, eligible solver failure, infrastructure failure or invalid evidence), and
receipt.json carries the records and the cohort summary beside the raw per-track outcomes.
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
from fsbench import admission  # noqa: E402 -- bootstrap imports for standalone execution
from fsbench.budget_proxy import BudgetProxy, Envelope  # noqa: E402
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
        options = [f"binary={args.rusty_binary}", "agents=off", f"max_turns={args.max_turns}",
                   f"max_requests={args.calls}", f"max_budget_tokens={args.rusty_tokens}",
                   f"budget_secs={args.wall - 200}", f"execution={args.rusty_execution}", "memory=off"]
        if args.rusty_verify:
            # Harbor parses --ak values as JSON or literals, so the command is JSON-encoded to stay a string.
            options.append(f"verify={json.dumps(args.rusty_verify)}")
            options.append(f"verify_timeout={args.rusty_verify_timeout}")
        return [str(HARBOR), "run", "-p", str(task), "-a", "fsbench.agents.rusty:Rusty", "-m", args.model,
                *(x for o in options for x in ("--ak", o)), *common]
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


def rusty_version(binary: Path) -> str:
    """What the binary says it is; `unavailable` when it cannot run here, which blocks admission."""
    try:
        out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def required_tools(task: Path) -> list[str]:
    """The task's MCP servers, which every track must be able to reach to be comparable."""
    import tomllib

    try:
        config = tomllib.loads((task / "task.toml").read_text())
    except (OSError, ValueError):
        return []
    return sorted(f"mcp:{s['name']}" for s in config.get("environment", {}).get("mcp_servers", []) if "name" in s)


def manifest_for(args, task: Path, jobs: dict[str, str], pins: dict, rev: str) -> dict:
    envelope = Envelope(calls=args.calls, wall_seconds=args.wall)
    images = dict(item.split("=", 1) for item in args.image)
    harnesses = {
        "rusty": {"version": rusty_version(args.rusty_binary) if args.rusty_binary.is_file() else "unavailable",
                  "binary_sha256": pins["rusty_binary_sha256"],
                  "options": {"agents": "off", "memory": "off", "execution": args.rusty_execution,
                              "max_turns": args.max_turns, "max_requests": args.calls,
                              "max_budget_tokens": args.rusty_tokens, "budget_secs": args.wall - 200,
                              "verify": args.rusty_verify,
                              "verify_timeout": args.rusty_verify_timeout if args.rusty_verify else None}},
        "mini": {"version": args.mswea_version, "config_sha256": pins["mswea_config_sha256"],
                 "options": {"config_file": str(args.mswea_config)}},
    }
    model = {"name": args.model,
             "inference": {k: getattr(envelope, k) for k in ("temperature", "top_p", "reasoning_effort",
                                                             "clear_thinking", "max_reply")},
             "budgets": {"calls": args.calls, "wall_seconds": args.wall, "input_tokens": envelope.input_tokens,
                         "output_tokens": envelope.output_tokens},
             "required_tools": required_tools(task)}
    attempts = [{"id": jobs[t], "harness": t, "seed": args.seed} for t in TRACKS]
    runtime = {**admission.task_runtime(task), "key_slots": pins["key_slots"], "concurrent_tracks": len(TRACKS),
               "gateway_ports": {t: args.port + i for i, t in enumerate(TRACKS)}, "cache": "none declared"}
    return admission.build_manifest(task=task, cohort=args.cohort, fsb_rev=rev, model=model, harnesses=harnesses,
                                    attempts=attempts, qualification_receipt=args.qualification_receipt,
                                    isolation_receipt=args.isolation_receipt, images=images, runtime=runtime)


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
    ap.add_argument("--cohort", required=True, choices=admission.COHORTS,
                    help="development pairs run unadmitted; selection and reporting pairs need every receipt")
    ap.add_argument("--seed", type=int, default=1, help="repetition number within the cohort (not a new task)")
    ap.add_argument("--qualification-receipt", type=Path, action="append",
                    help="gate receipt written by scripts/gate_task.py; repeatable, all must bind to one task revision")
    ap.add_argument("--isolation-receipt", type=Path, help="executed execution-boundary receipt for this task")
    ap.add_argument("--image", action="append", default=[], metavar="ROLE=sha256:DIGEST",
                    help="pinned image digest per execution role (simcloud, verifier); repeatable")
    ap.add_argument("--rusty-execution", default="standard", choices=("standard", "careful", "vibe"),
                    help="Rusty execution mode; the 2x2 ablation varies this and --rusty-verify")
    ap.add_argument("--rusty-verify", help="public acceptance command Rusty enforces before accepting completion "
                                           "(passed as --ak verify=...; needs an adapter that supports it)")
    ap.add_argument("--rusty-verify-timeout", type=int, default=60, help="seconds the public check may take")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and admission verdict and exit; "
                                                            "no gateway, no paid calls, nothing written")
    args = ap.parse_args()
    task = args.task.resolve()
    out = ROOT / "runs" / "paired" / args.tag
    if out.exists() and not args.dry_run:
        ap.error(f"{out} exists; tags must be unique")
    if any("=" not in item for item in args.image):
        ap.error("--image takes ROLE=sha256:DIGEST")
    slots = key_slots(args.swap)
    rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    pins = {"fsb_rev": rev, "task": task.name, "model": args.model,
            "rusty_binary_sha256": sha256(args.rusty_binary) if args.rusty_binary.is_file() else None,
            "mswea_version": args.mswea_version, "mswea_config_sha256": sha256(args.mswea_config),
            "envelope": {"calls": args.calls, "wall_seconds": args.wall}, "key_slots": slots,
            "cohort": args.cohort, "seed": args.seed}
    jobs = {t: f"{t}-{args.tag}" for t in TRACKS}
    manifest = manifest_for(args, task, jobs, pins, rev)
    if args.dry_run:
        print(json.dumps({"pins": pins, "jobs": jobs, "admission": manifest["admission"],
                          "task_digest": manifest["task"]["digest"],
                          "commands": {t: harbor_command(t, task, jobs[t], out / "jobs", args) for t in TRACKS}},
                         indent=2))
        return 0 if manifest["admission"]["launchable"] else 2
    if not manifest["admission"]["launchable"]:
        print(json.dumps({"launched": False, "admission": manifest["admission"]}, indent=2))
        return 2
    env = load_env()
    keys = {1: env.get("NVIDIA_API_KEY"), 2: env.get("NVIDIA_API_KEY_2")}
    if not all(keys.values()):
        ap.error("both NVIDIA_API_KEY and NVIDIA_API_KEY_2 must be set in .env")
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "attempts").mkdir()
    procs, sessions, records = {}, {}, {}
    for i, track in enumerate(TRACKS):
        proxy = BudgetProxy([keys[slots[track]]])
        sessions[track] = proxy.register(jobs[track], args.model, out / f"gateway-{track}.json",
                                         Envelope(calls=args.calls, wall_seconds=args.wall))
        port = args.port + i
        serve(proxy, args.bind, port)
        url = f"http://{args.bind}:{port}/v1"
        log = open(out / f"{track}.log", "w")
        try:
            procs[track] = (subprocess.Popen(harbor_command(track, task, jobs[track], out / "jobs", args), cwd=ROOT,
                                             env=track_env(track, sessions[track].token, url), stdout=log,
                                             stderr=subprocess.STDOUT), log)
        except OSError as error:
            log.close()
            records[track] = terminal_record(out, manifest, track, jobs[track], None, sessions[track].receipt,
                                             failure=f"Harbor could not start ({type(error).__name__})")
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
        records[track] = terminal_record(out, manifest, track, jobs[track], code, sessions[track].receipt)
    summary = admission.summarize(manifest, [records[t] for t in TRACKS if t in records])
    receipt = {"pins": pins, "manifest": str(out / "manifest.json"), "admission": manifest["admission"],
               "tracks": tracks, "attempts": summary["records"], "summary": summary["tracks"],
               "reportable": summary["reportable"], "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({t: {"status": records[t]["status"], "reward": records[t].get("reward"),
                          "harm_observed": records[t]["harm"]["observed"], "reason": records[t]["reason"]}
                      for t in TRACKS if t in records}))
    return 0


def terminal_record(out: Path, manifest: dict, track: str, job: str, code: int | None, gateway: Path,
                    failure: str | None = None) -> dict:
    """One record per launched attempt, written as soon as that attempt ends, however it ended."""
    attempt = next(a for a in manifest["attempts"] if a["id"] == job)
    record = admission.classify_attempt(out / "jobs" / job, exit_code=code, manifest=manifest, attempt=attempt,
                                        gateway_receipt=gateway if gateway.exists() else None)
    if failure:
        record.update(status="invalid_evidence", reason=failure)
    (out / "attempts" / f"{job}.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    sys.exit(main())
