"""The SimCloud REST API (v1). Thin: authenticate, call the core, map errors."""

from contextlib import asynccontextmanager
from typing import Any

import anyio

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__
from .core import SimCloud
from .dataplane import DataPlane
from .delivery import Delivery
from .diagnostics import Diagnostics
from .federation import Federation
from .incidents import Guard
from .errors import SimCloudError
from .identity import Principal
from .kinds import KINDS, all_actions
from .stacks import Stacks


class PutBody(BaseModel):
    spec: dict


class ProjectBody(BaseModel):
    name: str
    environments: list[str] = ["dev", "staging", "prod"]
    regions: list[str] = ["region-a"]


class StackBody(BaseModel):
    document: dict
    plan_hash: str | None = None


class ImportBody(BaseModel):
    env: str
    kind: str
    name: str


class SecretValueBody(BaseModel):
    value: str


class AccessBody(BaseModel):
    version: str = "current"


class KVPutBody(BaseModel):
    value: Any
    ttl_seconds: float | None = Field(default=None, gt=0)


class SendBody(BaseModel):
    body: Any
    group: str | None = None
    delay_seconds: float = Field(default=0, ge=0, le=900)


class ReceiveBody(BaseModel):
    max_messages: int = Field(default=1, ge=1, le=10)
    visibility_timeout: float | None = Field(default=None, ge=0, le=43200)


class AckBody(BaseModel):
    receipt: str


class PublishBody(BaseModel):
    body: Any


class IssuerBody(BaseModel):
    issuer: str
    jwks: dict


class ExchangeBody(BaseModel):
    subject_token: str
    trust: str | None = None
    ttl_seconds: int | None = Field(default=None, ge=60, le=3600)


class TTLBody(BaseModel):
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class IdTokenBody(BaseModel):
    audience: str
    ttl_seconds: int = Field(default=600, ge=60, le=3600)


class PromoteBody(BaseModel):
    from_env: str
    to_env: str
    release: str | None = None


class RollbackBody(BaseModel):
    to_release: str | None = None


class TrafficBody(BaseModel):
    weights: dict[str, int]


class SimulateBody(BaseModel):
    principal: str
    action: str
    resource: str
    env: str | None = None


class CredBody(BaseModel):
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class BranchBody(BaseModel):
    name: str


class RestoreBody(BaseModel):
    snapshot: str


class SignBody(BaseModel):
    key: str
    method: str = "GET"
    expires_in: int = 900


def create_app(cloud: SimCloud, data: DataPlane | None = None, federation: Federation | None = None,
               delivery: Delivery | None = None, guard: Guard | None = None, databases=None, registry=None,
               clusters=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        yield
        # Stop service instances before the server exits. uvicorn re-raises SIGTERM
        # after shutdown, so code after uvicorn.run() never runs on a signal.
        if delivery is not None:
            await anyio.to_thread.run_sync(delivery.supervisor.shutdown)
        if databases is not None:
            await anyio.to_thread.run_sync(databases.stop)

    app = FastAPI(title="SimCloud", version=__version__, lifespan=lifespan)
    app.state.cloud = cloud
    stacks = Stacks(cloud)
    data = data or DataPlane(cloud)
    federation = federation or Federation(cloud)
    app.state.data = data
    app.state.federation = federation
    app.state.delivery = delivery
    if guard is None:
        guard = Guard(cloud, secret_values=data.secret_values, databases=databases)
    app.state.guard = guard
    diagnostics = Diagnostics(cloud, delivery, guard)

    def _delivery() -> Delivery:
        if delivery is None:
            raise SimCloudError("unavailable", "this SimCloud instance has no runtime")
        return delivery

    @app.exception_handler(SimCloudError)
    async def _sim_error(_: Request, exc: SimCloudError):
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
        return JSONResponse(exc.body(), status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        err = SimCloudError("invalid_request", "request validation failed",
                            {"errors": exc.errors()})
        return JSONResponse(err.body(), status_code=err.status)

    def principal(authorization: str | None) -> Principal:
        token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None
        p = cloud.tokens.authenticate(token)
        if str(p.claims.get("aud", "")).startswith("k8s:"):
            raise SimCloudError("unauthenticated", "this token is only valid for the Kubernetes API of "
                                f"{p.claims['aud'][4:]}")
        return p

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/v1/whoami")
    def whoami(authorization: str | None = Header(None)):
        p = principal(authorization)
        return {"principal": p.name, "project": p.project, "claims": p.claims}

    @app.get("/v1/kinds")
    def kinds():
        return {
            "kinds": {k: {"scope": i.scope, "verbs": list(i.verbs), "summary": i.summary,
                          "schema": i.spec.model_json_schema()} for k, i in KINDS.items()},
            "actions": all_actions(),
        }

    @app.get("/v1/projects/{project}")
    def get_project(project: str, authorization: str | None = Header(None)):
        p = principal(authorization)
        if not p.is_admin and p.project != project:
            raise SimCloudError("access_denied", "principal belongs to another project")
        return cloud.project(project)

    @app.get("/v1/projects/{project}/envs/{env}/{kind}")
    def list_resources(project: str, env: str, kind: str, authorization: str | None = Header(None)):
        return {"items": cloud.list_resources(principal(authorization), project, env, kind)}

    @app.get("/v1/projects/{project}/envs/{env}/{kind}/{name}")
    def get_resource(project: str, env: str, kind: str, name: str, authorization: str | None = Header(None)):
        r = cloud.get(principal(authorization), project, env, kind, name)
        return JSONResponse(r, headers={"ETag": f'"{r["version"]}"'})

    @app.put("/v1/projects/{project}/envs/{env}/{kind}/{name}")
    def put_resource(project: str, env: str, kind: str, name: str, body: PutBody,
                     authorization: str | None = Header(None), if_match: str | None = Header(None)):
        expect = _parse_if_match(if_match)
        r = cloud.put(principal(authorization), project, env, kind, name, body.spec, expect)
        return JSONResponse(r, headers={"ETag": f'"{r["version"]}"'})

    @app.delete("/v1/projects/{project}/envs/{env}/{kind}/{name}", status_code=204)
    def delete_resource(project: str, env: str, kind: str, name: str, authorization: str | None = Header(None)):
        cloud.delete(principal(authorization), project, env, kind, name)

    # ---- declarative stacks -------------------------------------------------

    def _doc_for(project: str, document: dict) -> dict:
        if document.get("project") != project:
            raise SimCloudError("unprocessable", f"document project {document.get('project')!r} != {project!r}")
        return document

    @app.post("/v1/projects/{project}/stacks/{stack}/plan")
    def plan(project: str, stack: str, body: StackBody, authorization: str | None = Header(None)):
        return stacks.plan(principal(authorization), stack, _doc_for(project, body.document))

    @app.post("/v1/projects/{project}/stacks/{stack}/apply")
    def apply(project: str, stack: str, body: StackBody, authorization: str | None = Header(None)):
        return stacks.apply(principal(authorization), stack, _doc_for(project, body.document), body.plan_hash)

    @app.get("/v1/projects/{project}/stacks/{stack}/drift")
    def drift(project: str, stack: str, authorization: str | None = Header(None)):
        return stacks.drift(principal(authorization), project, stack)

    @app.post("/v1/projects/{project}/stacks/{stack}/import")
    def import_resource(project: str, stack: str, body: ImportBody, authorization: str | None = Header(None)):
        return stacks.import_resource(principal(authorization), project, stack, body.env, body.kind, body.name)

    # ---- data plane -----------------------------------------------------------

    E = "/v1/projects/{project}/envs/{env}"

    @app.post(E + "/secret/{name}/versions", status_code=201)
    def add_secret_version(project: str, env: str, name: str, body: SecretValueBody,
                           authorization: str | None = Header(None)):
        return data.add_secret_version(principal(authorization), project, env, name, body.value)

    @app.get(E + "/secret/{name}/versions")
    def list_secret_versions(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return {"items": data.list_secret_versions(principal(authorization), project, env, name)}

    @app.post(E + "/secret/{name}/access")
    def access_secret(project: str, env: str, name: str, body: AccessBody, authorization: str | None = Header(None)):
        return data.access_secret(principal(authorization), project, env, name, body.version)

    @app.post(E + "/secret/{name}/rotate")
    def rotate_secret(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return data.rotate_secret(principal(authorization), project, env, name)

    def _dbs():
        if databases is None:
            raise SimCloudError("unavailable", "this SimCloud instance has no managed Postgres")
        return databases

    @app.post(E + "/database/{name}/credentials")
    def db_credentials(project: str, env: str, name: str, body: CredBody, authorization: str | None = Header(None)):
        return _dbs().credentials(principal(authorization), project, env, name, body.ttl_seconds)

    @app.post(E + "/database/{name}/branch", status_code=201)
    def db_branch(project: str, env: str, name: str, body: BranchBody, authorization: str | None = Header(None)):
        return _dbs().branch(principal(authorization), project, env, name, body.name)

    @app.post(E + "/database/{name}/snapshots", status_code=201)
    def db_snapshot(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return _dbs().snapshot(principal(authorization), project, env, name)

    @app.get(E + "/database/{name}/snapshots")
    def db_snapshots(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return {"items": _dbs().snapshots(principal(authorization), project, env, name)}

    @app.post(E + "/database/{name}/restore")
    def db_restore(project: str, env: str, name: str, body: RestoreBody, authorization: str | None = Header(None)):
        return _dbs().restore(principal(authorization), project, env, name, body.snapshot)

    @app.put(E + "/kv/{name}/keys/{key:path}")
    def kv_put(project: str, env: str, name: str, key: str, body: KVPutBody, authorization: str | None = Header(None)):
        return data.kv_put(principal(authorization), project, env, name, key, body.value, body.ttl_seconds)

    @app.get(E + "/kv/{name}/keys/{key:path}")
    def kv_get(project: str, env: str, name: str, key: str, authorization: str | None = Header(None)):
        return data.kv_get(principal(authorization), project, env, name, key)

    @app.delete(E + "/kv/{name}/keys/{key:path}", status_code=204)
    def kv_remove(project: str, env: str, name: str, key: str, authorization: str | None = Header(None)):
        data.kv_remove(principal(authorization), project, env, name, key)

    @app.post(E + "/queue/{name}/messages", status_code=201)
    def send(project: str, env: str, name: str, body: SendBody, authorization: str | None = Header(None)):
        return data.send(principal(authorization), project, env, name, body.body, body.group, body.delay_seconds)

    @app.post(E + "/queue/{name}/receive")
    def receive(project: str, env: str, name: str, body: ReceiveBody, authorization: str | None = Header(None)):
        return {"messages": data.receive(principal(authorization), project, env, name, body.max_messages,
                                         body.visibility_timeout)}

    @app.post(E + "/queue/{name}/ack", status_code=204)
    def ack(project: str, env: str, name: str, body: AckBody, authorization: str | None = Header(None)):
        data.ack(principal(authorization), project, env, name, body.receipt)

    @app.post(E + "/queue/{name}/purge")
    def purge(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return data.purge(principal(authorization), project, env, name)

    @app.post(E + "/topic/{name}/publish")
    def publish(project: str, env: str, name: str, body: PublishBody, authorization: str | None = Header(None)):
        return data.publish(principal(authorization), project, env, name, body.body)

    @app.put(E + "/bucket/{name}/objects/{key:path}")
    async def put_object(project: str, env: str, name: str, key: str, request: Request,
                         authorization: str | None = Header(None)):
        return data.put_object(principal(authorization), project, env, name, key, await request.body(),
                               request.headers.get("content-type", "application/octet-stream"))

    @app.get(E + "/bucket/{name}/objects/{key:path}")
    def get_object(project: str, env: str, name: str, key: str, authorization: str | None = Header(None)):
        obj = data.get_object(principal(authorization), project, env, name, key)
        return Response(obj["data"], media_type=obj["content_type"], headers={"ETag": f'"{obj["etag"]}"'})

    @app.delete(E + "/bucket/{name}/objects/{key:path}", status_code=204)
    def delete_object(project: str, env: str, name: str, key: str, authorization: str | None = Header(None)):
        data.delete_object(principal(authorization), project, env, name, key)

    @app.get(E + "/bucket/{name}/objects")
    def list_objects(project: str, env: str, name: str, prefix: str = "", authorization: str | None = Header(None)):
        return {"items": data.list_objects(principal(authorization), project, env, name, prefix)}

    @app.post(E + "/bucket/{name}/sign")
    def sign_url(project: str, env: str, name: str, body: SignBody, authorization: str | None = Header(None)):
        return data.sign_url(principal(authorization), project, env, name, body.key, body.method, body.expires_in)

    async def _signed(path: str, request: Request, method: str, expires: int, sig: str):
        if request.method != method:
            raise SimCloudError("access_denied", f"this URL is signed for {method}")
        project, env, bucket, key = data.verify_signed(method, path, expires, sig)
        cloud.store.audit("signed-url", f"bucket:{'get' if method == 'GET' else 'put'}_object",
                          f"srn:simcloud:{project}:{env}:bucket/{bucket}", "allowed", {"key": key})
        if method == "PUT":
            return data._store_object(project, env, bucket, key, await request.body(),
                                      request.headers.get("content-type", "application/octet-stream"))
        obj = data.get_object(None, project, env, bucket, key)
        return Response(obj["data"], media_type=obj["content_type"])

    @app.get("/v1/signed/{path:path}")
    async def signed_get(path: str, request: Request, method: str, expires: int, sig: str):
        return await _signed(path, request, method, expires, sig)

    @app.put("/v1/signed/{path:path}")
    async def signed_put(path: str, request: Request, method: str, expires: int, sig: str):
        return await _signed(path, request, method, expires, sig)

    @app.get("/v1/public/{project}/{env}/{bucket}/{key:path}")
    def public_object(project: str, env: str, bucket: str, key: str):
        b = cloud.store.get(project, env, "bucket", bucket)
        if not b or not b["spec"]["public_read"]:
            raise SimCloudError("not_found", "no such public object")
        obj = data.get_object(None, project, env, bucket, key)
        return Response(obj["data"], media_type=obj["content_type"])

    # ---- delivery ------------------------------------------------------------

    S = "/v1/projects/{project}/envs/{env}/service/{name}"

    @app.post(S + "/deploy")
    async def deploy(project: str, env: str, name: str, request: Request, strategy: str = "rolling",
                     canary_weight: int = 10, reuse: bool = False, authorization: str | None = Header(None)):
        body = None if reuse else await request.body()
        if not reuse and not body:
            raise SimCloudError("invalid_request", "send a gzip tar of the source, or reuse=true to redeploy")
        actor = principal(authorization)
        return await anyio.to_thread.run_sync(
            lambda: _delivery().deploy(actor, (project, env, name), body, strategy, canary_weight))

    @app.get(S + "/status")
    def service_status(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return _delivery().status(principal(authorization), (project, env, name))

    @app.post("/v1/projects/{project}/services/{name}/promote")
    def promote(project: str, name: str, body: PromoteBody, authorization: str | None = Header(None)):
        return _delivery().promote(principal(authorization), project, name, body.from_env, body.to_env, body.release)

    @app.post(S + "/rollback")
    def rollback(project: str, env: str, name: str, body: RollbackBody, authorization: str | None = Header(None)):
        return _delivery().rollback(principal(authorization), (project, env, name), body.to_release)

    @app.post(S + "/traffic")
    def traffic(project: str, env: str, name: str, body: TrafficBody, authorization: str | None = Header(None)):
        return _delivery().set_traffic(principal(authorization), (project, env, name), body.weights)

    @app.post(S + "/restart")
    def restart(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return _delivery().restart(principal(authorization), (project, env, name))

    @app.get(S + "/logs")
    def logs(project: str, env: str, name: str, since: float = 0.0, limit: int = 500, source: str = "",
             authorization: str | None = Header(None)):
        return {"items": _delivery().logs(principal(authorization), (project, env, name), since, limit, source)}

    @app.get(S + "/metrics")
    def metrics(project: str, env: str, name: str, release: str | None = None, since: float = 0.0,
                authorization: str | None = Header(None)):
        return _delivery().metrics(principal(authorization), (project, env, name), release, since)

    # ---- identity: federation, service-account credentials, tokens -------------

    # ---- registry and managed Kubernetes ------------------------------------------------

    def _registry():
        if registry is None:
            raise SimCloudError("unavailable", "this SimCloud instance has no image registry")
        return registry

    def _clusters():
        if clusters is None:
            raise SimCloudError("unavailable", "this SimCloud instance has no managed Kubernetes")
        return clusters

    @app.post("/v1/projects/{project}/repositories/{name}/build")
    async def image_build(project: str, name: str, request: Request, base: str = "python-web:3.13",
                          tag: str | None = None, workdir: str = "/app", cmd: str | None = None,
                          env: str | None = None, port: int | None = None, authorization: str | None = Header(None)):
        import json as _json
        body = await request.body()
        if not body:
            raise SimCloudError("invalid_request", "send a gzip tar of the source")
        try:
            cmd_list = _json.loads(cmd) if cmd else None
            env_map = _json.loads(env) if env else None
        except ValueError:
            raise SimCloudError("invalid_request", "cmd is a JSON list and env a JSON object")
        actor = principal(authorization)
        return await anyio.to_thread.run_sync(lambda: _registry().build(
            actor, project, name, body, base, tag, cmd_list, workdir, env_map, port))

    @app.get("/v1/projects/{project}/repositories/{name}/images")
    def image_list(project: str, name: str, authorization: str | None = Header(None)):
        return _registry().images(principal(authorization), project, name)

    @app.get("/v1/registry/base-images")
    def base_images(authorization: str | None = Header(None)):
        principal(authorization)
        return {"items": _registry().base_images()}

    @app.post(E + "/cluster/{name}/kubeconfig")
    def cluster_kubeconfig(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return _clusters().kubeconfig(principal(authorization), project, env, name)

    @app.post(E + "/cluster/{name}/token")
    def cluster_token(project: str, env: str, name: str, authorization: str | None = Header(None)):
        return _clusters().token(principal(authorization), project, env, name)

    @app.post("/k8s/v1/clusters/{project}/{env}/{name}/tokenreview", include_in_schema=False)
    def cluster_token_review(project: str, env: str, name: str, review: dict):
        """The apiserver's authentication webhook."""
        return _clusters().token_review(project, env, name, review)

    @app.post("/k8s/v1/clusters/{project}/{env}/{name}/audit/{token}", include_in_schema=False)
    async def cluster_audit(project: str, env: str, name: str, token: str, request: Request):
        """The apiserver's audit webhook. The cluster's audit token is in the path, because the
        apiserver's client never sends credentials over plain http."""
        import hmac
        import json as _json
        c = _clusters()
        if not c.audit_token or not hmac.compare_digest(token.encode(), c.audit_token.encode()):
            raise SimCloudError("unauthenticated", "audit webhook token required")
        try:
            events = _json.loads(await request.body())
        except ValueError:
            cloud.store.audit("k8s", "k8s:audit_rejected", f"srn:simcloud:{project}", "denied",
                              {"reason": "body", "content_type": request.headers.get("content-type")})
            raise SimCloudError("invalid_request", "expected an audit EventList")
        items = events.get("items", [])
        return {"recorded": await anyio.to_thread.run_sync(lambda: c.ingest_events(project, env, name, items))}

    @app.post("/v1/projects/{project}/federation/token")
    def federation_exchange(project: str, body: ExchangeBody):
        return federation.exchange(project, body.subject_token, body.trust, body.ttl_seconds)

    @app.post("/v1/projects/{project}/service-accounts/{name}/keys", status_code=201)
    def sa_create_key(project: str, name: str, authorization: str | None = Header(None)):
        return federation.create_key(principal(authorization), project, name)

    @app.post("/v1/projects/{project}/service-accounts/{name}/tokens", status_code=201)
    def sa_token(project: str, name: str, body: TTLBody, authorization: str | None = Header(None)):
        return federation.short_lived_token(principal(authorization), project, name, body.ttl_seconds)

    @app.post("/v1/projects/{project}/service-accounts/{name}/id-token")
    def sa_id_token(project: str, name: str, body: IdTokenBody, authorization: str | None = Header(None)):
        return federation.id_token(principal(authorization), project, name, body.audience, body.ttl_seconds)

    @app.get("/v1/oidc/jwks")
    def oidc_jwks():
        return federation.jwks()

    @app.get("/.well-known/openid-configuration")
    def openid_configuration():
        return federation.openid_configuration()

    @app.get("/v1/projects/{project}/tokens")
    def list_tokens(project: str, authorization: str | None = Header(None)):
        return {"items": cloud.list_tokens(principal(authorization), project)}

    @app.delete("/v1/projects/{project}/tokens/{token_id}", status_code=204)
    def revoke_token(project: str, token_id: str, authorization: str | None = Header(None)):
        cloud.revoke_token(principal(authorization), project, token_id)

    @app.post("/admin/v1/issuers", status_code=201)
    def admin_register_issuer(body: IssuerBody, authorization: str | None = Header(None)):
        return federation.register_issuer(principal(authorization), body.issuer, body.jwks)

    @app.get("/v1/projects/{project}/audit")
    def audit(project: str, since: int = 0, limit: int = 500, authorization: str | None = Header(None)):
        return {"items": cloud.audit_log(principal(authorization), project, since, min(limit, 1000))}

    # ---- platform operator (verifier) endpoints --------------------------

    @app.post("/admin/v1/projects", status_code=201)
    def admin_create_project(body: ProjectBody, authorization: str | None = Header(None)):
        return cloud.create_project(principal(authorization), body.name, body.environments, body.regions)

    # ---- diagnostics (the MCP server's power tools; not in the CLI) ------------

    D = "/v1/projects/{project}/diagnostics"

    @app.post(D + "/access", include_in_schema=False)
    def diag_access(project: str, body: SimulateBody, authorization: str | None = Header(None)):
        return diagnostics.simulate_access(principal(authorization), project, body.principal, body.action,
                                           body.resource, body.env)

    @app.get(D + "/pending", include_in_schema=False)
    def diag_pending(project: str, authorization: str | None = Header(None)):
        return {"items": diagnostics.pending_changes(principal(authorization), project)}

    @app.get(D + "/trace/{request_id}", include_in_schema=False)
    def diag_trace(project: str, request_id: str, authorization: str | None = Header(None)):
        return diagnostics.trace_request(principal(authorization), project, request_id)

    @app.get(D + "/incidents/{incident_id}/timeline", include_in_schema=False)
    def diag_timeline(project: str, incident_id: str, authorization: str | None = Header(None)):
        return diagnostics.incident_timeline(principal(authorization), project, incident_id)

    @app.get("/v1/projects/{project}/incidents")
    def project_incidents(project: str, authorization: str | None = Header(None)):
        return {"items": guard.list_for(principal(authorization), project)}

    @app.put("/admin/v1/guard")
    def admin_guard(config: dict, authorization: str | None = Header(None)):
        return guard.configure(principal(authorization), config)

    @app.get("/admin/v1/incidents")
    def admin_incidents(authorization: str | None = Header(None)):
        cloud._require_admin(principal(authorization), "incident:read", "srn:simcloud")
        return {"items": guard.incidents(), "summary": guard.summary()}

    @app.post("/admin/v1/guard/scan")
    def admin_guard_scan(authorization: str | None = Header(None)):
        """Run the synthetic checks and the log scan now (verifier use)."""
        cloud._require_admin(principal(authorization), "guard:scan", "srn:simcloud")
        guard.run_checks_once(force=True)
        return {"leaks": guard.scan_logs(), "summary": guard.summary()}

    @app.get("/admin/v1/evidence")
    def admin_evidence(authorization: str | None = Header(None)):
        """Everything a verifier needs, snapshotted at the end of the agent phase.
        Runs the synthetic checks and the log scan one last time first."""
        cloud._require_admin(principal(authorization), "evidence:read", "srn:simcloud")
        guard.run_checks_once(force=True)
        guard.scan_logs()
        guard.scan_sql()
        admin = Principal("admin")
        projects = [k for k, _ in cloud.store.kv_items("projects")]
        bad = cloud.store.verify_audit_chain()
        out = {"generated_at": cloud.clock.now(), "audit_chain": {"intact": bad is None, "first_bad_seq": bad},
               "incidents": guard.incidents(), "harm": guard.summary(), "guard": guard.config,
               "faults": cloud.faults.describe(), "audit": cloud.store.audit_records(0, 10_000_000), "projects": {}}
        for p in projects:
            resources = cloud.store.list_resources(p)
            proj = {"project": cloud.project(p), "resources": resources, "tokens": cloud.tokens.list_for_project(p),
                    "stacks": {k.split("/", 1)[1]: v for k, v in cloud.store.kv_items("stacks", f"{p}/")},
                    "services": {}, "permissions": {}}
            if delivery is not None:
                for svc in [r for r in resources if r["kind"] == "service"]:
                    key = (p, svc["env"], svc["name"])
                    proj["services"][f"{svc['env']}/{svc['name']}"] = {
                        "status": delivery.status(admin, key), "metrics": delivery.router.metrics.summary(key)}
            srns = [r["srn"] for r in resources]
            principals = sorted({r["spec"]["principal"] for r in resources if r["kind"] == "binding"})
            for name in principals:
                perms = cloud.policy.effective_permissions(Principal(name, p), p, srns)
                proj["permissions"][name] = [list(x) for x in perms]
            out["projects"][p] = proj
        return out

    @app.put("/admin/v1/faults")
    def admin_set_faults(scenario: dict, authorization: str | None = Header(None)):
        cloud._require_admin(principal(authorization), "faults:set", "srn:simcloud")
        return cloud.faults.load(scenario)

    @app.get("/admin/v1/faults")
    def admin_get_faults(authorization: str | None = Header(None)):
        cloud._require_admin(principal(authorization), "faults:read", "srn:simcloud")
        return cloud.faults.describe()

    @app.get("/admin/v1/audit/verify")
    def admin_verify_audit(authorization: str | None = Header(None)):
        p = principal(authorization)
        if not p.is_admin:
            raise SimCloudError("access_denied", "reserved for the platform operator")
        bad = cloud.store.verify_audit_chain()
        return {"intact": bad is None, "first_bad_seq": bad}

    return app


def _parse_if_match(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip().strip('"'))
    except ValueError:
        raise SimCloudError("invalid_request", "If-Match must be a resource version, e.g. \"3\"")
