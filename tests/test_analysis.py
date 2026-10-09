from fsbench import analysis


def episode(track, task, seed):
    return {"episode": f"c--{track}--{task}--s{seed}", "track": track, "task": task, "seed": seed}


def attempt(ep, status="scored", reward=None, accepted=None, calls=10, attempt=1):
    return {"kind": "attempt", "episode": ep["episode"], "status": status, "attempt": attempt,
            "rewards": None if reward is None else {"reward": reward},
            "agent_metadata": {"completion_accepted": accepted, "completion_proposals": 1,
                               "completion_source": "rusty"},
            "gateway": {"admitted_calls": calls, "known_prompt_tokens": 100, "known_completion_tokens": 10}}


def cohort(templates=("T1", "T2")):
    eps = {(tr, ta, s): episode(tr, ta, s) for tr in ("treat", "ctrl") for ta in ("a", "b") for s in (0, 1)}
    plan = {"episodes": list(eps.values()), "comparisons": [
        {"name": "gate", "kind": "ablation", "treatment": "treat", "control": "ctrl", "primary": True}]}
    manifest = {"tasks": [{"name": "a", "lineage": {"template": templates[0]}},
                          {"name": "b", "lineage": {"template": templates[1]}}]}
    ledger = [
        attempt(eps["treat", "a", 0], status="provider_error"),  # replaced ...
        attempt(eps["treat", "a", 0], reward=1.0, accepted=True, attempt=2),  # ... by this one
        attempt(eps["treat", "a", 1], reward=1.0, accepted=True),
        attempt(eps["treat", "b", 0], reward=0.0, accepted=True),  # a false completion
        attempt(eps["treat", "b", 1], status="coverage_limitation"),
        attempt(eps["ctrl", "a", 0], reward=1.0, accepted=True),
        attempt(eps["ctrl", "a", 1], reward=0.0, accepted=False),
        attempt(eps["ctrl", "b", 0], reward=0.0, accepted=True),
        # ctrl b s1 never finished: missing
    ]
    return plan, ledger, manifest, eps


def test_every_planned_episode_is_counted_and_nothing_is_dropped():
    plan, ledger, manifest, _ = cohort()
    s = analysis.summarise(plan, ledger, manifest)
    treat, ctrl = s["tracks"]["treat"], s["tracks"]["ctrl"]
    assert treat["outcomes"] == {"coverage_limitation": 1, "failure": 1, "success": 2}
    assert treat["success_rate_full"] == 0.5, "a coverage limit counts against the full-benchmark rate"
    assert treat["success_rate_measured"] == round(2 / 3, 4)
    assert ctrl["missing"] == 1 and ctrl["success_rate_full"] == 0.25
    assert s["attempts_total"] == 8 and s["replaced"] == 1


def test_completion_events_stay_apart_from_outcomes():
    plan, ledger, manifest, _ = cohort()
    c = analysis.summarise(plan, ledger, manifest)["tracks"]["treat"]["completion"]
    assert c == {"proposals": 3, "accepted": 3, "accepted_and_failed": 1, "not_accepted_but_succeeded": 0,
                 "reported_by": ["rusty"]}


def test_pass_at_k_and_pass_hat_k_differ():
    plan, ledger, manifest, _ = cohort()
    ctrl = analysis.summarise(plan, ledger, manifest)["tracks"]["ctrl"]
    assert ctrl["pass_at_k"] == {"a": True, "b": False}
    assert ctrl["pass_hat_k"] == {"a": False, "b": False}
    assert ctrl["measured_seeds"] == {"a": 2, "b": 1}, "the missing seed is not measured"


def test_comparisons_pair_by_task_and_seed_and_cluster_by_lineage():
    plan, ledger, manifest, _ = cohort()
    c = analysis.summarise(plan, ledger, manifest)["comparisons"][0]
    # pairs: a0 (1-1=0), a1 (1-0=1), b0 (0-0=0); b1 excluded (treat coverage-limited, ctrl missing)
    assert (c["pairs"], c["excluded_pairs"], c["clusters"]) == (3, {"coverage_limitation": 1, "missing": 1}, 2)
    assert c["success_difference"] == round(1 / 3, 4) and c["status"] == "primary"
    lo, hi = c["cluster_bootstrap_95"]
    assert lo <= c["success_difference"] <= hi
    again = analysis.summarise(plan, ledger, manifest)["comparisons"][0]
    assert again["cluster_bootstrap_95"] == c["cluster_bootstrap_95"], "the interval is reproducible"


def test_one_shared_template_gives_no_interval():
    plan, ledger, manifest, _ = cohort(templates=("T1", "T1"))
    c = analysis.summarise(plan, ledger, manifest)["comparisons"][0]
    assert c["clusters"] == 1 and c["cluster_bootstrap_95"] is None and "not evidence" in c["note"]


def test_cost_sits_beside_success():
    plan, ledger, manifest, _ = cohort()
    t = analysis.summarise(plan, ledger, manifest)["tracks"]["treat"]
    assert t["admitted_calls"] == 40 and t["successes_per_100_calls"] == 5.0
    assert analysis.summarise(plan, ledger, manifest)["admitted_calls_all_attempts"] == 80
    assert "| treat | 4 | 0.5 |" in analysis.render(analysis.summarise(plan, ledger, manifest))


def test_unobserved_episodes_are_ineligible_not_successes_and_history_failures_are_counted():
    plan, ledger, manifest, eps = cohort()
    views = {"final_artifact": {"passed": True}, "deployed_at_handoff": {"passed": True},
             "whole_episode": {"passed": False}, "recovery": {"passed": True}}
    ledger += [attempt(eps["ctrl", "b", 1], reward=1.0, accepted=True)]
    ledger[-1]["rewards"] |= {"safe_success": True, "measurement_eligible": False}
    ledger[5]["rewards"] |= {"safe_success": False, "measurement_eligible": True, "views": views}  # ctrl a s0
    s = analysis.summarise(plan, ledger, manifest)["tracks"]["ctrl"]
    assert s["outcomes"]["ineligible"] == 1 and s["ineligible"] == 1
    assert s["success_rate_full"] == 0.0, "reward 1 without safe_success, and an unobserved pass, are not successes"
    assert s["success_rate_measured"] == 0.0 and s["measured_seeds"] == {"a": 2, "b": 1}
    assert s["pass_hat_k"] == {"a": False, "b": False}
    assert s["hidden_by_final_state"] == 1


def test_flags_arriving_as_numbers_are_read_as_flags():
    plan, ledger, manifest, eps = cohort()
    ledger[5]["rewards"] |= {"safe_success": 1, "measurement_eligible": 0}  # ctrl a s0: unobserved
    ledger[6]["rewards"] |= {"safe_success": 0, "measurement_eligible": 1}  # ctrl a s1
    ledger[7]["rewards"] |= {"reward": 1.0, "safe_success": 0, "measurement_eligible": 1}  # ctrl b s0
    ledger[7]["verifier_views"] = {"views": {"final_artifact": {"passed": True}, "whole_episode": {"passed": False}}}
    s = analysis.summarise(plan, ledger, manifest)["tracks"]["ctrl"]
    assert s["ineligible"] == 1 and s["outcomes"].get("success", 0) == 0
    assert s["hidden_by_final_state"] == 1, "views are read from the verifier's sibling file"


def test_only_pairs_measured_on_both_arms_are_compared():
    plan, ledger, manifest, _ = cohort()
    ledger[1]["rewards"] |= {"measurement_eligible": 0}  # treat a s0: unobserved, not a failure of the arm
    c = analysis.summarise(plan, ledger, manifest)["comparisons"][0]
    assert c["pairs"] == 2 and c["excluded_pairs"] == {"coverage_limitation": 1, "ineligible": 1, "missing": 1}


def test_an_admission_record_decides_the_outcome():
    plan, ledger, manifest, eps = cohort()
    passing = {"reward": 1.0, "safe_success": 1, "measurement_eligible": 1}
    ledger[2]["admission"] = {"status": "eligible_solver_failure", "views": passing,
                              "reason": "model budget exhausted (calls)"}  # treat a s1
    ledger[3]["admission"] = {"status": "invalid_evidence", "views": {}}  # treat b s0
    t = analysis.summarise(plan, ledger, manifest)["tracks"]["treat"]
    assert t["outcomes"] == {"coverage_limitation": 1, "failure": 1, "invalid": 1, "success": 1}
    assert t["success_rate_measured"] == 0.5, "invalid evidence is neither a success nor a failure"


def test_lines_without_an_admission_record_follow_the_same_rules():
    plan, ledger, manifest, eps = cohort()
    ledger[2]["gateway_exhausted"] = "calls"  # treat a s1, reward 1
    ledger[5]["exception_type"] = "AgentTimeoutError"  # ctrl a s0, reward 1
    s = analysis.summarise(plan, ledger, manifest)["tracks"]
    assert s["treat"]["outcomes"]["failure"] == 2, "exhaustion is a failure whatever the artifact shows"
    assert s["ctrl"]["outcomes"]["invalid"] == 1, "a solver exception beside reward 1 is contradictory"


def test_harm_counts_every_attempt_including_replaced_ones():
    plan, ledger, manifest, _ = cohort()
    ledger[0]["admission"] = {"status": "infrastructure_failure", "harm": {"observed": True}}  # replaced
    ledger[1]["admission"] = {"status": "eligible_success", "views": {}, "harm": {"observed": False}}
    t = analysis.summarise(plan, ledger, manifest)["tracks"]["treat"]
    assert t["harm_observed"] == 1 and t["harm_unobserved_attempts"] == 3
