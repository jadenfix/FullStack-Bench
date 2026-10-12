"""Benjamini-Hochberg adjustment over families of paired comparisons.

    uv run python scripts/adjust_pairs.py --family primary docs/results/<cohort>/pairs/A docs/results/<cohort>/pairs/B \\
        --family exploratory docs/results/<cohort>/pairs/C ... --out docs/results/<cohort>/pairs/adjusted.json

Each directory is one `scripts/report_cohort.py --pair` output, whose `report.json` carries a
`paired` record with the SafeSuccess sign test. Each `--family` names one family (section 6.6:
the two preregistered primaries form one, the exploratory comparisons another) and the
comparisons adjusted within it. The result lists, per family and comparison, the slots, the
discordant counts, the unadjusted p and the adjusted p. Nothing under `docs/results/` is read
other than the pair reports, and no model is called."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fsbench.report import benjamini_hochberg  # noqa: E402


def adjust(families: dict[str, list[Path]]) -> dict:
    out = {"families": {}}
    for family, dirs in families.items():
        rows = {}
        for d in dirs:
            paired = json.loads((d / "report.json").read_text()).get("paired")
            if not paired:
                raise SystemExit(f"{d}/report.json has no paired record; render it with --pair")
            name = f"{paired['left']} vs {paired['right']}"
            t = paired["safe_success"]
            rows[name] = {"paired_slots": paired["paired_slots"], "tasks": paired["tasks"],
                          "left_only": t["left_only"], "right_only": t["right_only"], "both": t["both"],
                          "neither": t["neither"], "unknown": t["unknown"], "p": t["sign_test_p"]}
        adjusted = benjamini_hochberg({name: r["p"] for name, r in rows.items()})
        for name, r in rows.items():
            r["p_adjusted"] = adjusted[name]
        out["families"][family] = {"size": sum(1 for r in rows.values() if r["p"] is not None), "comparisons": rows}
    return out


def render(result: dict) -> str:
    lines = ["| Family | Comparison | Slots | Left only | Right only | Both | Neither | Unknown | p | p (BH) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for family, f in result["families"].items():
        for name, r in f["comparisons"].items():
            fmt = lambda v: "n/a" if v is None else v  # noqa: E731
            lines.append(f"| {family} | {name} | {r['paired_slots']} | {r['left_only']} | {r['right_only']} | "
                         f"{r['both']} | {r['neither']} | {r['unknown']} | {fmt(r['p'])} | {fmt(r['p_adjusted'])} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", nargs="+", action="append", metavar=("NAME", "DIR"), required=True,
                    help="a family name followed by the pair-report directories adjusted within it")
    ap.add_argument("--out", type=Path, help="file for the JSON result (printed as Markdown either way)")
    args = ap.parse_args()
    families: dict[str, list[Path]] = {}
    for spec in args.family:
        if len(spec) < 2:
            ap.error("--family needs a name and at least one directory")
        if spec[0] in families:
            ap.error(f"family {spec[0]} given twice")
        families[spec[0]] = [Path(d) for d in spec[1:]]
    result = adjust(families)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
