"""Run a task's gates under Harbor: the oracle must score 1; nop and every wrong solution must score 0.

    uv run python scripts/gate_task.py tasks/ship-checkout-v2 [--oracle-runs N] [--parallel N]

Wrong solutions live in <task>/wrong_solutions/*.sh. Each one runs as the oracle of a temporary
copy of the task. Job names are unique per invocation, so Harbor never reuses an unfinished job.
Exit code 0 only if every gate holds.
"""

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARBOR = ROOT / ".venv" / "bin" / "harbor"


def run(task: Path, agent: str, job: str, jobs_dir: Path) -> dict:
    cmd = [str(HARBOR), "run", "-p", str(task), "-a", agent, "--job-name", job, "-o", str(jobs_dir), "-n", "1",
           "-y", "-q"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    trials = [p for p in (jobs_dir / job).iterdir() if p.is_dir()] if (jobs_dir / job).exists() else []
    reward = None
    failed = []
    for t in trials:
        r = t / "verifier" / "reward.txt"
        if r.exists():
            reward = float(r.read_text().strip())
        out = t / "verifier" / "test-stdout.txt"
        if out.exists():
            failed = [l.split("::")[1].split(" ")[0] for l in out.read_text().splitlines()
                      if l.startswith("FAILED") and "::" in l]
    return {"job": job, "agent": agent, "exit": proc.returncode, "reward": reward, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", type=Path)
    ap.add_argument("--oracle-runs", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--jobs-dir", type=Path, default=ROOT / "jobs")
    ap.add_argument("--wrong", action="append", help="only these wrong solutions (stem; repeatable)")
    ap.add_argument("--no-nop", action="store_true")
    args = ap.parse_args()
    task = args.task.resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = task.name
    tmp = Path(tempfile.mkdtemp(prefix=f"gates-{name}-"))
    plans = [("oracle", task, f"gate-{name}-oracle{i}-{stamp}", 1.0) for i in range(args.oracle_runs)]
    if not args.no_nop:
        plans.append(("nop", task, f"gate-{name}-nop-{stamp}", 0.0))
    for wrong in sorted((task / "wrong_solutions").glob("*.sh")):
        if args.wrong is not None and wrong.stem not in args.wrong:
            continue
        copy = tmp / f"{name}-{wrong.stem}"
        shutil.copytree(task, copy, ignore=shutil.ignore_patterns("wrong_solutions"))
        shutil.copy(wrong, copy / "solution" / "solve.sh")
        plans.append((f"wrong:{wrong.stem}", copy, f"gate-{name}-{wrong.stem}-{stamp}", 0.0))
    results = []
    with concurrent.futures.ThreadPoolExecutor(args.parallel) as pool:
        futures = {pool.submit(run, path, "nop" if label == "nop" else "oracle", job, args.jobs_dir): (label, want)
                   for label, path, job, want in plans}
        for f in concurrent.futures.as_completed(futures):
            label, want = futures[f]
            res = {**f.result(), "gate": label, "want": want}
            res["ok"] = res["reward"] == want
            results.append(res)
            print(json.dumps(res), flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    ok = all(r["ok"] for r in results)
    print(f"{'PASS' if ok else 'FAIL'}: {sum(r['ok'] for r in results)}/{len(results)} gates hold")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
