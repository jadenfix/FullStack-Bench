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
label says so. Nothing here calls a model.

Per-harness keys the paper's experiments section cites, all deterministic:

- `termination`: attempts by why they ended, read off the terminal record (`termination`):
  `handoff` (an eligible attempt with no exception: the agent ended its own episode),
  `budget_exhausted` (the gateway's `budget.exhausted`, counted by limit), `harness_stop` (one
  of admission's solver exceptions, or a nonzero agent exit status the ledger recorded, without
  an exhausted budget: a timeout, turn cap or the harness's own stop), `infrastructure`, else
  `unknown`.
- `active_at_call`: how many eligible attempts were still active at admitted call k, on a grid
  of every ACTIVE_GRID calls up to the envelope's call cap, from `budget.admitted_calls`.
- `trajectory`: aggregated from a per-attempt `trajectory_summary` on the terminal record, which
  `load_ledger` adds from `fsbench.trajectory.summary_for_trial` when an attempt's trial directory
  resolves (a published records directory, say); that module documents the schema and the
  tool-name taxonomy. Nothing parses a trace at attempt time, raw traces are never read here,
  an attempt without the summary counts under `missing`, and the key is null when no attempt
  carries one.
- `cost_estimate_usd`: charged tokens priced by the manifest's optional `pricing` block
  (`{"input_per_million", "output_per_million", "source"}`); null without one. No price is
  ever assumed, and the key carries the manifest's `source` string."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from fsbench import admission, isolation_gate, trajectory

VIEWS = ("final_artifact", "deployed_at_handoff", "whole_episode", "recovery")
ELIGIBILITY = ("observation_complete", "post_handoff_observed", "challenges_ran", "audit_chain_intact")
TERMINATIONS = ("handoff", "budget_exhausted", "harness_stop", "infrastructure", "unknown")
BUDGET_LIMITS = ("calls", "input_tokens", "output_tokens", "wall")
ACTIVE_GRID = 10


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


def benjamini_hochberg(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Benjamini-Hochberg adjusted p-values over one family of comparisons. A comparison whose
    p is None (no discordant pair) stays None and is not counted in the family size."""
    tested = sorted(((p, name) for name, p in pvalues.items() if p is not None), key=lambda x: x[0])
    m = len(tested)
    adjusted: dict[str, float | None] = {name: None for name in pvalues}
    running = 1.0
    for rank in range(m, 0, -1):  # from the largest p down, each adjusted value is the running minimum
        p, name = tested[rank - 1]
        running = min(running, p * m / rank)
        adjusted[name] = round(running, 4)
    return adjusted


def proportion(successes: int, n: int) -> dict:
    return {"count": successes, "n": n, "rate": round(successes / n, 4) if n else None, "ci95": wilson(successes, n)}


# ---- per-attempt derivations ----------------------------------------------------------------


def termination(record: dict) -> tuple[str, str | None]:
    """Why one attempt ended, from its terminal record: (reason, the exhausted limit or None)."""
    status = str(record.get("status", ""))
    if status == "infrastructure_failure":
        return "infrastructure", None
    exhausted = (record.get("budget") or {}).get("exhausted")
    if exhausted:
        return "budget_exhausted", exhausted if exhausted in BUDGET_LIMITS else "other"
    exception, exit_status = record.get("exception"), record.get("agent_exit_status")
    if exception in admission.SOLVER_EXCEPTIONS or (isinstance(exit_status, int) and exit_status != 0):
        return "harness_stop", None
    if status.startswith("eligible") and not exception:
        return "handoff", None
    return "unknown", None


# ---- loading --------------------------------------------------------------------------------


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    records = [json.loads(p.read_text()) for p in sorted((run_dir / "attempts").glob("*.json"))]
    return manifest, records


def load_ledger(ledger: Path, plan: Path, manifest: Path, *, rejudge: bool = False,
                records: Path | None = None, receipts: Path | None = None) -> list[tuple[dict, list[dict]]]:
    """Runs from the experiment runner's `ledger.jsonl`: one (manifest, records) pair per task.

    The ledger's attempt lines must carry the admission terminal record under `admission`
    (the output of `admission.classify_attempt` for that attempt); an attempt line without it is
    invalid evidence, never a scored result. The last attempt per episode is the one reported;
    replaced attempts stay in the ledger and are not counted. The plan's episodes are the planned
    attempts, so an episode that never ran is `missing`. With `rejudge`, embedded records are
    ignored and every line is classified again from its trial directory under the current rules,
    for a ledger written before an admission fix. `records` is a published copy of the run's
    evidence (`evidence/<job>/<trial>/`, `receipts/<job>.json`): the ledger's host paths are
    mapped onto it, so a re-judge can run anywhere the records are. `receipts` is the
    repository's `receipts/<task>/` directory: the experiment manifest carries no admission
    verdict of its own, so each task's is established here from its executed gate and isolation
    receipts against the manifest's checksum and image pins, as preflight did; without it, or
    with a receipt that fails, the cohort is not admitted and the report says so."""
    lines = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    if records is not None:
        lines = [_relocate(r, records) for r in lines]
    plan_data, m = json.loads(plan.read_text()), json.loads(manifest.read_text())
    harnesses, attempts = admission.from_tracks(m.get("tracks", []), plan_data.get("episodes", []))
    last: dict[str, dict] = {}
    for r in lines:
        if r.get("kind") == "attempt":
            last[r["episode"]] = r
    out = []
    for task in sorted({a["task"] for a in attempts if a.get("task")}):
        planned = [a for a in attempts if a.get("task") == task]
        task_meta = next((t for t in m.get("tasks", []) if t.get("name") == task), {})
        synthetic = {"cohort": m.get("cohort_role") or m.get("cohort"),
                     "task": {"name": task, "digest": task_meta.get("checksum")},
                     "harnesses": harnesses, "attempts": planned, "envelope": m.get("envelope"),
                     "pricing": m.get("pricing"),
                     "admission": m.get("admission") or _receipt_admission(receipts, task, task_meta)}
        records, revs = [], set()
        for a in planned:
            line = last.get(a["id"])
            if line is None:
                continue
            revs.add(_revision(line.get("fsb_revision")))
            records.append({**_ledger_record(line, synthetic, a, rejudge=rejudge), **_analysis_fields(line),
                            "attempt": a["id"]})
        synthetic["fsb_rev"] = ", ".join(sorted(r for r in revs if r)) or None
        out.append((synthetic, records))
    return out


def _receipt_admission(receipts: Path | None, task: str, task_meta: dict) -> dict:
    """A task's admission from the repository's executed receipts, bound to the manifest's task
    checksum and isolation pins. Absent receipts or a failing assessment leave it unadmitted."""
    if receipts is None:
        return {"admitted": False, "reason": "no receipts directory given"}
    gate, boundary = receipts / task / "gate.json", receipts / task / "isolation.json"
    checksum = task_meta.get("checksum") or ""
    qualification = ({"receipt": str(gate), **admission.assess_qualification([gate], checksum, "reporting")}
                     if gate.is_file() else {"receipt": None, "ok": False, "status": "unqualified",
                                             "reason": "no executed qualification receipt"})
    isolation = ({"receipt": str(boundary), **isolation_gate.assess_isolation(
        boundary, task_digest=checksum, images=task_meta.get("isolation_images") or {})}
        if boundary.is_file() else {"receipt": None, "ok": False, "status": "isolation_unproven",
                                    "reason": "no executed isolation receipt"})
    return {"admitted": bool(qualification["ok"] and isolation["ok"]), "qualification": qualification,
            "isolation": isolation, "source": "receipts"}


def _analysis_fields(line: dict) -> dict:
    """Fields the report keys derive at analysis time from the ledger line beside its admission
    record: the agent's exit status as the adapter recorded it, and the trajectory summary from
    the trial directory when it resolves (an absent trajectory leaves the key out, never zero)."""
    out = {"agent_exit_status": (line.get("agent_metadata") or {}).get("exit_status")}
    trial_dir = line.get("trial_dir")
    if trial_dir and Path(trial_dir).is_dir():
        summary = trajectory.summary_for_trial(Path(trial_dir))
        if summary is not None:
            out["trajectory_summary"] = summary
    return out


def _relocate(line: dict, records: Path) -> dict:
    """The ledger's `trial_dir` (`.../run/jobs/<job>/<trial>`) and `receipt`
    (`.../run/receipts/<job>.json`) as paths under a published records directory. A path that does
    not have that shape is left alone, and a missing file stays missing."""
    out = dict(line)
    for key, marker, sub in (("trial_dir", "/run/jobs/", "evidence"), ("receipt", "/run/receipts/", "receipts")):
        value = line.get(key)
        if isinstance(value, str) and marker in value:
            out[key] = str(records / sub / value.split(marker, 1)[1])
    return out


def _revision(rev) -> str | None:
    """The runner records `git_revision()` as {head, dirty}; older lines hold a string."""
    if isinstance(rev, dict):
        head = rev.get("head")
        return f"{head}+dirty" if head and rev.get("dirty") else head
    return rev if isinstance(rev, str) else None


def _ledger_record(line: dict, manifest: dict, attempt: dict, *, rejudge: bool = False) -> dict:
    """The admission record for one ledger line: the one the runner embedded, else the one its
    trial directory and gateway receipt yield now, else invalid evidence. A line the runner filed
    as an infrastructure error (its images were not the isolation pins, say) is never an eligible
    record, whatever the verifier judged: the operator's failure is replaced, not scored."""
    rec = _judged_record(line, manifest, attempt, rejudge=rejudge)
    if line.get("status") == "infra_error" and str(rec.get("status", "")).startswith("eligible"):
        return {**rec, "status": "infrastructure_failure", "scored_reward": None,
                "failure_class": line.get("failure_class") or "operator_setup",
                "reason": line.get("status_reason") or "the runner filed this attempt as an infrastructure error",
                "superseded": rec}
    return rec


def _judged_record(line: dict, manifest: dict, attempt: dict, *, rejudge: bool) -> dict:
    rec = line.get("admission")
    if isinstance(rec, dict) and not rejudge:
        return rec
    trial_dir = line.get("trial_dir")
    if trial_dir and Path(trial_dir).is_dir():
        receipt = Path(line["receipt"]) if line.get("receipt") else None
        return admission.classify_attempt(Path(trial_dir).parent, exit_code=line.get("harbor_exit"),
                                          manifest=manifest, attempt=attempt, gateway_receipt=receipt)
    if isinstance(rec, dict):  # asked to re-judge, but the trial directory is gone: the embedded record stands
        return {**rec, "rejudge": "trial directory unavailable; embedded record kept"}
    return {"attempt": attempt["id"], "harness": attempt["harness"], "seed": attempt.get("seed"),
            "status": "invalid_evidence", "reason": "ledger line carries no admission record and no trial directory"}


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
    for r in rows:  # the run's call cap and pricing ride on every row, so runs that disagree are visible
        r.update(planned=r.get("attempt") in planned, calls_cap=_calls_cap(manifest), pricing=manifest.get("pricing"))
    return rows


def _calls_cap(manifest: dict) -> int | None:
    """The envelope's call cap: the experiment manifest's `envelope.calls`, else the admission
    manifest's `model.budgets.calls`."""
    for cap in ((manifest.get("envelope") or {}).get("calls"), ((manifest.get("model") or {}).get("budgets") or {}).get("calls")):
        if isinstance(cap, int) and cap > 0:
            return cap
    return None


def _termination_counts(rows: list[dict]) -> dict:
    """Attempts by why they ended, over every recorded attempt (a missing one never ended)."""
    recorded = [r for r in rows if r.get("status") != "missing"]
    reasons = [termination(r) for r in recorded]
    out = {t: sum(1 for reason, _ in reasons if reason == t) for t in TERMINATIONS}
    limits = [limit for reason, limit in reasons if reason == "budget_exhausted"]
    return {**out, "budget_exhausted_by": {k: limits.count(k) for k in (*BUDGET_LIMITS, "other")}, "n": len(recorded)}


def _active_at_call(eligible: list[dict]) -> dict:
    """Eligible attempts still active at admitted call k: those whose admitted calls reached k, on a
    grid of every ACTIVE_GRID calls from 0 to the envelope's cap (the largest cap when runs differ)."""
    calls = sorted(c for c in ((r.get("budget") or {}).get("admitted_calls") for r in eligible)
                   if isinstance(c, int) and not isinstance(c, bool))
    caps = [r["calls_cap"] for r in eligible if isinstance(r.get("calls_cap"), int)]
    cap = max(caps) if caps else None
    if not calls:
        return {"n": 0, "cap": cap, "grid": [], "active": [], "median_calls": None, "max_calls": None}
    top = cap if cap is not None else calls[-1]
    grid = list(range(0, top + 1, ACTIVE_GRID))
    if grid[-1] != top:
        grid.append(top)
    return {"n": len(calls), "cap": cap, "grid": grid, "active": [sum(1 for c in calls if c >= k) for k in grid],
            "median_calls": statistics.median(calls), "max_calls": calls[-1]}


def _trajectory_table(eligible: list[dict]) -> dict:
    """Aggregates of the per-attempt trajectory summaries that are present; null keys otherwise."""
    summaries = [r["trajectory_summary"] for r in eligible if isinstance(r.get("trajectory_summary"), dict)]
    missing = len(eligible) - len(summaries)
    if not summaries:
        return {"n": 0, "missing": missing, "calls": None, "phase_shares": None, "shell_share": None,
                "out_of_interface_calls": None, "attempts_ended_on_out_of_interface": None,
                "interface_unknown": None, "attempts_without_edit": None, "repatches_mean": None,
                "compactions": None}
    calls = sum(s.get("calls") or 0 for s in summaries)

    def share(count):
        return round(count / calls, 4) if calls else None

    known = [s for s in summaries if s.get("out_of_interface_calls") is not None]
    return {"n": len(summaries), "missing": missing, "calls": calls, "compactions": _compaction_table(summaries),
            "phase_shares": {p: share(sum((s.get("phases") or {}).get(p) or 0 for s in summaries))
                             for p in trajectory.PHASES},
            "shell_share": share(sum(s.get("shell_calls") or 0 for s in summaries)),
            "out_of_interface_calls": sum(s["out_of_interface_calls"] for s in known),
            "attempts_ended_on_out_of_interface": sum(1 for s in known if s.get("ended_on_out_of_interface")),
            "interface_unknown": len(summaries) - len(known),
            "attempts_without_edit": sum(1 for s in summaries if not s.get("edits")),
            "repatches_mean": round(sum(s.get("repatches") or 0 for s in summaries) / len(summaries), 4)}


def _compaction_table(summaries: list[dict]) -> dict:
    """What followed compaction boundaries, summed over the attempts whose summary carries a
    `compactions` record (a summary an adapter wrote without one counts under `unknown`)."""
    records = [s["compactions"] for s in summaries if isinstance(s.get("compactions"), dict)]
    by_trigger: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for r in records:
        for trigger, n in (r.get("by_trigger") or {}).items():
            by_trigger[trigger] = by_trigger.get(trigger, 0) + n
        for model, n in (r.get("by_model") or {}).items():
            by_model[model] = by_model.get(model, 0) + n
    count = sum(r.get("count") or 0 for r in records)
    return {"attempts_with_compaction": sum(1 for r in records if r.get("count")), "count": count,
            "by_trigger": by_trigger, "by_model": by_model,
            "reverted_files": sum(r.get("reverted_files") or 0 for r in records),
            "repeated_after": sum(r.get("repeated_after") or 0 for r in records),
            "first_step_errors": sum(r.get("first_step_errors") or 0 for r in records),
            "rereads_after": sum(r.get("rereads_after") or 0 for r in records),
            "unknown": len(summaries) - len(records)}


def _cost_estimate(rows: list[dict], budget: dict) -> dict:
    """Charged tokens priced by the manifest's `pricing` block; null without exactly one such block."""
    blocks = {json.dumps(r["pricing"], sort_keys=True) for r in rows if isinstance(r.get("pricing"), dict)}
    null = {"usd": None, "source": None, "input_per_million": None, "output_per_million": None, "n": 0}
    if len(blocks) != 1:
        return {**null, "note": "no pricing block in the manifest" if not blocks else "runs disagree on pricing"}
    pricing = json.loads(blocks.pop())
    rates = pricing.get("input_per_million"), pricing.get("output_per_million")
    tokens = budget.get("input_tokens"), budget.get("output_tokens")
    if any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in rates) or any(t is None for t in tokens):
        return {**null, "source": pricing.get("source"), "note": "pricing rates or token totals are missing"}
    usd = (tokens[0]["total"] * rates[0] + tokens[1]["total"] * rates[1]) / 1e6
    return {"usd": round(usd, 6), "source": pricing.get("source"), "input_per_million": rates[0],
            "output_per_million": rates[1], "n": min(tokens[0]["n"], tokens[1]["n"]), "note": None}


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

    budget = {"calls": budget_sum("admitted_calls"), "input_tokens": budget_sum("input_charged"),
              "output_tokens": budget_sum("output_charged"), "exhausted": sum(1 for b in budgets if b.get("exhausted"))}
    return {
        "attempts": {"planned": sum(1 for r in rows if r.get("planned")), "eligible": len(eligible),
                     "infrastructure_failure": sum(1 for r in rows if r["status"] == "infrastructure_failure"),
                     "invalid_evidence": sum(1 for r in rows if r["status"] == "invalid_evidence"),
                     "missing": sum(1 for r in rows if r["status"] == "missing"),
                     "unplanned": sum(1 for r in rows if not r.get("planned"))},
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
        "budget": budget,
        "termination": _termination_counts(rows),
        "active_at_call": _active_at_call(eligible),
        "trajectory": _trajectory_table(eligible),
        "cost_estimate_usd": _cost_estimate(rows, budget),
    }


def _paired(rows: list[dict], left: str, right: str) -> dict:
    """Discordance between two harnesses on the (task, seed) slots where both are eligible."""
    by_slot: dict[tuple, dict[str, list[dict]]] = {}
    for r in rows:
        if str(r.get("status", "")).startswith("eligible"):
            by_slot.setdefault((r["task"], r.get("seed")), {}).setdefault(r["harness"], []).append(r)
    crowded = [(s, h) for s, hs in by_slot.items() for h, rs in hs.items() if len(rs) > 1 and h in (left, right)]
    if crowded:
        raise ValueError("more than one eligible attempt of one harness on a (task, seed) slot: "
                         + ", ".join(f"{h} on {s}" for s, h in crowded) + "; a pair is one attempt per side")
    paired = [(s, {h: rs[0] for h, rs in hs.items()}) for s, hs in by_slot.items() if left in hs and right in hs]

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


def _num(x) -> str:
    return "n/a" if x is None else f"{x:.2f}" if isinstance(x, float) else str(x)


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
    lines += ["", "## Termination (every recorded attempt)", "",
              "| Harness | n | handoff | budget exhausted (calls / input / output / wall / other) | harness stop | "
              "infrastructure | unknown |", "|---|---|---|---|---|---|---|"]
    for name, t in report["harnesses"].items():
        te, by = t["termination"], t["termination"]["budget_exhausted_by"]
        lines.append(f"| {name} | {te['n']} | {te['handoff']} | {te['budget_exhausted']} ({by['calls']} / {by['input_tokens']} / "
                     f"{by['output_tokens']} / {by['wall']} / {by['other']}) | {te['harness_stop']} | "
                     f"{te['infrastructure']} | {te['unknown']} |")
    lines += ["", "## Attempts active at admitted call k (eligible attempts)", "",
              "| Harness | n | median calls | max calls | cap | active at k |", "|---|---|---|---|---|---|"]
    for name, t in report["harnesses"].items():
        ac = t["active_at_call"]
        curve = " ".join(f"{k}:{n}" for k, n in zip(ac["grid"], ac["active"])) or "n/a"
        lines.append(f"| {name} | {ac['n']} | {_num(ac['median_calls'])} | {_num(ac['max_calls'])} | {_num(ac['cap'])} | {curve} |")
    lines += ["", "## Trajectory (eligible attempts with a trajectory summary; shares of tool calls)", "",
              "| Harness | Summarised / missing | Calls | " + " | ".join(trajectory.PHASES) + " | Shell share | "
              "Out-of-interface calls (attempts ending on one; interface unknown) | Without edit | Repatches mean |",
              "|---|" + "---|" * (len(trajectory.PHASES) + 6)]
    for name, t in report["harnesses"].items():
        tr = t["trajectory"]
        shares = " | ".join(_num((tr["phase_shares"] or {}).get(p)) for p in trajectory.PHASES)
        lines.append(f"| {name} | {tr['n']} / {tr['missing']} | {_num(tr['calls'])} | {shares} | {_num(tr['shell_share'])} | "
                     f"{_num(tr['out_of_interface_calls'])} ({_num(tr['attempts_ended_on_out_of_interface'])}; "
                     f"{_num(tr['interface_unknown'])}) | {_num(tr['attempts_without_edit'])} | {_num(tr['repatches_mean'])} |")
    lines += ["", "## Compactions (what followed each boundary, first five calls)", "",
              "| Harness | Attempts with one | Compactions (by trigger) | Repeated calls after | First-step errors | "
              "Re-reads after | Unknown |", "|---|---|---|---|---|---|---|"]
    for name, t in report["harnesses"].items():
        c = t["trajectory"].get("compactions")
        if not c:
            lines.append(f"| {name} | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        triggers = ", ".join(f"{k} {v}" for k, v in sorted(c["by_trigger"].items())) or "none"
        # The summary writer is named only when a recorded one differs from the session model.
        if any(m != "unrecorded" for m in c.get("by_model") or {}):
            triggers += "; by writer " + ", ".join(f"{k} {v}" for k, v in sorted(c["by_model"].items()))
        lines.append(f"| {name} | {c['attempts_with_compaction']} | {c['count']} ({triggers}) | {c['repeated_after']} | "
                     f"{c['first_step_errors']} | {c['rereads_after']} | {c['unknown']} |")
    lines += ["", "## Cost estimate (charged tokens at the manifest's pricing)", "",
              "| Harness | USD | Input $/M | Output $/M | Attempts priced | Source |", "|---|---|---|---|---|---|"]
    for name, t in report["harnesses"].items():
        c = t["cost_estimate_usd"]
        lines.append(f"| {name} | {_num(c['usd'])} | {_num(c['input_per_million'])} | {_num(c['output_per_million'])} | "
                     f"{c['n']} | {c['source'] or c.get('note') or 'n/a'} |")
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
