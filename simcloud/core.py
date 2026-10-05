"""The SimCloud core: every operation authorises, audits and then acts.

The HTTP API, the CLI and the MCP server are thin layers over this class, so
all three behave identically and every call lands in the audit log.
"""

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from .clock import Clock
from .errors import SimCloudError
from .faults import FaultEngine
from .identity import Principal, Tokens
from .kinds import KINDS, PROJECT_SCOPE, validate_spec
from .policy import PolicyEngine
from .store import Store, srn

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
PROJECTS = "projects"
IAM_KINDS = ("policy", "binding")


class SimCloud:
    def __init__(self, store: Store, clock: Clock, admin_token: str | None):
        self.store = store
        self.clock = clock
        self.faults = FaultEngine(clock)
        self.tokens = Tokens(store, clock, admin_token)
        self.policy = PolicyEngine(store, clock, lambda: self.faults.iam_propagation_seconds)
        # Hooks (delivery stops deleted services; the guard watches for critical issues):
        self.on_put: list = []     # (actor, project, env, kind, name, spec, previous_spec)
        self.on_delete: list = []  # (actor, project, env, kind, name)
        self.on_action: list = []  # (actor, project, env, action, resource) for every allowed action

    # ---- projects --------------------------------------------------------

    def create_project(self, actor: Principal, name: str, environments: list[str], regions: list[str]) -> dict:
        self._require_admin(actor, "project:create", f"srn:simcloud:{name}")
        for n in [name, *environments, *regions]:
            _check_name(n)
        if self.store.kv_get(PROJECTS, name):
            raise SimCloudError("conflict", f"project {name} exists")
        project = {"name": name, "environments": environments, "regions": regions}
        self.store.kv_put(PROJECTS, name, project)
        return project

    def project(self, name: str) -> dict:
        p = self.store.kv_get(PROJECTS, name)
        if not p:
            raise SimCloudError("not_found", f"project {name} not found")
        return p

    # ---- authorisation ---------------------------------------------------

    def authorize(self, actor: Principal, action: str, resource: str, project: str, env: str | None = None,
                  regions: list[str] | None = None) -> None:
        if not actor.is_admin:
            self.faults.control_plane(action, regions)
        context = {"env": env} if env else {}
        decision = self.policy.evaluate(actor, action, resource, project, context)
        self.store.audit(actor.name, action, resource, "allowed" if decision.allowed else "denied",
                         {"reason": decision.reason, "policy": decision.policy})
        if not decision.allowed:
            raise SimCloudError("access_denied", f"{actor.name} may not {action} on {resource}",
                                {"reason": decision.reason})
        for hook in self.on_action:
            hook(actor, project, env, action, resource)

    def _require_admin(self, actor: Principal, action: str, resource: str) -> None:
        self.store.audit(actor.name, action, resource, "allowed" if actor.is_admin else "denied", {})
        if not actor.is_admin:
            raise SimCloudError("access_denied", f"{action} is reserved for the platform operator")

    def _scope(self, project: str, env: str, kind: str) -> None:
        if kind not in KINDS:
            raise SimCloudError("not_found", f"unknown kind {kind!r}", {"kinds": sorted(KINDS)})
        p = self.project(project)
        if KINDS[kind].scope == "project":
            if env != PROJECT_SCOPE:
                raise SimCloudError("invalid_request", f"{kind} is project-level; use environment '{PROJECT_SCOPE}'")
        elif env not in p["environments"]:
            raise SimCloudError("not_found", f"environment {env!r} not in project {project}",
                                {"environments": p["environments"]})

    # ---- resources -------------------------------------------------------

    def get(self, actor: Principal, project: str, env: str, kind: str, name: str) -> dict:
        self._scope(project, env, kind)
        self.authorize(actor, f"{kind}:read", srn(project, env, kind, name), project, env)
        r = self.store.get(project, env, kind, name)
        if not r:
            raise SimCloudError("not_found", f"{kind}/{name} not found in {project}/{env}")
        return r

    def list_resources(self, actor: Principal, project: str, env: str, kind: str) -> list[dict]:
        self._scope(project, env, kind)
        self.authorize(actor, f"{kind}:list", srn(project, env, kind, "*"), project, env)
        return self.store.list_resources(project, env, kind)

    def put(self, actor: Principal, project: str, env: str, kind: str, name: str, spec: dict,
            expect_version: int | None = None) -> dict:
        self._scope(project, env, kind)
        _check_name(name)
        previous = self.store.get(project, env, kind, name)
        exists = previous is not None
        regions = spec.get("regions") if isinstance(spec.get("regions"), list) else None
        self.authorize(actor, f"{kind}:{'update' if exists else 'create'}", srn(project, env, kind, name), project, env,
                       regions)
        try:
            normalised = validate_spec(kind, spec)
        except ValidationError as e:
            raise SimCloudError("invalid_request", f"invalid {kind} spec",
                                {"errors": e.errors(include_url=False, include_context=False)})
        if not exists:
            self._check_quota(project, env, kind)
        status = None if exists else _initial_status(kind)
        resource = self.store.put(project, env, kind, name, normalised, status, expect_version)
        if kind in IAM_KINDS:
            resource["status"]["effective_at"] = self.policy.record(project, kind, name, normalised)
            self.store.set_status(project, env, kind, name, resource["status"])
        for hook in self.on_put:
            hook(actor, project, env, kind, name, normalised, previous["spec"] if previous else None)
        return resource

    def delete(self, actor: Principal, project: str, env: str, kind: str, name: str) -> None:
        self._scope(project, env, kind)
        self.authorize(actor, f"{kind}:delete", srn(project, env, kind, name), project, env)
        if not self.store.delete(project, env, kind, name):
            raise SimCloudError("not_found", f"{kind}/{name} not found in {project}/{env}")
        if kind in IAM_KINDS:
            self.policy.record(project, kind, name, None)
        for hook in self.on_delete:
            hook(actor, project, env, kind, name)

    def _check_quota(self, project: str, env: str, kind: str) -> None:
        for quota in self.store.list_resources(project, PROJECT_SCOPE, "quota"):
            limit = quota["spec"]["limits"].get(kind)
            if limit is not None and len(self.store.list_resources(project, env, kind)) >= limit:
                raise SimCloudError("quota_exceeded", f"quota {quota['name']} allows {limit} {kind} per environment",
                                    {"quota": quota["name"], "limit": limit})

    # ---- audit -----------------------------------------------------------

    def audit_log(self, actor: Principal, project: str, since_seq: int = 0, limit: int = 500) -> list[dict]:
        self.authorize(actor, "audit:read", f"srn:simcloud:{project}", project)
        records = self.store.audit_records(since_seq, limit)
        base = f"srn:simcloud:{project}"
        return [r for r in records if r["srn"] == base or r["srn"].startswith(base + ":")]

    # ---- tokens ----------------------------------------------------------

    def list_tokens(self, actor: Principal, project: str) -> list[dict]:
        self.authorize(actor, "token:list", f"srn:simcloud:{project}", project)
        return self.tokens.list_for_project(project)

    def revoke_token(self, actor: Principal, project: str, token_id: str) -> None:
        self.authorize(actor, "token:revoke", f"srn:simcloud:{project}", project)
        if not any(t["id"] == token_id for t in self.tokens.list_for_project(project)):
            raise SimCloudError("not_found", f"token {token_id} not found in {project}")
        self.tokens.revoke(token_id)

    # ---- seeding (platform operator only) --------------------------------

    def apply_seed(self, seed: dict, admin: Principal) -> dict[str, str]:
        """Create projects, resources and principals from a task's seed file.
        Returns {principal: token} for principals that asked for one; the
        server writes each token to the principal's token_file."""
        issued = {}
        for p in seed.get("projects", []):
            self.create_project(admin, p["name"], p.get("environments", ["dev", "staging", "prod"]),
                                p.get("regions", ["region-a"]))
        for r in seed.get("resources", []):
            self.put(admin, r["project"], r.get("env", PROJECT_SCOPE), r["kind"], r["name"], r.get("spec", {}))
        for pr in seed.get("principals", []):
            token, _ = self.tokens.issue(pr["name"], pr["project"], pr.get("ttl_seconds"))
            issued[pr["name"]] = token
            if pr.get("token_file"):
                path = Path(pr["token_file"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(token + "\n")
                os.chmod(path, 0o600)
        return issued


def load_seed(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text()) or {}


def _check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise SimCloudError("invalid_request", f"invalid name {name!r}: use lowercase letters, digits and '-', "
                            "starting with a letter, at most 63 characters")


def _initial_status(kind: str) -> dict:
    runtime = {"service", "function", "edge_function", "job", "cluster"}
    return {"phase": "pending" if kind in runtime else "ready"}
