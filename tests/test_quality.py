import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fsbench import quality as q

VENV_BIN = str(Path(sys.executable).parent)
APP = '''import time


def wait_for(check, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return False


def total(items):
    return sum(i["n"] for i in items)
'''
TEST = '''from app import total


def test_total():
    assert total([{"n": 1}, {"n": 2}]) == 3
'''


def git(repo, *args, date="2026-01-01T00:00:00Z"):
    env = {**os.environ, "GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "A",
           "GIT_COMMITTER_EMAIL": "a@x", "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", *args], cwd=repo, check=True, env=env, capture_output=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", VENV_BIN + os.pathsep + os.environ["PATH"])
    base = tmp_path / "base"
    (base / "tests").mkdir(parents=True)
    (base / "app.py").write_text(APP)
    (base / "tests" / "test_app.py").write_text(TEST)
    (base / "conftest.py").write_text("import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n")
    app = tmp_path / "app"
    shutil.copytree(base, app)
    git(app, "init", "-q", "-b", "main")
    git(app, "add", "-A")
    git(app, "commit", "-qm", "initial import")
    cfg = q.Config(app=app, base=base, scope=["app.py", "tests/**", "conftest.py"], run_as=None,
                   langs=[q.Lang(name="python", files=["**/*.py", "*.py"], test_globs=["tests/test_*.py"],
                                 test_cmd=f"{sys.executable} -m pytest -q -p no:cacheprovider {{tests}}")])
    return cfg


def commit(cfg, msg):
    git(cfg.app, "add", "-A")
    git(cfg.app, "commit", "-qm", msg, date="2026-02-01T00:00:00Z")


def test_untouched_repo_scores_nothing_applicable_but_is_clean(world):
    r = q.score(world)
    c = r["checks"]["practices"]
    assert c["existing_tests"]["score"] == 1 and c["tests_added"]["score"] == 0
    assert r["checks"]["style"]["lint"]["score"] is None


def test_a_good_change(world):
    (world.app / "app.py").write_text(APP.replace('return sum(i["n"] for i in items)',
                                                  'return sum(i.get("n", 0) for i in items)'))
    (world.app / "tests" / "test_app.py").write_text(TEST + '''

def test_total_skips_items_without_n():
    assert total([{"n": 1}, {}]) == 1
''')
    commit(world, "total: treat items without n as zero")
    r = q.score(world)
    assert r["practices"] == 1.0, r["checks"]["practices"]
    assert r["style"] == 1.0, r["checks"]["style"]
    keys = q.reward_keys(r)
    assert keys["practices.tests_added"] == 1.0 and "practices.commit_messages" in keys


def test_a_sloppy_change(world):
    text = APP.replace('return sum(i["n"] for i in items)', 'return sum(i.get("n", 0) for i in items)   ')
    text += "\nimport os\n\ndef BadName(a):\n    l = 1\n    return a\n"
    (world.app / "app.py").write_text(text)
    (world.app / "tests" / "test_app.py").write_text(
        "import pytest\nfrom app import total\n\n\n@pytest.mark.skip\ndef test_total():\n    assert total([]) == 0\n")
    (world.app / "tabs.py").write_text("def g():\n\treturn 1\n")  # tab indentation
    (world.app / "notes.log").write_text("debug\n")
    (world.app / "secrets.py").write_text("TOKEN = 'sct_0123abcd_" + "A" * 24 + "'\n")
    commit(world, "wip")
    (world.app / "scratch.py").write_text("x = 1\n")  # left uncommitted
    r = q.score(world)
    p, s = r["checks"]["practices"], r["checks"]["style"]
    assert p["tests_kept"]["score"] == 0 and p["commit_messages"]["score"] == 0
    assert p["no_secrets"]["score"] == 0 and p["in_scope"]["score"] == 0 and p["committed"]["score"] == 0
    assert s["lint"]["score"] == 0 and s["naming"]["score"] == 0 and s["hygiene"]["score"] == 0
    assert r["practices"] < 0.5 and r["style"] < 0.5


def test_tests_that_do_not_test_the_change_do_not_count(world):
    (world.app / "app.py").write_text(APP.replace("timeout=5", "timeout=10"))
    (world.app / "tests" / "test_more.py").write_text(
        "from app import total\n\n\ndef test_empty():\n    assert total([]) == 0\n")
    commit(world, "wait_for: longer default timeout")
    c = q.score(world)["checks"]["practices"]["tests_added"]
    assert c["score"] == 0 and "do not test the change" in c["detail"]


def test_rewritten_history_is_not_committed_work(world):
    (world.app / "app.py").write_text(APP + "\n\ndef more():\n    return 1\n")
    shutil.rmtree(world.app / ".git")
    git(world.app, "init", "-q", "-b", "main")
    git(world.app, "add", "-A")
    git(world.app, "commit", "-qm", "squashed everything into one commit")
    c = q.score(world)["checks"]["practices"]["committed"]
    assert c["score"] == 0 and "history" in c["detail"]


def test_mass_reformat_is_noise(world):
    reformatted = "\n".join(("    " + l if l.startswith("    ") else l) for l in APP.splitlines()) + "\n"
    (world.app / "app.py").write_text(reformatted)
    commit(world, "reformat app.py to 8-space indents")
    assert q.score(world)["checks"]["style"]["diff_noise"]["score"] == 0


def test_config_round_trip(tmp_path):
    (tmp_path / "quality.toml").write_text('''
app = "/app"
scope = ["pulse/**"]
[[lang]]
name = "python"
files = ["**/*.py"]
test_globs = ["tests/test_*.py"]
test_cmd = "python -m pytest -q {tests}"
''')
    cfg = q.load_config(tmp_path / "quality.toml")
    assert cfg.base == (tmp_path / "base-repo").resolve() and cfg.langs[0].test_cmd.endswith("{tests}")


def test_generic_linter_formatter_protected_and_task_checks(world):
    lint = "grep -Hn 'TODO' {files} | sed 's/^\\([^:]*:[0-9]*\\):/\\1: todo-left /' || true"
    fmt = "for f in {files}; do grep -q '  $' $f && echo $f; done; true"
    world.langs.append(q.Lang(name="text", files=["*.txt"], lint_cmd=lint, format_cmd=fmt))
    world.protected = ["frozen/**"]
    world.checks = [q.Check(name="gen_matches", cmd="test -f generated.txt")]
    world.scope.append("*.txt")
    (world.base / "notes.txt").write_text("clean\n")
    shutil.copy(world.base / "notes.txt", world.app / "notes.txt")
    git(world.app, "add", "-A")
    git(world.app, "commit", "-qm", "add notes for the release")
    (world.app / "notes.txt").write_text("clean\nTODO finish  \n")
    commit(world, "notes: start the release checklist")
    r = q.score(world)
    s, p = r["checks"]["style"], r["checks"]["practices"]
    assert s["lint"]["score"] == 0 and "todo-left" in s["lint"]["detail"]
    assert s["format"]["score"] == 0 and p["gen_matches"]["score"] == 0
    assert p["protected_unchanged"]["score"] == 1
    world.not_applicable = ["tests_added"]
    assert q.score(world)["checks"]["practices"]["tests_added"]["score"] is None


def test_deployed_check(tmp_path):
    import io
    import tarfile
    from fsbench import deployed_check as dc
    repo = tmp_path / "repo"
    (repo / "svc" / "pkg").mkdir(parents=True)
    (repo / "svc" / "main.py").write_text("print(1)\n")
    (repo / "svc" / "pkg" / "util.py").write_text("X = 1\n")
    (repo / "svc" / ".forge").mkdir()
    (repo / "svc" / ".forge" / "ci.yaml").write_text("test: true\n")
    (repo / "svc" / ".gitignore").write_text(".build/\n")
    (repo / "README.md").write_text("hi\n")
    dep = tmp_path / "deployed"
    dep.mkdir()

    def archive(name, files):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for n, data in files.items():
                ti = tarfile.TarInfo(n)
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
        (dep / f"{name}.tar.gz").write_bytes(buf.getvalue())
    archive("svc", {"./main.py": b"print(1)\n", "pkg/util.py": b"X = 1\n", "__pycache__/x.pyc": b"junk",
                    "./.forge/ci.yaml": b"test: true\n", ".gitignore": b".build/\n",
                    "./.build/pydeps/pkg.py": b"ignored build output"})
    assert dc.main(["x", str(dep), str(repo)]) == 0
    assert ".gitignore" in dc.archive_files(dep / "svc.tar.gz")
    (repo / "svc" / ".forge" / "ci.yaml").write_text("test: false\n")
    assert dc.main(["x", str(dep), str(repo)]) == 1
    archive("svc", {"main.py": b"print(2)\n", "pkg/util.py": b"X = 1\n"})  # hot-fixed in prod, not committed
    assert dc.main(["x", str(dep), str(repo)]) == 1


@pytest.mark.parametrize("name", ["../outside.py", "./../outside.py", "/outside.py"])
def test_deployed_archive_rejects_paths_outside_the_repo(tmp_path, name):
    import io
    import tarfile
    from fsbench import deployed_check as dc
    archive = tmp_path / "svc.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        entry = tarfile.TarInfo(name)
        entry.size = 1
        tar.addfile(entry, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="unsafe deployment archive path"):
        dc.archive_files(archive)


@pytest.mark.parametrize("directory,filename", [("__pycache__", "app.cpython-312.pyc"), (".build", "dependency.py")])
def test_committed_junk_is_caught_even_when_the_artifact_dropped_it(world, directory, filename):
    (world.app / "app.py").write_text(APP.replace("timeout=5", "timeout=6"))
    (world.app / directory).mkdir()
    (world.app / directory / filename).write_bytes(b"\x00")
    commit(world, "wait_for: slightly longer default timeout")
    shutil.rmtree(world.app / directory)  # what Harbor's artifact exclude does
    c = q.score(world)["checks"]["practices"]
    assert c["committed"]["score"] == 1, c["committed"]
    assert c["in_scope"]["score"] == 0 and directory in c["in_scope"]["detail"]


def test_untracked_build_dependencies_do_not_change_source_scope(world):
    (world.app / ".gitignore").write_text(".build/\n")
    git(world.app, "add", ".gitignore")
    git(world.app, "commit", "-qm", "ignore local build dependencies")
    (world.app / ".build" / "pydeps").mkdir(parents=True)
    (world.app / ".build" / "pydeps" / "dependency.py").write_text("VALUE = 1\n")
    d = q.diff_trees(world.base, world.app)
    assert not any(p.startswith(".build/") for p in d.changed)
    assert q.check_in_scope(world, q.Diff([], [], []))[0] == 1
    git(world.app, "add", "-f", ".build")
    assert q.check_in_scope(world, q.Diff([], [], []))[0] == 0
