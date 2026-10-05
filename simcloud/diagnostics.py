"""Diagnostics: the power tools behind the MCP server.

These endpoints are served under /v1/projects/<p>/diagnostics/ but are not in
the CLI; the MCP server is their client. They need `diagnostics:read`.
"""

from .core import SimCloud
from .errors import SimCloudError
from .identity import Principal


class Diagnostics:
    def __init__(self, cloud: SimCloud, delivery=None, guard=None):
        self.cloud, self.delivery, self.guard = cloud, delivery, guard

    def _auth(self, actor: Principal, project: str) -> None:
        self.cloud.project(project)
        self.cloud.authorize(actor, "diagnostics:read", f"srn:simcloud:{project}", project)

    def simulate_access(self, actor: Principal, project: str, principal: str, action: str, resource: str,
                        env: str | None = None) -> dict:
        """Would `principal` be allowed `action` on `resource`, now and once pending IAM changes land?"""
        self._auth(actor, project)
        who = Principal(principal, project)
        ctx = {"env": env} if env else {}
        now = self.cloud.policy.evaluate(who, action, resource, project, ctx)
        pending = self.cloud.policy.pending(project)
        out = {"principal": principal, "action": action, "resource": resource,
               "now": {"allowed": now.allowed, "reason": now.reason, "policy": now.policy, "statement": now.statement}}
        if pending:
            at = max(p["effective_at"] for p in pending)
            later = self.cloud.policy.evaluate(who, action, resource, project, ctx, at=at)
            out["after_pending"] = {"allowed": later.allowed, "reason": later.reason, "policy": later.policy,
                                    "effective_at": at}
        return out

    def pending_changes(self, actor: Principal, project: str) -> list[dict]:
        self._auth(actor, project)
        return self.cloud.policy.pending(project)

    def trace_request(self, actor: Principal, project: str, request_id: str) -> dict:
        """Every load-balancer hop and log line carrying this request id."""
        self._auth(actor, project)
        if self.delivery is None:
            raise SimCloudError("unavailable", "this SimCloud instance has no runtime")
        hops = self.delivery.router.find_requests(request_id, project)
        lines = []
        for (p, env, svc), entries in self.delivery.supervisor.logs.by_service().items():
            if p == project:
                lines += [{"service": f"{p}/{env}/{svc}", "line": l} for l in entries if request_id in l]
        return {"request_id": request_id, "hops": hops, "log_lines": lines}

    def incident_timeline(self, actor: Principal, project: str, incident_id: str, window: float = 300.0) -> dict:
        """An incident with the changes, deploys and denials around it."""
        self._auth(actor, project)
        if self.guard is None:
            raise SimCloudError("unavailable", "no incident ledger")
        inc = next((i for i in self.guard.incidents(project) if i["id"] == incident_id), None)
        if not inc:
            raise SimCloudError("not_found", f"incident {incident_id} not found")
        lo, hi = inc["started_at"] - window, (inc["ended_at"] or self.cloud.clock.now()) + window
        base = f"srn:simcloud:{project}"
        events = [r for r in self.cloud.store.audit_records(0, 1_000_000)
                  if lo <= r["ts"] <= hi and (r["srn"] == base or r["srn"].startswith(base + ":"))
                  and not r["action"].endswith((":read", ":list")) and r["principal"] != "guard"]
        return {"incident": inc, "events": [{k: e[k] for k in ("ts", "principal", "action", "srn", "outcome")}
                                            for e in events]}
