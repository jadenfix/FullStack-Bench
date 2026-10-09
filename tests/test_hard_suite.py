"""Design coverage and adversarial checks of curated candidate boundaries."""

import json
from types import SimpleNamespace

import pytest

from fsbench import author, hard_suite
from fsbench.llm import LLMError


def test_case_matrix_covers_bug_free_and_buggy_work_with_both_repair_scopes():
    cases = hard_suite.load_cases()
    assert len(cases) == 29
    combinations = {frozenset(c["work"]) for c in cases}
    for work in ({"bugfix"}, {"optimization"}, {"refactor"},
                 {"optimization", "bugfix"}, {"refactor", "bugfix"},
                 {"refactor", "optimization"}, {"refactor", "optimization", "bugfix"},
                 {"cloud", "bugfix"}, {"cloud", "feature"}):
        assert frozenset(work) in combinations
    assert {c["repair_scope"] for c in cases} == {"minimal", "structural", "extension"}
    assert any("c" in c["languages"] for c in cases)
    assert any("browser-cli" in c["interfaces"] and "mcp-stdio" in c["interfaces"] for c in cases)


@pytest.mark.parametrize("mutation", [
    lambda c: c["controls"][0].update(fails="unmentioned-rule"),
    lambda c: c["limits"].update(logical_p99_ms=float("nan")),
    lambda c: c["requirements"].append(c["requirements"][0]),
    lambda c: c["performance"].update(sizes=[2000, 8000, 10000]),
    lambda c: c["performance"].update(repetitions=True),
])
def test_invalid_design_cannot_enter_authoring(mutation):
    case = hard_suite.get_case("export-join-budget")
    mutation(case)
    assert hard_suite.validate_case(case)


def candidate(tmp_path):
    case = hard_suite.get_case("export-join-budget")
    (tmp_path / "tests").mkdir()
    (tmp_path / "wrong_solutions").mkdir()
    (tmp_path / "task.toml").write_text('''[metadata]
author_model = "z-ai/glm-5.3"
author_seed = 7
hard_case_id = "export-join-budget"
[agent]
timeout_sec = 10800
[environment]
cpus = 2
memory_mb = 4096
''')
    mapping = {}
    outcomes = {**hard_suite.COMMON_REQUIREMENTS, **{r["id"]: r["outcome"] for r in case["requirements"]}}
    brief = "\n".join(outcomes.values())
    (tmp_path / "instruction.md").write_text(brief)
    tests = []
    for key, quote in outcomes.items():
        test = "test_" + key.replace("-", "_")
        mapping[key] = {"brief_quote": quote, "test_name": test, "fact_paths": ["instruction.md"]}
        tests.append(f"def {test}():\n    pass\n")
    (tmp_path / "tests/test_outputs.py").write_text("\n".join(tests))
    (tmp_path / "tests/requirement_map.json").write_text(json.dumps(mapping))
    controls = {}
    for control in case["controls"]:
        path = f"wrong_solutions/{control['id']}.sh"
        (tmp_path / path).write_text("exit 0\n")
        controls[control["id"]] = {"requirement_id": control["fails"], "path": path,
                                  "test_name": mapping[control["fails"]]["test_name"]}
    (tmp_path / "tests/negative_control_map.json").write_text(json.dumps(controls))
    (tmp_path / "tests/performance_contract.json").write_text(json.dumps(case["performance"]))
    return case


def test_contract_static_success_does_not_claim_runtime_qualification(tmp_path):
    case = candidate(tmp_path)
    assert hard_suite.candidate_errors(tmp_path, case, "z-ai/glm-5.3", 7) == []


@pytest.mark.parametrize("file,mutate", [
    ("tests/requirement_map.json", lambda d: d.pop("parity")),
    ("tests/requirement_map.json", lambda d: d["parity"].update(brief_quote="hidden acceptance condition")),
    ("tests/requirement_map.json", lambda d: d["parity"].update(fact_paths=["../operator-secret"])),
    ("tests/negative_control_map.json", lambda d: d["drop-duplicates"].update(test_name="test_growth")),
    ("tests/negative_control_map.json", lambda d: d["drop-duplicates"].update(path="solution/solve.sh")),
    ("tests/performance_contract.json", lambda d: d.update(min_speedup=0.01)),
])
def test_contract_rejects_missing_hidden_or_weakened_conditions(tmp_path, file, mutate):
    case = candidate(tmp_path)
    path = tmp_path / file
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc))
    assert hard_suite.candidate_errors(tmp_path, case, "z-ai/glm-5.3", 7)


def test_task_prompt_is_operator_only_and_retains_bug_free_design():
    spec = hard_suite.plan("cache-index-extraction", 7)
    assert spec == hard_suite.plan("cache-index-extraction", 7)
    text = author.task_prompt(spec)
    assert "starting system must pass all old functional contracts" in text
    assert "do not grade taste outside taste/catalogue.yaml" in text
    assert "request_count" in text
    assert "candidate_errors" not in text


def test_hard_authoring_refuses_same_family_before_making_client(monkeypatch):
    monkeypatch.setattr(author, "Client", lambda **kw: pytest.fail("must reject before client setup"))
    with pytest.raises(ValueError, match="different model family"):
        author.run(7, "z-ai/glm-5.3", 0, False, hard_case_id="export-join-budget", qa_model="z-ai/glm-5.2")


def test_missing_credentials_create_honest_failure_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(author, "RUNS", tmp_path)
    def missing(**kwargs):
        raise LLMError("credentials unavailable")
    monkeypatch.setattr(author, "Client", missing)
    result = author.run(7, "z-ai/glm-5.3", 0, False, "missing", "export-join-budget")
    assert result["status"] == "llm_error"
    assert json.loads((tmp_path / "missing/result.json").read_text())["status"] == "llm_error"
    assert not list(tmp_path.rglob("solve.sh"))


def test_docker_preflight_prevents_paid_generation_without_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(author, "RUNS", tmp_path)
    monkeypatch.setattr(author, "Client", lambda **kw: SimpleNamespace(chat=lambda *a, **k: pytest.fail("no paid call")))
    monkeypatch.setattr(author.shutil, "which", lambda name: None)
    result = author.run(7, "z-ai/glm-5.3", 0, False, "missing", "export-join-budget")
    assert result["status"] == "environment_unavailable"


@pytest.mark.parametrize("verdict", [
    {"ok": True, "findings": ["omitted requirement"]},
    {"ok": False, "findings": []}, {"ok": "true", "findings": []},
])
def test_qa_cannot_pass_inconsistent_verdict(tmp_path, verdict):
    (tmp_path / "instruction.md").write_text("request")
    client = SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(text=json.dumps(verdict)))
    with pytest.raises(LLMError, match="malformed verdict"):
        author.review_candidate(client, tmp_path, hard_suite.plan("export-join-budget", 7), "nvidia/nemotron-3-ultra-550b-a55b")


def test_different_model_family_review_is_bounded_and_returns_findings(tmp_path):
    (tmp_path / "instruction.md").write_text("request")
    calls = []
    def chat(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(text='{"ok": false, "findings": ["no real traffic"]}')
    verdict, _ = author.review_candidate(SimpleNamespace(chat=chat), tmp_path,
        hard_suite.plan("export-join-budget", 7), "nvidia/nemotron-3-ultra-550b-a55b")
    assert verdict["findings"] == ["no real traffic"]
    assert calls[0][1]["attempts"] == 1 and calls[0][1]["max_tokens"] == 4000


def test_symlink_cannot_claim_external_fact_location(tmp_path):
    case = candidate(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("not solver-discoverable")
    (tmp_path / "outside-link").symlink_to(outside)
    path = tmp_path / "tests/requirement_map.json"
    mapping = json.loads(path.read_text())
    mapping["parity"]["fact_paths"] = ["outside-link"]
    path.write_text(json.dumps(mapping))
    assert hard_suite.candidate_errors(tmp_path, case, "z-ai/glm-5.3", 7)
