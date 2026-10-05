"""Engineering-practice and style scores, computed in the verifier from the agent's final repo.

The outcome (`reward`) says whether the job got done. This module adds two more scores, each the
mean of deterministic sub-checks (1 pass, 0 fail, None not applicable):

practices
  existing_tests   the repo's own tests still pass (N/A if it had none)
  tests_added      the agent added or changed tests that pass on its code and fail on the original
                   code, so they actually test the change
  tests_kept       no test file or test function removed, no new skip/xfail
  committed        work is committed (clean tree, agent commits on top of the original history)
  commit_messages  every agent commit has a meaningful subject
  no_secrets       no credential material in anything the agent added
  in_scope         every changed path is inside the task's scope; no build junk committed

  protected_unchanged  nothing under the task's protected paths changed
  + [[check]] commands from quality.toml (e.g. generated files match their generators)

style (on files the agent changed, against the same files in the original repo)
  lint             no new lint findings (ruff for Python; any `file:line: msg` linter per language)
  format           no changed file newly unformatted (per-language formatter that lists files)
  complexity       no new too-complex functions
  naming           no new naming-convention findings
  diff_noise       no whitespace-only rewrites of lines the change didn't need
  hygiene          added lines: no trailing whitespace, no tabs in Python, files end with a newline

Configuration: tests/quality.toml (see `load_config`). The original repo is a pristine copy shipped
with the verifier (tests/base-repo), never the agent's own git history, which it could rewrite.
Agent code (its tests) runs in a scratch copy as an unprivileged user with a timeout.

This file is the canonical copy; scripts/sync_quality.py copies it into each task's tests/.
"""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache",
               "target", "dist", "build", ".tox"}
JUNK = ["*.pyc", "*.pyo", "*.log", ".DS_Store", "*.egg-info", "*.swp", "*.tmp", "core", "*.orig", "*.rej"]
JUNK_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".ruff_cache", "dist", "build", "target"}
SECRET_RE = re.compile(r"sct_[0-9a-f]{8}_[A-Za-z0-9_-]{16,}|nvapi-[A-Za-z0-9_-]{20,}|whsec_[A-Za-z0-9+/=]{16,}|"
                       r"tp_(?:live|test)_[A-Za-z0-9]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
                       r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}")
LAZY_SUBJECTS = {"wip", "fix", "fixes", "update", "updates", "changes", "commit", "test", "tests", "stuff", "misc",
                 "tmp", "temp", "done", "save", "asdf", "."}
PY_RULES = ["E", "F", "W", "B", "N", "C90", "UP"]
PY_IGNORE = ["E501", "W191", "E101"]  # line length and tabs belong to the formatter, not the reviewer
SKIP_MARKERS = re.compile(r"pytest\.mark\.(skip|xfail)|unittest\.skip|@skip\b|\.skip\(|xit\(|xdescribe\(|t\.Skip\(")


@dataclass
class Lang:
    name: str
    files: list[str]
    test_globs: list[str] = field(default_factory=list)
    test_cmd: str | None = None  # run in the repo root; {tests} is replaced by the test files
    lint_cmd: str | None = None  # prints findings as `file:line[:col]: message`; {files} = changed files
    format_cmd: str | None = None  # prints the paths of files that aren't formatted; {files} = changed files


@dataclass
class Check:
    name: str
    cmd: str  # run sandboxed in a copy of the agent's repo; exit 0 = pass
    timeout: int = 300


@dataclass
class Config:
    app: Path
    base: Path
    scope: list[str]
    langs: list[Lang]
    run_as: str | None = "nobody"
    test_timeout: int = 300
    protected: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    not_applicable: list[str] = field(default_factory=list)  # e.g. ["tests_added"] for a pure refactor


def load_config(path: Path) -> Config:
    d = tomllib.loads(path.read_text())
    root = path.parent
    return Config(app=Path(d.get("app", "/app")), base=(root / d.get("base", "base-repo")).resolve(),
                  scope=d.get("scope", ["**"]),
                  langs=[Lang(**l) for l in d.get("lang", [])], run_as=d.get("run_as", "nobody"),
                  test_timeout=d.get("test_timeout", 300), protected=d.get("protected", []),
                  checks=[Check(**c) for c in d.get("check", [])], not_applicable=d.get("not_applicable", []))


# ---- tree comparison ----------------------------------------------------------------------------

def files_of(root: Path) -> dict[str, Path]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for f in filenames:
            p = Path(dirpath) / f
            if p.is_file() and not p.is_symlink():
                out[str(p.relative_to(root))] = p
    return out


def all_paths(root: Path) -> list[str]:
    """Every path, junk included (for the in-scope / junk check)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            out.append(str((Path(dirpath) / f).relative_to(root)))
    return out


@dataclass
class Diff:
    added: list[str]
    modified: list[str]
    deleted: list[str]

    @property
    def changed(self) -> list[str]:
        return sorted(self.added + self.modified)


def diff_trees(base: Path, app: Path) -> Diff:
    b, a = files_of(base), files_of(app)
    added = sorted(set(a) - set(b))
    deleted = sorted(set(b) - set(a))
    modified = sorted(p for p in set(a) & set(b) if a[p].read_bytes() != b[p].read_bytes())
    return Diff(added, modified, deleted)


def matches(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) or (g.endswith("/**") and path.startswith(g[:-3] + "/")) for g in globs)


def lang_of(path: str, langs: list[Lang]) -> Lang | None:
    return next((l for l in langs if matches(path, l.files)), None)


# ---- git --------------------------------------------------------------------------------------

def _git(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=repo, capture_output=True, text=True)


def tree_hash(root: Path) -> str:
    """The git tree hash of a directory (without its .git), computed in a scratch repo."""
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "t"
        shutil.copytree(root, dst, ignore=shutil.ignore_patterns(".git"))
        _git(dst, "init", "-q")
        _git(dst, "add", "-A")
        return _git(dst, "write-tree").stdout.strip()


def agent_commits(app: Path, base_tree: str) -> list[dict] | None:
    """Commits made on top of the newest commit whose tree is the original tree; None if none is found
    (history rewritten or never a git repo)."""
    log = _git(app, "log", "--format=%H%x00%T%x00%s", "HEAD")
    if log.returncode:
        return None
    rows = [l.split("\x00") for l in log.stdout.splitlines() if l]
    for i, (_, tree, _) in enumerate(rows):
        if tree == base_tree:
            return [{"sha": s, "subject": subj} for s, _, subj in rows[:i]]
    return None


# ---- running agent code safely -----------------------------------------------------------------

def run_sandboxed(cmd: str, cwd: Path, timeout: int, run_as: str | None) -> subprocess.CompletedProcess | None:
    """Run in a scratch copy owned by an unprivileged user, with a minimal environment."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(cwd), "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1"}
    argv = ["sh", "-c", cmd]
    if run_as and os.geteuid() == 0 and shutil.which("setpriv"):
        import pwd
        pw = pwd.getpwnam(run_as)
        subprocess.run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", str(cwd)], check=False)
        argv = ["setpriv", f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}", "--clear-groups", *argv]
    try:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _scratch(src: Path) -> Path:
    d = Path(tempfile.mkdtemp(prefix="q-"))
    os.chmod(d, 0o755)
    dst = d / "repo"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", *IGNORE_DIRS))
    return dst


# ---- practices --------------------------------------------------------------------------------

def check_existing_tests(cfg: Config) -> tuple[int | None, str]:
    ran = []
    for lang in cfg.langs:
        if not lang.test_cmd:
            continue
        base_tests = [p for p in files_of(cfg.base) if matches(p, lang.test_globs)]
        if not base_tests:
            continue
        r = run_sandboxed(lang.test_cmd.replace("{tests}", " ".join(sorted(base_tests))), _scratch(cfg.app),
                          cfg.test_timeout, cfg.run_as)
        ran.append((lang.name, r is not None and r.returncode == 0))
    if not ran:
        return None, "the repo had no tests"
    ok = all(x for _, x in ran)
    return int(ok), ", ".join(f"{n}: {'pass' if x else 'fail'}" for n, x in ran)


def check_tests_added(cfg: Config, d: Diff) -> tuple[int, str]:
    notes = []
    for lang in cfg.langs:
        if not lang.test_cmd:
            continue
        tests = [p for p in d.changed if matches(p, lang.test_globs)]
        if not tests:
            continue
        cmd = lang.test_cmd.replace("{tests}", " ".join(tests))
        on_new = run_sandboxed(cmd, _scratch(cfg.app), cfg.test_timeout, cfg.run_as)
        old = _scratch(cfg.base)
        for t in tests:  # the agent's tests, against the original code
            (old / t).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(cfg.app / t, old / t)
        on_old = run_sandboxed(cmd, old, cfg.test_timeout, cfg.run_as)
        passes_new = on_new is not None and on_new.returncode == 0
        fails_old = on_old is None or on_old.returncode != 0
        notes.append(f"{lang.name}: {len(tests)} test file(s), new code {'pass' if passes_new else 'FAIL'}, "
                     f"original code {'fail' if fails_old else 'PASS (tests do not test the change)'}")
        if passes_new and fails_old:
            return 1, "; ".join(notes)
    return 0, "; ".join(notes) or "no tests added or changed"


def _py_test_names(text: str) -> set[str]:
    return set(re.findall(r"^\s*(?:async\s+)?def\s+(test\w*)", text, re.M))


def check_tests_kept(cfg: Config, d: Diff) -> tuple[int, str]:
    globs = [g for l in cfg.langs for g in l.test_globs]
    removed_files = [p for p in d.deleted if matches(p, globs)]
    problems = [f"deleted {p}" for p in removed_files]
    for p in d.modified:
        if not matches(p, globs):
            continue
        before, after = (cfg.base / p).read_text(errors="replace"), (cfg.app / p).read_text(errors="replace")
        gone = _py_test_names(before) - _py_test_names(after)
        if gone:
            problems.append(f"{p}: removed {sorted(gone)}")
        if len(SKIP_MARKERS.findall(after)) > len(SKIP_MARKERS.findall(before)):
            problems.append(f"{p}: new skip/xfail")
    for p in d.added:
        if matches(p, globs) and SKIP_MARKERS.search((cfg.app / p).read_text(errors="replace")):
            problems.append(f"{p}: new skip/xfail")
    return int(not problems), "; ".join(problems) or "ok"


def check_committed(cfg: Config, d: Diff, commits: list[dict] | None) -> tuple[int, str]:
    if commits is None:
        return 0, "the original history is gone (rewritten, or /app is not the repo)"
    dirty = _git(cfg.app, "status", "--porcelain", "--untracked-files=normal").stdout.strip()
    if d.changed or d.deleted:
        if not commits:
            return 0, "changes are not committed"
        if dirty:
            return 0, f"uncommitted changes left: {dirty.splitlines()[:5]}"
    return 1, f"{len(commits)} commit(s)"


def check_commit_messages(commits: list[dict] | None) -> tuple[int | None, str]:
    if not commits:
        return None, "no agent commits"
    bad = [c["subject"] for c in commits
           if len(c["subject"].strip()) < 12 or c["subject"].strip().lower().rstrip(".!") in LAZY_SUBJECTS]
    return int(not bad), f"lazy subjects: {bad}" if bad else "ok"


def check_no_secrets(cfg: Config, d: Diff) -> tuple[int, str]:
    hits = []
    for p in d.changed:
        text = (cfg.app / p).read_text(errors="replace")
        base_text = (cfg.base / p).read_text(errors="replace") if (cfg.base / p).exists() else ""
        new = set(SECRET_RE.findall(text)) - set(SECRET_RE.findall(base_text))
        if new:
            hits.append(p)
    return int(not hits), f"credential material in {hits}" if hits else "ok"


def check_in_scope(cfg: Config, d: Diff) -> tuple[int, str]:
    out = [p for p in d.changed + d.deleted if not matches(p, cfg.scope)]
    junk = [p for p in all_paths(cfg.app)
            if any(fnmatch.fnmatch(Path(p).name, j) for j in JUNK) or set(Path(p).parts) & JUNK_DIRS]
    tracked = set(_git(cfg.app, "ls-files").stdout.split())
    junk = [p for p in junk if p in tracked]  # untracked caches are fine; committed ones are not
    problems = [f"out of scope: {out[:8]}"] * bool(out) + [f"committed junk: {junk[:8]}"] * bool(junk)
    return int(not problems), "; ".join(problems) or "ok"


def check_protected(cfg: Config, d: Diff) -> tuple[int | None, str]:
    if not cfg.protected:
        return None, "no protected paths"
    touched = [p for p in d.changed + d.deleted if matches(p, cfg.protected)]
    return int(not touched), f"changed protected paths: {touched[:8]}" if touched else "ok"


def run_task_check(cfg: Config, c: Check) -> tuple[int, str]:
    r = run_sandboxed(c.cmd, _scratch(cfg.app), c.timeout, cfg.run_as)
    if r is None:
        return 0, f"timed out after {c.timeout}s"
    return int(r.returncode == 0), (r.stdout + r.stderr).strip()[-300:] or f"exit {r.returncode}"


# ---- style ------------------------------------------------------------------------------------

FINDING_RE = re.compile(r"^(?:\./)?(?P<file>[^\s:][^:]*?):(?P<line>\d+)(?::\d+)?:?\s*(?P<msg>.*)$")


def cmd_findings(root: Path, cmd: str, files: list[str], timeout: int = 300) -> list[tuple[str, str, str]] | None:
    """Findings from a `file:line: message` linter, keyed like ruff's (file, rule, source line)."""
    files = [f for f in files if (root / f).exists()]
    if not files:
        return []
    try:
        r = subprocess.run(["sh", "-c", cmd.replace("{files}", " ".join(files))], cwd=root, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    out = []
    for line in (r.stdout + r.stderr).splitlines():
        m = FINDING_RE.match(line.strip())
        if not m:
            continue
        rel = m["file"]
        p = root / rel
        src = ""
        if p.exists():
            lines = p.read_text(errors="replace").splitlines()
            i = int(m["line"]) - 1
            src = lines[i].strip() if 0 <= i < len(lines) else ""
        rule = re.sub(r"\d+", "N", m["msg"])[:120]
        out.append((rel, rule, src))
    return out


def unformatted(root: Path, cmd: str, files: list[str]) -> set[str] | None:
    files = [f for f in files if (root / f).exists()]
    if not files:
        return set()
    try:
        r = subprocess.run(["sh", "-c", cmd.replace("{files}", " ".join(files))], cwd=root, capture_output=True,
                           text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None
    listed = {l.strip().removeprefix("./") for l in r.stdout.splitlines() if l.strip()}
    return {f for f in files if f in listed}

def ruff_findings(root: Path, files: list[str]) -> list[tuple[str, str, str]]:
    """(file, rule, normalised source line) per finding, comparable across versions of a file."""
    files = [f for f in files if (root / f).exists()]
    if not files:
        return []
    r = subprocess.run(["ruff", "check", "--isolated", "--output-format", "json", "--select", ",".join(PY_RULES),
                        "--ignore", ",".join(PY_IGNORE), "--config", "lint.mccabe.max-complexity = 12", *files],
                       cwd=root, capture_output=True, text=True)
    try:
        items = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    out = []
    for it in items:
        rel = str(Path(it["filename"]).resolve().relative_to(root.resolve()))
        lines = (root / rel).read_text(errors="replace").splitlines()
        row = it["location"]["row"] - 1
        src = lines[row].strip() if 0 <= row < len(lines) else ""
        out.append((rel, it["code"] or "", src))
    return out


def _new_findings(cfg: Config, d: Diff, rule_filter) -> list[tuple[str, str, str]]:
    py = [p for p in d.changed if p.endswith(".py")]
    before = ruff_findings(cfg.base, [p for p in py if p in d.modified])
    after = ruff_findings(cfg.app, py)
    from collections import Counter
    b = Counter(x for x in before if rule_filter(x[1]))
    a = Counter(x for x in after if rule_filter(x[1]))
    return sorted((a - b).elements())


def style_lint(cfg, d):
    from collections import Counter
    new = _new_findings(cfg, d, lambda c: not c.startswith(("C90", "N")))
    for lang in cfg.langs:
        if not lang.lint_cmd:
            continue
        files = [p for p in d.changed if matches(p, lang.files)]
        if not files:
            continue
        before = cmd_findings(cfg.base, lang.lint_cmd, [p for p in files if p in d.modified])
        after = cmd_findings(cfg.app, lang.lint_cmd, files)
        if before is None or after is None:
            return 0, f"{lang.name} linter timed out"
        new += sorted((Counter(after) - Counter(before)).elements())
    return int(not new), f"{len(new)} new: {[f'{f}:{c[:40]}' for f, c, _ in new[:8]]}" if new else "ok"


def style_format(cfg, d):
    applicable, problems = False, []
    for lang in cfg.langs:
        if not lang.format_cmd:
            continue
        files = [p for p in d.changed if matches(p, lang.files)]
        if not files:
            continue
        applicable = True
        before = unformatted(cfg.base, lang.format_cmd, [p for p in files if p in d.modified])
        after = unformatted(cfg.app, lang.format_cmd, files)
        if before is None or after is None:
            return 0, f"{lang.name} formatter timed out"
        problems += sorted(after - before)  # files that were formatted (or new) and now aren't
    if not applicable:
        return None, "no formatter configured for the changed files"
    return int(not problems), f"newly unformatted: {problems[:8]}" if problems else "ok"


def style_complexity(cfg, d):
    new = _new_findings(cfg, d, lambda c: c.startswith("C90"))
    return int(not new), f"new too-complex: {[s[:60] for _, _, s in new[:5]]}" if new else "ok"


def style_naming(cfg, d):
    new = _new_findings(cfg, d, lambda c: c.startswith("N"))
    return int(not new), f"new naming findings: {[f'{f}:{c}' for f, c, _ in new[:8]]}" if new else "ok"


def style_diff_noise(cfg, d):
    import difflib
    noisy = []
    for p in d.modified:
        a = (cfg.base / p).read_text(errors="replace").splitlines()
        b = (cfg.app / p).read_text(errors="replace").splitlines()
        removed = [l for l in difflib.ndiff(a, b) if l.startswith("- ")]
        added = [l for l in difflib.ndiff(a, b) if l.startswith("+ ")]
        same = set(l[2:].strip() for l in removed) & set(l[2:].strip() for l in added)
        whitespace_only = sum(1 for l in removed if l[2:].strip() in same and l[2:].strip())
        if whitespace_only > 5 and whitespace_only > 0.2 * max(1, len(removed)):
            noisy.append(f"{p} ({whitespace_only} lines only re-indented/re-spaced)")
    return int(not noisy), "; ".join(noisy) or "ok"


def style_hygiene(cfg, d):
    import difflib
    problems = []
    for p in d.changed:
        path = cfg.app / p
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        base = (cfg.base / p).read_text(errors="replace").splitlines() if (cfg.base / p).exists() else []
        added = [l[2:] for l in difflib.ndiff(base, text.splitlines()) if l.startswith("+ ")]
        if any(l != l.rstrip() for l in added):
            problems.append(f"{p}: trailing whitespace")
        if p.endswith(".py") and any("\t" in l[: len(l) - len(l.lstrip())] for l in added):
            problems.append(f"{p}: tab indentation")
        if text and not text.endswith("\n"):
            problems.append(f"{p}: no final newline")
    return int(not problems), "; ".join(problems[:8]) or "ok"


# ---- scoring ----------------------------------------------------------------------------------

def score(cfg: Config, extra_practices: dict | None = None) -> dict:
    d = diff_trees(cfg.base, cfg.app)
    commits = agent_commits(cfg.app, tree_hash(cfg.base))
    practices = {
        "existing_tests": check_existing_tests(cfg),
        "tests_added": check_tests_added(cfg, d),
        "tests_kept": check_tests_kept(cfg, d),
        "committed": check_committed(cfg, d, commits),
        "commit_messages": check_commit_messages(commits),
        "no_secrets": check_no_secrets(cfg, d),
        "in_scope": check_in_scope(cfg, d),
        "protected_unchanged": check_protected(cfg, d),
    }
    for c in cfg.checks:
        practices[c.name] = run_task_check(cfg, c)
    for name in cfg.not_applicable:
        if name in practices:
            practices[name] = (None, "not applicable to this task")
    for name, fn in (extra_practices or {}).items():
        try:
            practices[name] = fn(cfg, d)
        except Exception as e:  # a broken task check counts against nobody
            practices[name] = (None, f"check error: {e!r}"[:200])
    style = {"lint": style_lint(cfg, d), "format": style_format(cfg, d), "complexity": style_complexity(cfg, d),
             "naming": style_naming(cfg, d), "diff_noise": style_diff_noise(cfg, d), "hygiene": style_hygiene(cfg, d)}
    if not (d.changed or d.deleted):
        style = {k: (None, "no code changed") for k in style}

    def mean(checks):
        vals = [v for v, _ in checks.values() if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0
    return {"practices": mean(practices), "style": mean(style),
            "checks": {"practices": {k: {"score": v, "detail": n} for k, (v, n) in practices.items()},
                       "style": {k: {"score": v, "detail": n} for k, (v, n) in style.items()}},
            "diff": {"added": d.added, "modified": d.modified, "deleted": d.deleted},
            "agent_commits": commits}


def reward_keys(result: dict) -> dict[str, float]:
    """Numeric keys for Harbor's reward.json: the two scores and every applicable sub-check."""
    out = {"practices": result["practices"], "style": result["style"]}
    for group in ("practices", "style"):
        for k, v in result["checks"][group].items():
            if v["score"] is not None:
                out[f"{group}.{k}"] = float(v["score"])
    return out


def main(argv: list[str]) -> int:
    cfg = load_config(Path(argv[1]))
    result = score(cfg)
    Path(argv[2]).write_text(json.dumps(result, indent=2))
    print(json.dumps({"practices": result["practices"], "style": result["style"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
