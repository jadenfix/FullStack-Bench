"""Ordered edit list per merge-duplicate-contacts attempt, from the published (redacted) trajectories.

    python gen_edits.py <fsb checkout with fsbench> <records dir> <ledger> <out dir>
"""
import json
import re
import sys
from pathlib import Path

fsb, records, ledger, out = (Path(a) for a in sys.argv[1:5])
sys.path.insert(0, str(fsb))
from fsbench.trajectory import classify_call, tool_calls, trajectory_files  # noqa: E402

# Shell commands that may write files without a redirect, tee or sed -i (which classify_call catches).
MAYBE_WRITE = re.compile(r"write_text|write_bytes|open\([^)]*['\"][wax]\+?['\"]|\bgit\s+(apply|checkout|restore)\b|"
                         r"\bpatch\s|\bapply_patch\b|\bcp\s|\bmv\s|\brm\s|\bperl\s+-[a-z]*i|<<\s*['\"]?\w+")
FENCE = "````"
out.mkdir(parents=True, exist_ok=True)
rows = []
attempts = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
attempts = [a for a in attempts if a.get("kind") == "attempt" and a["task"] == "merge-duplicate-contacts"]
for a in sorted(attempts, key=lambda a: (a["track"], a["seed"], a["attempt"])):
    job = Path(a["trial_dir"]).parent.name
    trial = records / "evidence" / job / Path(a["trial_dir"]).name
    files = trajectory_files(trial)
    lines = [f"# {a['track']} / merge-duplicate-contacts / seed {a['seed']} / attempt {a['attempt']}", "",
             f"- Ledger status: `{a['status']}`; reward: {(a.get('rewards') or {}).get('reward')}",
             f"- Trajectory: " + ", ".join(f"`{f.relative_to(records.parent.parent.parent.parent)}`" for f in files),
             f"- Final data state: `jobs/{job}/{trial.name}/artifacts/evidence/` in the M4 run directory on the "
             "run host (not published).", "",
             "Every call `fsbench.trajectory.classify_call` files as an edit, plus every other shell call that "
             "may write a file (marked *possible write*), in call order. `#` is the call's index in the attempt.", ""]
    n_edit = n_maybe = n = 0
    for f in files:
        for call in tool_calls(json.loads(f.read_text())):
            n += 1
            phase, path, shell = classify_call(call["tool"], call["arguments"])
            command = str(call["arguments"].get("command") or call["arguments"].get("cmd") or "")
            kind = "edit" if phase == "edit" else ("possible write" if shell and MAYBE_WRITE.search(command) else None)
            if kind is None:
                continue
            n_edit += kind == "edit"
            n_maybe += kind == "possible write"
            status = " (failed)" if call["failed"] else ""
            lines += [f"## #{n} {kind}: `{call['tool']}`" + (f" → `{path}`" if path else "") + status, ""]
            body = command if shell else json.dumps(call["arguments"], indent=2, ensure_ascii=False)
            lines += [FENCE + ("bash" if shell else "json"), body.rstrip(), FENCE, ""]
    name = f"{a['track']}--s{a['seed']}--a{a['attempt']}.md"
    (out / name).write_text("\n".join(lines) + "\n")
    rows.append((name, a, files, n, n_edit, n_maybe))
readme = ["# merge-duplicate-contacts: edit lists per attempt", "",
          "For the post-freeze reviewer-acceptance read in section 7. Not for merge. One file per M4 attempt on "
          "`merge-duplicate-contacts`, built by reading each attempt's published trajectory (SimCloud tokens "
          "already redacted) with `fsbench.trajectory.tool_calls` and `classify_call`. Nothing is rescored.", "",
          "A call is listed when `classify_call` files it as an edit (an edit tool, or a shell redirect, `tee` or "
          "`sed -i` target), or when it is a shell call whose text may write a file without those (a heredoc, "
          "`cp`/`mv`/`rm`, `git apply/checkout/restore`, `patch`, a Python write): those are marked *possible "
          "write* and need reading. The final data state of each attempt is "
          "`jobs/<job>/<trial>/artifacts/evidence/*.json` in the run directory on the run host, not in the "
          "published records. Trajectory paths are those of branch `results/reporting-v1-trajectories`.", "",
          "| File | Track | Seed | Status | Reward | Calls | Edits | Possible writes | Trajectory |",
          "|---|---|---|---|---|---|---|---|---|"]
for name, a, files, n, e, m in rows:
    traj = ", ".join(f"`{f.relative_to(records.parent.parent.parent.parent)}`" for f in files)
    readme.append(f"| [{name}]({name}) | {a['track']} | {a['seed']} | {a['status']} | "
                  f"{(a.get('rewards') or {}).get('reward')} | {n} | {e} | {m} | {traj} |")
(out / "README.md").write_text("\n".join(readme) + "\n")
print(f"{len(rows)} attempts written")
for name, a, files, n, e, m in rows:
    print(f"{name:40s} calls {n:4d} edits {e:3d} possible {m:3d} reward {(a.get('rewards') or {}).get('reward')}")
