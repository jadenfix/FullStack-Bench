"""Operational realism and exact-reskin rejection for new design inputs."""

import copy
import json

import pytest
import yaml

from fsbench import hard_suite


def test_new_batch_preserves_the_first_thirteen_and_adds_sixteen_distinct_cases():
    initial = yaml.safe_load(hard_suite.CATALOGUE.read_text())["cases"]
    cases = hard_suite.load_cases()
    assert cases[:13] == initial
    new = cases[13:29]
    assert len(new) == 16
    assert all(c["batch"] == "incident-workflows-v2" for c in new)
    assert not ({c["id"] for c in initial} & {c["id"] for c in new})
    assert not hard_suite.novelty_errors(cases)
    assert set(hard_suite.FOUNDATION_SIGNATURES) == {c["id"] for c in initial}


@pytest.mark.parametrize("field", ["trigger", "baseline", "impact", "scale", "change_window", "mitigation"])
def test_new_design_without_operational_context_is_rejected(field):
    case = hard_suite.get_case("cancellation-pool-exhaustion")
    del case["realism"][field]
    assert any(f"realism.{field}" in e for e in hard_suite.validate_case(case))


@pytest.mark.parametrize("field", ["protected", "capability_needs", "sources"])
def test_new_design_cannot_omit_protected_state_or_runtime_needs(field):
    case = hard_suite.get_case("snapshot-cdc-handoff")
    case["realism"][field] = []
    assert any(f"realism.{field}" in e for e in hard_suite.validate_case(case))


@pytest.mark.parametrize("field", ["realism", "novelty"])
def test_malformed_incident_metadata_is_rejected(field):
    case = hard_suite.get_case("snapshot-cdc-handoff")
    case[field] = None
    assert f"{field} must be an object" in hard_suite.validate_case(case)


@pytest.mark.parametrize("sources", ["https://example.com", [None], [{"title": "Incomplete"}]])
def test_malformed_source_records_are_rejected(sources):
    case = hard_suite.get_case("snapshot-cdc-handoff")
    case["realism"]["sources"] = sources
    assert any("realism.sources" in e for e in hard_suite.validate_case(case))


def test_removing_the_batch_cannot_bypass_incident_context_validation(tmp_path, monkeypatch):
    data = yaml.safe_load(hard_suite.INCIDENT_CATALOGUE.read_text())
    del data["cases"][0]["batch"]
    path = tmp_path / "incidents.yaml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(hard_suite, "INCIDENT_CATALOGUE", path)
    with pytest.raises(ValueError, match="declare their batch"):
        hard_suite.load_cases()


def test_renaming_a_case_does_not_make_its_mechanism_new():
    cases = hard_suite.load_cases()
    clone = copy.deepcopy(cases[13])
    clone["id"] = "different-company-upload"
    clone["title"] = "A completely different report product"
    clone["mechanism"] = "  " + clone["mechanism"].upper() + "  "
    errors = hard_suite.novelty_errors([*cases, clone])
    assert any("duplicates the mechanism" in e for e in errors)
    assert any("repeats the causal signature" in e for e in errors)


def test_rewording_a_new_design_with_the_same_causal_signature_is_rejected():
    cases = hard_suite.load_cases()
    clone = copy.deepcopy(cases[-1])
    clone.update(id="reworded-pagination", mechanism="Different prose describing the same fault boundary.")
    assert any("repeats the causal signature" in e for e in hard_suite.novelty_errors([*cases, clone]))


def test_new_design_cannot_reuse_an_original_causal_signature():
    cases = hard_suite.load_cases()
    clone = copy.deepcopy(cases[-1])
    for key, value in zip(("boundary", "failure_mode", "invariant"), hard_suite.FOUNDATION_SIGNATURES["export-join-budget"]):
        clone["novelty"][key] = value
    errors = hard_suite.novelty_errors([*cases[:-1], clone])
    assert any("repeats the causal signature of export-join-budget" in e for e in errors)


def test_invalid_or_self_referential_nearest_case_cannot_support_novelty():
    cases = hard_suite.load_cases()
    cases[-1]["novelty"]["closest_case"] = cases[-1]["id"]
    assert any("closest_case" in e for e in hard_suite.novelty_errors(cases))
    cases[-1]["novelty"]["closest_case"] = "not-a-known-case"
    assert any("closest_case" in e for e in hard_suite.novelty_errors(cases))


def test_custom_catalogue_remains_standalone_and_rejects_duplicate_mechanisms(tmp_path):
    case = hard_suite.get_case("sdk-retry-isolation")
    clone = {**case, "id": "renamed-sdk"}
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "status": "design_only", "cases": [case, clone]}))
    with pytest.raises(ValueError, match="duplicates the mechanism"):
        hard_suite.load_cases(path)
    path.write_text(yaml.safe_dump({"schema_version": 1, "status": "design_only", "cases": [case]}))
    assert hard_suite.load_cases(path) == [case]


def test_prompt_supplies_actual_nearest_design_without_replacing_operator_contract():
    case = hard_suite.get_case("snapshot-cdc-handoff")
    text = hard_suite.prompt(case)
    design = json.loads(text.split("Design JSON (never solver-facing):\n", 1)[1])
    assert design["closest_case_design"]["id"] == "online-index-migration"
    assert design["closest_case_design"]["mechanism"] != case["mechanism"]
    assert design["realism"]["capability_needs"] == case["realism"]["capability_needs"]
    assert "a process kill is not proof" in text
    assert "retroactively undoing a pre-existing outage" in text


def test_new_bug_free_cases_do_not_claim_an_old_functional_failure():
    cases = {c["id"]: c for c in hard_suite.load_cases()}
    for case_id in ("restore-erasure-tombstones", "infrastructure-state-adoption", "fair-tenant-job-admission", "consistent-pagination-snapshot"):
        assert "bugfix" not in cases[case_id]["work"]
    assert {"refactor", "optimization", "bugfix"} <= set(cases["dependency-planner-invalidation"]["work"])
    assert cases["cancellation-pool-exhaustion"]["repair_scope"] == "minimal"
