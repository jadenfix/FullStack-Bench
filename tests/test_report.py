"""The cohort report: every number carries its denominator, unknown views stay unknown, cohorts
never mix, and the paired table counts only slots where both harnesses produced eligible evidence."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fsbench import admission, report
from test_admission import gateway, manifest_for, write_trial

ROOT = Path(__file__).resolve().parent.parent
SEEDS = (1, 2, 3)


@pytest.fixture
def task(tmp_path):
    task = tmp_path / "task"
    (task / "tests").mkdir(parents=True)
    (task / "task.toml").write_text('[verifier]\nenvironment_mode="separate"\n[environment]\ncpus=2\nmemory_mb=4096\n')
    (task / "instruction.md").write_text("do the thing\n")
    return task


def attempts():
    return [{"id": f"{h}-s{s}", "harness": h, "seed": s} for s in SEEDS for h in ("rusty", "mini")]


def record(task, tmp_path, m, attempt_id, *, reward=1.0, views=None, exception=None, metadata=None, exhausted=None,
           calls=1):
    job = tmp_path / "jobs" / attempt_id
    # Harbor's result.json differs per trial; the fixture's would not, and admission rejects shared trials.
    trial = write_trial(job, m["task"]["digest"], reward=reward, exception=exception,
                        metadata={"trial_marker": attempt_id, **(metadata or {})})
    if views is not None:
        (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": reward, **views}))
    attempt = next(a for a in m["attempts"] if a["id"] == attempt_id)
    usage = [{"status": "completed", "http_status": 200, "usage_known": True}] * calls
    return admission.classify_attempt(job, exit_code=0, manifest=m, attempt=attempt,
                                      gateway_receipt=gateway(tmp_path / f"{attempt_id}.gw.json", exhausted=exhausted,
                                                              records=usage))


FULL = {"safe_success": 1.0, "measurement_eligible": 1.0, "view_final_artifact": 1.0, "view_deployed_at_handoff": 1.0,
        "view_whole_episode": 1.0, "view_recovery": 1.0, "eligibility_observation_complete": 1.0,
        "eligibility_post_handoff_observed": 1.0, "eligibility_challenges_ran": 1.0, "eligibility_audit_chain_intact": 1.0}


def test_views_ride_on_the_terminal_record(task, tmp_path):
    m = manifest_for(task, tmp_path)
    r = record(task, tmp_path, m, "rusty-s1", views={**FULL, "note": "ignored", "bad": float("nan")})
    assert r["views"] == FULL  # strings and non-finite values never enter the record
    assert record(task, tmp_path, m, "mini-s1")["views"] == {}  # no reward.json: absent, not zero


def test_report_counts_only_eligible_and_keeps_unknown_views_unknown(task, tmp_path):
    m = manifest_for(task, tmp_path, attempts=attempts())
    partial = {**FULL, "view_recovery": 0.0, "measurement_eligible": 0.0, "eligibility_challenges_ran": 0.0}
    records = [
        record(task, tmp_path, m, "rusty-s1", views=FULL, metadata={"goal_status": "done", "completion_accepted": True}),
        record(task, tmp_path, m, "rusty-s2", reward=0.0, views=partial, metadata={"completion_accepted": True}),
        record(task, tmp_path, m, "rusty-s3", exception="EnvironmentStartTimeoutError"),  # infrastructure, never eligible
        record(task, tmp_path, m, "mini-s1", views=FULL),
        record(task, tmp_path, m, "mini-s2", reward=0.0),  # no reward.json at all: views unknown
        record(task, tmp_path, m, "mini-s3", reward=0.0, views={**FULL, "safe_success": 0.0, "view_final_artifact": 0.0}),
    ]
    out = report.aggregate([(m, records)], pair=("rusty", "mini"))
    assert out["claim_label"] == "executed" and out["reportable"] and out["full_benchmark_claim"]
    rusty, mini = out["harnesses"]["rusty"], out["harnesses"]["mini"]
    assert rusty["attempts"] == {"planned": 3, "eligible": 2, "infrastructure_failure": 1, "invalid_evidence": 0,
                                 "missing": 0, "unplanned": 0}
    assert rusty["safe_success"] == {"count": 1, "n": 2, "rate": 0.5, "ci95": report.wilson(1, 2)}
    assert rusty["measurement_eligible"]["count"] == 1 and rusty["safe_success_among_measured"] == report.proportion(1, 1)
    assert rusty["views"]["recovery"] == {**report.proportion(1, 2), "unknown": 0}
    assert rusty["completion"]["accepted"]["count"] == 2 and rusty["completion"]["accepted_without_success"] == 1
    assert rusty["completion"]["accepted_and_independent"] == report.proportion(1, 2)
    assert rusty["failure_classes"] == {"operator_setup": 1, "coverage_limitation": 0, "harness": 0, "solver": 1}
    assert mini["views"]["final_artifact"] == {**report.proportion(1, 2), "unknown": 1}
    assert mini["completion"]["success_without_acceptance"] == 1
    assert mini["budget"]["calls"] == {"total": 3, "n": 3}
    p = out["paired"]
    assert p["paired_slots"] == 2  # seed 3 has no eligible rusty record
    assert p["safe_success"] == {"left_only": 0, "right_only": 0, "both": 1, "neither": 1, "unknown": 0, "sign_test_p": None}
    assert p["views"]["recovery"]["unknown"] == 1  # mini-s2 has no view record


def test_cohorts_never_mix_and_development_is_never_reportable(task, tmp_path):
    dev = manifest_for(task, tmp_path, cohort="development")
    (tmp_path / "r").mkdir()
    rep = manifest_for(task, tmp_path / "r", cohort="reporting")
    with pytest.raises(ValueError, match="span cohorts"):
        report.aggregate([(dev, []), (rep, [])])
    out = report.aggregate([(dev, [record(task, tmp_path, dev, "rusty-s1", views=FULL)])])
    assert out["claim_label"].startswith("exploratory (development") and not out["reportable"]
    assert out["harnesses"]["mini"]["attempts"] == {"planned": 1, "eligible": 0, "infrastructure_failure": 0,
                                                     "invalid_evidence": 0, "missing": 1, "unplanned": 0}
    assert report.aggregate([(dev, [])], pair=("rusty", "mini"))["paired"]["paired_slots"] == 0
    with pytest.raises(ValueError, match="no records"):
        report.aggregate([(dev, [])], pair=("rusty", "claude"))


def test_statistics_are_exact():
    assert report.wilson(0, 0) is None and report.wilson(5, 5) == (0.5655, 1.0) and report.wilson(0, 10) == (0.0, 0.2775)
    assert report.sign_test(0, 0) is None and report.sign_test(5, 0) == 0.0625 and report.sign_test(3, 3) == 1.0
    assert report.sign_test(8, 1) == pytest.approx(0.0391, abs=1e-4)


def test_cli_writes_json_and_markdown(task, tmp_path):
    m = manifest_for(task, tmp_path, attempts=attempts())
    run = tmp_path / "run"
    (run / "attempts").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps(m))
    for aid, kw in (("rusty-s1", dict(views=FULL)), ("mini-s1", dict(reward=0.0, views={**FULL, "safe_success": 0.0}))):
        (run / "attempts" / f"{aid}.json").write_text(json.dumps(record(task, tmp_path, m, aid, **kw)))
    out_dir = tmp_path / "out"
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "report_cohort.py"), str(run), "--pair", "rusty", "mini",
                          "--out", str(out_dir)], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "report.json").read_text())
    assert data["paired"]["safe_success"]["left_only"] == 1 and data["harnesses"]["mini"]["safe_success"]["count"] == 0
    md = (out_dir / "report.md").read_text()
    assert md == proc.stdout and "| rusty | 1 / 3 | 1/1 = 1.00 [0.21, 1.00]" in md and "| 0 / 0 / 2 |" in md and "sign test p" in md
    bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "report_cohort.py"), str(tmp_path / "nowhere")],
                         capture_output=True, text=True, cwd=ROOT)
    assert bad.returncode == 2 and "manifest.json" in bad.stderr


def test_ledger_attempts_report_through_their_admission_records(task, tmp_path):
    m = manifest_for(task, tmp_path, attempts=attempts())
    tracks = [{"name": "rusty", "harness": "rusty", "version": "0.9.0", "binary_sha256": "e" * 64},
              {"name": "mini", "harness": "mini-swe-agent", "version": "2.4.6", "config_sha256": "f" * 64}]
    episodes = [{"episode": a["id"], "track": a["harness"], "task": "task", "seed": a["seed"]} for a in attempts()]
    good = record(task, tmp_path, m, "rusty-s1", views=FULL)
    replaced = {"kind": "attempt", "episode": "mini-s1", "status": "infra_error", "admission": None}
    final = record(task, tmp_path, m, "mini-s1", reward=0.0, views={**FULL, "safe_success": 0.0})
    lines = [{"kind": "run", "fsb_revision": {"head": "abc", "dirty": False}},
             {"kind": "attempt", "episode": "rusty-s1", "admission": good, "fsb_revision": {"head": "abc", "dirty": True}},
             replaced, {"kind": "attempt", "episode": "mini-s1", "admission": final},
             {"kind": "attempt", "episode": "rusty-s2", "status": "scored"}]  # no admission record
    (tmp_path / "ledger.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    (tmp_path / "plan.json").write_text(json.dumps({"episodes": episodes}))
    (tmp_path / "experiment.json").write_text(json.dumps({"cohort_role": "development", "tracks": tracks,
                                                          "tasks": [{"name": "task", "checksum": m["task"]["digest"]}]}))
    runs = report.load_ledger(tmp_path / "ledger.jsonl", tmp_path / "plan.json", tmp_path / "experiment.json")
    assert len(runs) == 1 and runs[0][0]["fsb_rev"] == "abc+dirty"
    out = report.aggregate(runs, pair=("rusty", "mini"))
    assert out["claim_label"].startswith("exploratory (development")
    assert out["harnesses"]["rusty"]["attempts"] == {"planned": 3, "eligible": 1, "infrastructure_failure": 0,
                                                     "invalid_evidence": 1, "missing": 1, "unplanned": 0}
    assert out["harnesses"]["mini"]["attempts"]["eligible"] == 1 and out["paired"]["safe_success"]["left_only"] == 1
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "report_cohort.py"), "--ledger",
                           str(tmp_path / "ledger.jsonl"), str(tmp_path / "plan.json"), str(tmp_path / "experiment.json")],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0 and "exploratory (development" in proc.stdout


def test_ledger_line_without_embedded_record_is_classified_from_its_trial(task, tmp_path):
    m = manifest_for(task, tmp_path)
    episodes = [{"episode": "rusty-s1", "track": "rusty", "task": "task", "seed": 1}]
    tracks = [{"name": "rusty", "harness": "rusty", "version": "0.9.0", "binary_sha256": "e" * 64}]
    job = tmp_path / "jobs" / "rusty-s1"
    trial = write_trial(job, m["task"]["digest"], reward=1.0, metadata={"goal_status": "done"})
    (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": 1.0, **FULL}))
    receipt = gateway(tmp_path / "gw.json")
    line = {"kind": "attempt", "episode": "rusty-s1", "status": "scored", "trial_dir": str(trial),
            "receipt": str(receipt), "harbor_exit": 0, "fsb_revision": "deadbeef"}
    (tmp_path / "ledger.jsonl").write_text(json.dumps(line) + "\n")
    (tmp_path / "plan.json").write_text(json.dumps({"episodes": episodes}))
    (tmp_path / "experiment.json").write_text(json.dumps({"cohort_role": "reporting", "tracks": tracks,
                                                          "tasks": [{"name": "task", "checksum": m["task"]["digest"]}],
                                                          "admission": {"admitted": True}}))
    [(synthetic, records)] = report.load_ledger(tmp_path / "ledger.jsonl", tmp_path / "plan.json",
                                                tmp_path / "experiment.json")
    assert synthetic["fsb_rev"] == "deadbeef" and len(records) == 1
    assert records[0]["status"] == "eligible_success" and records[0]["views"] == FULL
    assert records[0]["budget"]["admitted_calls"] == 1 and records[0]["goal_claimed"] is True


def test_stray_records_and_crowded_slots_are_not_pooled(task, tmp_path):
    m = manifest_for(task, tmp_path)
    good = record(task, tmp_path, m, "rusty-s1", views=FULL)
    stray = {**good, "attempt": "rusty-s9"}
    out = report.aggregate([(m, [good, stray])])
    assert out["harnesses"]["rusty"]["attempts"] == {"planned": 1, "eligible": 1, "infrastructure_failure": 0,
                                                     "invalid_evidence": 1, "missing": 0, "unplanned": 1}
    twin = manifest_for(task, tmp_path, attempts=[{"id": "rusty-a", "harness": "rusty", "seed": 1},
                                                   {"id": "rusty-b", "harness": "rusty", "seed": 1},
                                                   {"id": "mini-a", "harness": "mini", "seed": 1}])
    records = [record(task, tmp_path, twin, "rusty-a", views=FULL),
               record(task, tmp_path, twin, "rusty-b", reward=0.0, views={**FULL, "safe_success": 0.0}),
               record(task, tmp_path, twin, "mini-a", reward=0.0, views={**FULL, "safe_success": 0.0})]
    with pytest.raises(ValueError, match="more than one eligible attempt"):
        report.aggregate([(twin, records)], pair=("rusty", "mini"))
    assert report.aggregate([(twin, records)])["harnesses"]["rusty"]["safe_success"]["n"] == 2  # unpaired tables still count both


def test_operator_refusal_with_a_passing_reward_is_contradictory(task, tmp_path):
    m = manifest_for(task, tmp_path)
    r = record(task, tmp_path, m, "rusty-s1", reward=1.0, exception="RustyConfigurationError")
    assert r["status"] == "invalid_evidence" and "contradictory" in r["reason"]


def test_rejudge_reclassifies_from_the_trial_under_current_rules(task, tmp_path):
    """A ledger written before an admission fix carries the old record; --rejudge reads the trial
    again. The embedded record stands only when the trial directory is gone."""
    m = manifest_for(task, tmp_path)
    episodes = [{"episode": "rusty-s1", "track": "rusty", "task": "task", "seed": 1}]
    tracks = [{"name": "rusty", "harness": "rusty", "version": "0.9.0", "binary_sha256": "e" * 64}]
    job = tmp_path / "jobs" / "rusty-s1"
    trial = write_trial(job, m["task"]["digest"], reward=1.0, exception="NonZeroAgentExitCodeError")
    (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": 1.0, **FULL}))
    receipt = gateway(tmp_path / "gw.json", exhausted="input_tokens")
    stale = {"attempt": "rusty-s1", "harness": "rusty", "seed": 1, "status": "invalid_evidence",
             "reason": "NonZeroAgentExitCodeError with reward 1 is contradictory"}
    line = {"kind": "attempt", "episode": "rusty-s1", "admission": stale, "trial_dir": str(trial), "receipt": str(receipt),
            "harbor_exit": 0, "fsb_revision": "old"}
    (tmp_path / "ledger.jsonl").write_text(json.dumps(line) + "\n")
    (tmp_path / "plan.json").write_text(json.dumps({"episodes": episodes}))
    (tmp_path / "experiment.json").write_text(json.dumps({"cohort_role": "development", "tracks": tracks,
                                                          "tasks": [{"name": "task", "checksum": m["task"]["digest"]}]}))
    args = (tmp_path / "ledger.jsonl", tmp_path / "plan.json", tmp_path / "experiment.json")
    [(_, kept)] = report.load_ledger(*args)
    assert kept[0]["status"] == "invalid_evidence"
    [(_, again)] = report.load_ledger(*args, rejudge=True)
    assert again[0]["status"] == "eligible_solver_failure" and "exhausted" in again[0]["reason"]
    import shutil
    shutil.rmtree(trial)
    [(_, gone)] = report.load_ledger(*args, rejudge=True)
    assert gone[0]["status"] == "invalid_evidence" and gone[0]["rejudge"].startswith("trial directory unavailable")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "report_cohort.py"), "--rejudge", "--ledger", *map(str, args)],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0 and "exploratory (development" in proc.stdout


def test_rejudge_reads_published_records_in_place_of_host_paths(task, tmp_path):
    """A ledger names paths on the run host; a published records directory holds the same trial
    directories and receipts under evidence/ and receipts/, and --records maps onto it."""
    m = manifest_for(task, tmp_path)
    episodes = [{"episode": "rusty-s1", "track": "rusty", "task": "task", "seed": 1}]
    tracks = [{"name": "rusty", "harness": "rusty", "version": "0.9.0", "binary_sha256": "e" * 64}]
    records = tmp_path / "published"
    job = records / "evidence" / "rusty-s1--a1"
    trial = write_trial(job, m["task"]["digest"], reward=1.0, exception="NonZeroAgentExitCodeError")
    (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": 1.0, **FULL}))
    (records / "receipts").mkdir()
    gateway(records / "receipts" / "rusty-s1--a1.json", exhausted="input_tokens")
    host = "/host/scratch/phase5/pilot/run"
    line = {"kind": "attempt", "episode": "rusty-s1", "status": "scored",
            "trial_dir": f"{host}/jobs/rusty-s1--a1/{trial.name}", "receipt": f"{host}/receipts/rusty-s1--a1.json",
            "harbor_exit": 0, "fsb_revision": {"head": "abc", "dirty": False}}
    (tmp_path / "ledger.jsonl").write_text(json.dumps(line) + "\n")
    (tmp_path / "plan.json").write_text(json.dumps({"episodes": episodes}))
    (tmp_path / "experiment.json").write_text(json.dumps({"cohort_role": "development", "tracks": tracks,
                                                          "tasks": [{"name": "task", "checksum": m["task"]["digest"]}]}))
    args = (tmp_path / "ledger.jsonl", tmp_path / "plan.json", tmp_path / "experiment.json")
    [(_, unmapped)] = report.load_ledger(*args, rejudge=True)
    assert unmapped[0]["status"] == "invalid_evidence"  # the host path does not exist here
    [(_, mapped)] = report.load_ledger(*args, rejudge=True, records=records)
    assert mapped[0]["status"] == "eligible_solver_failure" and "exhausted" in mapped[0]["reason"]
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "report_cohort.py"), "--rejudge", "--records", str(records),
                           "--ledger", *map(str, args)], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0 and "exploratory (development" in proc.stdout


def test_an_attempt_the_runner_filed_as_infrastructure_is_never_counted_as_eligible(task, tmp_path):
    """The runner files an attempt that ran on images outside the isolation pins as infra_error and
    replaces it; its embedded record still says what the verifier judged. If the replacement never
    ran, that line is the episode's last, and it must not count as a success."""
    m = manifest_for(task, tmp_path)
    episodes = [{"episode": "rusty-s1", "track": "rusty", "task": "task", "seed": 1}]
    tracks = [{"name": "rusty", "harness": "rusty", "version": "0.9.0", "binary_sha256": "e" * 64}]
    judged = {"attempt": "rusty-s1", "harness": "rusty", "seed": 1, "status": "eligible_success",
              "evidence_valid": True, "scored_reward": 1.0, "functional": True}
    line = {"kind": "attempt", "episode": "rusty-s1", "status": "infra_error", "failure_class": "operator_setup",
            "status_reason": "the trial ran on images other than the task's isolation pins",
            "trial_images": {"simcloud": {"ran": "sha256:" + "9" * 64, "pinned": "sha256:" + "1" * 64}},
            "admission": judged, "harbor_exit": 0, "fsb_revision": {"head": "abc", "dirty": False}}
    (tmp_path / "ledger.jsonl").write_text(json.dumps(line) + "\n")
    (tmp_path / "plan.json").write_text(json.dumps({"episodes": episodes}))
    (tmp_path / "experiment.json").write_text(json.dumps({"cohort_role": "development", "tracks": tracks,
                                                          "tasks": [{"name": "task", "checksum": m["task"]["digest"]}]}))
    [(_, records)] = report.load_ledger(tmp_path / "ledger.jsonl", tmp_path / "plan.json", tmp_path / "experiment.json")
    assert records[0]["status"] == "infrastructure_failure" and records[0]["failure_class"] == "operator_setup"
    assert records[0]["superseded"]["status"] == "eligible_success" and "isolation pins" in records[0]["reason"]
    out = report.aggregate(report.load_ledger(tmp_path / "ledger.jsonl", tmp_path / "plan.json", tmp_path / "experiment.json"))
    table = out["harnesses"]["rusty"]
    assert table["safe_success"]["count"] == 0 and table["attempts"]["infrastructure_failure"] == 1


def test_termination_reasons_come_from_the_terminal_record(task, tmp_path):
    m = manifest_for(task, tmp_path, attempts=attempts())
    records = [
        record(task, tmp_path, m, "rusty-s1", views=FULL),  # the agent ended its own episode
        record(task, tmp_path, m, "rusty-s2", reward=0.0, exception="NonZeroAgentExitCodeError", exhausted="calls"),
        record(task, tmp_path, m, "rusty-s3", exception="EnvironmentStartTimeoutError"),
        record(task, tmp_path, m, "mini-s1", reward=0.0, exception="AgentTimeoutError"),
        record(task, tmp_path, m, "mini-s2", reward=0.0, exception="SomethingOdd"),  # invalid evidence
    ]
    assert [report.termination(r) for r in records] == [("handoff", None), ("budget_exhausted", "calls"),
                                                        ("infrastructure", None), ("harness_stop", None), ("unknown", None)]
    assert report.termination({**records[0], "agent_exit_status": 1}) == ("harness_stop", None)
    assert report.termination({**records[0], "agent_exit_status": 0}) == ("handoff", None)
    assert report.termination({**records[0], "budget": {"exhausted": "accounting_anomaly"}}) == ("budget_exhausted", "other")
    out = report.aggregate([(m, records)])
    assert out["harnesses"]["rusty"]["termination"] == {
        "handoff": 1, "budget_exhausted": 1, "harness_stop": 0, "infrastructure": 1, "unknown": 0, "n": 3,
        "budget_exhausted_by": {"calls": 1, "input_tokens": 0, "output_tokens": 0, "wall": 0, "other": 0}}
    mini = out["harnesses"]["mini"]["termination"]
    assert mini["harness_stop"] == 1 and mini["unknown"] == 1 and mini["n"] == 2  # mini-s3 is missing: it never ended


def test_active_at_call_counts_eligible_attempts_still_running_at_each_grid_point(task, tmp_path):
    m = manifest_for(task, tmp_path, attempts=attempts())  # the admission manifest pins 250 calls
    records = [record(task, tmp_path, m, "rusty-s1", views=FULL, calls=25),
               record(task, tmp_path, m, "rusty-s2", reward=0.0, views={**FULL, "safe_success": 0.0}, calls=10),
               record(task, tmp_path, m, "rusty-s3", exception="EnvironmentStartTimeoutError", calls=100)]  # not eligible
    ac = report.aggregate([(m, records)])["harnesses"]["rusty"]["active_at_call"]
    assert ac["n"] == 2 and ac["cap"] == 250 and ac["median_calls"] == 17.5 and ac["max_calls"] == 25
    assert ac["grid"] == list(range(0, 251, 10)) and ac["active"][:4] == [2, 2, 1, 0] and ac["active"][-1] == 0
    assert report.aggregate([(m, [])])["harnesses"]["rusty"]["active_at_call"] == {
        "n": 0, "cap": None, "grid": [], "active": [], "median_calls": None, "max_calls": None}
    uncapped = {**m, "model": {**m["model"], "budgets": {}}}
    assert report.aggregate([(uncapped, records[:1])])["harnesses"]["rusty"]["active_at_call"]["grid"] == [0, 10, 20, 25]


def test_trajectory_keys_aggregate_summaries_and_are_null_without_any(task, tmp_path):
    from fsbench import trajectory
    m = manifest_for(task, tmp_path, attempts=attempts())
    a = record(task, tmp_path, m, "rusty-s1", views=FULL)
    b = record(task, tmp_path, m, "rusty-s2", reward=0.0, views={**FULL, "safe_success": 0.0})
    c = record(task, tmp_path, m, "rusty-s3", reward=0.0, views={**FULL, "safe_success": 0.0})
    bare = report.aggregate([(m, [a, b, c])])["harnesses"]["rusty"]["trajectory"]
    assert bare["n"] == 0 and bare["missing"] == 3 and bare["phase_shares"] is None and bare["shell_share"] is None
    assert bare["out_of_interface_calls"] is None and bare["attempts_without_edit"] is None and bare["repatches_mean"] is None
    assert bare["compactions"] is None
    calls = [{"tool": "read_file", "arguments": {"path": "a"}}, {"tool": "bash", "arguments": {"command": "cat a"}},
             {"tool": "write_file", "arguments": {"path": "a"}}, {"tool": "write_file", "arguments": {"path": "a"}},
             {"tool": "mystery", "arguments": {}}]
    a["trajectory_summary"] = trajectory.summarize(calls, offered={"read_file", "bash", "write_file"})
    b["trajectory_summary"] = trajectory.summarize(calls[:2])  # explores only; nothing refused
    t = report.aggregate([(m, [a, b, c])])["harnesses"]["rusty"]["trajectory"]
    assert t["n"] == 2 and t["missing"] == 1 and t["calls"] == 7
    assert t["phase_shares"] == {"explore": round(4 / 7, 4), "edit": round(2 / 7, 4), "run": 0.0, "verify": 0.0,
                                 "other": round(1 / 7, 4)}
    assert t["shell_share"] == round(2 / 7, 4) and t["attempts_without_edit"] == 1 and t["repatches_mean"] == 0.5
    assert t["out_of_interface_calls"] == 1 and t["attempts_ended_on_out_of_interface"] == 1 and t["interface_unknown"] == 0
    # A summary an adapter wrote without the interface fields still counts as unknown, never as zero.
    b["trajectory_summary"] = {**b["trajectory_summary"], "out_of_interface_calls": None, "ended_on_out_of_interface": None}
    assert report.aggregate([(m, [a, b, c])])["harnesses"]["rusty"]["trajectory"]["interface_unknown"] == 1
    md = report.render_markdown(report.aggregate([(m, [a, b, c])]))
    assert "| rusty | 2 / 1 | 7 | 0.57 | 0.29 | 0.00 | 0.00 | 0.14 | 0.29 | 1 (1; 1) | 1 | 0.50 |" in md
    assert "## Termination" in md and "## Attempts active at admitted call k" in md and "## Cost estimate" in md


def test_cost_estimate_needs_the_manifest_pricing_block(task, tmp_path):
    m = manifest_for(task, tmp_path, attempts=attempts())
    records = [record(task, tmp_path, m, "rusty-s1", views=FULL), record(task, tmp_path, m, "rusty-s2", views=FULL)]
    none = report.aggregate([(m, records)])["harnesses"]["rusty"]["cost_estimate_usd"]
    assert none["usd"] is None and none["source"] is None and none["note"] == "no pricing block in the manifest"
    pricing = {"input_per_million": 2.0, "output_per_million": 8.0, "source": "list price, 2026-10-01"}
    cost = report.aggregate([({**m, "pricing": pricing}, records)])["harnesses"]["rusty"]["cost_estimate_usd"]
    assert cost == {"usd": 0.00012, "source": "list price, 2026-10-01", "input_per_million": 2.0, "output_per_million": 8.0,
                    "n": 2, "note": None}  # the gateway fixture charges 10 input and 5 output tokens per attempt
    md = report.render_markdown(report.aggregate([({**m, "pricing": pricing}, records)]))
    assert "| rusty | 0.00 | 2.00 | 8.00 | 2 | list price, 2026-10-01 |" in md
    (tmp_path / "r2").mkdir()
    other = {**manifest_for(task, tmp_path / "r2", attempts=attempts()), "pricing": {**pricing, "input_per_million": 3.0}}
    mixed = report.aggregate([({**m, "pricing": pricing}, records), (other, [])])["harnesses"]["rusty"]["cost_estimate_usd"]
    assert mixed["usd"] is None and mixed["note"] == "runs disagree on pricing"
    short = report.aggregate([({**m, "pricing": {"source": "unpriced"}}, records)])["harnesses"]["rusty"]["cost_estimate_usd"]
    assert short["usd"] is None and short["source"] == "unpriced"


def test_ledger_lines_gain_exit_status_and_a_trajectory_summary_from_published_records(task, tmp_path):
    """The ledger carries the adapter's exit status beside the admission record; the trajectory
    summary is read from the trial directory only when it resolves, here through --records."""
    m = manifest_for(task, tmp_path)
    episodes = [{"episode": "rusty-s1", "track": "rusty", "task": "task", "seed": 1}]
    tracks = [{"name": "rusty", "harness": "rusty", "version": "0.9.0", "binary_sha256": "e" * 64}]
    judged = record(task, tmp_path, m, "rusty-s1", views=FULL, calls=12)
    records = tmp_path / "published"
    agent = records / "evidence" / "rusty-s1--a1" / "task__abc123" / "agent"
    agent.mkdir(parents=True)
    agent.joinpath("rusty.trajectory.json").write_text(json.dumps({"messages": [
        {"role": "assistant", "tool_calls": [{"function": {"name": "bash", "arguments": json.dumps({"command": "ls"})}}]}]}))
    host = "/host/scratch/phase5/pilot/run"
    line = {"kind": "attempt", "episode": "rusty-s1", "status": "scored", "admission": judged,
            "agent_metadata": {"exit_status": 0, "goal_status": "done"},
            "trial_dir": f"{host}/jobs/rusty-s1--a1/task__abc123", "receipt": f"{host}/receipts/rusty-s1--a1.json"}
    (tmp_path / "ledger.jsonl").write_text(json.dumps(line) + "\n")
    (tmp_path / "plan.json").write_text(json.dumps({"episodes": episodes}))
    pricing = {"input_per_million": 1.0, "output_per_million": 1.0, "source": "test"}
    (tmp_path / "experiment.json").write_text(json.dumps({"cohort_role": "development", "tracks": tracks,
                                                          "tasks": [{"name": "task", "checksum": m["task"]["digest"]}],
                                                          "envelope": {"calls": 40}, "pricing": pricing}))
    args = (tmp_path / "ledger.jsonl", tmp_path / "plan.json", tmp_path / "experiment.json")
    [(synthetic, [plain])] = report.load_ledger(*args)
    assert synthetic["envelope"] == {"calls": 40} and synthetic["pricing"] == pricing
    assert plain["agent_exit_status"] == 0 and "trajectory_summary" not in plain  # the host path does not exist here
    [(_, [mapped])] = report.load_ledger(*args, records=records)
    assert mapped["trajectory_summary"]["calls"] == 1 and mapped["trajectory_summary"]["files"] == ["agent/rusty.trajectory.json"]
    table = report.aggregate(report.load_ledger(*args, records=records))["harnesses"]["rusty"]
    assert table["termination"]["handoff"] == 1 and table["active_at_call"]["cap"] == 40
    assert table["active_at_call"]["grid"] == [0, 10, 20, 30, 40] and table["active_at_call"]["active"] == [1, 1, 0, 0, 0]
    assert table["trajectory"] == {"n": 1, "missing": 0, "calls": 1, "shell_share": 1.0, "out_of_interface_calls": 0,
                                   "phase_shares": {"explore": 1.0, "edit": 0.0, "run": 0.0, "verify": 0.0, "other": 0.0},
                                   "attempts_ended_on_out_of_interface": 0, "interface_unknown": 0,
                                   "attempts_without_edit": 1, "repatches_mean": 0.0,
                                   "compactions": {"attempts_with_compaction": 0, "count": 0, "by_trigger": {}, "by_model": {},
                                                   "repeated_after": 0, "first_step_errors": 0, "rereads_after": 0,
                                                   "unknown": 0}}
    assert mapped["trajectory_summary"]["compactions"]["count"] == 0  # the pinned Rusty records none
    assert table["cost_estimate_usd"]["usd"] == 1.5e-05 and table["cost_estimate_usd"]["source"] == "test"
