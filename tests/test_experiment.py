import copy
import json
from pathlib import Path

import pytest

from fsbench import experiment

ROOT = Path(__file__).resolve().parent.parent
SHA = "a" * 64


def manifest(**over):
    m = {
        "schema": experiment.SCHEMA, "name": "ablation-dev", "cohort_role": "development",
        "model": {"id": "nvidia/nemotron-3-super-120b-a12b", "revision": "pinned-2026-10-09"},
        "inference": {"temperature": 0.2, "top_p": 1.0, "max_reply": 16384, "reasoning_effort": None},
        "envelope": {"calls": 250, "input_tokens": 12_000_000, "output_tokens": 500_000, "wall_seconds": 7200},
        "seeds": [0, 1],
        "tasks": [{"name": "ship-checkout-v2", "checksum": SHA,
                   "public_check": "curl -sf 'http://web/checkout/quote?cart=demo' | grep -q '{\"engine\": \"v2\"'"},
                  {"name": "stop-double-charges", "checksum": "b" * 64, "public_check": "true"}],
        "tracks": [{"name": "mini", "harness": "mini-swe", "version": "2.4.6", "config_sha256": SHA,
                    "config_file": "configs/mswea-compact.yaml"},
                   *experiment.rusty_ablation("pins/rusty", SHA)],
    }
    m.update(over)
    return m


def test_a_matched_ablation_manifest_is_plannable():
    assert experiment.validate(manifest(), ROOT / "tasks") == []
    names = [t["name"] for t in experiment.rusty_ablation("b", SHA)]
    assert names == ["rusty-baseline", "rusty-verify", "rusty-careful", "rusty-combined"]
    cells = {(t["execution"], t["verify"]) for t in experiment.rusty_ablation("b", SHA)}
    assert cells == {("standard", False), ("standard", True), ("careful", False), ("careful", True)}


@pytest.mark.parametrize("change, message", [
    (lambda m: m["model"].pop("revision"), "model.revision"),
    (lambda m: m["inference"].pop("top_p"), "inference must pin"),
    (lambda m: m["envelope"].update(calls=0), "envelope must pin"),
    (lambda m: m.update(seeds=[1, 1]), "seeds"),
    (lambda m: m["tasks"][0].update(checksum="short"), "checksum"),
    (lambda m: m["tasks"][1].update(public_check=None), "verify needs a public_check"),
    (lambda m: m["tasks"][1].update(public_check="a\nb"), "one-line command"),
    (lambda m: m["tracks"][1].update(memory="learn"), "memory and agents must be off"),
    (lambda m: m["tracks"][1].update(execution="vibe"), "standard or careful"),
    (lambda m: m["tracks"][1].pop("verify"), "must pin"),
    (lambda m: m["tracks"][1].update(max_requests=200), "could bind first"),
    (lambda m: m["tracks"].append(copy.deepcopy(m["tracks"][1])), "track names must be unique"),
    (lambda m: m["tracks"][0].pop("config_file"), "config_file required"),
    (lambda m: m.update(cohort_role="reporting"), "task_filtering_models"),
    (lambda m: m.update(cohort_role="reporting", task_filtering_models=[m["model"]["id"]]), "must not have filtered"),
    (lambda m: m["tasks"].append({"name": "no-such-task", "checksum": SHA, "public_check": "true"}), "not found"),
])
def test_unmatched_or_underspecified_manifests_are_refused(change, message):
    m = manifest()
    change(m)
    errors = experiment.validate(m, ROOT / "tasks")
    assert any(message in e for e in errors), errors


def test_plan_gives_every_track_the_same_public_check_and_only_rusty_enforces_it(tmp_path):
    from harbor.cli.utils import parse_kwargs
    from harbor.utils.templating import render_prompt_template

    plan = experiment.plan(manifest(), ROOT / "tasks", tmp_path / "templates")
    eps = plan["episodes"]
    assert len(eps) == 5 * 2 * 2 and len({e["episode"] for e in eps}) == len(eps)
    assert plan["executed"] is False and len(plan["manifest_sha256"]) == 64
    for task in ("ship-checkout-v2", "stop-double-charges"):
        same = {e["template_sha256"] for e in eps if e["task"] == task}
        assert len(same) == 1, "every track sees the identical instruction template"
    check = manifest()["tasks"][0]["public_check"]
    rendered = render_prompt_template(Path(plan["templates"]["ship-checkout-v2"]["path"]), "BRIEF")
    assert rendered.startswith("BRIEF") and check in rendered  # braces in the check survive Jinja
    for e in eps:
        kwargs = parse_kwargs([a for i, a in enumerate(e["command"]) if i and e["command"][i - 1] == "--ak"])
        assert "prompt_template_path" in kwargs
        enforced = "verify" in kwargs
        assert enforced == (e["track"] in ("rusty-verify", "rusty-combined"))
        if enforced:
            want = check if e["task"] == "ship-checkout-v2" else "true"
            assert kwargs["verify"] == want  # exactly, not a JSON-parsed bool
        if e["track"].startswith("rusty"):
            assert kwargs["memory"] == "off" and kwargs["agents"] == "off"


def test_plan_refuses_an_invalid_manifest(tmp_path):
    with pytest.raises(ValueError, match="not plannable"):
        experiment.plan(manifest(name="Bad Name"), ROOT / "tasks", tmp_path)


def test_cli_validate_and_plan(tmp_path, capsys, monkeypatch):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest()))
    monkeypatch.setattr("sys.argv", ["x", "plan", str(path), "--tasks", str(ROOT / "tasks"),
                                     "--out", str(tmp_path / "plan.json")])
    assert experiment.main() == 0
    assert json.loads((tmp_path / "plan.json").read_text())["executed"] is False
    assert "nothing was run" in capsys.readouterr().out
