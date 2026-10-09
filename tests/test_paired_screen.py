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
                          "--tag", "dry", "--rusty-binary", str(binary), "--swap", "--dry-run",
                          "--cohort", "development"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    plan = json.loads(out.stdout)
    assert plan["pins"]["key_slots"] == {"rusty": 2, "mini": 1} and plan["pins"]["mswea_version"] == "2.4.6"
    assert plan["jobs"] == {"rusty": "rusty-dry", "mini": "mini-dry"}
    rusty = plan["commands"]["rusty"]
    assert "max_requests=250" in rusty and "agents=off" in rusty and "memory=off" in rusty
    assert "execution=standard" in rusty and not any(o.startswith("verify") for o in rusty)
    assert plan["commands"]["mini"][plan["commands"]["mini"].index("-m") + 1] == "openai/nvidia/nemotron-3-super-120b-a12b"
    assert plan["admission"]["launchable"] and not plan["admission"]["admitted"]
    assert len(plan["task_digest"]) == 64
    assert not (ROOT / "runs" / "paired" / "dry").exists()


def test_verify_is_refused_until_the_adapter_consumes_it(tmp_path, monkeypatch):
    from fsbench.agents.rusty import Rusty
    assert not ps.rusty_supports("verify")  # the adapter does not take it yet; Harbor would drop it silently
    binary = tmp_path / "rusty"
    binary.write_bytes(b"elf")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "paired_screen.py"), "tasks/ship-checkout-v2",
                          "--tag", "dry3", "--rusty-binary", str(binary), "--dry-run",
                          "--cohort", "development", "--rusty-verify", "bash /work/check.sh"],
                         cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 2 and "SUPPORTED_OPTIONS" in out.stderr and out.stdout == ""
    monkeypatch.setattr(Rusty, "SUPPORTED_OPTIONS", ("verify", "verify_timeout"), raising=False)
    assert ps.rusty_supports("verify")
    args = type("A", (), {"rusty_binary": binary, "max_turns": 25, "calls": 250, "rusty_tokens": 1, "wall": 900,
                          "rusty_execution": "careful", "rusty_verify": "bash /work/check.sh",
                          "rusty_verify_timeout": 60, "model": ps.MODEL})
    cmd = ps.harbor_command("rusty", ROOT / "tasks" / "ship-checkout-v2", "j", tmp_path, args)
    assert 'verify="bash /work/check.sh"' in cmd and "verify_timeout=60" in cmd


    binary = tmp_path / "rusty"
    binary.write_bytes(b"elf")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "paired_screen.py"), "tasks/ship-checkout-v2",
                          "--tag", "dry2", "--rusty-binary", str(binary), "--dry-run", "--cohort", "selection"],
                         cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 2
    plan = json.loads(out.stdout)
    assert not plan["admission"]["launchable"]
    reasons = " ".join(plan["admission"]["reasons"])
    assert "qualification" in reasons and "isolation" in reasons and "harness rusty lacks a version" in reasons
    assert not (ROOT / "runs" / "paired" / "dry2").exists()


def test_terminal_record_is_written_even_when_harbor_never_started(tmp_path):
    manifest = {"cohort": "development", "task": {"digest": "a" * 64, "verifier_mode": "separate",
                                                  "outcome_checks": None, "harm_check": "test_no_incidents_caused"},
                "attempts": [{"id": "rusty-x", "harness": "rusty", "seed": 1}]}
    (tmp_path / "attempts").mkdir()
    record = ps.terminal_record(tmp_path, manifest, "rusty", "rusty-x", None, tmp_path / "gateway-rusty.json",
                                failure="Harbor could not start (FileNotFoundError)")
    assert record["status"] == "invalid_evidence" and record["reason"].startswith("Harbor could not start")
    assert json.loads((tmp_path / "attempts" / "rusty-x.json").read_text())["attempt"] == "rusty-x"


def test_required_tools_come_from_the_task(tmp_path):
    assert ps.required_tools(ROOT / "tasks" / "ship-checkout-v2") == ["mcp:simcloud"]
    assert ps.required_tools(tmp_path) == []
