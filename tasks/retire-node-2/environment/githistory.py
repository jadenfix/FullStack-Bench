"""Builds the repo's git history at image build time from history/steps.yaml, then deletes itself.

Each step is one commit: {date, author, message, files: [path | path@N]}. `path` takes the file's
final content from the repo; `path@N` takes history/versions/N/path. Files keep their last
committed content until a later step changes them. A final step brings the tree to the repo as it
is, so the checked-out HEAD always matches what ships.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

repo, hist = Path(sys.argv[1]), Path(sys.argv[2])
final = Path(sys.argv[3])  # pristine copy of the repo's final tree
steps = yaml.safe_load((hist / "steps.yaml").read_text())


def git(*args, env=None):
    subprocess.run(["git", *args], cwd=repo, check=True, env={**os.environ, **(env or {})},
                   stdout=subprocess.DEVNULL)


for p in list(repo.iterdir()):
    shutil.rmtree(p) if p.is_dir() else p.unlink()
git("init", "-q", "-b", "main")
for step in steps:
    for f in step["files"]:
        path, _, ver = f.partition("@")
        src = (hist / "versions" / ver / path) if ver else (final / path)
        dst = repo / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    name, email = step["author"].rsplit(" <", 1)
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email.rstrip(">"), "GIT_AUTHOR_DATE": step["date"],
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email.rstrip(">"), "GIT_COMMITTER_DATE": step["date"]}
    git("add", "-A", env=env)
    git("commit", "-q", "--allow-empty", "-m", step["message"], env=env)
# anything left unmentioned arrives with the last step's author, so HEAD == the shipped tree
shutil.copytree(final, repo, dirs_exist_ok=True)
git("add", "-A")
r = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo)
if r.returncode:
    last = steps[-1]
    name, email = last["author"].rsplit(" <", 1)
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email.rstrip(">"), "GIT_AUTHOR_DATE": last["date"],
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email.rstrip(">"), "GIT_COMMITTER_DATE": last["date"]}
    git("commit", "-q", "-m", "tidy", env=env)
