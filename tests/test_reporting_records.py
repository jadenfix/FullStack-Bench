"""The published reporting records are the run's own: every file admission hashed still hashes the
same, the manifest is the frozen one, the plan and manifest match the run line's hashes, every
planned episode has an attempt, and the derived trajectory summaries equal their derivation."""

import hashlib
import json
from pathlib import Path

import pytest

from fsbench.runner import manifest_sha256
from fsbench.trajectory import summary_for_trial, trajectory_files

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "docs/results/reporting/records"

pytestmark = pytest.mark.skipif(not (RECORDS / "ledger.jsonl").is_file(), reason="no published reporting records")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lines() -> list[dict]:
    return [json.loads(line) for line in (RECORDS / "ledger.jsonl").read_text().splitlines() if line.strip()]


def attempts() -> list[dict]:
    return [line for line in lines() if line.get("kind") == "attempt"]


def trial(attempt: dict) -> Path:
    # A ledger line's trial_dir is a run-host path: .../run/jobs/<job>/<trial> -> evidence/<job>/<trial>.
    return RECORDS / "evidence" / attempt["job"] / Path(attempt["trial_dir"]).name


def test_the_manifest_is_the_frozen_one_and_the_run_ran_this_plan():
    manifest = RECORDS / "manifest.json"
    assert manifest.read_bytes() == (ROOT / "experiments/reporting-v1.json").read_bytes()
    runs = [line for line in lines() if line.get("kind") == "run"]
    assert runs, "the ledger has no run line"
    plan = json.loads((RECORDS / "plan.json").read_text())
    for run in runs:
        assert run["manifest_sha256"] == manifest_sha256(json.loads(manifest.read_text())) == plan["manifest_sha256"]
        assert run["plan_sha256"] == hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
        assert run["fsb_revision"]["dirty"] is False


def test_the_stop_line_is_the_ledgers_last_and_every_planned_episode_ran():
    stops = [line for line in lines() if line.get("kind") == "stop"]
    assert stops and json.loads((RECORDS / "stop.json").read_text()) == stops[-1]
    assert stops[-1]["reason"] == "complete"
    assert stops[-1]["admitted_calls"] == sum(a["gateway"]["admitted_calls"] for a in attempts())
    planned = {e["episode"] for e in json.loads((RECORDS / "plan.json").read_text())["episodes"]}
    assert {a["episode"] for a in attempts()} == planned


def test_every_receipt_and_evidence_file_hashes_as_admission_recorded():
    for attempt in attempts():
        budget = attempt["admission"]["budget"]
        assert sha256(RECORDS / "receipts" / Path(attempt["receipt"]).name) == budget["sha256"], attempt["job"]
        files = attempt["admission"]["files_sha256"]
        assert files, attempt["job"]
        for rel, digest in files.items():
            assert sha256(trial(attempt) / rel) == digest, f"{attempt['job']}: {rel}"


def test_published_trajectory_summaries_equal_their_derivation():
    for attempt in attempts():
        published = trial(attempt) / "trajectory_summary.json"
        assert published.is_file(), f"{attempt['job']}: no trajectory summary"
        # Derived from the raw files, not read back: summary_for_trial falls back to this file without them.
        assert trajectory_files(trial(attempt)), f"{attempt['job']}: summary published without its trajectories"
        assert json.loads(published.read_text()) == summary_for_trial(trial(attempt)), attempt["job"]
