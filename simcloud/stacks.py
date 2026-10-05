"""Declarative infrastructure: `simcloud.yaml`, plan, apply, drift, import.

A stack is a named set of resources managed by one document. SimCloud keeps
each stack's state (the last-applied, normalised spec of every resource it
manages), so it can tell three things apart:
- a change: the document differs from the stack's state
- drift: live resources differ from the stack's state (someone edited them
  outside the stack)
- a delete: the stack manages a resource the document no longer lists

Document shape:

    project: shop
    iam:                       # project-level kinds (policy, binding, ...)
      policy:
        ci-deploy: {statements: [...]}
    environments:
      prod:
        queue:
          orders: {max_receives: 5, dead_letter_queue: orders-dlq}
"""

import hashlib
from dataclasses import dataclass

from pydantic import ValidationError

from .core import SimCloud
from .errors import SimCloudError
from .identity import Principal
from .kinds import KINDS, PROJECT_SCOPE, validate_spec
from .store import canonical, srn

STACKS = "stacks"
# Apply order: identity first, then data and networking, then compute. Deletes run in reverse.
ORDER = ["service_account", "policy", "binding", "trust", "quota", "secret", "database", "kv", "bucket", "queue",
         "topic", "cache", "repository", "dns_record", "certificate", "firewall_rule", "cluster", "job", "function",
         "service", "edge_function", "cdn", "alert"]
assert sorted(ORDER) == sorted(KINDS), "every kind needs an apply order"

Key = tuple[str, str, str]  # (env, kind, name)


@dataclass
class Change:
    action: str  # create | update | delete | noop
    env: str
    kind: str
    name: str
    before: dict | None
    after: dict | None
    drifted: bool = False  # live differs from the stack's state

    def as_dict(self, project: str) -> dict:
        return {"action": self.action, "env": self.env, "kind": self.kind, "name": self.name,
                "srn": srn(project, self.env, self.kind, self.name), "drifted": self.drifted,
                "diff": _diff(self.before, self.after)}


def parse_document(doc: dict) -> tuple[str, dict[Key, dict]]:
    if not isinstance(doc, dict) or "project" not in doc:
        raise SimCloudError("unprocessable", "document must be a mapping with a 'project' key")
    unknown = set(doc) - {"project", "iam", "environments"}
    if unknown:
        raise SimCloudError("unprocessable", f"unknown top-level keys: {sorted(unknown)}")
    wanted: dict[Key, dict] = {}
    errors = []
    sections = [(PROJECT_SCOPE, doc.get("iam") or {})] + list((doc.get("environments") or {}).items())
    for env, kinds in sections:
        for kind, items in (kinds or {}).items():
            if kind not in KINDS:
                errors.append({"env": env, "kind": kind, "error": "unknown kind"})
                continue
            expected_scope = "project" if env == PROJECT_SCOPE else "env"
            if KINDS[kind].scope != expected_scope:
                where = "under 'iam'" if KINDS[kind].scope == "project" else "under 'environments'"
                errors.append({"env": env, "kind": kind, "error": f"{kind} belongs {where}"})
                continue
            for name, spec in (items or {}).items():
                try:
                    wanted[(env, kind, name)] = validate_spec(kind, spec or {})
                except ValidationError as e:
                    errors.append({"env": env, "kind": kind, "name": name,
                                   "error": e.errors(include_url=False, include_context=False)})
    if errors:
        raise SimCloudError("unprocessable", "document has invalid resources", {"errors": errors})
    return doc["project"], wanted


class Stacks:
    def __init__(self, cloud: SimCloud):
        self.cloud = cloud
        self.store = cloud.store

    def state(self, project: str, stack: str) -> dict[Key, dict]:
        raw = self.store.kv_get(STACKS, f"{project}/{stack}") or {}
        return {tuple(k.split("|")): v for k, v in raw.items()}

    def _save(self, project: str, stack: str, state: dict[Key, dict]) -> None:
        self.store.kv_put(STACKS, f"{project}/{stack}", {"|".join(k): v for k, v in state.items()})

    def plan(self, actor: Principal, stack: str, doc: dict) -> dict:
        project, wanted = parse_document(doc)
        self.cloud.project(project)
        state = self.state(project, stack)
        changes = []
        for key in sorted(set(wanted) | set(state), key=_order_key):
            env, kind, name = key
            live = self.cloud.get(actor, project, env, kind, name) if self._exists(project, key) else None
            live_spec = live["spec"] if live else None
            drifted = key in state and live_spec != state[key]
            if key not in wanted:
                action = "delete" if live else "noop"
                changes.append(Change(action, env, kind, name, live_spec, None, drifted))
            elif live_spec is None:
                changes.append(Change("create", env, kind, name, None, wanted[key], key in state))
            elif live_spec != wanted[key]:
                changes.append(Change("update", env, kind, name, live_spec, wanted[key], drifted))
            else:
                changes.append(Change("noop", env, kind, name, live_spec, wanted[key], drifted))
        out = [c.as_dict(project) for c in changes]
        return {"project": project, "stack": stack, "changes": out, "plan_hash": _hash(out),
                "summary": _summary(out)}

    def apply(self, actor: Principal, stack: str, doc: dict, plan_hash: str | None = None) -> dict:
        plan = self.plan(actor, stack, doc)
        if plan_hash and plan_hash != plan["plan_hash"]:
            raise SimCloudError("conflict", "the plan changed since it was reviewed; run plan again",
                                {"expected": plan_hash, "current": plan["plan_hash"]})
        project, wanted = parse_document(doc)
        state = self.state(project, stack)
        applied = []
        creates_updates = [c for c in plan["changes"] if c["action"] in ("create", "update")]
        deletes = [c for c in plan["changes"] if c["action"] == "delete"][::-1]
        try:
            for c in creates_updates:
                key = (c["env"], c["kind"], c["name"])
                self.cloud.put(actor, project, *key, wanted[key])
                state[key] = wanted[key]
                applied.append(c)
            for c in deletes:
                key = (c["env"], c["kind"], c["name"])
                self.cloud.delete(actor, project, *key)
                state.pop(key, None)
                applied.append(c)
            for c in plan["changes"]:
                key = (c["env"], c["kind"], c["name"])
                if c["action"] == "noop" and key in wanted:
                    state[key] = wanted[key]
                if c["action"] == "noop" and key not in wanted:
                    state.pop(key, None)
        finally:
            # Partial applies keep what succeeded, like real IaC tools.
            self._save(project, stack, state)
        return {**plan, "applied": len(applied)}

    def drift(self, actor: Principal, project: str, stack: str) -> dict:
        out = []
        for key, spec in sorted(self.state(project, stack).items(), key=lambda kv: _order_key(kv[0])):
            env, kind, name = key
            live = self.cloud.get(actor, project, env, kind, name) if self._exists(project, key) else None
            if live is None:
                out.append({"env": env, "kind": kind, "name": name, "drift": "deleted_outside_stack"})
            elif live["spec"] != spec:
                out.append({"env": env, "kind": kind, "name": name, "drift": "modified_outside_stack",
                            "diff": _diff(spec, live["spec"])})
        return {"project": project, "stack": stack, "drift": out}

    def import_resource(self, actor: Principal, project: str, stack: str, env: str, kind: str, name: str) -> dict:
        live = self.cloud.get(actor, project, env, kind, name)
        state = self.state(project, stack)
        for other in self._stacks_managing(project, (env, kind, name), exclude=stack):
            raise SimCloudError("conflict", f"{kind}/{name} is already managed by stack {other}")
        state[(env, kind, name)] = live["spec"]
        self._save(project, stack, state)
        return {"imported": live["srn"]}

    def _stacks_managing(self, project: str, key: Key, exclude: str):
        for k, raw in self.store.kv_items(STACKS, f"{project}/"):
            name = k.split("/", 1)[1]
            if name != exclude and "|".join(key) in raw:
                yield name

    def _exists(self, project: str, key: Key) -> bool:
        return self.store.get(project, *key) is not None


def _order_key(key: Key):
    env, kind, name = key
    return (ORDER.index(kind), env, name)


def _diff(before: dict | None, after: dict | None) -> dict:
    before, after = before or {}, after or {}
    return {k: {"before": before.get(k), "after": after.get(k)}
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}


def _hash(changes: list[dict]) -> str:
    return hashlib.sha256(canonical(changes).encode()).hexdigest()[:16]


def _summary(changes: list[dict]) -> dict:
    counts = {"create": 0, "update": 0, "delete": 0, "noop": 0}
    for c in changes:
        counts[c["action"]] += 1
    counts["drifted"] = sum(1 for c in changes if c["drifted"])
    return counts
