"""Run submitted code as somebody else.

Every service instance, build and job SimCloud starts is submitted code: the agent wrote or
deployed it. The control plane is the operator. The two must not share an identity, an
environment or files, or the isolation receipt (`fsbench/isolation_gate.py`) fails and no trial
is admitted:

- When the control plane runs as root (the sidecar image), workloads run as the `workload`
  user through `setpriv`, with a workdir owned by that user, and Postgres runs as `simcloud`
  (initdb refuses root). Operator material (`state.db`, `pg.admin`, the OIDC and secrets keys,
  logs, artifacts, evidence) stays root-only.
- When it runs unprivileged (the test suite, an in-process development world) nothing can
  change identity; workloads run as the same user and the boundary is unproven, which the
  receipt says. Development runs never produce receipts.

The environment a workload gets is built from nothing: the platform variables, its own spec,
its secrets and DSNs (`Delivery.workload_env`) and a short pass-through list of toolchain
settings. The operator's environment, which holds the admin token, is never the base.
"""

from __future__ import annotations

import os
import pwd
import shutil
import stat
from pathlib import Path

WORKLOAD_USER = os.environ.get("SIMCLOUD_WORKLOAD_USER", "workload")
POSTGRES_USER = os.environ.get("SIMCLOUD_POSTGRES_USER", "simcloud")
# Toolchain settings a workload may inherit from the image. Nothing here is a secret.
PASS_THROUGH = ("PATH", "LANG", "LC_ALL", "TZ", "GOTOOLCHAIN", "GOFLAGS", "GOPROXY", "JAVA_HOME", "NODE_OPTIONS",
                "NODE_NO_WARNINGS", "UV_INDEX_URL", "PIP_INDEX_URL", "PIP_NO_CACHE_DIR", "PYTHONDONTWRITEBYTECODE",
                "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _lookup(name: str) -> tuple[int, int] | None:
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        return None
    return pw.pw_uid, pw.pw_gid


def can_separate() -> bool:
    """True when this process can hand work to another identity."""
    return os.geteuid() == 0 and shutil.which("setpriv") is not None


def identity(name: str = WORKLOAD_USER) -> tuple[int, int] | None:
    """(uid, gid) that submitted code will run as, or None when it will run as this process."""
    if not can_separate():
        return None
    return _lookup(name)


def as_user(cmd: list[str], name: str = WORKLOAD_USER) -> list[str]:
    """`cmd`, prefixed so that it runs as `name` when this process can arrange it."""
    ident = identity(name)
    if ident is None:
        return list(cmd)
    uid, gid = ident
    return ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--inh-caps=-all", "--no-new-privs",
            *cmd]


def workload_env(extra: dict[str, str], home: str | Path) -> dict[str, str]:
    """A workload's environment from scratch: pass-through toolchain settings, a home it owns,
    then what the platform gives it. Never the operator's environment."""
    env = {k: os.environ[k] for k in PASS_THROUGH if k in os.environ}
    env.setdefault("PATH", DEFAULT_PATH)
    env.setdefault("LANG", "C.UTF-8")
    env["HOME"] = str(home)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def give(path: Path, name: str = WORKLOAD_USER, root: Path | None = None) -> None:
    """Make `path` (recursively) the workload's: owned by it, with no access for others. The
    operator's directories between `root` and `path` become traversable (mode +x for others)
    but not listable, so the workload can reach its workdir and nothing else."""
    ident = identity(name)
    if ident is None:
        return
    uid, gid = ident
    if root is not None and Path(root) in Path(path).parents:
        parent = Path(path).parent
        while True:
            st = os.lstat(parent)
            if stat.S_ISLNK(st.st_mode):
                break
            if st.st_uid != uid:
                os.chmod(parent, (st.st_mode & 0o777) | 0o011)
            if parent == root or parent == parent.parent:
                break
            parent = parent.parent
    # Never follow a link: a workload could plant one pointing at operator material, and this
    # runs as root. Links are not given (`_unpack` refuses them anyway); whatever they point at
    # keeps its owner and mode.
    for root, dirs, files in os.walk(path, followlinks=False):
        os.lchown(root, uid, gid)
        os.chmod(root, 0o750)
        for f in files:
            p = os.path.join(root, f)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                continue
            os.lchown(p, uid, gid)
            os.chmod(p, 0o700 if st.st_mode & 0o111 else 0o600)
    os.chmod(path, 0o750)


def keep_private(path: Path) -> None:
    """Operator-only: nobody else reads or enters it. A no-op for a path that does not exist."""
    try:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except (FileNotFoundError, PermissionError):
        pass


def postgres_identity() -> tuple[int, int] | None:
    """Who Postgres runs as when this process is root: it refuses to run as root itself."""
    if not can_separate():
        return None
    return _lookup(POSTGRES_USER)
