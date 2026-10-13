"""The failure-mechanism table (section 6.4) and the artifact-versus-SafeSuccess gap list (6.2),
derived from the records: for every eligible solver failure, the earliest failed lifecycle stage
from the verifier's per-check output, the invariants violated at that stage, whether an incident
was attributed to the agent, and how the attempt ended.

    uv run python scripts/failure_mechanisms.py docs/results/<cohort>/records --tasks tasks --receipts receipts \\
        --out docs/results/<cohort>

Inputs: the published records (ledger, plan, manifest, `evidence/<job>/<trial>/verifier/ctrf.json`),
each task's `tests/views.json` (check to view) and `task.toml` (the lineage family and causal
defect). Output: `mechanisms.json` and `mechanisms.md`. Nothing is read from an agent trace; the
verifier's output and the terminal records are the only inputs."""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fsbench import report  # noqa: E402

STAGES = ("final_artifact", "deployed_at_handoff", "whole_episode", "recovery")  # lifecycle order
INCIDENT_CHECK = "test_no_incidents_caused"


def failed_checks(records: Path, row: dict) -> list[str] | None:
    """Check names the verifier failed for this attempt, from its CTRF output; None without one."""
    job, trial = Path(row.get("job_dir") or "").name, row.get("trial")
    if not job or not trial:
        return None
    path = records / "evidence" / job / trial / "verifier" / "ctrf.json"
    if not path.is_file():
        return None
    try:
        tests = json.loads(path.read_text())["results"]["tests"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return sorted({t["name"].split("::")[-1] for t in tests if t.get("status") == "failed"})


def task_maps(tasks: Path, names: list[str]) -> dict[str, dict]:
    out = {}
    for name in names:
        views = json.loads((tasks / name / "tests" / "views.json").read_text())
        meta = tomllib.loads((tasks / name / "task.toml").read_text()).get("metadata", {}).get("lineage", {})
        out[name] = {"stage_of": {check: stage for stage, checks in views.items() for check in checks},
                     "family": meta.get("family"), "causal_defect": meta.get("causal_defect")}
    return out


def classify(row: dict, checks: list[str] | None, stage_of: dict[str, str]) -> dict:
    """The earliest failed stage, by the verifier's checks when present and by the recorded views
    otherwise, with the invariants violated at that stage."""
    views = row.get("views") or {}
    failed_stages = [s for s in STAGES if views.get(f"view_{s}") == 0.0]
    by_stage = {s: sorted(c for c in (checks or []) if stage_of.get(c) == s) for s in STAGES}
    earliest = (next((s for s in STAGES if by_stage[s]), None) or (failed_stages[0] if failed_stages else None)
                or "none (budget or stop)")  # every view passed: the envelope or the harness ended it
    reason, limit = report.termination(row)
    return {"earliest_stage": earliest, "invariants": by_stage.get(earliest, []),
            "failed_stages": failed_stages, "checks_from": "verifier" if checks is not None else "views",
            "incident": bool(checks and INCIDENT_CHECK in checks) or (views.get("view_whole_episode") == 0.0 and checks is None),
            "termination": reason if reason != "budget_exhausted" else f"budget_exhausted:{limit}"}


def build(records: Path, tasks: Path, receipts: Path | None) -> dict:
    runs = report.load_ledger(records / "ledger.jsonl", records / "plan.json", records / "manifest.json",
                              records=records, receipts=receipts)
    rows = [{**r, "task": m["task"]["name"]} for m, rs in runs for r in rs]  # a run is one task
    maps = task_maps(tasks, sorted({r["task"] for r in rows}))
    failures, gap = [], []
    for r in sorted(rows, key=lambda r: (r.get("task") or "", r.get("harness") or "", r.get("seed") or 0)):
        if r.get("status") != "eligible_solver_failure":  # the admission verdict, not a view: an
            continue  # exhausted budget beside a passing artifact is a failure under the envelope rule
        views = r.get("views") or {}
        entry = {"task": r["task"], "harness": r["harness"], "seed": r.get("seed"),
                 **classify(r, failed_checks(records, r), maps[r["task"]]["stage_of"])}
        failures.append(entry)
        if views.get("view_final_artifact") == 1.0:
            gap.append(entry)
    by_task = {}
    for name, m in maps.items():
        mine = [f for f in failures if f["task"] == name]
        by_task[name] = {"family": m["family"], "causal_defect": m["causal_defect"], "failures": len(mine),
                         "earliest_stage": dict(Counter(f["earliest_stage"] for f in mine)),
                         "first_invariants": dict(Counter(c for f in mine for c in f["invariants"])),
                         "termination": dict(Counter(f["termination"] for f in mine)),
                         "incidents": sum(f["incident"] for f in mine),
                         "by_harness": {h: dict(Counter(f["earliest_stage"] for f in mine if f["harness"] == h))
                                        for h in sorted({f["harness"] for f in mine})}}
    return {"failures": failures, "by_task": by_task,
            "gap": {"count": len(gap), "by_harness": dict(Counter(g["harness"] for g in gap)),
                    "by_task": dict(Counter(g["task"] for g in gap)),
                    "by_stage": dict(Counter(g["earliest_stage"] for g in gap)), "attempts": gap},
            "checks_from_verifier": sum(f["checks_from"] == "verifier" for f in failures),
            "n_failures": len(failures)}


def render(result: dict) -> str:
    lines = ["# Failure mechanisms (eligible solver failures)", "",
             f"Failures: {result['n_failures']}; with the verifier's per-check output: "
             f"{result['checks_from_verifier']}. Stages in lifecycle order: " + ", ".join(STAGES) + ".", "",
             "| Task | Family | Failures | Earliest failed stage | First violated invariants | Incidents | Termination |",
             "|---|---|---|---|---|---|---|"]
    fmt = lambda d: ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: (-kv[1], str(kv[0])))) or "none"  # noqa: E731
    for name, t in result["by_task"].items():
        lines.append(f"| {name} | {t['family']} | {t['failures']} | {fmt(t['earliest_stage'])} | "
                     f"{fmt(t['first_invariants'])} | {t['incidents']} | {fmt(t['termination'])} |")
    g = result["gap"]
    lines += ["", f"## Final artifact passed, SafeSuccess failed: {g['count']} attempts", "",
              f"By harness: {fmt(g['by_harness'])}. By task: {fmt(g['by_task'])}. By earliest failed stage: {fmt(g['by_stage'])}.",
              "", "| Harness | Task | Seed | Earliest failed stage | Violated invariants | Termination |",
              "|---|---|---|---|---|---|"]
    for a in g["attempts"]:
        lines.append(f"| {a['harness']} | {a['task']} | {a['seed']} | {a['earliest_stage']} | "
                     f"{', '.join(a['invariants']) or 'n/a (views only)'} | {a['termination']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("records", type=Path)
    ap.add_argument("--tasks", type=Path, default=Path("tasks"))
    ap.add_argument("--receipts", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    result = build(a.records, a.tasks, a.receipts)
    text = render(result)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "mechanisms.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        (a.out / "mechanisms.md").write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
