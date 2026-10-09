from pathlib import Path

from fsbench import author
from fsbench.bundle import parse


def test_plans_are_deterministic_and_consistent():
    for seed in range(40):
        s = author.plan(seed)
        assert s == author.plan(seed)
        areas = [s["primary_area"], *s["secondary_areas"]]
        if any("Tillpoint" in a for a in areas):
            assert "tillpoint" in s["vendors"]
        if any("Passkeep" in a for a in areas):
            assert "passkeep" in s["vendors"]
        assert len(set(areas)) == 3 and set(s["drifts"]) <= set(author.CATALOGUE)


def test_exemplars_leave_out_generated_files_but_keep_the_generator():
    files = parse(author._exemplar("stop-double-charges"))
    assert "build_world.py" in files and "environment/simcloud/seed.yaml" not in files
    assert "environment/repo/ordersvc/payclient.py" in files
    assert "environment/simcloud/seed.yaml" in parse(author._exemplar("ship-checkout-v2"))  # hand-written seed


def test_prompt_mentions_the_vendors_and_drifts():
    spec = {**author.plan(3), "vendors": ["tillpoint"], "drifts": ["drain-default"]}
    p = author.system_prompt(spec)
    assert "# Vendor skill: tillpoint" in p and "drain-default" in p and "=== FILE: instruction.md ===" in p


def test_build_world_runs_without_network(monkeypatch, tmp_path):
    seen = {}

    class R:
        returncode, stderr, stdout = 0, "", ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return R()
    monkeypatch.setattr(author.subprocess, "run", fake_run)
    (tmp_path / "build_world.py").write_text("print(1)")
    assert author.sandbox_build_world(tmp_path) is None
    cmd = seen["cmd"]
    assert cmd[:3] == ["docker", "run", "--rm"] and cmd[cmd.index("--network") + 1] == "none"


def test_world_boot_has_no_egress_reports_service_logs_and_cleans_up(monkeypatch, tmp_path):
    calls = []

    class R:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def fake_run(cmd, **kw):
        calls.append(cmd)
        override = cmd[cmd.index("-f", cmd.index("-f") + 1) + 1]
        assert "internal: true" in open(override).read()
        if "up" in cmd:
            return R(1, stderr="dependency failed to start")
        if "logs" in cmd:
            return R(stdout="SimCloudError: seed deployment retail/staging/inventory failed")
        if "cp" in cmd:
            dest = Path(cmd[-1]) / "retail" / "staging"
            dest.mkdir(parents=True)
            (dest / "inventory.log").write_text("ok\nNameError: name 'ctx' is not defined\n")
        return R()
    monkeypatch.setattr(author.subprocess, "run", fake_run)
    assert author.sandbox_boot_world(tmp_path) is None and not calls  # no compose file: nothing to boot
    (tmp_path / "environment").mkdir()
    (tmp_path / "environment" / "docker-compose.yaml").write_text("services: {}\n")
    err = author.sandbox_boot_world(tmp_path)
    assert "seed deployment retail/staging/inventory failed" in err
    assert "retail/staging/inventory.log" in err and "NameError" in err
    assert [c for c in calls if "up" in c][0][-1] == "simcloud"
    assert "down" in calls[-1] and "--volumes" in calls[-1]


def test_vendor_tasks_get_the_simulator_seed_format():
    with_vendor = author.system_prompt({**author.plan(3), "vendors": ["passkeep"], "drifts": []})
    assert "# SimSaaS seed format" in with_vendor and 'if "identity" in seed:' in with_vendor
    assert "# SimSaaS seed format" not in author.system_prompt({**author.plan(3), "vendors": [], "drifts": []})


def _trial(jobs, job, trial, stdout="", oracle=""):
    d = jobs / job / trial
    (d / "verifier").mkdir(parents=True)
    (d / "agent").mkdir()
    (d / "verifier" / "test-stdout.txt").write_text(stdout)
    (d / "agent" / "oracle.txt").write_text(oracle)


def test_gate_feedback_quotes_the_failing_assertions(tmp_path):
    _trial(tmp_path, "j1", "t1", "noise\nE   AssertionError: SKU-2002 reserved: 31 != 37\n", "deployed\nTraceback: boom\n")
    notes = author.gate_feedback([
        {"gate": "oracle", "want": 1.0, "reward": 0.0, "ok": False, "job": "j1", "trial": "t1",
         "failed": ["test_outputs.py::test_inventory_repaired"]},
        {"gate": "wrong:no_dedup", "want": 0.0, "reward": 1.0, "ok": False, "job": "j2", "trial": "t2", "failed": []},
        {"gate": "nop", "want": 0.0, "reward": 0.0, "ok": True},
    ], tmp_path)
    assert len(notes) == 2
    assert "not solvable" in notes[0] and "test_inventory_repaired" in notes[0]
    assert "reserved: 31 != 37" in notes[0] and "noise" not in notes[0] and "Traceback: boom" in notes[0]
    assert "wrong_solutions/no_dedup.sh scored 1.0" in notes[1]


def test_gates_run_the_oracle_alone_first(monkeypatch, tmp_path):
    calls = []

    class R:
        returncode, stderr = 1, ""
        stdout = ('{"gate": "oracle", "want": 1.0, "reward": 0.0, "ok": false, "job": "j", "trial": "t", '
                  '"failed": []}\nFAIL: 0/1 gates hold\n')

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return R()
    monkeypatch.setattr(author.subprocess, "run", fake_run)
    notes = author.gate_errors(tmp_path, tmp_path / "jobs")
    assert len(calls) == 1 and "--no-wrong" in calls[0] and "--no-nop" in calls[0]
    assert notes and "not solvable" in notes[0]
