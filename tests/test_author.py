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
