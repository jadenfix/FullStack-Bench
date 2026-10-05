"""Policy evaluation.

Rules (documented in the skill):
- Deny by default. An explicit deny beats any allow.
- A principal's statements come from every policy named in a binding for it.
- Patterns: '*' matches any run of characters, '?' one character.
- Conditions: every operator in a statement must hold; a missing context key
  makes that condition false.
- Changes propagate: a policy or binding change takes effect
  `propagation_seconds` after it is written (0 unless a fault scenario sets
  it), like eventually consistent IAM on real clouds.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from .clock import Clock
from .identity import Principal
from .kinds import KINDS
from .store import Store

HISTORY = "iam_history"


@lru_cache(maxsize=4096)
def _pattern(glob: str) -> re.Pattern:
    out = []
    for ch in glob:
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
    return re.compile("".join(out) + r"\Z")


def matches(glob: str, value: str) -> bool:
    return bool(_pattern(glob).match(value))


@dataclass
class Decision:
    allowed: bool
    reason: str
    policy: str | None = None
    statement: int | None = None


class PolicyEngine:
    def __init__(self, store: Store, clock: Clock, propagation_seconds: Callable[[], float] = lambda: 0.0):
        self.store = store
        self.clock = clock
        self.propagation_seconds = propagation_seconds

    # Writes go through here so the history (and so propagation) is recorded.
    def record(self, project: str, kind: str, name: str, spec: dict | None) -> float:
        effective_at = self.clock.now() + self.propagation_seconds()
        existing = self.store.kv_items(HISTORY, f"{project}/{kind}/{name}/")
        seq = len(existing) + 1
        self.store.kv_put(HISTORY, f"{project}/{kind}/{name}/{seq:010d}", {"spec": spec, "effective_at": effective_at})
        return effective_at

    def _effective(self, project: str, kind: str, at: float | None = None) -> dict[str, dict]:
        now = self.clock.now() if at is None else at
        latest: dict[str, tuple[str, dict | None]] = {}
        for key, entry in self.store.kv_items(HISTORY, f"{project}/{kind}/"):
            name, seq = key[len(project) + len(kind) + 2:].rsplit("/", 1)
            if entry["effective_at"] <= now and (name not in latest or seq > latest[name][0]):
                latest[name] = (seq, entry["spec"])
        return {n: spec for n, (_, spec) in latest.items() if spec is not None}

    def statements_for(self, principal: Principal, project: str, at: float | None = None) -> list[tuple[str, int, dict]]:
        policies = self._effective(project, "policy", at)
        out = []
        for binding in self._effective(project, "binding", at).values():
            if binding["principal"] != principal.name:
                continue
            for pname in binding["policies"]:
                for i, st in enumerate(policies.get(pname, {}).get("statements", [])):
                    out.append((pname, i, st))
        return out

    def evaluate(self, principal: Principal, action: str, resource: str, project: str,
                 context: dict | None = None, at: float | None = None) -> Decision:
        if principal.is_admin:
            return Decision(True, "admin")
        if principal.project != project:
            return Decision(False, "principal belongs to another project")
        ctx = {"principal": principal.name, "principal_type": principal.type, **(context or {})}
        ctx.update({f"claims.{k}": str(v) for k, v in principal.claims.items()})
        allow = None
        for pname, i, st in self.statements_for(principal, project, at):
            if not any(matches(a, action) for a in st["actions"]):
                continue
            if not any(matches(r, resource) for r in st["resources"]):
                continue
            if not _conditions_hold(st.get("conditions") or {}, ctx):
                continue
            if st["effect"] == "deny":
                return Decision(False, "explicit deny", pname, i)
            allow = allow or Decision(True, "allowed", pname, i)
        return allow or Decision(False, "no statement allows this action")

    def pending(self, project: str) -> list[dict]:
        """IAM changes written but not yet in effect."""
        now = self.clock.now()
        out = []
        for key, entry in self.store.kv_items(HISTORY, f"{project}/"):
            if entry["effective_at"] > now:
                _, kind, name, _seq = key.split("/")
                out.append({"kind": kind, "name": name, "effective_at": entry["effective_at"],
                            "deleted": entry["spec"] is None})
        return out

    def effective_permissions(self, principal: Principal, project: str, resources: list[str],
                              context: dict | None = None) -> list[tuple[str, str]]:
        """Every (action, srn) the principal may perform on the given resources.
        The verifier compares this with a task's stated need to grade least privilege."""
        out = []
        for res in resources:
            kind = res.rsplit(":", 1)[1].split("/", 1)[0]
            for verb in KINDS[kind].verbs:
                action = f"{kind}:{verb}"
                if self.evaluate(principal, action, res, project, context).allowed:
                    out.append((action, res))
        return out


def _conditions_hold(conditions: dict, ctx: dict) -> bool:
    for op, pairs in conditions.items():
        for key, expected in pairs.items():
            if key not in ctx:
                return False
            actual = ctx[key]
            values = expected if isinstance(expected, list) else [expected]
            if op == "string_equals" and str(actual) not in [str(v) for v in values]:
                return False
            if op == "string_not_equals" and str(actual) in [str(v) for v in values]:
                return False
            if op == "string_like" and not any(matches(str(v), str(actual)) for v in values):
                return False
            if op == "bool" and bool(actual) != bool(expected):
                return False
            if op not in ("string_equals", "string_not_equals", "string_like", "bool"):
                return False
    return True

