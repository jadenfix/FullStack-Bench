"""Lossless CTRF reporting keyed by complete pytest node IDs, including parameters."""

import json
import time
from pathlib import Path

import pytest

_records = {}
_started = None


def pytest_addoption(parser):
    parser.addoption("--fullstack-ctrf", default=None, help="lossless CTRF receipt path")


def pytest_sessionstart(session):
    global _started
    _records.clear()
    _started = time.time()


def pytest_collection_finish(session):
    for item in session.items:
        _records[item.nodeid] = {"name": item.nodeid, "status": "pending", "duration": 0,
                                 "file_path": item.nodeid.split("::")[0], "retries": 0}


def pytest_runtest_logreport(report):
    record = _records.setdefault(report.nodeid, {"name": report.nodeid, "status": "pending",
                                                "duration": 0, "retries": 0})
    record["duration"] += report.duration
    if report.failed:
        record["status"] = "failed"
        record["message"] = str(report.longrepr)
    elif report.skipped and record["status"] != "failed":
        record["status"] = "skipped"
    elif report.when == "call":
        record["call_passed"] = report.passed
    elif report.when == "teardown" and record.get("call_passed") and record["status"] == "pending":
        record["status"] = "passed"


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session):
    output = session.config.getoption("--fullstack-ctrf")
    if not output:
        return
    tests = [{k: v for k, v in r.items() if k != "call_passed"} for r in _records.values()]
    statuses = [r["status"] for r in tests]
    summary = {"tests": len(tests), **{s: statuses.count(s) for s in ("passed", "failed", "skipped", "pending")},
               "other": 0, "start": _started, "stop": time.time()}
    data = {"results": {"tool": {"name": "pytest", "version": pytest.__version__},
                        "summary": summary, "tests": tests}, "pytest_exit_status": int(session.exitstatus)}
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
