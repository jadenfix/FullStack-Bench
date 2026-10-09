"""Run a task's gates under Harbor: the oracle must score 1; nop and every wrong solution must score 0.

    uv run python scripts/gate_task.py tasks/ship-checkout-v2 [--oracle-runs N] [--parallel N]

Wrong solutions live in <task>/wrong_solutions/*.sh. Each one runs as the oracle of a temporary
copy of the task. Job names are unique per invocation, so Harbor never reuses an unfinished job.
Exit code 0 only if every gate holds.
For qualification, explicitly select the required repetitions, pinned outcome count and
independent complete solution. This command does not perform egress, leak or human-review gates.
"""

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARBOR = ROOT / ".venv" / "bin" / "harbor"
sys.path.insert(0, str(ROOT))
from fsbench.gates import assess_gate  # noqa: E402 -- bootstrap imports for standalone execution


def prepare_independent_task(task: Path, destination: Path, solution: Path) -> Path:
    """A standalone independent replay must not receive the reference assets."""
    def ignore(directory, names):
        return [name for name in names if name in {'solution', 'wrong_solutions'}] if Path(directory) == task else []

    shutil.copytree(task, destination, ignore=ignore)
    (destination / 'solution').mkdir()
    shutil.copy(solution, destination / 'solution' / 'solve.sh')
    return destination


def check_intended_failures(result: dict, required: set[str]) -> dict:
    """A shortcut must fail its named invariant, not merely earn zero overall."""
    if not result.get("ok") or not required:
        return result
    rejected = {name.rsplit("::", 1)[-1].split("[", 1)[0] for name in result.get("failed", [])}
    missing = required - rejected
    if missing:
        return {**result, "ok": False, "status": "control_failure",
                "reason": f"shortcut did not fail its intended probes: {sorted(missing)}"}
    return result


def run(task: Path, agent: str, job: str, jobs_dir: Path, expected: float,
        expected_test_count: int | None = None, wall_timeout_sec: float | None = None) -> dict:
    config = tomllib.loads((task / "task.toml").read_text())
    timeout = wall_timeout_sec or (config.get("agent", {}).get("timeout_sec", 18000)
        + config.get("verifier", {}).get("timeout_sec", 1800)
        + sum(c.get("timeout_sec", 900) for c in config.get("verifier", {}).get("collect", []))
        + config.get("environment", {}).get("build_timeout_sec", 1800) + 1800)
    cmd = [str(HARBOR), "run", "-p", str(task), "-a", agent, "--job-name", job, "-o", str(jobs_dir), "-n", "1",
           "-y", "-q"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"job": job, "agent": agent, "exit": None, "status": "invalid_run", "ok": False,
                "reward": None, "reason": "outer runner deadline expired; reconcile preserved trial before retrying"}
    except OSError as error:
        return {"job": job, "agent": agent, "exit": None, "status": "invalid_run", "ok": False,
                "reward": None, "reason": f"Harbor could not start ({type(error).__name__})"}
    return {"job": job, "agent": agent, "exit": proc.returncode,
            **assess_gate(jobs_dir / job, exit_code=proc.returncode, expected=expected,
                          expected_test_count=expected_test_count,
                          expected_verifier_mode=config.get("verifier", {}).get("environment_mode"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", type=Path)
    ap.add_argument("--oracle-runs", type=int, default=1)
    ap.add_argument("--nop-runs", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--expected-test-count", type=int, help="pinned number of outcome checks")
    ap.add_argument("--wall-timeout-sec", type=float, help="outer deadline; otherwise derived from task timeouts")
    ap.add_argument("--independent-solution", type=Path, help="independently prepared complete solve.sh")
    ap.add_argument("--jobs-dir", type=Path, default=ROOT / "jobs")
    ap.add_argument("--wrong", action="append", help="only these wrong solutions (stem; repeatable)")
    ap.add_argument("--no-nop", action="store_true")
    ap.add_argument("--no-wrong", action="store_true", help="skip the wrong solutions (e.g. oracle first)")
    args = ap.parse_args()
    if min(args.oracle_runs, args.nop_runs, args.parallel) < 1 or (args.expected_test_count is not None and args.expected_test_count < 1) or (args.wall_timeout_sec is not None and args.wall_timeout_sec <= 0):
        ap.error("run counts, parallelism and timeout must be positive")
    task = args.task.resolve()
    if args.independent_solution and not args.independent_solution.is_file():
        ap.error("independent solution must be a saved complete solve.sh")
    available_wrong = {p.stem for p in (task / "wrong_solutions").glob("*.sh")}
    if args.wrong and set(args.wrong) - available_wrong:
        ap.error("a selected wrong solution does not exist in this task")
    # Curated candidates must reject each shortcut at its intended probe. Merely
    # failing an unrelated IAM, build or infrastructure check is not evidence.
    required_failures = {}
    control_map = task / "tests/negative_control_map.json"
    if control_map.exists():
        try:
            controls = json.loads(control_map.read_text())
            for control in controls.values():
                path = Path(control["path"])
                if path.parts[:1] != ("wrong_solutions",) or len(path.parts) != 2 or path.suffix != ".sh":
                    raise ValueError("control path must name a wrong_solutions shell script")
                if path.stem not in available_wrong or not control["test_name"].startswith("test_"):
                    raise ValueError("control solution or rejecting probe missing")
                required_failures.setdefault(path.stem, set()).add(control["test_name"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            ap.error(f"invalid negative-control map: {error}")
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]
    name = task.name
    tmp = Path(tempfile.mkdtemp(prefix=f"gates-{name}-"))
    plans = [("oracle", task, f"gate-{name}-oracle{i}-{stamp}", 1.0) for i in range(args.oracle_runs)]
    if not args.no_nop:
        plans.extend(("nop", task, f"gate-{name}-nop{i}-{stamp}", 0.0) for i in range(args.nop_runs))
    if args.independent_solution:
        independent = tmp / f"{name}-independent"
        prepare_independent_task(task, independent, args.independent_solution)
        plans.append(("independent", independent, f"gate-{name}-independent-{stamp}", 1.0))
    for wrong in sorted((task / "wrong_solutions").glob("*.sh")):
        if args.no_wrong or (args.wrong is not None and wrong.stem not in args.wrong):
            continue
        copy = tmp / f"{name}-{wrong.stem}"
        shutil.copytree(task, copy, ignore=shutil.ignore_patterns("wrong_solutions"))
        shutil.copy(wrong, copy / "solution" / "solve.sh")
        plans.append((f"wrong:{wrong.stem}", copy, f"gate-{name}-{wrong.stem}-{stamp}", 0.0))
    results = []
    with concurrent.futures.ThreadPoolExecutor(args.parallel) as pool:
        futures = {pool.submit(run, path, "nop" if label == "nop" else "oracle", job, args.jobs_dir, want,
                              args.expected_test_count, args.wall_timeout_sec): (label, want)
                   for label, path, job, want in plans}
        for f in concurrent.futures.as_completed(futures):
            label, want = futures[f]
            res = {**f.result(), "gate": label, "want": want}
            if label.startswith("wrong:"):
                res = check_intended_failures(res, required_failures.get(label.removeprefix("wrong:"), set()))
            results.append(res)
            print(json.dumps(res), flush=True)
            args.jobs_dir.mkdir(parents=True, exist_ok=True)
            (args.jobs_dir / f"gate-receipt-{name}-{stamp}.json").write_text(json.dumps(results, indent=2) + "\n")
    shutil.rmtree(tmp, ignore_errors=True)
    ok = all(r["ok"] for r in results)
    print(f"{'PASS' if ok else 'FAIL'}: {sum(r['ok'] for r in results)}/{len(results)} gates hold")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
