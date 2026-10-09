"""Summarise a run ledger against its plan, without overstating it.

    uv run python -m fsbench.analysis PLAN LEDGER [--manifest MANIFEST] [--json OUT]

Rules this module keeps:
- Every planned episode appears. An episode with no final attempt (replacements exhausted, or not
  run yet) is `missing`, never dropped; a coverage limitation is reported beside the result and
  counted as a failure in the full-benchmark rate, and excluded only in the separately labelled
  covered rate.
- Three completion events stay apart: raw completion proposals, completions the runtime accepted,
  and the verifier's independent outcome. An accepted completion with a failing outcome is a
  false completion; fewer of them is not better engineering unless outcomes also improve.
- Reliability: pass@k (some seed of a task succeeds) and pass^k (every seed succeeds) per task.
  Selecting the passing attempt with the hidden grader is not a deployable agent and is never
  reported as one.
- Comparisons are the manifest's declared ones, paired by (task, seed). Uncertainty resamples
  clusters (the task's lineage template when the manifest gives it, otherwise the task), so
  repeated seeds of one task are not counted as independent problems. Preregistered primary
  comparisons are labelled apart from exploratory ones, and the cluster count is printed beside
  every interval.
- Cost sits beside success: admitted calls and provider-reported tokens from the gateway, per
  track over final attempts, and for the whole cohort including replaced attempts.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from .runner import REPLACEABLE


def final_attempts(ledger: list[dict]) -> dict[str, dict]:
    """The last attempt per episode, if it is final (not one awaiting replacement)."""
    last: dict[str, dict] = {}
    for r in ledger:
        if r.get("kind") == "attempt":
            last[r["episode"]] = r
    return {e: r for e, r in last.items() if r["status"] not in REPLACEABLE}


def outcome(r: dict | None) -> str:
    if r is None:
        return "missing"
    if r["status"] == "scored":
        return "success" if (r.get("rewards") or {}).get("reward") == 1.0 else "failure"
    return r["status"]


def summarise(plan: dict, ledger: list[dict], manifest: dict | None = None, *, resamples: int = 2000,
              seed: int = 0) -> dict:
    finals = final_attempts(ledger)
    episodes = plan["episodes"]
    rows = [{**e, "outcome": outcome(finals.get(e["episode"])), "attempt": finals.get(e["episode"])} for e in episodes]
    tracks = sorted({e["track"] for e in episodes})
    lineage = {t["name"]: (t.get("lineage") or {}).get("template") or t["name"] for t in (manifest or {}).get("tasks", [])}
    out = {"tracks": {}, "comparisons": [], "attempts_total": sum(r.get("kind") == "attempt" for r in ledger),
           "replaced": sum(r.get("kind") == "attempt" and r["status"] in REPLACEABLE for r in ledger),
           # The cohort's spend, replaced attempts included; per-track cost counts final attempts only.
           "admitted_calls_all_attempts": sum((r.get("gateway") or {}).get("admitted_calls", 0)
                                              for r in ledger if r.get("kind") == "attempt")}
    for track in tracks:
        mine = [r for r in rows if r["track"] == track]
        counts = {k: sum(r["outcome"] == k for r in mine) for k in sorted({r["outcome"] for r in mine})}
        covered = [r for r in mine if r["outcome"] in ("success", "failure")]
        meta = [(r["attempt"] or {}).get("agent_metadata") or {} for r in covered]
        accepted = [m.get("completion_accepted") is True for m in meta]
        succeeded = [r["outcome"] == "success" for r in covered]
        by_task: dict[str, list[bool]] = {}
        for r in mine:
            by_task.setdefault(r["task"], []).append(r["outcome"] == "success")
        gateway = [(r["attempt"] or {}).get("gateway") or {} for r in mine if r["attempt"]]
        calls = sum(g.get("admitted_calls", 0) for g in gateway)
        out["tracks"][track] = {
            "planned": len(mine), "outcomes": counts,
            "success_rate_full": round(sum(r["outcome"] == "success" for r in mine) / len(mine), 4),
            "success_rate_covered": round(sum(succeeded) / len(covered), 4) if covered else None,
            "coverage_limitations": counts.get("coverage_limitation", 0), "missing": counts.get("missing", 0),
            "completion": {
                "proposals": sum(m.get("completion_proposals") or 0 for m in meta),
                "accepted": sum(accepted),
                "accepted_and_failed": sum(a and not s for a, s in zip(accepted, succeeded)),
                "not_accepted_but_succeeded": sum(s and not a for a, s in zip(accepted, succeeded)),
                "reported_by": sorted({m.get("completion_source") for m in meta if m.get("completion_source")}),
            },
            "pass_at_k": {t: any(v) for t, v in sorted(by_task.items())},
            "pass_hat_k": {t: all(v) for t, v in sorted(by_task.items())},
            "throttle_confounded": sum(bool((r["attempt"] or {}).get("throttle_confounded")) for r in mine),
            "admitted_calls": calls,
            "known_prompt_tokens": sum(g.get("known_prompt_tokens", 0) for g in gateway),
            "known_completion_tokens": sum(g.get("known_completion_tokens", 0) for g in gateway),
            "successes_per_100_calls": round(100 * sum(r["outcome"] == "success" for r in mine) / calls, 3)
            if calls else None,
        }
    rng = random.Random(seed)
    cell = {(r["track"], r["task"], r["seed"]): r["outcome"] for r in rows}
    for c in plan.get("comparisons", []):
        pairs = []
        for (track, task, s), o in cell.items():
            if track == c["treatment"] and (c["control"], task, s) in cell:
                pairs.append({"task": task, "cluster": lineage.get(task, task),
                              "diff": (o == "success") - (cell[(c["control"], task, s)] == "success"),
                              "complete": o not in ("missing",) and cell[(c["control"], task, s)] != "missing"})
        usable = [p for p in pairs if p["complete"]]
        clusters = sorted({p["cluster"] for p in usable})
        estimate = sum(p["diff"] for p in usable) / len(usable) if usable else None
        interval = None
        if len(clusters) >= 2:
            grouped = {k: [p["diff"] for p in usable if p["cluster"] == k] for k in clusters}
            draws = []
            for _ in range(resamples):
                pick = [x for k in rng.choices(clusters, k=len(clusters)) for x in grouped[k]]
                draws.append(sum(pick) / len(pick))
            draws.sort()
            interval = [round(draws[int(0.025 * resamples)], 4), round(draws[int(0.975 * resamples) - 1], 4)]
        out["comparisons"].append({
            "name": c["name"], "kind": c["kind"], "status": "primary" if c.get("primary") else "exploratory",
            "treatment": c["treatment"], "control": c["control"], "pairs": len(usable),
            "incomplete_pairs": len(pairs) - len(usable), "clusters": len(clusters),
            "success_difference": round(estimate, 4) if estimate is not None else None,
            "cluster_bootstrap_95": interval,
            "note": None if len(clusters) >= 2 else "fewer than two clusters: no interval; not evidence of an effect",
        })
    return out


def render(s: dict) -> str:
    lines = ["| track | planned | success (full) | success (covered) | missing | coverage limits | "
             "accepted+failed | calls | successes/100 calls |", "|---|---|---|---|---|---|---|---|---|"]
    for name, t in s["tracks"].items():
        lines.append(f"| {name} | {t['planned']} | {t['success_rate_full']} | {t['success_rate_covered']} | "
                     f"{t['missing']} | {t['coverage_limitations']} | {t['completion']['accepted_and_failed']} | "
                     f"{t['admitted_calls']} | {t['successes_per_100_calls']} |")
    if s["comparisons"]:
        lines += ["", "| comparison | kind | status | pairs | clusters | difference | 95% cluster bootstrap |",
                  "|---|---|---|---|---|---|---|"]
        for c in s["comparisons"]:
            lines.append(f"| {c['name']} | {c['kind']} | {c['status']} | {c['pairs']} | {c['clusters']} | "
                         f"{c['success_difference']} | {c['cluster_bootstrap_95'] or c['note']} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan", type=Path)
    ap.add_argument("ledger", type=Path)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    ledger = [json.loads(line) for line in a.ledger.read_text().splitlines() if line.strip()]
    s = summarise(json.loads(a.plan.read_text()), ledger,
                  json.loads(a.manifest.read_text()) if a.manifest else None)
    if a.json:
        a.json.write_text(json.dumps(s, indent=2) + "\n")
    print(render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
