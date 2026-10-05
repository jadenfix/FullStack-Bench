"""Resource kinds, their spec schemas and the actions that apply to them.

This table is the single source for API validation, policy actions, the
declarative spec and the generated skill docs. Specs are pydantic models so
their JSON schema can be published to agents.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROJECT_SCOPE = "_"  # env value for project-level resources (policies, bindings, ...)


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- identity ------------------------------------------------------------

class Statement(Spec):
    effect: Literal["allow", "deny"]
    actions: list[str] = Field(min_length=1, description="Action patterns, e.g. 'service:deploy' or 'kv:*'.")
    resources: list[str] = Field(min_length=1, description="SRN patterns; '*' matches any run of characters.")
    conditions: dict[str, dict[str, str | list[str] | bool]] = Field(
        default_factory=dict,
        description="Operator -> {context key -> value(s)}. Operators: string_equals, string_not_equals, string_like, bool.",
    )


class PolicySpec(Spec):
    description: str = ""
    statements: list[Statement] = Field(min_length=1)


class BindingSpec(Spec):
    principal: str = Field(description="e.g. 'user:dev', 'service-account:api', 'federated:ci-deploy'.")
    policies: list[str] = Field(min_length=1)


class ServiceAccountSpec(Spec):
    description: str = ""


class TrustSpec(Spec):
    """Workload identity federation: who may exchange an OIDC token for a short-lived SimCloud token."""
    issuer: str
    audience: str = "simcloud"
    subject: str = Field(description="Exact subject or a pattern with '*', e.g. 'repo:shop/api:ref:refs/heads/main'.")
    claims: dict[str, str] = Field(default_factory=dict, description="Extra claims that must match exactly.")
    service_account: str
    max_ttl_seconds: int = Field(default=900, ge=60, le=3600)


class QuotaSpec(Spec):
    limits: dict[str, int] = Field(default_factory=dict, description="Kind -> max count per environment.")
    monthly_budget_cents: int | None = None


# ---- compute -------------------------------------------------------------

class Probe(Spec):
    path: str = "/healthz"
    interval_seconds: int = Field(default=5, ge=1)
    timeout_seconds: int = Field(default=2, ge=1)
    failure_threshold: int = Field(default=3, ge=1)


class ServiceSpec(Spec):
    source: str = Field(default="upload", description="Where releases come from; 'upload' means `sc deploy` archives.")
    build: list[str] = Field(default_factory=list, description="Run once per release in the source root, e.g. ['pip', 'install', '-r', 'requirements.txt', '-t', 'deps'].")
    command: list[str] = Field(default_factory=list, description="Start command for each instance; it must listen on $PORT.")
    port: int = Field(default=8080, ge=1, le=65535)
    env: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict, description="Env var -> secret name; mounted at start, never logged.")
    min_instances: int = Field(default=1, ge=0)
    max_instances: int = Field(default=3, ge=1)
    cpu_millis: int = Field(default=500, ge=100)
    memory_mb: int = Field(default=512, ge=64)
    readiness: Probe = Field(default_factory=Probe)
    service_account: str | None = None
    regions: list[str] = Field(default_factory=lambda: ["region-a"])
    drain_seconds: int = Field(default=10, ge=0, description="Time a stopping instance keeps serving in-flight requests.")
    rollout_timeout_seconds: int = Field(default=120, ge=5, le=1800,
                                         description="A release that isn't ready within this time fails and is not routed to.")


class FunctionSpec(Spec):
    source: str
    handler: str
    timeout_seconds: int = Field(default=30, ge=1, le=900)
    max_concurrency: int = Field(default=10, ge=1)
    env: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    service_account: str | None = None
    triggers: list[dict[str, str]] = Field(default_factory=list, description="e.g. {'queue': 'orders'} or {'http': '/hook'}.")


class EdgeFunctionSpec(Spec):
    source: str
    routes: list[str] = Field(min_length=1)
    env: dict[str, str] = Field(default_factory=dict)


class JobSpec(Spec):
    source: str
    command: list[str] = Field(default_factory=list)
    schedule: str | None = Field(default=None, description="Cron expression; omit for a one-off job.")
    concurrency: Literal["allow", "forbid", "replace"] = "allow"
    max_retries: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=600, ge=1)
    secrets: dict[str, str] = Field(default_factory=dict)
    service_account: str | None = None


class ClusterSpec(Spec):
    version: str = "1.34"
    nodes: int = Field(default=3, ge=1, le=5)
    zones: list[str] = Field(default_factory=lambda: ["zone-1", "zone-2", "zone-3"])


# ---- data ----------------------------------------------------------------

class DatabaseSpec(Spec):
    engine: Literal["postgres"] = "postgres"
    version: str = "17"
    storage_gb: int = Field(default=10, ge=1)
    read_replicas: int = Field(default=0, ge=0, le=3)
    pitr_days: int = Field(default=7, ge=0, le=35)
    pooler: bool = False


class KVSpec(Spec):
    default_ttl_seconds: int | None = None


class BucketSpec(Spec):
    public_read: bool = False
    versioning: bool = False
    lifecycle_days: int | None = None


class QueueSpec(Spec):
    visibility_timeout_seconds: int = Field(default=30, ge=0, le=43200)
    max_receives: int | None = Field(default=None, ge=1, description="Move to dead_letter_queue after this many receives.")
    dead_letter_queue: str | None = None
    fifo: bool = False


class TopicSpec(Spec):
    subscriptions: list[str] = Field(default_factory=list, description="Queue names that receive every message.")


class CacheSpec(Spec):
    memory_mb: int = Field(default=256, ge=32)
    eviction: Literal["allkeys-lru", "noeviction"] = "allkeys-lru"


class SecretSpec(Spec):
    description: str = ""
    rotation_days: int | None = None


# ---- networking ----------------------------------------------------------

class DNSRecordSpec(Spec):
    type: Literal["A", "AAAA", "CNAME", "TXT"]
    value: str
    ttl_seconds: int = Field(default=300, ge=30)


class CertificateSpec(Spec):
    domains: list[str] = Field(min_length=1)
    auto_renew: bool = True
    validity_days: int = Field(default=90, ge=1, le=397)


class CDNRule(Spec):
    path: str
    ttl_seconds: int = Field(ge=0)
    vary: list[str] = Field(default_factory=list)


class CDNSpec(Spec):
    origin: str = Field(description="Service name the CDN fronts.")
    domains: list[str] = Field(min_length=1)
    rules: list[CDNRule] = Field(default_factory=list)


class FirewallRuleSpec(Spec):
    direction: Literal["ingress", "egress"]
    action: Literal["allow", "deny"]
    targets: list[str] = Field(min_length=1, description="Service names this rule applies to.")
    peers: list[str] = Field(min_length=1, description="Service names, 'internet', or CIDRs.")
    ports: list[int] = Field(default_factory=list)
    priority: int = Field(default=1000, ge=0, le=65535)


class AlertSpec(Spec):
    metric: str
    threshold: float
    comparison: Literal["above", "below"] = "above"
    window_seconds: int = Field(default=300, ge=60)
    notify: list[str] = Field(default_factory=list)


class KindInfo(BaseModel):
    name: str
    scope: Literal["project", "env"]
    spec: type[Spec]
    verbs: tuple[str, ...]
    summary: str


CRUD = ("create", "read", "update", "delete", "list")

KINDS: dict[str, KindInfo] = {k.name: k for k in [
    KindInfo(name="policy", scope="project", spec=PolicySpec, verbs=CRUD, summary="A set of allow/deny statements."),
    KindInfo(name="binding", scope="project", spec=BindingSpec, verbs=CRUD, summary="Attaches policies to a principal."),
    KindInfo(name="service_account", scope="project", spec=ServiceAccountSpec, verbs=CRUD + ("impersonate", "create_key"),
             summary="A non-human identity that workloads run as."),
    KindInfo(name="trust", scope="project", spec=TrustSpec, verbs=CRUD,
             summary="Lets an external OIDC identity (e.g. CI) obtain short-lived tokens for a service account."),
    KindInfo(name="quota", scope="project", spec=QuotaSpec, verbs=CRUD, summary="Per-environment limits and budget."),
    KindInfo(name="service", scope="env", spec=ServiceSpec,
             verbs=CRUD + ("deploy", "promote", "rollback", "set_traffic", "logs", "restart"),
             summary="A long-running container service behind the load balancer."),
    KindInfo(name="function", scope="env", spec=FunctionSpec, verbs=CRUD + ("deploy", "invoke", "logs"),
             summary="Event- or HTTP-triggered code with cold starts and a concurrency limit."),
    KindInfo(name="edge_function", scope="env", spec=EdgeFunctionSpec, verbs=CRUD + ("deploy", "logs"),
             summary="Code that runs at the edge in front of origins."),
    KindInfo(name="job", scope="env", spec=JobSpec, verbs=CRUD + ("run", "logs"), summary="A one-off or scheduled batch job."),
    KindInfo(name="cluster", scope="env", spec=ClusterSpec, verbs=CRUD + ("kubeconfig",),
             summary="A managed Kubernetes cluster."),
    KindInfo(name="database", scope="env", spec=DatabaseSpec, verbs=CRUD + ("connect", "branch", "restore"),
             summary="Managed Postgres."),
    KindInfo(name="kv", scope="env", spec=KVSpec, verbs=CRUD + ("get", "put", "remove"), summary="A key-value store."),
    KindInfo(name="bucket", scope="env", spec=BucketSpec,
             verbs=CRUD + ("get_object", "put_object", "delete_object", "sign_url"), summary="Object storage."),
    KindInfo(name="queue", scope="env", spec=QueueSpec, verbs=CRUD + ("send", "receive", "ack", "purge"),
             summary="A message queue with visibility timeout and dead-lettering."),
    KindInfo(name="topic", scope="env", spec=TopicSpec, verbs=CRUD + ("publish",), summary="Pub/sub fan-out to queues."),
    KindInfo(name="cache", scope="env", spec=CacheSpec, verbs=CRUD + ("connect",), summary="A managed Redis cache."),
    KindInfo(name="secret", scope="env", spec=SecretSpec, verbs=CRUD + ("access", "add_version", "rotate"),
             summary="A versioned secret value. Values are only returned by 'access'."),
    KindInfo(name="dns_record", scope="env", spec=DNSRecordSpec, verbs=CRUD, summary="A DNS record."),
    KindInfo(name="certificate", scope="env", spec=CertificateSpec, verbs=CRUD + ("renew",), summary="A managed TLS certificate."),
    KindInfo(name="cdn", scope="env", spec=CDNSpec, verbs=CRUD + ("purge",), summary="A CDN in front of a service."),
    KindInfo(name="firewall_rule", scope="env", spec=FirewallRuleSpec, verbs=CRUD, summary="Network allow/deny between services."),
    KindInfo(name="alert", scope="env", spec=AlertSpec, verbs=CRUD, summary="A metric threshold alert."),
]}


EXTRA_ACTIONS = ("audit:read", "diagnostics:read", "incident:read", "metrics:read", "token:list", "token:revoke")


def all_actions() -> list[str]:
    return sorted([f"{k}:{v}" for k, info in KINDS.items() for v in info.verbs] + list(EXTRA_ACTIONS))


def validate_spec(kind: str, spec: dict) -> dict:
    """Validate and normalise a spec (defaults filled in). Raises pydantic.ValidationError."""
    return KINDS[kind].spec.model_validate(spec).model_dump(mode="json")
