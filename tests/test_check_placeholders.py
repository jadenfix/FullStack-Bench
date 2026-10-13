import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_placeholders as cp  # noqa: E402


def test_wildcard_segments_must_resolve_under_every_child():
    node = {"harnesses": {"a": {"harm": {"x": 1}}, "b": {"harm": {}}}}
    assert cp.resolve(node, ["harnesses", "<h>", "harm"]) is None
    assert cp.resolve(node, ["harnesses", "*", "harm", "x"]) == "x"  # b.harm has no x
    assert cp.resolve(node, ["harnesses", "a", "harm", "x"]) is None
    assert cp.resolve(node, ["harnesses", "c"]) == "c"
    assert cp.resolve({"v": None}, ["v"]) is None  # presence, not value


def test_paired_keys_resolve_in_every_pair_report(tmp_path):
    cohort = tmp_path / "results" / "reporting"
    for pair, keys in (("l-vs-r", {"safe_success": 1}), ("m-vs-r", {})):
        (cohort / "pairs" / pair).mkdir(parents=True)
        (cohort / "pairs" / pair / "report.json").write_text(
            json.dumps({"paired": keys})
        )
    (cohort / "report.json").write_text(
        json.dumps({"harnesses": {"a": {"safe_success": 0.5}}})
    )
    (cohort / "mechanisms.json").write_text(json.dumps({"gap": {"count": 3}}))
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "README.md").write_text(
        "`{{report.<key>}}` is the notation and is skipped"
    )
    (paper / "06.md").write_text(
        "{{report.harnesses.<h>.safe_success}} {{mechanisms.gap.count}}\n"
        "{{report.paired.safe_success}} {{report.harnesses.a.missing}}\n"
    )
    problems = cp.check(paper, tmp_path / "results")
    assert len(problems) == 2
    assert (
        "06.md:2: {{report.paired.safe_success}}: no key `safe_success` in pair m-vs-r"
        in problems[0]
    )
    assert "06.md:2: {{report.harnesses.a.missing}}: no key `missing`" in problems[1]


def test_committed_paper_resolves():
    assert cp.check(ROOT / "docs" / "paper", ROOT / "docs" / "results") == []
