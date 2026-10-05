"""The SimCloud REST API (v1). Thin: authenticate, call the core, map errors."""

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__
from .core import SimCloud
from .errors import SimCloudError
from .identity import Principal
from .kinds import KINDS, all_actions


class PutBody(BaseModel):
    spec: dict


class ProjectBody(BaseModel):
    name: str
    environments: list[str] = ["dev", "staging", "prod"]
    regions: list[str] = ["region-a"]


def create_app(cloud: SimCloud) -> FastAPI:
    app = FastAPI(title="SimCloud", version=__version__)
    app.state.cloud = cloud

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
