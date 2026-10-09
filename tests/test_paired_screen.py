import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import paired_screen as ps  # noqa: E402


def test_keys_alternate_with_swap():
    assert ps.key_slots(False) == {"rusty": 1, "mini": 2}
    assert ps.key_slots(True) == {"rusty": 2, "mini": 1}


def test_track_env_carries_only_the_trial_token(monkeypatch):
    for k, v in {"NVIDIA_API_KEY": "real1", "NVIDIA_API_KEY_2": "real2", "OPENAI_API_KEY": "x",
                 "MSWEA_API_KEY": "y", "RUSTY_MODEL": "z", "NVIDIA_API_BASE": "u", "HOME": "/h"}.items():
        monkeypatch.setenv(k, v)
    rusty = ps.track_env("rusty", "tok", "http://gw/v1")
    mini = ps.track_env("mini", "tok", "http://gw/v1")
    for env in (rusty, mini):
        assert "real1" not in env.values() and "real2" not in env.values() and env["HOME"] == "/h"
        assert not {"MSWEA_API_KEY", "RUSTY_MODEL", "NVIDIA_API_BASE", "NVIDIA_API_KEY_2"} & env.keys()
    assert rusty["NVIDIA_API_KEY"] == "tok" and rusty["RUSTY_BASE_URL"] == "http://gw/v1"
    assert mini["OPENAI_API_KEY"] == "tok" and "NVIDIA_API_KEY" not in mini


def test_outcome_and_gateway_summary(tmp_path):
    trial = tmp_path / "job" / "ship__abc"
    (trial / "verifier").mkdir(parents=True)
    (trial / "verifier" / "reward.txt").write_text("1")
    (trial / "verifier" / "ctrf.json").write_text(json.dumps({"results": {"tests": [
        {"name": "test_outputs.py::test_a", "status": "passed"}, {"name": "test_outputs.py::test_b", "status": "failed"}]}}))
    (trial / "result.json").write_text(json.dumps({"exception_info": None,
                                                   "agent_result": {"metadata": {"goal_status": "done"}}}))
    got = ps.outcome(tmp_path / "job")
    assert got["reward"] == 1.0 and got["checks"] == {"test_a": "passed", "test_b": "failed"}
    assert got["exception"] is None and got["agent_metadata"] == {"goal_status": "done"}
    assert ps.outcome(tmp_path / "empty") == {"status": "no_trial"}
    receipt = tmp_path / "gw.json"
    receipt.write_text(json.dumps({"calls": 2, "input_charged": 10, "output_charged": 5, "exhausted": None,
                                   "usage_records": [{"status": "completed", "http_status": 200},
                                                     {"status": "upstream_error", "http_status": 429},
                                                     {"status": "completed", "http_status": 200}]}))
    assert ps.gateway_summary(receipt)["attempts"] == {"completed": 2, "upstream_error_429": 1}


def test_dry_run_pins_inputs_without_calling_anything(tmp_path):
    binary = tmp_path / "rusty"
    binary.write_bytes(b"elf")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "paired_screen.py"), "tasks/ship-checkout-v2",
                          "--tag", "dry", "--rusty-binary", str(binary), "--swap", "--dry-run"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    plan = json.loads(out.stdout)
    assert plan["pins"]["key_slots"] == {"rusty": 2, "mini": 1} and plan["pins"]["mswea_version"] == "2.4.6"
    assert plan["jobs"] == {"rusty": "rusty-dry", "mini": "mini-dry"}
    assert "max_requests=250" in plan["commands"]["rusty"] and "agents=off" in plan["commands"]["rusty"]
    assert plan["commands"]["mini"][plan["commands"]["mini"].index("-m") + 1] == "openai/nvidia/nemotron-3-super-120b-a12b"
    assert not (ROOT / "runs" / "paired" / "dry").exists()
