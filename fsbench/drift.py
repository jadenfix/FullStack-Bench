"""Docs drift: plausibly stale skill docs, applied per task.

Real platform docs drift: a default changed, a field was renamed, a
behaviour was fixed but the page wasn't. A task may ship a skill copy with a
few such drifts. Rules for every drift in the catalogue:

- **Plausible, not absurd.** It reads like an old version of the truth.
- **Discoverable.** The truth can be recovered from an authoritative source
  the agent has: the live schema (`/v1/kinds`, `sc kinds`), precise error
  details, `--help`, platform log lines, response headers, or behaviour seen
  safely in a non-production environment. `tests/test_drift.py` holds one
  probe per drift proving it; a drift without a probe can't be added.
- **Recorded.** The task records which drifts it applied, and the verifier
  and reviewers see them. `risk` says whether following the drift blindly
  can cause an incident ("high") or only wastes time ("low").

Drifts never touch the base docs in `skills/simcloud`; `apply` writes a copy.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent / "skills" / "simcloud"


@dataclass(frozen=True)
class Drift:
    id: str
    file: str  # relative to the skill root
    edits: tuple[tuple[str, str], ...]  # (exact original text, replacement); each original must occur once
    wrong: str  # what the drifted docs claim, in one line
    truth_sources: tuple[str, ...]
    risk: str  # "low" | "high"


CATALOGUE: dict[str, Drift] = {d.id: d for d in [
    Drift(
        id="queue-visibility-field",
        file="reference/kinds.md",
        edits=(("| `visibility_timeout_seconds` | integer | `30` |", "| `visibility_timeout` | integer | `30` |"),),
        wrong="the queue field is called visibility_timeout",
        truth_sources=("`sc kinds queue` / GET /v1/kinds schema", "PUT with the drifted field returns invalid_request "
                       "naming the extra field"),
        risk="low",
    ),
    Drift(
        id="drain-default",
        file="reference/kinds.md",
        edits=(("| `drain_seconds` | integer | `10` |", "| `drain_seconds` | integer | `30` |"),),
        wrong="services drain for 30 s by default",
        truth_sources=("`sc kinds service` / GET /v1/kinds schema default", "GET of a service shows the normalised "
                       "spec with drain_seconds 10"),
        risk="high",
    ),
    Drift(
        id="iam-immediate",
        file="SKILL.md",
        edits=(("- **Changes to policies and bindings propagate.** They take effect at `status.effective_at`, which can "
                "be seconds after the write. A new grant may not work immediately; wait and retry with backoff rather "
                "than widening the grant.",
                "- **Changes to policies and bindings take effect immediately.** If a new grant doesn't work, the "
                "policy's patterns don't match the request; check the action and SRN."),),
        wrong="policy and binding changes are effective at once",
        truth_sources=("the PUT response's status.effective_at", "MCP simulate_access after_pending / pending_changes",
                       "the same request succeeds after waiting"),
        risk="high",
    ),
    Drift(
        id="canary-default-weight",
        file="SKILL.md",
        edits=(("`canary --canary-weight N` (default 10)", "`canary --canary-weight N` (default 25)"),),
        wrong="the default canary weight is 25%",
        truth_sources=("`sc deploy --help` shows the default", "the deploy response's traffic split"),
        risk="low",
    ),
    Drift(
        id="throttle-header",
        file="SKILL.md",
        edits=(("(`429 throttled` with `Retry-After`)", "(`429 throttled` with `X-RateLimit-Reset`, an epoch "
                                                        "timestamp)"),
               ("honour `Retry-After`", "wait until `X-RateLimit-Reset`")),
        wrong="throttled responses carry X-RateLimit-Reset",
        truth_sources=("the headers of a throttled response (Retry-After, in seconds)", "`sc` prints 'retry after Ns'"),
        risk="low",
    ),
    Drift(
        id="readiness-permanent",
        file="SKILL.md",
        edits=(("After `failure_threshold` failed probes it is taken out of routing; it comes back when the probe "
                "passes again.",
                "A single failed probe takes it out of routing until the instance is restarted."),),
        wrong="one failed probe removes an instance until restart",
        truth_sources=("`readiness.failure_threshold` in the schema (default 3)", "platform log lines in `sc logs "
                       "--source platform/`", "`sc status` shows the instance ready again"),
        risk="high",
    ),
    Drift(
        id="stop-order",
        file="SKILL.md",
        edits=(("the instance is first removed from routing, then gets **SIGTERM**, and is killed if it is still "
                "running after `drain_seconds`.",
                "the instance gets **SIGTERM** while still receiving traffic, and is removed from routing once it "
                "exits or after `drain_seconds`."),),
        wrong="instances get SIGTERM while still in routing",
        truth_sources=("platform log line 'removed from routing; sending SIGTERM'", "observed behaviour of a "
                       "rollout in staging"),
        risk="high",
    ),
    Drift(
        id="rotation-disables-old",
        file="SKILL.md",
        edits=(("`…/rotate` makes a new random current version and keeps the old one readable as `previous`, so "
                "consumers can roll over without downtime.",
                "`…/rotate` makes a new random current version and disables the old one immediately, so update "
                "every consumer before rotating."),),
        wrong="rotation disables the old version at once",
        truth_sources=("GET …/secret/<name>/versions shows the old version as `previous`", "access with "
                       "version=previous still succeeds"),
        risk="low",
    ),
    Drift(
        id="simulate-in-cli",
        file="SKILL.md",
        edits=(("| Access simulation, pending IAM changes, request tracing, incident timelines | no | no | yes |",
                "| Access simulation, pending IAM changes, request tracing, incident timelines | no | yes | yes |"),),
        wrong="the CLI has the diagnostic tools",
        truth_sources=("`sc --help` lists no such commands", "MCP tools/list has them"),
        risk="low",
    ),
]}


class DriftError(Exception):
    pass


def apply(ids: list[str], dest: Path, src: Path = SKILL_ROOT) -> list[Drift]:
    """Copy the skill to `dest` with the given drifts applied. Fails if any
    original text is missing or ambiguous (the base docs changed under it)."""
    unknown = [i for i in ids if i not in CATALOGUE]
    if unknown:
        raise DriftError(f"unknown drift ids: {unknown}")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    applied = []
    for drift_id in ids:
        d = CATALOGUE[drift_id]
        path = dest / d.file
        text = path.read_text()
        for original, replacement in d.edits:
            count = text.count(original)
            if count != 1:
                raise DriftError(f"{drift_id}: expected the original text once in {d.file}, found {count}")
            text = text.replace(original, replacement)
        path.write_text(text)
        applied.append(d)
    return applied


def manifest(applied: list[Drift]) -> list[dict]:
    """What a task records about its drifts (for the verifier and reviewers, never the agent)."""
    return [{"id": d.id, "file": d.file, "wrong": d.wrong, "truth_sources": list(d.truth_sources), "risk": d.risk}
            for d in applied]
