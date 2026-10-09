import copy
import json
from pathlib import Path

import pytest

from fsbench import experiment

ROOT = Path(__file__).resolve().parent.parent
SHA = "a" * 64


FROZEN = {"harness_frozen_at": "a" * 40, "evaluator_frozen_at": "b" * 40,
          "base_images": {"fullstack-bench/simcloud:dev": "sha256:" + "c" * 64}}


def manifest(**over):
    m = {
        "schema": experiment.SCHEMA, "name": "ablation-dev", "cohort_role": "development",
        "model": {"id": "nvidia/nemotron-3-super-120b-a12b", "revision": "pinned-2026-10-09"},
        "inference": {"temperature": 0.2, "top_p": 1.0, "max_reply": 16384, "reasoning_effort": None},
        "envelope": {"calls": 250, "input_tokens": 12_000_000, "output_tokens": 500_000, "wall_seconds": 7200},
        "seeds": [0, 1],
        "tasks": [{"name": "ship-checkout-v2", "checksum": SHA, "generalization": "new_mechanism",
                   "lineage": {"template": "ship-checkout", "causal_mechanism": "iam-propagation-on-promote"},
                   "public_check": "curl -sf 'http://web/checkout/quote?cart=demo' | grep -q '{\"engine\": \"v2\"'",
                   "public_check_scope": "partial", "public_check_source": "template"},
                  {"name": "stop-double-charges", "checksum": "b" * 64, "generalization": "new_mechanism",
                   "lineage": {"template": "double-charge", "causal_mechanism": "non-idempotent-retry"},
                   "public_check": "true", "public_check_scope": "partial", "public_check_source": "template"}],
        "runtime": {"cpus_reserved": 2, "cpus_limit": 2, "memory_reserved_mb": 4096, "memory_limit_mb": 4096,
                    "max_concurrent_trials": 2, "cache": "cold", "ordering": "counterbalanced", "order_seed": 7,
                    "key_slots": [1, 2]},
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
    (lambda m: m["tasks"].append({"name": "no-such-task", "checksum": SHA, "public_check": "true",
                                  "generalization": "new_mechanism",
                                  "lineage": {"template": "x", "causal_mechanism": "y"}}), "not found"),
    (lambda m: m["tasks"][0].pop("lineage"), "lineage must name"),
    (lambda m: m["tasks"][0].pop("public_check_scope"), "public_check_scope"),
    (lambda m: m["tasks"][0].update(challenges=["a", "a"]), "distinct names"),
    (lambda m: m["tasks"][0].pop("public_check_source"), "public_check_source must be"),
    (lambda m: m["tasks"][1].update(public_check_source="brief"), "does not name"),
    (lambda m: m.update(cohort_role="reporting", task_filtering_models=[], **FROZEN,
                        development_mechanisms=[]), "predeclared challenges"),
    (lambda m: m["tasks"][0].update(generalization="new"), "generalization must be"),
    (lambda m: reporting_ready(m).update(cohort_role="reporting", task_filtering_models=[],
                                         **FROZEN, development_mechanisms=["non-idempotent-retry"]),
     "only be familiar_family"),
    (lambda m: m.update(cohort_role="reporting", task_filtering_models=[], development_mechanisms=[]),
     "harness_frozen_at"),
    (lambda m: m.update(cohort_role="reporting", task_filtering_models=[], development_mechanisms=[],
                        **dict(FROZEN, evaluator_frozen_at="main")), "evaluator_frozen_at"),
    (lambda m: m.update(cohort_role="reporting", task_filtering_models=[], development_mechanisms=[],
                        **dict(FROZEN, base_images={})), "pin base_images"),
    (lambda m: m.update(base_images={"fullstack-bench/simcloud:dev": "latest"}), "sha256 image ID"),
    (lambda m: m["tracks"][1].pop("allow_destructive"), "allow_destructive"),
    (lambda m: m.update(privileged_hints=["fault is in payclient.py"]), "only in a diagnostic cohort"),
    (lambda m: m.pop("runtime"), "runtime must pin"),
    (lambda m: m["runtime"].update(memory_reserved_mb=8192), "reservation cannot exceed"),
    (lambda m: m["runtime"].update(memory_limit_mb="4G"), "positive integers"),
    (lambda m: m["runtime"].update(cache="sometimes"), "cold or warm"),
    (lambda m: m["runtime"].update(max_concurrent_trials=3), "share a key"),
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


def test_unknown_revision_is_allowed_but_must_be_said_and_diagnostics_may_carry_hints():
    m = manifest(cohort_role="diagnostic", privileged_hints=["oracle fault location"])
    m["model"]["revision"] = "unknown"
    assert experiment.validate(m, ROOT / "tasks") == []
    m["model"]["revision"] = ""
    assert any("unknown" in e for e in experiment.validate(m, ROOT / "tasks"))


def reporting_ready(m):
    for t, names in zip(m["tasks"], (["customer-quotes"], ["retried-checkout"])):
        t.update(challenges=names, post_handoff_observed_required=True)
    m["comparisons"] = [{"name": "verify-in-rusty", "kind": "ablation", "treatment": "rusty-verify",
                         "control": "rusty-baseline", "primary": True}]
    return m


def test_familiar_family_tasks_may_report_after_development_saw_the_mechanism():
    m = reporting_ready(manifest(cohort_role="reporting", task_filtering_models=[], **FROZEN,
                                 development_mechanisms=["non-idempotent-retry"]))
    m["tasks"][1]["generalization"] = "familiar_family"
    assert experiment.validate(m, ROOT / "tasks") == []
    m["tasks"][0]["post_handoff_observed_required"] = False
    assert any("post-handoff window" in e for e in experiment.validate(m, ROOT / "tasks"))


@pytest.mark.parametrize("ordering", ["counterbalanced", "randomized"])
def test_schedule_rotates_harness_order_and_keys(ordering, tmp_path):
    m = manifest()
    m["runtime"]["ordering"] = ordering
    planned = experiment.plan(m, ROOT / "tasks", tmp_path)
    eps = planned["episodes"]
    assert planned["key_imbalance"] == 0 and all(set(u.values()) == {2} for u in planned["key_uses"].values())
    blocks = {}
    for e in eps:
        blocks.setdefault(e["block"], []).append(e)
    assert len(blocks) == 2 * 2 and all(len(b) == 5 for b in blocks.values())
    assert all({e["track"] for e in b} == {t["name"] for t in m["tracks"]} for b in blocks.values())
    firsts = {b[0]["track"] for b in blocks.values()}
    assert len(firsts) > 1, "the same harness must not always run first"
    for track in {e["track"] for e in eps}:
        uses = [e["key_slot"] for e in eps if e["track"] == track]
        assert abs(uses.count(1) - uses.count(2)) <= 1, f"{track} is tied to one key: {uses}"
    waves = {}
    for e in eps:
        waves.setdefault(e["wave"], []).append(e["key_slot"])
    assert all(len(w) <= 2 and len(set(w)) == len(w) for w in waves.values()), "a wave never shares a key"
    again = experiment.plan(m, ROOT / "tasks", tmp_path)["episodes"]
    assert [e["episode"] for e in again] == [e["episode"] for e in eps], "the order is reproducible"


def test_a_check_named_in_the_brief_leaves_the_brief_untouched(tmp_path):
    root = tmp_path / "tasks"
    for name in ("ship-checkout-v2", "stop-double-charges"):
        (root / name).mkdir(parents=True)
        (root / name / "task.toml").write_text("")
        (root / name / "instruction.md").write_text("Run `public-check` to check part of it.\n")
    m = manifest()
    for t in m["tasks"]:
        t.update(public_check="public-check", public_check_source="brief")
    assert experiment.validate(m, root) == []
    plan = experiment.plan(m, root, tmp_path / "templates")
    assert plan["templates"] == {} and all(e["template_sha256"] is None for e in plan["episodes"])
    assert not any(a.startswith("prompt_template_path=") for e in plan["episodes"] for a in e["command"])
    verify = [a for e in plan["episodes"] for a in e["command"] if a.startswith("verify=")]
    assert verify and set(verify) == {'verify="public-check"'}


def gated_mini(**over):
    return {"name": "mini-verify", "harness": "mini-swe", "version": "2.4.6", "config_sha256": SHA,
            "config_file": "configs/mswea-compact.yaml", "verify": True, "gate_rounds": 3, **over}


def comparisons():
    return [{"name": "rusty-vs-mini", "kind": "whole_system", "treatment": "rusty-baseline", "control": "mini",
             "primary": True},
            {"name": "verify-in-rusty", "kind": "ablation", "treatment": "rusty-verify", "control": "rusty-baseline",
             "primary": True},
            {"name": "verify-transferred", "kind": "transfer", "treatment": "mini-verify", "control": "mini",
             "primary": True}]


def test_the_public_check_gate_transfers_to_the_baseline(tmp_path):
    m = manifest()
    m["tracks"].append(gated_mini())
    m["comparisons"] = comparisons()
    assert experiment.validate(m, ROOT / "tasks") == []
    plan = experiment.plan(m, ROOT / "tasks", tmp_path)
    assert plan["comparisons"] == m["comparisons"]
    cmd = next(e["command"] for e in plan["episodes"] if e["track"] == "mini-verify")
    assert cmd[cmd.index("-a") + 1] == "fsbench.agents.gated_mini:GatedMini"
    assert "max_rounds=3" in cmd and any(a.startswith("verify=") for a in cmd)
    plain = next(e["command"] for e in plan["episodes"] if e["track"] == "mini")
    assert plain[plain.index("-a") + 1] == "mini-swe-agent" and not any(a.startswith("verify=") for a in plain)
    m["tracks"][-1].pop("gate_rounds")
    assert any("gate_rounds" in e for e in experiment.validate(m, ROOT / "tasks"))


@pytest.mark.parametrize("change, message", [
    (lambda c: c[1].update(treatment="rusty-combined"), "exactly one treatment"),
    (lambda c: c[2].update(treatment="rusty-verify"), "two mini-swe tracks"),
    (lambda c: c[0].update(treatment="mini-verify"), "different harnesses"),
    (lambda c: c[0].update(control="rusty-baseline"), "two different tracks"),
    (lambda c: c[0].update(kind="headline"), "kind must be one of"),
    (lambda c: c.append(dict(c[0])), "names must be unique"),
])
def test_comparisons_compare_what_their_kind_says(change, message):
    m = manifest()
    m["tracks"].append(gated_mini())
    m["comparisons"] = comparisons()
    change(m["comparisons"])
    assert any(message in e for e in experiment.validate(m, ROOT / "tasks"))


def test_a_transfer_arm_may_not_differ_outside_the_gate():
    m = manifest()
    m["tracks"].append(gated_mini(config_file="configs/other.yaml"))
    m["comparisons"] = comparisons()
    assert any("differ outside the treatment" in e and "config_file" in e for e in experiment.validate(m, ROOT / "tasks"))


def test_a_reporting_cohort_preregisters_a_primary_comparison():
    m = manifest(cohort_role="reporting")
    m["comparisons"] = [dict(c, primary=False) for c in comparisons()[:2]]
    assert any("preregister at least one primary" in e for e in experiment.validate(m, ROOT / "tasks"))


def test_rusty_runs_with_its_destructive_step_guard_unless_pinned_otherwise(tmp_path):
    m = manifest()
    assert all(t["allow_destructive"] is False for t in m["tracks"] if t["harness"] == "rusty")
    cmd = next(e["command"] for e in experiment.plan(m, ROOT / "tasks", tmp_path)["episodes"]
               if e["track"] == "rusty-baseline")
    assert "allow_destructive=false" in cmd
