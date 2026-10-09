"""Validate coupled episodes before they can become authoring inputs."""

import copy
import json

import pytest
import yaml

from fsbench import hard_suite


def test_six_new_designs_preserve_all_previous_design_inputs():
    prior = []
    for path in (hard_suite.CATALOGUE, hard_suite.INCIDENT_CATALOGUE):
        prior.extend(yaml.safe_load(path.read_text())["cases"])
    cases = hard_suite.load_cases()
    assert cases[:29] == prior
    new = cases[29:]
    assert len(new) == 6
    assert all(case["batch"] == "coupled-workflows-v3" for case in new)
    assert not ({c["id"] for c in prior} & {c["id"] for c in new})
    assert not hard_suite.novelty_errors(cases)


@pytest.mark.parametrize("case_id", [
    "tenant-shard-relocation", "event-time-metering-settlement",
    "controller-storage-version-upgrade", "schema-rollout-job-replay",
    "authorization-graph-revocation", "capacity-plan-commit",
])
def test_new_design_reaches_authoring_with_operational_and_coupling_context(case_id):
    case = hard_suite.get_case(case_id)
    assert not hard_suite.validate_case(case)
    text = hard_suite.prompt(case)
    design = json.loads(text.split("Design JSON (never solver-facing):\n", 1)[1])
    assert design["multifaceted"] == case["multifaceted"]
    assert design["realism"] == case["realism"]
    assert design["closest_case_design"]["id"] == case["novelty"]["closest_case"]
    assert "do not merely run two" in text
    assert "same evolving world" in text
    assert len(text.encode()) < 512_000


@pytest.mark.parametrize("mutation, expected", [
    (lambda c: c.update(multifaceted=None), "multifaceted must be an object"),
    (lambda c: c["multifaceted"].update(interactions=[]), "ordered fault pairs"),
    (lambda c: c["multifaceted"]["interactions"].__setitem__(0, None), "interactions must be objects"),
    (lambda c: c["multifaceted"]["interactions"][0].update(first="invisible_fault"), "declared schedule"),
    (lambda c: c["multifaceted"]["interactions"][0].update(then=c["multifaceted"]["interactions"][0]["first"]), "distinct declared"),
    (lambda c: c["multifaceted"]["interactions"][0].update(requirements=["invented", "hidden"]), "declared requirements"),
    (lambda c: c["multifaceted"]["interactions"][0].update(requirements=["single-authority", "single-authority"]), "distinct declared requirements"),
    (lambda c: c["multifaceted"]["interactions"][0].update(requirements=["single-authority"]), "at least two"),
    (lambda c: c["multifaceted"]["interactions"][0].update(coupling="More layers"), "observable coupling"),
    (lambda c: c["multifaceted"].update(journeys=["API unit test"]), "journeys"),
    (lambda c: c["multifaceted"].update(recovery=["Rebuild"]), "recovery"),
    (lambda c: c["multifaceted"].update(oracle_strategy=""), "oracle_strategy"),
    (lambda c: c["multifaceted"].update(feasibility=""), "feasibility"),
    (lambda c: c.update(interfaces=c["interfaces"][:3]), "at least 4 interfaces"),
    (lambda c: c.update(layers=c["layers"][:6]), "at least 7 layers"),
    (lambda c: c["controls"][0].update(fails=c["controls"][1]["fails"]), "cover every case requirement"),
    (lambda c: c["realism"].update(mitigation=""), "realism.mitigation"),
])
def test_invalid_or_disconnected_episode_cannot_enter_authoring(mutation, expected):
    case = hard_suite.get_case("tenant-shard-relocation")
    mutation(case)
    assert any(expected in error for error in hard_suite.validate_case(case))


def test_repeating_a_fault_pair_is_not_additional_interaction_coverage():
    case = hard_suite.get_case("tenant-shard-relocation")
    facets = case["multifaceted"]
    facets["interactions"][1] = copy.deepcopy(facets["interactions"][0])
    assert "duplicate ordered interaction pair" in hard_suite.validate_case(case)


def test_retagging_new_batch_cannot_bypass_episode_validation(tmp_path, monkeypatch):
    data = yaml.safe_load(hard_suite.MULTIFACETED_CATALOGUE.read_text())
    data["cases"][0]["batch"] = "incident-workflows-v2"
    del data["cases"][0]["multifaceted"]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(hard_suite, "MULTIFACETED_CATALOGUE", path)
    with pytest.raises(ValueError, match="declare their batch"):
        hard_suite.load_cases()


def test_planning_extension_does_not_invent_an_old_correctness_bug():
    case = hard_suite.get_case("capacity-plan-commit")
    assert "bugfix" not in case["work"]
    assert case["repair_scope"] == "extension"
    assert "any feasible route" in case["multifaceted"]["feasibility"]
    assert "global optimum" in hard_suite.prompt(case)
