"""The frozen reporting output re-renders from the published records with the M5 commands of
`docs/PAPER_PLAN.md`: the report and each comparison reproduce every committed value (a later key
may be added, none may change), and the family adjustment, the bootstrap and the mechanism table
reproduce byte for byte. A code change that would move a number the paper cites fails here."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPORTING = ROOT / "docs/results/reporting"
RECORDS = REPORTING / "records"

pytestmark = pytest.mark.skipif(not (RECORDS / "ledger.jsonl").is_file(), reason="no published reporting records")


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True, capture_output=True, timeout=600)


def changed(new, committed, path: str = "") -> list[str]:
    """Where `new` does not reproduce a committed value. Keys only `new` has are additions."""
    if isinstance(committed, dict):
        if not isinstance(new, dict):
            return [path or "/"]
        return [d for k, v in committed.items()
                for d in ([f"{path}/{k} (missing)"] if k not in new else changed(new[k], v, f"{path}/{k}"))]
    return [] if new == committed else [path or "/"]


def cohort(*extra: str) -> list[str]:
    return ["scripts/report_cohort.py", "--ledger", str(RECORDS / "ledger.jsonl"), str(RECORDS / "plan.json"),
            str(RECORDS / "manifest.json"), "--records", str(RECORDS), "--receipts", "receipts", *extra]


def test_the_frozen_reporting_output_rerenders_from_the_published_records(tmp_path):
    run(*cohort("--out", str(tmp_path / "report")))
    new = json.loads((tmp_path / "report/report.json").read_text())
    assert changed(new, json.loads((REPORTING / "report.json").read_text())) == []

    adjusted = json.loads((REPORTING / "pairs/adjusted.json").read_text())
    families = []
    for family, body in adjusted["families"].items():
        dirs = []
        for name in body["comparisons"]:
            left, right = name.split(" vs ")
            out = tmp_path / "pairs" / f"{left}-vs-{right}"
            run(*cohort("--pair", left, right, "--out", str(out)))
            committed = json.loads((REPORTING / "pairs" / out.name / "report.json").read_text())
            assert changed(json.loads((out / "report.json").read_text()), committed) == [], out.name
            dirs.append(str(out))
        families += ["--family", family, *dirs]
    run("scripts/adjust_pairs.py", *families, "--out", str(tmp_path / "adjusted.json"))
    assert (tmp_path / "adjusted.json").read_bytes() == (REPORTING / "pairs/adjusted.json").read_bytes()

    run("-m", "fsbench.analysis", str(RECORDS / "plan.json"), str(RECORDS / "ledger.jsonl"),
        "--manifest", str(RECORDS / "manifest.json"), "--json", str(tmp_path / "analysis.json"))
    assert (tmp_path / "analysis.json").read_bytes() == (REPORTING / "analysis.json").read_bytes()

    run("scripts/failure_mechanisms.py", str(RECORDS), "--tasks", "tasks", "--receipts", "receipts",
        "--out", str(tmp_path / "mechanisms"))
    for name in ("mechanisms.json", "mechanisms.md"):
        assert (tmp_path / "mechanisms" / name).read_bytes() == (REPORTING / name).read_bytes(), name


def test_changed_tells_an_addition_from_a_change():
    committed = {"a": 1, "b": {"c": [1, 2]}}
    assert changed({"a": 1, "b": {"c": [1, 2], "d": 0}, "e": 0}, committed) == []
    assert changed({"a": 2, "b": {"c": [1, 2]}}, committed) == ["/a"]
    assert changed({"a": 1, "b": {}}, committed) == ["/b/c (missing)"]
