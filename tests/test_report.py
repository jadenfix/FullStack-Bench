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


def record(task, tmp_path, m, attempt_id, *, reward=1.0, views=None, exception=None, metadata=None):
    job = tmp_path / "jobs" / attempt_id
    # Harbor's result.json differs per trial; the fixture's would not, and admission rejects shared trials.
    trial = write_trial(job, m["task"]["digest"], reward=reward, exception=exception,
                        metadata={"trial_marker": attempt_id, **(metadata or {})})
    if views is not None:
        (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": reward, **views}))
    attempt = next(a for a in m["attempts"] if a["id"] == attempt_id)
    return admission.classify_attempt(job, exit_code=0, manifest=m, attempt=attempt,
                                      gateway_receipt=gateway(tmp_path / f"{attempt_id}.gw.json"))


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
