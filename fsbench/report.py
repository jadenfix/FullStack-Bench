"""Paper tables from terminal records, deterministically and with every caveat attached.

Input: one or more cohort runs, each a manifest (`manifest.json`) and its terminal records
(`attempts/*.json`) as `scripts/paired_screen.py` writes them. Output: per-harness counts over
eligible attempts (SafeSuccess, the four evaluation views, measurement eligibility, the three
completion events, failure classes, harm, budget) and a paired comparison between two harnesses
on the (task, seed) slots where both produced eligible evidence.

What this module refuses to do: it never averages development and reporting cohorts, never
counts an attempt that admission rejected, never turns a null view into a 0, and never reports
a proportion without its denominator and a Wilson interval. The paired test is an exact sign
test on discordant slots; with the pilot's handful of tasks it is descriptive, and the claim
label says so. Nothing here calls a model."""

from __future__ import annotations

import json
import math
from pathlib import Path

from fsbench import admission

VIEWS = ("final_artifact", "deployed_at_handoff", "whole_episode", "recovery")
ELIGIBILITY = ("observation_complete", "post_handoff_observed", "challenges_ran", "audit_chain_intact")


# ---- statistics -----------------------------------------------------------------------------


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion; None when there is nothing to estimate."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def sign_test(wins: int, losses: int) -> float | None:
    """Exact two-sided sign test on discordant pairs; None without any discordance."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def proportion(successes: int, n: int) -> dict:
    return {"count": successes, "n": n, "rate": round(successes / n, 4) if n else None, "ci95": wilson(successes, n)}


# ---- loading --------------------------------------------------------------------------------


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    records = [json.loads(p.read_text()) for p in sorted((run_dir / "attempts").glob("*.json"))]
    return manifest, records


def load_ledger(ledger: Path, plan: Path, manifest: Path) -> list[tuple[dict, list[dict]]]:
    """Runs from the experiment runner's `ledger.jsonl`: one (manifest, records) pair per task.

    The ledger's attempt lines must carry the admission terminal record under `admission`
    (the output of `admission.classify_attempt` for that attempt); an attempt line without it is
    invalid evidence, never a scored result. The last attempt per episode is the one reported;
    replaced attempts stay in the ledger and are not counted. The plan's episodes are the planned
    attempts, so an episode that never ran is `missing`."""
    lines = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    plan_data, m = json.loads(plan.read_text()), json.loads(manifest.read_text())
    harnesses, attempts = admission.from_tracks(m.get("tracks", []), plan_data.get("episodes", []))
    runs_header = next((r for r in lines if r.get("kind") == "run"), {})
    last: dict[str, dict] = {}
    for r in lines:
        if r.get("kind") == "attempt":
            last[r["episode"]] = r
    out = []
    for task in sorted({a["task"] for a in attempts if a.get("task")}):
        planned = [a for a in attempts if a.get("task") == task]
        records = []
        for a in planned:
            line = last.get(a["id"])
            if line is None:
                continue
            rec = line.get("admission")
            if not isinstance(rec, dict):
                rec = {"attempt": a["id"], "harness": a["harness"], "seed": a.get("seed"),
                       "status": "invalid_evidence", "reason": "ledger line carries no admission record"}
            records.append({**rec, "attempt": a["id"]})
        task_meta = next((t for t in m.get("tasks", []) if t.get("name") == task), {})
        synthetic = {"cohort": m.get("cohort_role") or m.get("cohort"), "fsb_rev": runs_header.get("fsb_revision"),
                     "task": {"name": task, "digest": task_meta.get("checksum")},
                     "harnesses": harnesses, "attempts": planned,
                     "admission": m.get("admission", {"admitted": False})}
        out.append((synthetic, records))
    return out


# ---- aggregation ----------------------------------------------------------------------------


def _slot_rows(manifest: dict, records: list[dict]) -> list[dict]:
    """One row per record after `admission.summarize` has rejected duplicates and unplanned slots."""
    summary = admission.summarize(manifest, records)
    planned = {a["id"]: a for a in manifest.get("attempts", [])}
    task = manifest.get("task", {}).get("name") or manifest.get("task", {}).get("digest", "?")[:12]
    rows = []
    for r in summary["records"]:
        a = planned.get(r.get("attempt"), {})
        rows.append({**r, "task": task, "harness": a.get("harness", r.get("harness")),
                     "seed": a.get("seed", r.get("seed")), "cohort": manifest.get("cohort"),
                     "admitted": bool(manifest.get("admission", {}).get("admitted"))})
    recorded = {r.get("attempt") for r in summary["records"]}
    for aid, a in planned.items():  # a planned attempt with no record is visible, never silently absent
        if aid not in recorded:
            rows.append({"attempt": aid, "status": "missing", "reason": "no terminal record", "task": task,
                         "harness": a.get("harness"), "seed": a.get("seed"), "cohort": manifest.get("cohort")})
    return rows


def _view_counts(rows: list[dict]) -> dict:
    out = {}
    for view in VIEWS:
        key = f"view_{view}"
        known = [r for r in rows if key in (r.get("views") or {})]
        out[view] = {**proportion(sum(1 for r in known if r["views"][key] == 1.0), len(known)),
                     "unknown": len(rows) - len(known)}
    return out


def _harness_table(rows: list[dict]) -> dict:
    eligible = [r for r in rows if str(r.get("status", "")).startswith("eligible")]
    success = [r for r in eligible if r["status"] == "eligible_success"]
    measured = [r for r in eligible if (r.get("views") or {}).get("measurement_eligible") == 1.0]
    safe_measured = [r for r in success if r in measured]
    accepted = [r for r in eligible if (r.get("completion") or {}).get("accepted")]
    budgets = [r.get("budget") or {} for r in eligible]

    def budget_sum(key):
        values = [b.get(key) for b in budgets if isinstance(b.get(key), (int, float))]
        return {"total": sum(values), "n": len(values)} if values else None

    return {
        "attempts": {"planned": len(rows), "eligible": len(eligible),
                     "infrastructure_failure": sum(1 for r in rows if r["status"] == "infrastructure_failure"),
                     "invalid_evidence": sum(1 for r in rows if r["status"] == "invalid_evidence"),
                     "missing": sum(1 for r in rows if r["status"] == "missing")},
        "safe_success": proportion(len(success), len(eligible)),
        "measurement_eligible": proportion(len(measured), len(eligible)),
        "safe_success_among_measured": proportion(len(safe_measured), len(measured)),
        "views": _view_counts(eligible),
        "eligibility": {flag: proportion(sum(1 for r in eligible if (r.get("views") or {}).get(f"eligibility_{flag}") == 1.0),
                                         sum(1 for r in eligible if f"eligibility_{flag}" in (r.get("views") or {})))
                        for flag in ELIGIBILITY},
        "harm": {"observed_harm": sum(1 for r in rows if (r.get("harm") or {}).get("observed") is True),
                 "unobserved": sum(1 for r in rows if (r.get("harm") or {}).get("observed") is None),
                 "harm_free_success": sum(1 for r in success if (r.get("harm") or {}).get("observed") is False)},
        "completion": {
            "proposed_total": sum((r.get("completion") or {}).get("proposed") or 0 for r in eligible),
            "accepted": proportion(len(accepted), len(eligible)),
            "accepted_and_independent": proportion(sum(1 for r in accepted if r["status"] == "eligible_success"), len(accepted)),
            "accepted_without_success": sum(1 for r in accepted if r["status"] != "eligible_success"),
            "success_without_acceptance": sum(1 for r in success if r not in accepted),
            "public_check": {"passed": sum(1 for r in eligible if r.get("public_check_passed") is True),
                             "failed": sum(1 for r in eligible if r.get("public_check_passed") is False),
                             "not_run": sum(1 for r in eligible if r.get("public_check_passed") is None)}},
        "failure_classes": {c: sum(1 for r in rows if r.get("failure_class") == c) for c in admission.FAILURE_CLASSES},
        "coverage_limited": sum(1 for r in rows if (r.get("coverage") or {}).get("restricted")),
        "budget": {"calls": budget_sum("admitted_calls"), "input_tokens": budget_sum("input_charged"),
                   "output_tokens": budget_sum("output_charged"),
                   "exhausted": sum(1 for b in budgets if b.get("exhausted"))},
    }


def _paired(rows: list[dict], left: str, right: str) -> dict:
    """Discordance between two harnesses on the (task, seed) slots where both are eligible."""
    by_slot: dict[tuple, dict[str, dict]] = {}
    for r in rows:
        if str(r.get("status", "")).startswith("eligible"):
            by_slot.setdefault((r["task"], r.get("seed")), {})[r["harness"]] = r
    paired = [(s, h) for s, h in by_slot.items() if left in h and right in h]

    def outcome(r, key=None):
        if key is None:
            return r["status"] == "eligible_success"
        value = (r.get("views") or {}).get(key)
        return None if value is None else value == 1.0

    def table(key=None):
        wins = losses = both = neither = unknown = 0
        for _, h in paired:
            a, b = outcome(h[left], key), outcome(h[right], key)
            if a is None or b is None:
                unknown += 1
            elif a and not b:
                wins += 1
            elif b and not a:
                losses += 1
            elif a and b:
                both += 1
            else:
                neither += 1
        return {"left_only": wins, "right_only": losses, "both": both, "neither": neither, "unknown": unknown,
                "sign_test_p": sign_test(wins, losses)}

    tasks = sorted({s[0] for s, _ in paired})
    return {"left": left, "right": right, "paired_slots": len(paired), "tasks": tasks,
            "safe_success": table(), "views": {v: table(f"view_{v}") for v in VIEWS},
            "note": ("paired slots span " + str(len(tasks)) + " task(s); the sign test treats slots as exchangeable, "
                     "which seeds within one task are not, so read it as descriptive")}


def aggregate(runs: list[tuple[dict, list[dict]]], *, pair: tuple[str, str] | None = None) -> dict:
    """Tables over several runs that share a cohort. Mixed cohorts are refused: a development
    pilot and a reporting cohort are different experiments."""
    cohorts = {m.get("cohort") for m, _ in runs}
    if len(cohorts) != 1:
        raise ValueError(f"runs span cohorts {sorted(c or '?' for c in cohorts)}; report each cohort on its own")
    cohort = cohorts.pop()
    rows = [row for m, rs in runs for row in _slot_rows(m, rs)]
    harnesses = sorted({r["harness"] for r in rows if r.get("harness")})
    admitted = all(m.get("admission", {}).get("admitted") for m, _ in runs)
    reportable = admitted and cohort == "reporting"
    limited = sum(1 for r in rows if (r.get("coverage") or {}).get("restricted"))
    out = {
        "cohort": cohort, "runs": len(runs), "tasks": sorted({r["task"] for r in rows}),
        "fsb_revs": sorted({m.get("fsb_rev") for m, _ in runs if m.get("fsb_rev")}),
        "claim_label": ("executed" if reportable else
                        f"exploratory ({cohort} cohort; not admitted)" if not admitted else
                        f"exploratory ({cohort} cohort; never reportable)"),
        "reportable": reportable, "full_benchmark_claim": limited == 0,
        "harnesses": {h: _harness_table([r for r in rows if r["harness"] == h]) for h in harnesses},
        "by_task": {t: {h: _harness_table([r for r in rows if r["task"] == t and r["harness"] == h]) for h in harnesses}
                    for t in sorted({r["task"] for r in rows})},
        "records": len(rows),
    }
    if pair:
        if any(h not in harnesses for h in pair):
            raise ValueError(f"pair {pair} names a harness with no records (have {harnesses})")
        out["paired"] = _paired(rows, *pair)
    return out


# ---- rendering ------------------------------------------------------------------------------


def _pct(p: dict) -> str:
    if p["n"] == 0:
        return "n/a (0)"
    lo, hi = p["ci95"]
    return f"{p['count']}/{p['n']} = {p['rate']:.2f} [{lo:.2f}, {hi:.2f}]"


def render_markdown(report: dict) -> str:
    lines = [f"# Cohort report: {report['cohort']}", "",
             f"Claim label: **{report['claim_label']}**. Tasks: {', '.join(report['tasks']) or 'none'}. "
             f"FullStack-Bench revisions: {', '.join(report['fsb_revs']) or 'unknown'}. "
             f"Records: {report['records']}."]
    if not report["full_benchmark_claim"]:
        lines.append("Some attempts ran with a harness that declared an interface unsupported; "
                     "the comparison covers the compatible subset only.")
    lines += ["", "Proportions are over eligible attempts with a 95% Wilson interval; a view with a check "
              "that did not run is unknown, not failed.", "", "## Per harness", "",
              "| Harness | Eligible / planned | SafeSuccess | Measurement eligible | SafeSuccess among measured | "
              "Accepted → independent | Accepted w/o success | Harm observed | Infra / invalid / missing |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, t in report["harnesses"].items():
        a = t["attempts"]
        lines.append(f"| {name} | {a['eligible']} / {a['planned']} | {_pct(t['safe_success'])} | "
                     f"{_pct(t['measurement_eligible'])} | {_pct(t['safe_success_among_measured'])} | "
                     f"{_pct(t['completion']['accepted_and_independent'])} | "
                     f"{t['completion']['accepted_without_success']} | {t['harm']['observed_harm']} "
                     f"(unobserved {t['harm']['unobserved']}) | {a['infrastructure_failure']} / {a['invalid_evidence']} / "
                     f"{a['missing']} |")
    lines += ["", "## Four views (eligible attempts; unknown = a check did not run)", "",
              "| Harness | " + " | ".join(VIEWS) + " |", "|---|" + "---|" * len(VIEWS)]
    for name, t in report["harnesses"].items():
        cells = [f"{_pct(t['views'][v])} (unknown {t['views'][v]['unknown']})" for v in VIEWS]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## Failure classes and budget", "",
              "| Harness | " + " | ".join(admission.FAILURE_CLASSES) + " | Coverage limited | Budget exhausted | Calls | Input tokens | Output tokens |",
              "|---|" + "---|" * (len(admission.FAILURE_CLASSES) + 5)]
    for name, t in report["harnesses"].items():
        fc = " | ".join(str(t["failure_classes"][c]) for c in admission.FAILURE_CLASSES)
        b = t["budget"]

        def tot(x):
            return str(x["total"]) if x else "n/a"
        lines.append(f"| {name} | {fc} | {t['coverage_limited']} | {b['exhausted']} | {tot(b['calls'])} | "
                     f"{tot(b['input_tokens'])} | {tot(b['output_tokens'])} |")
    if "paired" in report:
        p = report["paired"]
        lines += ["", f"## Paired: {p['left']} vs {p['right']} on {p['paired_slots']} shared (task, seed) slots", "",
                  "| Outcome | " + f"{p['left']} only | {p['right']} only | both | neither | unknown | sign test p |",
                  "|---|---|---|---|---|---|---|"]
        for label, t in [("SafeSuccess", p["safe_success"])] + [(v, p["views"][v]) for v in VIEWS]:
            pv = "n/a" if t["sign_test_p"] is None else f"{t['sign_test_p']:.3f}"
            lines.append(f"| {label} | {t['left_only']} | {t['right_only']} | {t['both']} | {t['neither']} | "
                         f"{t['unknown']} | {pv} |")
        lines += ["", p["note"] + "."]
    return "\n".join(lines) + "\n"
