"""`sc`, the SimCloud command-line client.

Configuration, first match wins:
  URL:     --url, SIMCLOUD_URL, ~/.simcloud/config.yaml `url`, http://127.0.0.1:7400
  Token:   SIMCLOUD_TOKEN, SIMCLOUD_TOKEN_FILE, config `token_file`, ~/.simcloud/token
  Project: --project, SIMCLOUD_PROJECT, config `project`, the token's own project

Exit codes: 0 success, 1 API error (code and message on stderr), 2 usage error.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import yaml

CONFIG = Path.home() / ".simcloud" / "config.yaml"


class CLIError(Exception):
    pass


def _config() -> dict:
    try:
        return yaml.safe_load(CONFIG.read_text()) or {}
    except FileNotFoundError:
        return {}


def _token(cfg: dict) -> str | None:
    if os.environ.get("SIMCLOUD_TOKEN"):
        return os.environ["SIMCLOUD_TOKEN"]
    for path in (os.environ.get("SIMCLOUD_TOKEN_FILE"), cfg.get("token_file"), str(Path.home() / ".simcloud" / "token")):
        if path and Path(path).exists():
            return Path(path).read_text().strip()
    return None


class Client:
    def __init__(self, args, http: httpx.Client | None = None):
        cfg = _config()
        self.base = (args.url or os.environ.get("SIMCLOUD_URL") or cfg.get("url") or "http://127.0.0.1:7400").rstrip("/")
        token = _token(cfg)
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = http or httpx.Client(timeout=60)
        self._project = args.project or os.environ.get("SIMCLOUD_PROJECT") or cfg.get("project")

    def call(self, method: str, path: str, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        r = self.http.request(method, self.base + path, headers=headers, **kwargs)
        if r.status_code >= 400:
            try:
                err = r.json()["error"]
                msg = f"{err['code']}: {err['message']}"
                if err.get("details"):
                    msg += "\n" + yaml.safe_dump(err["details"], sort_keys=False).rstrip()
            except Exception:
                msg = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.headers.get("Retry-After"):
                msg += f"\nretry after {r.headers['Retry-After']}s"
            raise CLIError(msg)
        return r.json() if r.content else None

    @property
    def project(self) -> str:
        if not self._project:
            self._project = self.call("GET", "/v1/whoami")["project"]
            if not self._project:
                raise CLIError("no project: pass --project or set SIMCLOUD_PROJECT")
        return self._project


def _emit(data, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(data, indent=2, sort_keys=False))
    else:
        print(yaml.safe_dump(data, sort_keys=False).rstrip())


def _load_file(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    return yaml.safe_load(text) or {}


def _print_plan(plan: dict) -> None:
    symbols = {"create": "+", "update": "~", "delete": "-", "noop": " "}
    for c in plan["changes"]:
        if c["action"] == "noop" and not c["drifted"]:
            continue
        drift = "  (drifted)" if c["drifted"] else ""
        print(f"{symbols[c['action']]} {c['env']}/{c['kind']}/{c['name']}{drift}")
        for field, d in c["diff"].items():
            if c["action"] == "create":
                print(f"    {field} = {json.dumps(d['after'])}")
            elif c["action"] == "update":
                print(f"    {field}: {json.dumps(d['before'])} -> {json.dumps(d['after'])}")
    s = plan["summary"]
    print(f"plan {plan['plan_hash']}: {s['create']} to create, {s['update']} to update, "
          f"{s['delete']} to delete, {s['drifted']} drifted")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sc", description="SimCloud command-line client")
    p.add_argument("--url")
    p.add_argument("--project")
    p.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="show the authenticated principal")
    k = sub.add_parser("kinds", help="list resource kinds, or show one kind's schema")
    k.add_argument("kind", nargs="?")

    g = sub.add_parser("get", help="get one resource, or list a kind")
    g.add_argument("env", help="environment, or '_' for project-level kinds")
    g.add_argument("kind")
    g.add_argument("name", nargs="?")

    put = sub.add_parser("put", help="create or replace a resource from a spec file")
    put.add_argument("env")
    put.add_argument("kind")
    put.add_argument("name")
    put.add_argument("-f", "--file", required=True, help="spec file (YAML/JSON), or '-' for stdin")
    put.add_argument("--if-match", type=int, help="only apply if the resource is at this version")

    d = sub.add_parser("delete", help="delete a resource")
    d.add_argument("env")
    d.add_argument("kind")
    d.add_argument("name")

    for name, help_text in (("plan", "show what applying a document would change"),
                            ("apply", "apply a document (prints the plan it applied)")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("-f", "--file", default="simcloud.yaml")
        s.add_argument("--stack", default="default")
        if name == "apply":
            s.add_argument("--plan-hash", help="refuse to apply unless the plan still has this hash")

    dr = sub.add_parser("drift", help="show resources changed outside a stack")
    dr.add_argument("--stack", default="default")

    imp = sub.add_parser("import", help="adopt an existing resource into a stack")
    imp.add_argument("env")
    imp.add_argument("kind")
    imp.add_argument("name")
    imp.add_argument("--stack", default="default")

    a = sub.add_parser("audit", help="show the project's audit log")
    a.add_argument("--since", type=int, default=0)
    a.add_argument("--limit", type=int, default=100)
    return p


def run(args, client: Client) -> None:
    cmd = args.cmd
    if cmd == "whoami":
        _emit(client.call("GET", "/v1/whoami"), args.output)
    elif cmd == "kinds":
        data = client.call("GET", "/v1/kinds")
        if args.kind:
            if args.kind not in data["kinds"]:
                raise CLIError(f"unknown kind {args.kind!r}")
            _emit(data["kinds"][args.kind], args.output)
        else:
            _emit({k: f"[{v['scope']}] {v['summary']}" for k, v in data["kinds"].items()}, args.output)
    elif cmd == "get":
        path = f"/v1/projects/{client.project}/envs/{args.env}/{args.kind}"
        _emit(client.call("GET", path + (f"/{args.name}" if args.name else "")), args.output)
    elif cmd == "put":
        headers = {"If-Match": f'"{args.if_match}"'} if args.if_match is not None else {}
        _emit(client.call("PUT", f"/v1/projects/{client.project}/envs/{args.env}/{args.kind}/{args.name}",
                          json={"spec": _load_file(args.file)}, headers=headers), args.output)
    elif cmd == "delete":
        client.call("DELETE", f"/v1/projects/{client.project}/envs/{args.env}/{args.kind}/{args.name}")
        print(f"deleted {args.env}/{args.kind}/{args.name}")
    elif cmd in ("plan", "apply"):
        doc = _load_file(args.file)
        project = doc.get("project") or client.project
        body = {"document": doc}
        if cmd == "apply" and args.plan_hash:
            body["plan_hash"] = args.plan_hash
        result = client.call("POST", f"/v1/projects/{project}/stacks/{args.stack}/{cmd}", json=body)
        if args.output == "json":
            _emit(result, "json")
        else:
            _print_plan(result)
            if cmd == "apply":
                print(f"applied {result['applied']} change(s)")
    elif cmd == "drift":
        _emit(client.call("GET", f"/v1/projects/{client.project}/stacks/{args.stack}/drift"), args.output)
    elif cmd == "import":
        _emit(client.call("POST", f"/v1/projects/{client.project}/stacks/{args.stack}/import",
                          json={"env": args.env, "kind": args.kind, "name": args.name}), args.output)
    elif cmd == "audit":
        _emit(client.call("GET", f"/v1/projects/{client.project}/audit",
                          params={"since": args.since, "limit": args.limit}), args.output)


def main(argv: list[str] | None = None, http: httpx.Client | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args, Client(args, http))
    except CLIError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (FileNotFoundError, yaml.YAMLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except httpx.HTTPError as e:
        print(f"error: cannot reach SimCloud: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
