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
    assert rusty["attempts"] == {"planned": 3, "eligible": 2, "infrastructure_failure": 1, "invalid_evidence": 0, "missing": 0}
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
                                                     "invalid_evidence": 0, "missing": 1}
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
