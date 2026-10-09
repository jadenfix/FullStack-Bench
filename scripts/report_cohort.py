"""Render the paper tables for one cohort from its run directories.

    uv run python scripts/report_cohort.py runs/paired/<tag> [runs/paired/<tag2> ...] \
        --pair rusty mini --out docs/results/<cohort>

Each run directory holds the `manifest.json` and `attempts/*.json` that `scripts/paired_screen.py`
wrote. The script writes `report.json` (every number with its denominator and interval) and
`report.md` (the tables), and prints the Markdown. Runs from different cohorts are refused;
report each cohort separately. No model is called and nothing under `runs/` is modified."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsbench import report  # noqa: E402 -- bootstrap import for standalone execution
import json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path, help="run directories with manifest.json and attempts/")
    ap.add_argument("--pair", nargs=2, metavar=("LEFT", "RIGHT"), help="two harness names to compare per (task, seed)")
    ap.add_argument("--out", type=Path, help="directory for report.json and report.md (printed only when omitted)")
    args = ap.parse_args()
    runs = []
    for run in args.runs:
        if not (run / "manifest.json").is_file():
            ap.error(f"{run} has no manifest.json")
        runs.append(report.load_run(run))
    try:
        result = report.aggregate(runs, pair=tuple(args.pair) if args.pair else None)
    except ValueError as e:
        ap.error(str(e))
    text = report.render_markdown(result)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        (args.out / "report.md").write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
