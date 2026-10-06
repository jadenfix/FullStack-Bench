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
import time
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

    dep = sub.add_parser("deploy", help="upload a source directory and roll out a new release")
    dep.add_argument("env")
    dep.add_argument("service")
    dep.add_argument("--source", default=".", help="directory to upload (default: current directory)")
    dep.add_argument("--strategy", choices=["rolling", "canary", "none"], default="rolling")
    dep.add_argument("--canary-weight", type=int, default=10, help="percent of traffic for the canary (default: 10)")
    dep.add_argument("--reuse", action="store_true", help="redeploy the serving artifact with the current spec")

    st = sub.add_parser("status", help="show a service's releases, traffic and instances")
    st.add_argument("env")
    st.add_argument("service")

    pm = sub.add_parser("promote", help="deploy the artifact serving in one environment to another")
    pm.add_argument("service")
    pm.add_argument("--from", dest="from_env", required=True)
    pm.add_argument("--to", dest="to_env", required=True)
    pm.add_argument("--release")

    rb = sub.add_parser("rollback", help="move all traffic to an earlier ready release")
    rb.add_argument("env")
    rb.add_argument("service")
    rb.add_argument("--to", dest="to_release")

    tr = sub.add_parser("traffic", help="split traffic between releases, e.g. r3=90 r4=10")
    tr.add_argument("env")
    tr.add_argument("service")
    tr.add_argument("weights", nargs="+")

    rs = sub.add_parser("restart", help="rolling restart of a service")
    rs.add_argument("env")
    rs.add_argument("service")

    lg = sub.add_parser("logs", help="show a service's logs (build, platform and app)")
    lg.add_argument("env")
    lg.add_argument("service")
    lg.add_argument("--since", type=float, default=0.0)
    lg.add_argument("--limit", type=int, default=200)
    lg.add_argument("--source", default="", help="prefix filter: app/, build/ or platform/")

    mt = sub.add_parser("metrics", help="request metrics measured at the load balancer")
    mt.add_argument("env")
    mt.add_argument("service")
    mt.add_argument("--release")
    mt.add_argument("--since", type=float, default=0.0)

    db = sub.add_parser("db", help="managed Postgres: credentials, snapshots, branches")
    dbs = db.add_subparsers(dest="db_cmd", required=True)
    dc = dbs.add_parser("credentials", help="short-lived credentials and a DSN for a database")
    dc.add_argument("env")
    dc.add_argument("name")
    dc.add_argument("--ttl", type=int, default=3600, help="seconds the credentials stay valid (default: 3600)")
    for cmd_name, help_text in (("snapshot", "take a snapshot of a database"), ("snapshots", "list snapshots")):
        x = dbs.add_parser(cmd_name, help=help_text)
        x.add_argument("env")
        x.add_argument("name")
    br = dbs.add_parser("branch", help="copy a database into a new database resource")
    br.add_argument("env")
    br.add_argument("name")
    br.add_argument("new_name")

    jb = sub.add_parser("job", help="batch jobs: deploy, run, run history and logs")
    jbs = jb.add_subparsers(dest="job_cmd", required=True)
    jd = jbs.add_parser("deploy", help="upload a source directory as the job's new release")
    jd.add_argument("env")
    jd.add_argument("name")
    jd.add_argument("--source", default=".")
    jr = jbs.add_parser("run", help="start a run (arguments after -- are appended to the command)")
    jr.add_argument("env")
    jr.add_argument("name")
    jr.add_argument("--wait", action="store_true", help="wait for the run to finish")
    jr.add_argument("--timeout", type=float, default=600)
    jr.add_argument("args", nargs="*")
    for cmd_name, help_text in (("runs", "list a job's runs"),):
        x = jbs.add_parser(cmd_name, help=help_text)
        x.add_argument("env")
        x.add_argument("name")
    jl = jbs.add_parser("logs", help="a run's output (default: the latest run)")
    jl.add_argument("env")
    jl.add_argument("name")
    jl.add_argument("--run")

    bd = sub.add_parser("build", help="build an image from a source directory into a registry repository")
    bd.add_argument("repository")
    bd.add_argument("--source", default=".", help="directory to build from (default: current directory)")
    bd.add_argument("--base", default="python-web:3.13", help="base image (default: python-web:3.13)")
    bd.add_argument("--tag")
    bd.add_argument("--workdir", default="/app", help="where the source goes in the image (default: /app)")
    bd.add_argument("--cmd", dest="start_cmd", help="start command, e.g. 'python -m uvicorn app:app --port 8080'")
    bd.add_argument("--port", type=int, help="port the image listens on (recorded as EXPOSE)")
    bd.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="image environment (repeatable)")

    im = sub.add_parser("images", help="list a repository's tags and images")
    im.add_argument("repository")

    k8 = sub.add_parser("k8s", help="managed Kubernetes: kubeconfig and tokens for kubectl")
    k8s_ = k8.add_subparsers(dest="k8s_cmd", required=True)
    kc = k8s_.add_parser("kubeconfig", help="print (or write) a kubeconfig for a cluster")
    kc.add_argument("env")
    kc.add_argument("cluster")
    kc.add_argument("--write", metavar="PATH", help="write it to PATH (e.g. ~/.kube/config) instead of printing")
    kt = k8s_.add_parser("token", help="an ExecCredential for kubectl (used by the kubeconfig)")
    kt.add_argument("--env", required=True)
    kt.add_argument("cluster")

    sub.add_parser("incidents", help="show incidents on this project (outages and critical issues)")

    w = sub.add_parser("wait", help="wait until a service is serving (or a given release is), or time out")
    w.add_argument("env")
    w.add_argument("service")
    w.add_argument("--release", help="wait until this release serves traffic")
    w.add_argument("--timeout", type=float, default=300)
    w.add_argument("--interval", type=float, default=2)

    cmpp = sub.add_parser("compare", help="compare one resource's spec across two environments")
    cmpp.add_argument("kind")
    cmpp.add_argument("name")
    cmpp.add_argument("--from", dest="from_env", required=True)
    cmpp.add_argument("--to", dest="to_env", required=True)

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
    elif cmd == "deploy":
        from .delivery import pack_directory
        svc = f"/v1/projects/{client.project}/envs/{args.env}/service/{args.service}"
        params = {"strategy": args.strategy, "canary_weight": args.canary_weight, "reuse": args.reuse}
        content = None if args.reuse else pack_directory(Path(args.source))
        result = client.call("POST", svc + "/deploy", params=params, content=content,
                             headers={"Content-Type": "application/gzip"}, timeout=600)
        _emit(result, args.output)
        if result.get("error"):
            raise CLIError(f"release {result['release']} is {result['state']}: {result['error']}")
    elif cmd == "status":
        _emit(client.call("GET", f"/v1/projects/{client.project}/envs/{args.env}/service/{args.service}/status"),
              args.output)
    elif cmd == "promote":
        _emit(client.call("POST", f"/v1/projects/{client.project}/services/{args.service}/promote",
                          json={"from_env": args.from_env, "to_env": args.to_env, "release": args.release},
                          timeout=600), args.output)
    elif cmd == "rollback":
        _emit(client.call("POST", f"/v1/projects/{client.project}/envs/{args.env}/service/{args.service}/rollback",
                          json={"to_release": args.to_release}, timeout=600), args.output)
    elif cmd == "traffic":
        try:
            weights = {k: int(v) for k, v in (w.split("=", 1) for w in args.weights)}
        except ValueError:
            raise CLIError("weights look like r3=90 r4=10")
        _emit(client.call("POST", f"/v1/projects/{client.project}/envs/{args.env}/service/{args.service}/traffic",
                          json={"weights": weights}, timeout=600), args.output)
    elif cmd == "restart":
        _emit(client.call("POST", f"/v1/projects/{client.project}/envs/{args.env}/service/{args.service}/restart",
                          timeout=600), args.output)
    elif cmd == "logs":
        items = client.call("GET", f"/v1/projects/{client.project}/envs/{args.env}/service/{args.service}/logs",
                            params={"since": args.since, "limit": args.limit, "source": args.source})["items"]
        if args.output == "json":
            _emit(items, "json")
        for item in items if args.output != "json" else []:
            print(f"{item['ts']:.3f} [{item['source']}] {item['line']}")
    elif cmd == "metrics":
        params = {"since": args.since, **({"release": args.release} if args.release else {})}
        _emit(client.call("GET", f"/v1/projects/{client.project}/envs/{args.env}/service/{args.service}/metrics",
                          params=params), args.output)
    elif cmd == "wait":
        path = f"/v1/projects/{client.project}/envs/{args.env}/service/{args.service}/status"
        deadline = time.monotonic() + args.timeout
        while True:
            st = client.call("GET", path)
            ready = [i for i in st["instances"] if i["state"] == "ready"]
            serving = st["traffic"]
            if (args.release and serving.get(args.release, 0) > 0 and any(i["release"] == args.release for i in ready)) \
                    or (not args.release and serving and all(any(i["release"] == r for i in ready) for r in serving)):
                _emit({"ready": True, "traffic": serving, "ready_instances": len(ready)}, args.output)
                return
            if time.monotonic() >= deadline:
                raise CLIError(f"timed out after {args.timeout:.0f}s; traffic={serving}, "
                               f"instances={[(i['id'], i['release'], i['state']) for i in st['instances']]}")
            time.sleep(args.interval)
    elif cmd == "compare":
        a = client.call("GET", f"/v1/projects/{client.project}/envs/{args.from_env}/{args.kind}/{args.name}")["spec"]
        b = client.call("GET", f"/v1/projects/{client.project}/envs/{args.to_env}/{args.kind}/{args.name}")["spec"]
        diff = {k: {args.from_env: a.get(k), args.to_env: b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
        _emit({"identical": not diff, "differences": diff}, args.output)
    elif cmd == "db":
        base = f"/v1/projects/{client.project}/envs/{args.env}/database/{args.name}"
        if args.db_cmd == "credentials":
            _emit(client.call("POST", base + "/credentials", json={"ttl_seconds": args.ttl}), args.output)
        elif args.db_cmd == "snapshot":
            _emit(client.call("POST", base + "/snapshots", timeout=600), args.output)
        elif args.db_cmd == "snapshots":
            _emit(client.call("GET", base + "/snapshots"), args.output)
        elif args.db_cmd == "branch":
            _emit(client.call("POST", base + "/branch", json={"name": args.new_name}, timeout=600), args.output)
    elif cmd == "job":
        from .delivery import pack_directory
        base = f"/v1/projects/{client.project}/envs/{args.env}/job/{args.name}"
        if args.job_cmd == "deploy":
            _emit(client.call("POST", base + "/deploy", content=pack_directory(Path(args.source)),
                              headers={"Content-Type": "application/gzip"}, timeout=600), args.output)
        elif args.job_cmd == "run":
            run = client.call("POST", base + "/run", json={"args": args.args, "wait": args.wait,
                                                         "timeout": args.timeout}, timeout=args.timeout + 30)
            _emit(run, args.output)
            if args.wait and run["status"] != "succeeded":
                raise CLIError(f"run {run['id']} {run['status']}: {run.get('reason')}")
        elif args.job_cmd == "runs":
            _emit(client.call("GET", base + "/runs")["items"], args.output)
        elif args.job_cmd == "logs":
            run_id = args.run or (client.call("GET", base + "/runs")["items"] or [{}])[-1].get("id")
            if not run_id:
                raise CLIError("the job has no runs yet")
            for line in client.call("GET", base + f"/runs/{run_id}/logs")["lines"]:
                print(line)
    elif cmd == "build":
        import shlex
        from .delivery import pack_directory
        params = {"base": args.base, "workdir": args.workdir}
        if args.tag:
            params["tag"] = args.tag
        if args.start_cmd:
            params["cmd"] = json.dumps(shlex.split(args.start_cmd))
        if args.port:
            params["port"] = args.port
        if args.env:
            try:
                params["env"] = json.dumps(dict(e.split("=", 1) for e in args.env))
            except ValueError:
                raise CLIError("--env looks like KEY=VALUE")
        _emit(client.call("POST", f"/v1/projects/{client.project}/repositories/{args.repository}/build",
                          params=params, content=pack_directory(Path(args.source)),
                          headers={"Content-Type": "application/gzip"}, timeout=600), args.output)
    elif cmd == "images":
        _emit(client.call("GET", f"/v1/projects/{client.project}/repositories/{args.repository}/images"), args.output)
    elif cmd == "k8s":
        if args.k8s_cmd == "kubeconfig":
            out = client.call("POST", f"/v1/projects/{client.project}/envs/{args.env}/cluster/{args.cluster}/kubeconfig")
            if args.write:
                path = Path(args.write).expanduser()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(out["kubeconfig"])
                path.chmod(0o600)
                print(f"wrote {path} (context {out['context']})")
            else:
                print(out["kubeconfig"].rstrip())
        else:
            print(json.dumps(client.call(
                "POST", f"/v1/projects/{client.project}/envs/{args.env}/cluster/{args.cluster}/token")))
    elif cmd == "incidents":
        _emit(client.call("GET", f"/v1/projects/{client.project}/incidents"), args.output)
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
