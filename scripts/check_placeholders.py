"""Every `{{report.<key>}}` and `{{mechanisms.<key>}}` placeholder under `docs/paper/` must name a
key its cohort's rendered results hold (the mechanical check in `docs/PAPER_PLAN.md`).

    uv run python scripts/check_placeholders.py [--paper docs/paper] [--results docs/results]

The body resolves against `docs/results/reporting/`, Appendix A against `docs/results/development/`
(`COHORT_OF`). `report.<key>` is looked up in that cohort's `report.json`; `report.paired.<key>` in
every pair report under its `pairs/`; `mechanisms.<key>` in its `mechanisms.json`. A segment
written `<name>` or `*` stands for any key at that level, and the rest of the path must resolve
under each of them. Only key presence is checked, never a value, so a key that is null for want
of an input (the trajectory measures before the summaries are published) still resolves. The
paper README is skipped: its placeholders are the notation, not citations of a key."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{(report|mechanisms)\.([^}]+)\}\}")
COHORT_OF = {"appendix-a-pilot.md": "development"}
DEFAULT_COHORT = "reporting"
SKIP = {"README.md"}


def resolve(node, segments: list[str]) -> str | None:
    """None when every path the segments describe exists under `node`, else the first missing key."""
    if not segments:
        return None
    head, rest = segments[0], segments[1:]
    if not isinstance(node, dict):
        return head
    if head == "*" or (head.startswith("<") and head.endswith(">")):
        if not node:
            return head
        for child in node.values():
            missing = resolve(child, rest)
            if missing is not None:
                return missing
        return None
    if head not in node:
        return head
    return resolve(node[head], rest)


def load_cohort(results: Path, cohort: str) -> dict:
    base = results / cohort
    out = {"report": json.loads((base / "report.json").read_text())}
    mech = base / "mechanisms.json"
    out["mechanisms"] = json.loads(mech.read_text()) if mech.is_file() else None
    out["pairs"] = (
        {
            p.name: json.loads((p / "report.json").read_text())
            for p in sorted((base / "pairs").iterdir())
            if (p / "report.json").is_file()
        }
        if (base / "pairs").is_dir()
        else {}
    )
    return out


def check_placeholder(source: str, key: str, cohort: dict) -> str | None:
    """A problem description, or None when the placeholder resolves."""
    segments = key.split(".")
    if source == "mechanisms":
        if cohort["mechanisms"] is None:
            return "the cohort has no mechanisms.json"
        missing = resolve(cohort["mechanisms"], segments)
        return None if missing is None else f"no key `{missing}`"
    if segments[0] == "paired":
        if not cohort["pairs"]:
            return "the cohort has no pair reports"
        for name, pair in cohort["pairs"].items():
            missing = resolve(pair, segments)
            if missing is not None:
                return f"no key `{missing}` in pair {name}"
        return None
    missing = resolve(cohort["report"], segments)
    return None if missing is None else f"no key `{missing}`"


def check(paper: Path, results: Path) -> list[str]:
    """One line per placeholder that does not resolve: `file:line: {{...}}: problem`."""
    problems, cohorts = [], {}
    for path in sorted(paper.glob("*.md")):
        if path.name in SKIP:
            continue
        name = COHORT_OF.get(path.name, DEFAULT_COHORT)
        if name not in cohorts:
            cohorts[name] = load_cohort(results, name)
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for match in PLACEHOLDER.finditer(line):
                problem = check_placeholder(
                    match.group(1), match.group(2), cohorts[name]
                )
                if problem is not None:
                    problems.append(
                        f"{path}:{lineno}: {match.group(0)}: {problem} ({name})"
                    )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paper", type=Path, default=Path("docs/paper"))
    ap.add_argument("--results", type=Path, default=Path("docs/results"))
    args = ap.parse_args(argv)
    problems = check(args.paper, args.results)
    for p in problems:
        print(p)
    count = sum(
        len(PLACEHOLDER.findall(p.read_text()))
        for p in args.paper.glob("*.md")
        if p.name not in SKIP
    )
    print(f"{count} placeholders, {len(problems)} unresolved")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
