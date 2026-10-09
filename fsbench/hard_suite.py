"""Curated design inputs for NVIDIA authoring; none are qualified tasks.

The catalogue is operator-only. Its causal mechanisms and controls must never
be copied into the customer-facing instruction or solver workspace.
"""

import json
import math
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = Path(__file__).with_name("hard_suite.yaml")
INCIDENT_CATALOGUE = Path(__file__).with_name("hard_suite_incidents.yaml")
WORK = {"bugfix", "refactor", "optimization", "cloud", "feature"}
# Preserve the first catalogue's bytes while comparing its causal mechanisms
# with new designs. These labels are operator metadata, never solver hints.
FOUNDATION_SIGNATURES = {
    "sdk-retry-isolation": ("sdk-refresh-durable-receipt", "operation-identity-diverges-after-lost-reply", "tenant-scoped-authorized-replay"),
    "mcp-cli-stream-resume": ("stream-session-cancellation-job", "request-id-conflated-with-job-id", "independent-resumable-bounded-streams"),
    "reconciliation-linearization": ("invoice-payment-match-replay", "quadratic-global-display-number-join", "exact-tenant-balances-with-linear-growth"),
    "export-join-budget": ("export-snapshot-enrichment", "repeated-correct-membership-scan", "exact-export-with-bounded-growth"),
    "control-plane-extraction": ("adapter-global-control-plane-state", "instances-share-mutable-config", "independent-state-and-release"),
    "inventory-outbox-recovery": ("reservation-database-broker-lease", "split-write-and-unfenced-compensation", "stock-conservation-through-recovery"),
    "region-lease-failover": ("replica-watermark-leader-epoch", "standby-admitted-before-ack-watermark", "durable-acks-with-fenced-old-leader"),
    "tenant-preview-infrastructure": ("preview-resource-identity-retention", "preview-lifecycle-not-yet-implemented", "isolated-replayable-preview-lifecycle"),
    "online-index-migration": ("index-generation-access-epoch", "quadratic-query-and-stale-revocation", "authorized-current-results-through-cutover"),
    "binary-ingest-framing": ("wire-frame-decoder-sequence-ledger", "read-boundary-and-growing-buffer-copy", "exact-bounded-durable-ingest"),
    "approval-product-evolution": ("approval-role-state-transition", "adapter-policy-and-state-check-diverge", "authorized-once-only-payout"),
    "build-provenance-cache": ("build-input-tested-served-digest", "generator-lockfile-omitted-from-cache", "same-tested-artifact-promoted"),
    "cache-index-extraction": ("cache-instance-adapter-invalidation", "global-state-and-collection-scan", "independent-fresh-efficient-cache"),
}
COMMON_REQUIREMENTS = {
    "evidence-intact": "Operator evidence is intact and bound to the task and serving artifacts.",
    "no-incidents": "No solver-caused SEV1 or SEV2 incident during the entire episode.",
    "live-slo": "Scheduled customer requests meet the published latency and error budgets throughout the change window.",
    "fresh-rebuild": "Declared source and infrastructure artifacts rebuild on a fresh SimCloud and pass the same journeys.",
}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def validate_case(case: dict) -> list[str]:
    errors = []
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,79}", case.get("id", "")):
        errors.append("invalid case id")
    for key in ("title", "domain", "mechanism", "symptoms", "evolution"):
        if not isinstance(case.get(key), str) or not case[key].strip():
            errors.append(f"{key} must be a nonempty string")
    for key, minimum in (("layers", 5), ("interfaces", 3), ("languages", 1),
                         ("schedule", 4), ("investigation", 3), ("harness_failures", 3)):
        values = case.get(key, [])
        if not isinstance(values, list) or len(values) < minimum or any(not isinstance(v, str) or not v for v in values):
            errors.append(f"{key} needs at least {minimum} strings")
        elif len(set(values)) != len(values):
            errors.append(f"{key} contains duplicates")
    work = case.get("work", [])
    if not isinstance(work, list) or not work or any(w not in WORK for w in work) or len(set(work)) != len(work):
        errors.append("work contains unknown or duplicate dimensions")
    if case.get("architecture") not in {"sound", "coupled"}:
        errors.append("architecture must be sound or coupled")
    if case.get("repair_scope") not in {"minimal", "structural", "extension"}:
        errors.append("unknown repair_scope")
    limits = case.get("limits", {})
    for key in ("agent_seconds", "cpus", "memory_mb", "logical_p99_ms", "soak_seconds", "request_count"):
        if not _positive(limits.get(key)):
            errors.append(f"limits.{key} must be positive and finite")
    if not _positive(limits.get("error_budget")) or limits["error_budget"] >= 1:
        errors.append("error_budget must be between zero and one")
    requirements = case.get("requirements", [])
    ids = [r.get("id") for r in requirements]
    if len(requirements) < 4 or len(set(ids)) != len(ids) or set(ids) & COMMON_REQUIREMENTS.keys():
        errors.append("need four unique case requirements without reserved ids")
    for requirement in requirements:
        if any(not isinstance(requirement.get(k), str) or not requirement[k].strip() for k in ("id", "outcome", "probe")):
            errors.append("requirements need id, outcome and probe")
    controls = case.get("controls", [])
    if len(controls) < 4 or len({c.get("id") for c in controls}) != len(controls):
        errors.append("need four unique negative controls")
    for control in controls:
        if control.get("fails") not in ids or not control.get("shortcut"):
            errors.append("each control must target an explicit case requirement")
    if "optimization" in work:
        perf = case.get("performance", {})
        sizes = perf.get("sizes", [])
        if len(sizes) < 3 or any(not isinstance(n, int) or isinstance(n, bool) or n <= 0 for n in sizes) or sizes != sorted(set(sizes)):
            errors.append("performance needs three increasing positive integer sizes")
        elif len({sizes[i + 1] / sizes[i] for i in range(len(sizes) - 1)}) != 1:
            errors.append("performance sizes must have one declared growth factor")
        for key in ("min_speedup", "max_growth_ratio", "max_peak_rss_mb"):
            if not _positive(perf.get(key)):
                errors.append(f"performance.{key} must be positive and finite")
        if not isinstance(perf.get("repetitions"), int) or isinstance(perf.get("repetitions"), bool) or perf["repetitions"] < 3:
            errors.append("performance needs at least three repetitions")
        if not perf.get("workload"):
            errors.append("performance needs a named end-to-end workload")
        distributions = perf.get("distributions", [])
        if not isinstance(distributions, list) or len(distributions) < 2 or any(not isinstance(d, str) or not d for d in distributions) or len(set(distributions)) != len(distributions):
            errors.append("performance needs at least two named input distributions")
    elif "performance" in case:
        errors.append("a performance contract requires the optimization dimension")
    if case.get("batch") == "incident-workflows-v2":
        realism = case.get("realism", {})
        if not isinstance(realism, dict):
            errors.append("realism must be an object")
            realism = {}
        for key in ("trigger", "baseline", "impact", "scale", "change_window", "mitigation"):
            if not isinstance(realism.get(key), str) or len(realism[key].strip()) < 20:
                errors.append(f"realism.{key} needs concrete operational context")
        for key, minimum in (("protected", 3), ("capability_needs", 2)):
            values = realism.get(key, [])
            if (not isinstance(values, list) or len(values) < minimum
                    or any(not isinstance(v, str) or not v.strip() for v in values)):
                errors.append(f"realism.{key} needs at least {minimum} named constraints")
        sources = realism.get("sources", [])
        if not isinstance(sources, list) or not sources or any(not isinstance(s, dict)
                              or not isinstance(s.get("title"), str) or not s["title"].strip()
                              or not isinstance(s.get("url"), str) or not s["url"].startswith("https://") for s in sources):
            errors.append("realism.sources needs named primary-source URLs")
        novelty = case.get("novelty", {})
        if not isinstance(novelty, dict):
            errors.append("novelty must be an object")
            novelty = {}
        for key in ("boundary", "failure_mode", "invariant", "closest_case", "distinction"):
            if not isinstance(novelty.get(key), str) or not novelty[key].strip():
                errors.append(f"novelty.{key} is required")
    return errors


def novelty_errors(cases: list[dict]) -> list[str]:
    """Reject exact causal reskins; independent QA still reviews semantic novelty."""
    ids = {case["id"] for case in cases}
    signatures, errors = {}, []
    mechanisms = {}
    for case in cases:
        mechanism = " ".join(case["mechanism"].casefold().split())
        if mechanism in mechanisms:
            errors.append(f"{case['id']}: duplicates the mechanism of {mechanisms[mechanism]}")
        mechanisms[mechanism] = case["id"]
        novelty = case.get("novelty")
        signature = (tuple(" ".join(novelty[key].casefold().split()) for key in ("boundary", "failure_mode", "invariant"))
                     if novelty else FOUNDATION_SIGNATURES.get(case["id"]))
        if signature is None:
            continue
        if signature in signatures:
            errors.append(f"{case['id']}: repeats the causal signature of {signatures[signature]}")
        signatures[signature] = case["id"]
        if novelty and (novelty["closest_case"] not in ids or novelty["closest_case"] == case["id"]):
            errors.append(f"{case['id']}: closest_case must refer to a different known case")
    return errors


def load_cases(path: Path = CATALOGUE) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    if data.get("schema_version") != 1 or data.get("status") != "design_only":
        raise ValueError("catalogue must use schema 1 and design_only status")
    cases = data["cases"]
    if path.resolve() == CATALOGUE.resolve():
        incidents = yaml.safe_load(INCIDENT_CATALOGUE.read_text())
        if incidents.get("schema_version") != 1 or incidents.get("status") != "design_only":
            raise ValueError("incident catalogue must use schema 1 and design_only status")
        if any(c.get("batch") != "incident-workflows-v2" for c in incidents["cases"]):
            raise ValueError("incident catalogue cases must declare their batch")
        cases.extend(incidents["cases"])
    if len({c.get("id") for c in cases}) != len(cases):
        raise ValueError("duplicate case id")
    for case in cases:
        errors = validate_case(case)
        if errors:
            raise ValueError(f"{case.get('id')}: {'; '.join(errors)}")
    errors = novelty_errors(cases)
    if errors:
        raise ValueError("; ".join(errors))
    return cases


def get_case(case_id: str) -> dict:
    for case in load_cases():
        if case["id"] == case_id:
            return case
    raise ValueError(f"unknown case {case_id!r}")


def plan(case_id: str, seed: int) -> dict:
    case = get_case(case_id)
    return {
        "seed": seed, "primary_area": case["layers"][0],
        "secondary_areas": case["layers"][1:], "domain": case["domain"],
        "vendors": case.get("vendors", []), "drifts": [], "steps_min": 8,
        "layers_min": len(case["layers"]), "hard_case": case,
        "weirdness": {"id": case["id"], "mechanism": case["mechanism"], "signals": case["investigation"]},
    }


def prompt(case: dict) -> str:
    context = {**case, "common_requirements": COMMON_REQUIREMENTS}
    if case.get("novelty"):
        context["closest_case_design"] = get_case(case["novelty"]["closest_case"])
    return """# Operator-only hard-case design
Build a healthy golden system before introducing the causal failures below.
For bug-free cases the starting system must pass all old functional contracts;
it fails only the declared new scale, product or deployment requirement. Do not
inject an unrelated bug just to make those cases harder. For sound architectures
accept a focused repair; for coupled systems test independent state ownership,
recovery and deployment behavior rather than file layout or patch size.

All graded conditions, resource ceilings, frame limits, snapshot and watermark
semantics, fault precedence and compatibility obligations belong in instruction.md.
Do not copy this design's mechanism, controls, or harness diagnostics into it.
All deciding facts must be discoverable in source, live state or offline docs.
Use real serving services and actual browser/CLI/MCP/SDK clients, not a fake
function labeled as another interface. Native tasks ship a pinned compiler and
sanitizers. Browser tasks ship one pinned browser and CLI for both tracks.
MCP protocol version and full required wire contracts must be pinned and documented.
Any missing platform capability must be named in BUILD_NOTES.md; never simulate
passing evidence or silently replace a real client journey with a unit test.

Use barriers and observed preconditions for fault schedules, not random sleeps.
The operator collector owns timing, arrival schedules, durable-state snapshots,
cgroup/process memory and immutable artifact identity. Include queue delay,
backoff and retries in logical latency; no coordinated omission. Maintain live
synthetic traffic throughout the episode. A nop must fail for the intended reason
and every negative control must fail its named probe, not an unrelated gate.

Optimization cases publish three sizes, five repetitions, multiple key
distributions, correctness and memory budgets. Warm up consistently; interleave
reference and candidate samples on the same pinned machine. Time the complete
workload and verify every output before awarding speed. A growth ratio is an
empirical scaling check, not a proof of asymptotic complexity. Calibrate budgets
against two independent valid implementations before freezing, never against
one favored model. Absolute product SLOs still apply; publish any CPU floor that
controls meaningful measurement. Preserve raw samples and noise diagnostics.

Include tests/requirement_map.json: an object keyed by every case requirement id
and these common ids: evidence-intact, no-incidents, live-slo, fresh-rebuild.
Each value has a literal nonempty brief_quote from instruction.md, a test_name
defined in tests/test_outputs.py, and fact_paths (existing task-relative files).
Several facts may share a test but no requirement may be omitted.
Include tests/negative_control_map.json: keyed by every control id, each value
has requirement_id, a path under wrong_solutions/ and its rejecting test_name.
The rejecting test must be the mapped requirement's test. Controls are full
solutions with just that shortcut; save per-probe control receipts when gating.
Include tests/performance_contract.json equal to the performance object below
when present. This pins design budgets; changes require a new design revision.
If there is a structural requirement, exercise it through independent instances,
rollback and later evolution; do not grade taste outside taste/catalogue.yaml.
Keep reward binary AND and the existing separate practices and style scores.
The later requirement is stated up front and delivered as an operator event.
Record actual author_model, author_seed and hard_case_id in task metadata.

For incident-workflows-v2 preserve the concrete trigger, affected-user impact,
representative scale, safe mitigation, protected state and discovery evidence.
Define the precise product policies left as design choices (supported recurrence
subset, merge policy, expiry behavior, encryption-version retention) in the brief
before grading. Do not infer those policies from a preferred implementation.
Explain baseline-attributed degradation and the mitigation deadline up front;
no task may require retroactively undoing a pre-existing outage or data leak.
Use bounded fixture-scale workloads and pinned dependencies to reproduce the
same failure boundary; do not claim that small fixture counts prove production
throughput. Measure foreground traffic and recovery as well as final state.
No wall-clock waiting for an actual DST date, nightly job or expiry is required:
use controlled event releases or a documented clock where the mechanism allows.

The closest_case_design is supplied only for independent novelty review. A new
company name or error string is insufficient: the fault boundary, evidence and
required repair must differ meaningfully. Review for overlap with this design,
then author a distinct system. This comparison and causal metadata remain
operator-only. Copy primary-source documentation for pinned dependency versions
into the offline bundle. Name absent capability_needs in BUILD_NOTES.md; a mock
success flag cannot stand in for logical replication, OpenTofu state transitions,
real browser profiles, connected socket destination checks or storage crashes.
In particular, a process kill is not proof of simulated power-loss persistence.

Design JSON (never solver-facing):
""" + json.dumps(context, indent=2)


def _local_file(task: Path, name: str, parent: str | None = None) -> Path | None:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        return None
    root = task.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or (parent and not path.is_relative_to(root / parent)):
        return None
    return path if path.is_file() else None


def candidate_errors(task: Path, case: dict, author_model: str, seed: int) -> list[str]:
    """Additional necessary static conditions; this is not semantic QA or qualification."""
    errors = []
    try:
        meta = tomllib.loads((task / "task.toml").read_text())["metadata"]
        for key, expected in (("author_model", author_model), ("author_seed", seed), ("hard_case_id", case["id"])):
            if meta.get(key) != expected:
                errors.append(f"metadata.{key} must record actual value {expected!r}")
        limits = tomllib.loads((task / "task.toml").read_text())
        if limits["agent"]["timeout_sec"] != case["limits"]["agent_seconds"]:
            errors.append("agent timeout differs from the curated budget")
        for key in ("cpus", "memory_mb"):
            if limits["environment"][key] != case["limits"][key]:
                errors.append(f"environment.{key} differs from the curated budget")
        mapping = json.loads((task / "tests/requirement_map.json").read_text())
        expected_ids = set(COMMON_REQUIREMENTS) | {r["id"] for r in case["requirements"]}
        if set(mapping) != expected_ids:
            errors.append("requirement map must cover exactly the declared requirements")
        brief = (task / "instruction.md").read_text()
        tests = (task / "tests/test_outputs.py").read_text()
        for key, item in mapping.items():
            quote = item.get("brief_quote")
            if not isinstance(quote, str) or not quote.strip() or quote not in brief:
                errors.append(f"{key}: acceptance quote missing from brief")
            test = item.get("test_name", "")
            if not re.fullmatch(r"test_[a-zA-Z0-9_]+", test) or not re.search(rf"^def {re.escape(test)}\(", tests, re.M):
                errors.append(f"{key}: outcome test missing")
            facts = item.get("fact_paths", [])
            if not facts or any(_local_file(task, path) is None for path in facts):
                errors.append(f"{key}: fact locations missing or outside the task")
        controls = json.loads((task / "tests/negative_control_map.json").read_text())
        if set(controls) != {c["id"] for c in case["controls"]}:
            errors.append("control map must cover exactly the declared shortcuts")
        for control in case["controls"]:
            item = controls.get(control["id"], {})
            if item.get("requirement_id") != control["fails"]:
                errors.append(f"{control['id']}: wrong rejecting requirement")
            if item.get("test_name") != mapping.get(control["fails"], {}).get("test_name"):
                errors.append(f"{control['id']}: wrong rejecting probe")
            path = _local_file(task, item.get("path"), "wrong_solutions")
            if path is None or path.suffix != ".sh":
                errors.append(f"{control['id']}: missing full negative solution")
        if "performance" in case:
            actual = json.loads((task / "tests/performance_contract.json").read_text())
            if actual != case["performance"]:
                errors.append("performance contract differs from the declared design")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append(f"hard-case contract missing or malformed ({type(error).__name__})")
    return errors
