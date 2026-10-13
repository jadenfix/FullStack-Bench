"""Render the paper's Appendix B and check section 6.1's protocol values against their sources.

    uv run python scripts/render_receipts.py --rusty-binary <pins/rusty> \
        --rusty-receipt <rusty>/docs/receipts/fsb-reporting-v1.md --rusty-src <rusty checkout at the pin> [--write]

Every value comes from a file or a command, never from memory: `experiments/reporting-v1.json`,
the plan `fsbench.experiment.plan` makes from it, `receipts/`, the pinned Rusty binary, Rusty's
receipt for this configuration, and Rusty's source at the pinned commit (for its context
handling). Before rendering, the sources are checked against each other: the binary's sha256
against the manifest, its `--capabilities` against the receipt's, the mini-swe-agent config
against its digest, each base pin against `receipts/README.md`, and per task the manifest
checksum, `task_checksum()` and every isolation receipt's digest and pins, every gate and every
probe. Without `--write` it compares the rendering with `docs/paper/appendix-b-receipts.md` and
looks for each 6.1 value in that section, exiting 1 on any difference. No model is called and
no file outside the repository is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsbench.experiment import plan as make_plan  # noqa: E402 -- bootstrap import for standalone execution
from fsbench.isolation_gate import FORBIDDEN_CAPABILITIES  # noqa: E402
from fsbench.runner import manifest_sha256, task_checksum  # noqa: E402

MANIFEST = ROOT / "experiments/reporting-v1.json"
APPENDIX = ROOT / "docs/paper/appendix-b-receipts.md"
SECTION = ROOT / "docs/paper/06-experiments.md"
WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()


def context_handling(src: Path) -> dict:
    """Rusty's context handling as its pinned source states it: the default window, the reply
    reserve, the two history thresholds and the characters-per-token estimate."""
    ctx = (src / "src/context.rs").read_text()
    agent = (src / "src/agent.rs").read_text()
    window = re.search(r'RUSTY_CONTEXT_TOKENS"\)[^;]*unwrap_or\(([\d_]+)\)', ctx)
    reserve = re.search(r"pub const REPLY_RESERVE: usize = ([\d_]+);", ctx)
    per_token = re.search(r"\n    chars / (\d+)\n", ctx)
    thresholds = re.findall(r"estimate\(&self\.history\) > budget \* (\d+) / 10", agent)
    if not (window and reserve and per_token and len(thresholds) == 2):
        raise SystemExit("Rusty's context handling is not where this script expects it")
    return {"window": int(window.group(1).replace("_", "")), "reserve": int(reserve.group(1).replace("_", "")),
            "per_token": int(per_token.group(1)), "elide": int(thresholds[0]) * 10, "compact": int(thresholds[1]) * 10}


def short(h: str, n: int = 12) -> str:
    return h.split(":")[-1][:n]


def render(rusty_binary: Path, rusty_receipt: Path, rusty_src: Path) -> tuple[str, list[str]]:
    m = json.loads(MANIFEST.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        plan = make_plan(m, ROOT / "tasks", Path(tmp))
    assert plan["manifest_sha256"] == manifest_sha256(m)
    file_sha = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    freeze = git("log", "-1", "--format=%H", "--diff-filter=A", "--", str(MANIFEST.relative_to(ROOT)))
    # The earliest merge on the path from the freeze commit is the one that brought it in.
    merges = git("log", "--reverse", "--format=%H", "--merges", "--ancestry-path", f"{freeze}..HEAD").splitlines()[:1]
    frozen = (merges[0] if merges else freeze)[:7]

    tracks = {t["name"]: t for t in m["tracks"]}
    rusty = [t for t in m["tracks"] if t["harness"] == "rusty"]
    mini = [t for t in m["tracks"] if t["harness"] != "rusty"]
    assert len({t["binary_sha256"] for t in rusty}) == 1 and len({t["version"] for t in rusty}) == 1
    assert len({t["config_sha256"] for t in mini}) == 1 and len({t["version"] for t in mini}) == 1
    assert hashlib.sha256(rusty_binary.read_bytes()).hexdigest() == rusty[0]["binary_sha256"], "binary differs from the pin"
    cfg = ROOT / mini[0]["config_file"]
    assert hashlib.sha256(cfg.read_bytes()).hexdigest() == mini[0]["config_sha256"], "mini config differs from the pin"
    caps = json.loads(subprocess.run([str(rusty_binary), "--capabilities"], capture_output=True, text=True,
                                     check=True).stdout)
    receipt = rusty_receipt.read_text()
    receipt_caps = re.search(r"## `rusty --capabilities`\s*```json\n(.*?)```", receipt, re.S).group(1)
    assert json.loads(receipt_caps) == caps, "--capabilities differs from Rusty's receipt"
    assert m["harness_frozen_at"] in receipt, "Rusty's receipt names another commit"
    qa = re.search(r"\| QA \| (.*?) \|", receipt).group(1)
    toolchain = re.search(r"\| Toolchain \| (.*?) \|", receipt).group(1)
    ctx = context_handling(rusty_src)
    # The cohort ran the adapter at the evaluator commit; later adapters pin the window (#47).
    adapter = git("show", f"{m['evaluator_frozen_at']}:fsbench/agents/rusty.py")
    assert "RUSTY_CONTEXT_TOKENS" not in adapter, "the adapter at the evaluator commit sets the window"

    env, rt, inf = m["envelope"], m["runtime"], m["inference"]
    episodes = plan["episodes"]
    slots = Counter(e["key_slot"] for e in episodes)
    config_of = {rootfs: config for _, rootfs, config in
                 re.findall(r"\| `([^`]+)` \| `(sha256:[0-9a-f]{64})` \| `(sha256:[0-9a-f]{64})` \|",
                            (ROOT / "receipts/README.md").read_text())}
    assert all(rootfs in config_of for rootfs in m["base_images"].values()), "a base pin is not in receipts/README.md"

    b = ["# Appendix B. Receipts (Lane A)", "",
         "Every value below is read from a file in this repository or from the pinned binary by "
         "`scripts/render_receipts.py`, which checks them against each other before writing (the run binary against the manifest's "
         "digest, `--capabilities` against Rusty's receipt, the mini-swe-agent config against its digest, "
         "each base pin against `receipts/README.md`). Full digests are in the files named.", "",
         "## B.1 Frozen configuration", "",
         "| Item | Value | Source |", "|---|---|---|",
         f"| Reporting manifest | `experiments/reporting-v1.json`, added in `{freeze[:7]}`"
         + (f" (merged as `{merges[0][:7]}`)" if merges else "") + f"; file sha256 `{file_sha[:16]}…`; "
         f"canonical hash in the plan `{plan['manifest_sha256'][:16]}…` | the file; the plan |",
         f"| Evaluator commit | `{m['evaluator_frozen_at']}` | `evaluator_frozen_at` |",
         f"| Harness commit (Rusty) | `{m['harness_frozen_at']}` | `harness_frozen_at` |",
         f"| Model | `{m['model']['id']}`, revision `{m['model']['revision']}` (the endpoint's model listing "
         "carries no version field) | `model` |",
         f"| Inference | temperature {inf['temperature']}, top_p {inf['top_p']}, at most {inf['max_reply']} reply "
         f"tokens, reasoning effort {'unset' if inf['reasoning_effort'] is None else inf['reasoning_effort']} "
         "| `inference` |",
         f"| Envelope per episode | {env['calls']} calls, {env['input_tokens']:,} input and {env['output_tokens']:,} "
         f"output tokens, {env['wall_seconds']} s | `envelope` |",
         f"| Runtime per trial | {rt['cpus_limit']} CPUs and {rt['memory_limit_mb']} MB (reserved = limit), "
         f"{rt['max_concurrent_trials']} concurrent, key slots {rt['key_slots']}, {rt['cache']} layer cache, "
         f"{rt['ordering']} order (seed {rt['order_seed']}) | `runtime` |",
         "", "## B.2 Base images", "",
         "The two `fullstack-bench` images are built from the evaluator commit and tagged `:dev`; the verifiers "
         "build from `python:3.12-slim`. Pinned by rootfs-layers-v1 identity "
         "(`fsbench.isolation_gate.rootfs_identity`); config IDs for traceability.", "",
         "| Image | rootfs-layers-v1 | Config ID |", "|---|---|---|"]
    b += [f"| `{name}` | `{short(rootfs, 16)}…` | `{short(config_of[rootfs], 16)}…` |"
          for name, rootfs in m["base_images"].items()]
    b += ["", "## B.3 Tasks", "",
          "Gate receipts (`receipts/<task>/gate.json`, from `scripts/gate_task.py`) run the reference solution "
          "ten times, the empty solution three times, every wrong solution once and an independently written "
          "solution once, on the task digest and base images above; `fsbench.admission.assess_qualification` "
          "passes each at reporting level. The ten reference runs are also the verifier-stability check: the same "
          "digest scored 1 every time, and the empty solution 0 every time. Isolation receipts "
          "(`isolation-m1.json` at M1, `isolation.json` as admitted, `isolation-refresh.json` from the run "
          "checkout just before the freeze) probe every execution surface and pass "
          "`fsbench.isolation_gate.assess_isolation` against the manifest's pins.", "",
          "| Task | Digest | Reference | Empty | Wrong (each fails) | Independent | Outcome checks | Surfaces "
          "| simcloud pin | verifier pin |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for t in m["tasks"]:
        name, rd = t["name"], ROOT / "receipts" / t["name"]
        gates = json.loads((rd / "gate.json").read_text())
        isolations = [json.loads(p.read_text()) for p in sorted(rd.glob("isolation*.json"))]
        iso = json.loads((rd / "isolation.json").read_text())
        assert t["checksum"] == task_checksum(ROOT / "tasks" / name), f"{name}: digest differs"
        assert all(r["task_digest"] == t["checksum"] for r in isolations), f"{name}: a receipt names another digest"
        assert iso["images"] == t["isolation_images"], f"{name}: isolation.json pins differ from the manifest"
        assert all(g["ok"] for g in gates), f"{name}: a gate failed"
        assert all(p["executed"] is True and p["exit"] == 0
                   and all(p["observations"][c] is False for c in FORBIDDEN_CAPABILITIES)
                   for r in isolations for p in r["probes"]), f"{name}: a probe failed"
        counts = Counter(g["gate"].split(":")[0] for g in gates)
        wrong = sorted(g["gate"].split(":", 1)[1] for g in gates if g["gate"].startswith("wrong:"))
        outcome = {g["outcome_count"] for g in gates}
        assert len(outcome) == 1, f"{name}: gates disagree on the outcome checks"
        b.append(f"| {name} | `{t['checksum'][:12]}…` | {counts['oracle']}/10 | {counts['nop']}/3 "
                 f"| {len(wrong)}: {', '.join(wrong)} | {counts['independent']}/1 | {outcome.pop()} "
                 f"| {len(iso['probes'])} | `{short(t['isolation_images']['simcloud'])}…` "
                 f"| `{short(t['isolation_images']['verifier'])}…` |")
    b += ["", "`receipts/README.md` records why `stop-double-charges` was re-probed after M1 (its verifier's "
          "identity differed between the long-lived M1 checkout and a fresh one, by cached layers only) and that "
          "the refresh in the run checkout matched every pin.", "",
          "## B.4 Harnesses", "",
          "| Item | Value | Source |", "|---|---|---|",
          f"| Rusty commit | `{m['harness_frozen_at']}` | Rusty `docs/receipts/fsb-reporting-v1.md` |",
          f"| Rusty QA | {qa} | same |",
          f"| Toolchain | {toolchain} | same |",
          f"| Cohort binary | static musl build of that commit, sha256 `{rusty[0]['binary_sha256'][:16]}…`, "
          f"`--version` prints `{rusty[0]['version']}` | manifest `tracks[].binary_sha256`, `version` |",
          f"| Context handling | window assumed to be {ctx['window']:,} tokens (`RUSTY_CONTEXT_TOKENS` unset; the "
          f"adapter at the evaluator commit does not set it); history budget = window minus a {ctx['reserve']:,}-token reply reserve and "
          f"the system prompt; above {ctx['elide']}% of it old tool outputs are shrunk, above {ctx['compact']}% "
          f"older turns are summarised; usage estimated at about {WORDS.get(ctx['per_token'], ctx['per_token'])} "
          "characters per token | Rusty `src/context.rs`, `src/agent.rs` at that commit; `fsbench/agents/rusty.py` at the "
          "evaluator commit |",
          f"| `--capabilities` | identical to the receipt's: contract {caps['contract']}; modes "
          f"{', '.join(caps['mode'])}; toolsets {', '.join(caps['toolset'])} | the binary |",
          f"| mini-swe-agent | {mini[0]['version']}, config `{mini[0]['config_file']}` sha256 "
          f"`{mini[0]['config_sha256'][:16]}…` | manifest `tracks[]` |", ""]

    # Phrases 6.1 must contain, whitespace-normalised.
    phrases = [
        f"frozen by the merge `{frozen}`", f"file sha256 `{file_sha[:16]}…`",
        f"evaluator commit `{m['evaluator_frozen_at'][:7]}`", f"commit `{m['harness_frozen_at'][:7]}` (`{rusty[0]['version']}`)",
        f"mini-swe-agent is version {mini[0]['version']}", f"`{m['model']['id']}` with temperature {inf['temperature']}",
        f"top_p {inf['top_p']} and at most {inf['max_reply']} reply tokens",
        f"{env['calls']} admitted calls, {env['input_tokens']:,} input tokens",
        f"{env['output_tokens']:,} output tokens and {env['wall_seconds']} s",
        f"capped at {len(episodes) * env['calls']:,} admitted calls",
        f"{tracks['mini-verify']['gate_rounds']} gate rounds and the same {tracks['rusty-verify']['verify_timeout']} s",
        f"{len(m['tasks'])} tasks x {len(m['tracks'])} tracks x {len(m['seeds'])} seeds give {len(episodes)} episodes",
        f"fixed seed ({rt['order_seed']})", ", ".join(f"{v} on slot {k}" for k, v in sorted(slots.items())),
        f"{len({e['wave'] for e in episodes})} waves of at most {rt['max_concurrent_trials']}",
        f"limited to {rt['cpus_limit']} CPUs and {rt['memory_limit_mb']} MB",
    ]
    return "\n".join(b) + "\n", phrases


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rusty-binary", type=Path, required=True, help="the cohort's pinned Rusty binary")
    ap.add_argument("--rusty-receipt", type=Path, required=True, help="Rusty's docs/receipts/fsb-reporting-v1.md")
    ap.add_argument("--rusty-src", type=Path, required=True, help="a Rusty checkout at the pinned commit")
    ap.add_argument("--write", action="store_true", help="write the appendix instead of comparing with it")
    a = ap.parse_args()
    appendix, phrases = render(a.rusty_binary, a.rusty_receipt, a.rusty_src)
    if a.write:
        APPENDIX.write_text(appendix)
        print(f"wrote {APPENDIX.relative_to(ROOT)}")
    elif APPENDIX.read_text() != appendix:
        print(f"{APPENDIX.relative_to(ROOT)} differs from its sources; rerun with --write and review the diff")
        return 1
    text = SECTION.read_text()
    section = " ".join(text[text.index("## 6.1"):text.index("## 6.2")].split())
    missing = [p for p in phrases if p not in section]
    for p in missing:
        print(f"6.1 does not state: {p}")
    if missing:
        return 1
    print("appendix B and 6.1 agree with their sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
