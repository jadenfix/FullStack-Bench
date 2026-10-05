"""The SimCloud REST API (v1). Thin: authenticate, call the core, map errors."""

from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__
from .core import SimCloud
from .dataplane import DataPlane
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


class SignBody(BaseModel):
    key: str
    method: str = "GET"
    expires_in: int = 900


def create_app(cloud: SimCloud, data: DataPlane | None = None) -> FastAPI:
    app = FastAPI(title="SimCloud", version=__version__)
    app.state.cloud = cloud
    stacks = Stacks(cloud)
    data = data or DataPlane(cloud)
    app.state.data = data

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
        return cloud.tokens.authenticate(token)

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

    @app.api_route("/v1/signed/{path:path}", methods=["GET", "PUT"])
    async def signed(path: str, request: Request, method: str, expires: int, sig: str):
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

    @app.get("/v1/public/{project}/{env}/{bucket}/{key:path}")
    def public_object(project: str, env: str, bucket: str, key: str):
        b = cloud.store.get(project, env, "bucket", bucket)
        if not b or not b["spec"]["public_read"]:
            raise SimCloudError("not_found", "no such public object")
        obj = data.get_object(None, project, env, bucket, key)
        return Response(obj["data"], media_type=obj["content_type"])

    @app.get("/v1/projects/{project}/audit")
    def audit(project: str, since: int = 0, limit: int = 500, authorization: str | None = Header(None)):
        return {"items": cloud.audit_log(principal(authorization), project, since, min(limit, 1000))}

    # ---- platform operator (verifier) endpoints --------------------------

    @app.post("/admin/v1/projects", status_code=201)
    def admin_create_project(body: ProjectBody, authorization: str | None = Header(None)):
        return cloud.create_project(principal(authorization), body.name, body.environments, body.regions)

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
