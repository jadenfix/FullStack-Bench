# support API. routes register themselves with @route below; the server is stdlib on purpose
# (the platform's python image has no framework and nobody wanted to vendor one).
import json
import os
import signal
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db, reports
from .settings import S

ROUTES = {}
_inflight = [0]
_lock = threading.Lock()


def route(method, pattern):
    def deco(fn):
        ROUTES[(method, pattern)] = fn
        return fn
    return deco


def _log(**kw):
    print(json.dumps({"ts": round(time.time(), 3), **kw}), flush=True)


@route("GET", "/healthz")
def health(req, *args):
    return 200, {"ok": True}  # liveness: the process answers; it does not touch the database


@route("GET", "/customers/{id}")
def get_customer(req, cid):
    try:
        row = db.customer(cid)
    except db.PoolExhausted as e:
        _log(event="pool_exhausted", path=req.path, detail=str(e))
        return 503, {"error": "database busy", "detail": str(e)}
    return (200, row) if row else (404, {"error": "no such customer"})


@route("GET", "/admin/pool")
def pool(req, *args):
    return 200, db.POOL.snapshot()


@route("GET", "/reports/activity")
def activity(req, *args):
    q = parse_qs(urlparse(req.path).query)
    customer, days = (q.get("customer") or [None])[0], int((q.get("days") or ["30"])[0])
    if not customer:
        return 400, {"error": "customer is required"}
    deadline = int(req.headers.get("x-deadline-ms") or S.default_deadline_ms)
    request_id = req.headers.get("x-request-id") or f"local-{time.time_ns()}"
    return "stream", reports.stream(request_id, customer, days, deadline)


def _match(method, path):
    for (m, pattern), fn in ROUTES.items():
        if m != method:
            continue
        p_parts, parts = pattern.strip("/").split("/"), path.strip("/").split("/")
        if len(p_parts) != len(parts):
            continue
        args = []
        for pp, part in zip(p_parts, parts):
            if pp.startswith("{"):
                args.append(part)
            elif pp != part:
                break
        else:
            return fn, args
    return None, []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, data: bytes, content_type="application/json"):
        self.send_response(code)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _go(self, method):
        with _lock:
            _inflight[0] += 1
        try:
            fn, args = _match(method, self.path.split("?")[0])
            if fn is None:
                return self._send(404, b'{"error": "not found"}')
            code, out = fn(self, *args)
            if code != "stream":
                return self._send(code, json.dumps(out, default=str).encode())
            # a streamed report: rows go out as they are produced, so the client can render early
            chunks = []
            try:
                for line in out:
                    chunks.append(line.encode())
            except reports.DeadlineExceeded as e:
                _log(event="report_deadline", detail=str(e))
                return self._send(504, json.dumps({"error": "report deadline exceeded", "detail": str(e)}).encode())
            except db.PoolExhausted as e:
                _log(event="pool_exhausted", path=self.path, detail=str(e))
                return self._send(503, json.dumps({"error": "database busy", "detail": str(e)}).encode())
            self._send(200, b"".join(chunks), "application/x-ndjson")
        except Exception:
            _log(event="error", detail=traceback.format_exc()[-500:])
            self._send(500, b'{"error": "internal"}')
        finally:
            with _lock:
                _inflight[0] -= 1

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", int(S.port)), Handler)
    srv.daemon_threads = True

    def drain(*_):
        def go():
            srv.shutdown()
            while _inflight[0]:
                time.sleep(0.02)
            os._exit(0)
        threading.Thread(target=go, daemon=True).start()

    signal.signal(signal.SIGTERM, drain)
    _log(event="listening", port=S.port, routes=len(ROUTES), pool_max=S.pool_max)
    srv.serve_forever()
    time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
