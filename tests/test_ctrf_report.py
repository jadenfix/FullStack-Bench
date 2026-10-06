import json
import os
import subprocess
import sys
from pathlib import Path


def run_report(tmp_path, code):
    (tmp_path / "test_cases.py").write_text(code)
    output = tmp_path / "ctrf.json"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    process = subprocess.run([sys.executable, "-m", "pytest", "-p", "fsbench.ctrf_report",
                              "--fullstack-ctrf", str(output), "-q", "test_cases.py"],
                             cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    return process, json.loads(output.read_text())


def test_parameter_failure_cannot_be_overwritten_by_later_success(tmp_path):
    process, data = run_report(tmp_path, 'import pytest\n@pytest.mark.parametrize("n", [0,1,2])\n'
                              'def test_case(n):\n    assert n != 0\n')
    assert process.returncode == 1
    assert data["results"]["summary"]["tests"] == 3
    assert data["results"]["summary"]["failed"] == 1
    assert data["results"]["summary"]["passed"] == 2
    assert [t["name"] for t in data["results"]["tests"]] == [f"test_cases.py::test_case[{n}]" for n in range(3)]


def test_teardown_failure_overrides_a_successful_call(tmp_path):
    process, data = run_report(tmp_path, 'import pytest\n@pytest.fixture\ndef broken():\n'
                              '    yield\n    assert False\ndef test_case(broken):\n    assert True\n')
    assert process.returncode == 1
    assert data["results"]["summary"]["failed"] == 1
    assert data["results"]["summary"]["passed"] == 0


def test_skips_and_collection_errors_remain_visible(tmp_path):
    process, data = run_report(tmp_path, 'import pytest\n@pytest.mark.skip\ndef test_case():\n    pass\n')
    assert process.returncode == 0 and data["results"]["summary"]["skipped"] == 1
    process, data = run_report(tmp_path, 'def broken(:\n')
    assert process.returncode == 2 and data["pytest_exit_status"] == 2
    assert data["results"]["summary"]["tests"] == 0
