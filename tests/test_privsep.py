"""Submitted code never inherits the operator's identity or environment (simcloud/privsep.py)."""

import os
import stat
from pathlib import Path

import pytest

from simcloud import privsep


def test_workload_env_starts_from_nothing(monkeypatch):
    monkeypatch.setenv("SIMCLOUD_ADMIN_TOKEN", "op-secret")
    monkeypatch.setenv("SIMSAAS_ADMIN_TOKEN", "pay-secret")
    monkeypatch.setenv("GOTOOLCHAIN", "local")
    env = privsep.workload_env({"PORT": 8080, "SIMCLOUD_TOKEN": "sa-token"}, home="/work")
    assert "SIMCLOUD_ADMIN_TOKEN" not in env and "SIMSAAS_ADMIN_TOKEN" not in env
    assert env["GOTOOLCHAIN"] == "local" and env["HOME"] == "/work" and env["PORT"] == "8080"
    assert env["SIMCLOUD_TOKEN"] == "sa-token" and env["PATH"]


def test_pass_through_names_hold_no_secret():
    assert not any("TOKEN" in k or "KEY" in k or "PASSWORD" in k for k in privsep.PASS_THROUGH)


def test_unprivileged_process_runs_workloads_as_itself(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert privsep.identity() is None
    assert privsep.as_user(["python", "app.py"]) == ["python", "app.py"]


def test_root_prefixes_setpriv_for_the_workload_user(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(privsep.shutil, "which", lambda name: "/usr/bin/setpriv")
    monkeypatch.setattr(privsep, "_lookup", lambda name: (10100, 10100) if name == "workload" else None)
    argv = privsep.as_user(["python", "app.py"])
    assert argv[0] == "setpriv" and "--reuid=10100" in argv and "--no-new-privs" in argv and argv[-2:] == ["python", "app.py"]
    assert privsep.as_user(["postgres"], "nobody-here") == ["postgres"]


def test_root_without_the_user_runs_nothing_as_root_silently(monkeypatch):
    """A missing workload user is the dev-mode fallback, not a silent root workload: the
    identity is None, and the receipt will say the boundary is unproven."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(privsep.shutil, "which", lambda name: "/usr/bin/setpriv")
    monkeypatch.setattr(privsep, "_lookup", lambda name: None)
    assert privsep.identity() is None


@pytest.mark.skipif(os.geteuid() != 0 or privsep.identity() is None, reason="needs root and the workload user")
def test_give_hands_a_workdir_to_the_workload(tmp_path):
    d = tmp_path / "rel"
    (d / "pkg").mkdir(parents=True)
    (d / "pkg" / "app.py").write_text("print('hi')\n")
    (d / "run.sh").write_text("#!/bin/sh\n")
    (d / "run.sh").chmod(0o755)
    privsep.give(d)
    uid, gid = privsep.identity()
    assert all(p.stat().st_uid == uid for p in [d, d / "pkg", d / "pkg" / "app.py", d / "run.sh"])
    assert stat.S_IMODE((d / "pkg" / "app.py").stat().st_mode) == 0o600
    assert stat.S_IMODE((d / "run.sh").stat().st_mode) == 0o700
    assert stat.S_IMODE(d.stat().st_mode) == 0o750


def test_keep_private_tolerates_missing_paths(tmp_path):
    privsep.keep_private(tmp_path / "nope")
    f = tmp_path / "pg.admin"
    f.write_text("x")
    privsep.keep_private(f)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    privsep.keep_private(tmp_path)
    assert stat.S_IMODE(Path(tmp_path).stat().st_mode) == 0o700
