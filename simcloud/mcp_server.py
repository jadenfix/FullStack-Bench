"""SimCloud MCP server (stdio).

Exposes the same operations as `sc` as MCP tools, using the same
configuration (SIMCLOUD_URL, SIMCLOUD_TOKEN / SIMCLOUD_TOKEN_FILE,
SIMCLOUD_PROJECT). Messages are newline-delimited JSON-RPC 2.0 on
stdin/stdout, per the MCP stdio transport.

Run: `simcloud-mcp` (or `python -m simcloud.mcp_server`).
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import yaml

from . import __version__
from .cli import CLIError, Client

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]


def _s(**props) -> dict:
    required = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


STR = {"type": "string"}
INT = {"type": "integer"}
OBJ = {"type": "object"}


def opt(schema: dict) -> dict:
    return {**schema, "optional": True}


# name -> (description, input schema, handler(client, args) -> result)
def _svc(a):
    return f"/v1/projects/{{p}}/envs/{a['env']}/service/{a['service']}"


TOOLS = {
    "whoami": ("Show the authenticated principal and project.", _s(),
               lambda c, a: c.call("GET", "/v1/whoami")),
    "kinds": ("List resource kinds with scope, verbs and JSON schema (or one kind).", _s(kind=opt(STR)),
              lambda c, a: (lambda d: d["kinds"][a["kind"]] if a.get("kind") else
                            {k: {"scope": v["scope"], "summary": v["summary"]} for k, v in d["kinds"].items()})(
                  c.call("GET", "/v1/kinds"))),
    "get_resource": ("Get one resource. env is '_' for project-level kinds.", _s(env=STR, kind=STR, name=STR),
                     lambda c, a: c.call("GET", f"/v1/projects/{c.project}/envs/{a['env']}/{a['kind']}/{a['name']}")),
    "list_resources": ("List resources of a kind in an environment.", _s(env=STR, kind=STR),
                       lambda c, a: c.call("GET", f"/v1/projects/{c.project}/envs/{a['env']}/{a['kind']}")),
    "put_resource": ("Create or replace a resource. if_match makes it conditional on the current version.",
                     _s(env=STR, kind=STR, name=STR, spec=OBJ, if_match=opt(INT)),
                     lambda c, a: c.call("PUT", f"/v1/projects/{c.project}/envs/{a['env']}/{a['kind']}/{a['name']}",
                                         json={"spec": a["spec"]},
                                         headers={"If-Match": f'"{a["if_match"]}"'} if "if_match" in a else {})),
    "delete_resource": ("Delete a resource.", _s(env=STR, kind=STR, name=STR),
                        lambda c, a: c.call("DELETE", f"/v1/projects/{c.project}/envs/{a['env']}/{a['kind']}/{a['name']}")
                        or {"deleted": True}),
    "plan": ("Plan a simcloud.yaml document (pass document, or file = path to it).",
             _s(document=opt(OBJ), file=opt(STR), stack=opt(STR)),
             lambda c, a: _stack(c, a, "plan")),
    "apply": ("Apply a simcloud.yaml document. plan_hash refuses a changed plan.",
              _s(document=opt(OBJ), file=opt(STR), stack=opt(STR), plan_hash=opt(STR)),
              lambda c, a: _stack(c, a, "apply")),
    "drift": ("Show resources changed outside a stack.", _s(stack=opt(STR)),
              lambda c, a: c.call("GET", f"/v1/projects/{c.project}/stacks/{a.get('stack', 'default')}/drift")),
    "deploy": ("Upload a source directory as a new release and roll it out.",
               _s(env=STR, service=STR, source=opt(STR), strategy=opt({"enum": ["rolling", "canary", "none"]}),
                  canary_weight=opt(INT), reuse=opt({"type": "boolean"})),
               lambda c, a: _deploy(c, a)),
    "service_status": ("Releases, traffic and instances of a service.", _s(env=STR, service=STR),
                       lambda c, a: c.call("GET", _svc(a).format(p=c.project) + "/status")),
    "promote": ("Deploy the artifact serving in from_env to to_env.",
                _s(service=STR, from_env=STR, to_env=STR, release=opt(STR)),
                lambda c, a: c.call("POST", f"/v1/projects/{c.project}/services/{a['service']}/promote",
                                    json={"from_env": a["from_env"], "to_env": a["to_env"], "release": a.get("release")},
                                    timeout=600)),
    "rollback": ("Move all traffic to an earlier ready release (aborts a canary).",
                 _s(env=STR, service=STR, to_release=opt(STR)),
                 lambda c, a: c.call("POST", _svc(a).format(p=c.project) + "/rollback",
                                     json={"to_release": a.get("to_release")}, timeout=600)),
    "set_traffic": ("Split traffic between releases, e.g. {\"r3\": 90, \"r4\": 10}.",
                    _s(env=STR, service=STR, weights=OBJ),
                    lambda c, a: c.call("POST", _svc(a).format(p=c.project) + "/traffic",
                                        json={"weights": a["weights"]}, timeout=600)),
    "restart": ("Rolling restart of a service.", _s(env=STR, service=STR),
                lambda c, a: c.call("POST", _svc(a).format(p=c.project) + "/restart", timeout=600)),
    "logs": ("Service logs; source filters by prefix: app/, build/ or platform/.",
             _s(env=STR, service=STR, since=opt({"type": "number"}), limit=opt(INT), source=opt(STR)),
             lambda c, a: c.call("GET", _svc(a).format(p=c.project) + "/logs",
                                 params={"since": a.get("since", 0), "limit": a.get("limit", 200),
                                         "source": a.get("source", "")})),
    "metrics": ("Request metrics measured at the load balancer.",
                _s(env=STR, service=STR, release=opt(STR), since=opt({"type": "number"})),
                lambda c, a: c.call("GET", _svc(a).format(p=c.project) + "/metrics",
                                    params={k: a[k] for k in ("release", "since") if k in a})),
    "incidents": ("Incidents on this project: outages and critical issues.", _s(),
                  lambda c, a: c.call("GET", f"/v1/projects/{c.project}/incidents")),
    "audit": ("The project's audit log.", _s(since=opt(INT), limit=opt(INT)),
              lambda c, a: c.call("GET", f"/v1/projects/{c.project}/audit",
                                  params={"since": a.get("since", 0), "limit": a.get("limit", 100)})),
    "secret_add_version": ("Add a new current version to a secret.", _s(env=STR, name=STR, value=STR),
                           lambda c, a: c.call("POST", f"/v1/projects/{c.project}/envs/{a['env']}/secret/{a['name']}/versions",
                                               json={"value": a["value"]})),
    "secret_access": ("Read a secret value (version: 'current', 'previous' or a number).",
                      _s(env=STR, name=STR, version=opt(STR)),
                      lambda c, a: c.call("POST", f"/v1/projects/{c.project}/envs/{a['env']}/secret/{a['name']}/access",
                                          json={"version": a.get("version", "current")})),
    "queue_send": ("Send a message to a queue.", _s(env=STR, queue=STR, body={}, group=opt(STR)),
                   lambda c, a: c.call("POST", f"/v1/projects/{c.project}/envs/{a['env']}/queue/{a['queue']}/messages",
                                       json={"body": a["body"], "group": a.get("group")})),
    "queue_receive": ("Receive up to max_messages; ack each with its receipt before the visibility timeout.",
                      _s(env=STR, queue=STR, max_messages=opt(INT)),
                      lambda c, a: c.call("POST", f"/v1/projects/{c.project}/envs/{a['env']}/queue/{a['queue']}/receive",
                                          json={"max_messages": a.get("max_messages", 1)})),
    "queue_ack": ("Acknowledge a received message.", _s(env=STR, queue=STR, receipt=STR),
                  lambda c, a: c.call("POST", f"/v1/projects/{c.project}/envs/{a['env']}/queue/{a['queue']}/ack",
                                      json={"receipt": a["receipt"]}) or {"acked": True}),
    "kv_get": ("Read a key from a KV store.", _s(env=STR, store=STR, key=STR),
               lambda c, a: c.call("GET", f"/v1/projects/{c.project}/envs/{a['env']}/kv/{a['store']}/keys/{a['key']}")),
    "kv_put": ("Write a key to a KV store.", _s(env=STR, store=STR, key=STR, value={}, ttl_seconds=opt({"type": "number"})),
               lambda c, a: c.call("PUT", f"/v1/projects/{c.project}/envs/{a['env']}/kv/{a['store']}/keys/{a['key']}",
                                   json={"value": a["value"], "ttl_seconds": a.get("ttl_seconds")})),
}


def _stack(c: Client, a: dict, op: str):
    doc = a.get("document")
    if doc is None:
        doc = yaml.safe_load(Path(a.get("file", "simcloud.yaml")).read_text()) or {}
    body = {"document": doc, **({"plan_hash": a["plan_hash"]} if a.get("plan_hash") else {})}
    project = doc.get("project") or c.project
    return c.call("POST", f"/v1/projects/{project}/stacks/{a.get('stack', 'default')}/{op}", json=body)


def _deploy(c: Client, a: dict):
    from .delivery import pack_directory
    params = {"strategy": a.get("strategy", "rolling"), "canary_weight": a.get("canary_weight", 10),
              "reuse": bool(a.get("reuse"))}
    content = None if a.get("reuse") else pack_directory(Path(a.get("source", ".")))
    return c.call("POST", _svc(a).format(p=c.project) + "/deploy", params=params, content=content,
                  headers={"Content-Type": "application/gzip"}, timeout=600)


class Server:
    def __init__(self, client: Client):
        self.client = client

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notification
            return None
        try:
            if method == "initialize":
                requested = (msg.get("params") or {}).get("protocolVersion")
                version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "simcloud", "version": __version__},
                          "instructions": "SimCloud platform tools. Read /skills/simcloud/SKILL.md for concepts."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": n, "description": d, "inputSchema": _clean(schema)}
                                    for n, (d, schema, _) in TOOLS.items()]}
            elif method == "tools/call":
                params = msg.get("params") or {}
                result = self._call(params.get("name"), params.get("arguments") or {})
            else:
                return _error(mid, -32601, f"method not found: {method}")
        except Exception as e:  # never crash the transport
            return _error(mid, -32603, f"internal error: {e}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _call(self, name: str, args: dict) -> dict:
        if name not in TOOLS:
            return _tool_result(f"unknown tool {name!r}", error=True)
        try:
            out = TOOLS[name][2](self.client, args)
        except CLIError as e:
            return _tool_result(str(e), error=True)
        except (KeyError, FileNotFoundError, yaml.YAMLError) as e:
            return _tool_result(f"bad arguments: {e}", error=True)
        except httpx.HTTPError as e:
            return _tool_result(f"cannot reach SimCloud: {e}", error=True)
        return _tool_result(json.dumps(out, indent=2) if out is not None else "ok")


def _clean(schema: dict) -> dict:
    return json.loads(json.dumps(schema))


def _tool_result(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(stdin=sys.stdin, stdout=sys.stdout, client: Client | None = None) -> None:
    server = Server(client or Client(SimpleNamespace(url=None, project=None)))
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = _error(None, -32700, "parse error")
        else:
            reply = server.handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
