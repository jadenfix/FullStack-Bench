# contacts API. routes register themselves with @route below; the server is stdlib on purpose
# (the platform's python image has no framework and nobody wanted to vendor one).
import json
import os
import signal
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db
from .normalize import email_key
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
def health(body, *args):
    return 200, {"ok": True}


@route("POST", "/contacts")
def create_contact(body, *args):
    try:
        tenant, email, name = body["tenant_id"], body["email"], body.get("name") or ""
    except (KeyError, TypeError):
        return 400, {"error": "tenant_id and email are required"}
    if not isinstance(email, str) or "@" not in email:
        return 400, {"error": "email must be an address"}
    key = email_key(email)
    existing = db.find_active(tenant, email.strip().lower())  # lookups predate the key; see DATA_CONTRACT
    if existing:
        return 200, existing
    cid = db.create(tenant, email, key, name)
    _log(event="contact_created", id=cid, tenant=tenant)
    return 201, db.get(cid)


@route("GET", "/contacts/{id}")
def get_contact(body, cid):
    row = db.get(cid)
    if not row:
        return 404, {"error": "no such contact"}
    row["activities"] = db.activities(cid)
    return 200, row


@route("POST", "/contacts/{id}/touch")
def touch_contact(body, cid):
    # the sync agent calls this on every CRM page view; it is also the platform's write probe
    return (200, {"ok": True}) if db.touch(cid) else (404, {"error": "no such contact"})


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

    def _go(self, method):
        with _lock:
            _inflight[0] += 1
        try:
            fn, args = _match(method, self.path.split("?")[0])
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}
            if fn is None:
                code, out = 404, {"error": "not found"}
            else:
                code, out = fn(body, *args)
        except Exception:
            code, out = 500, {"error": "internal"}
            _log(event="error", detail=traceback.format_exc()[-500:])
        finally:
            with _lock:
                _inflight[0] -= 1
        data = json.dumps(out, default=str).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
    _log(event="listening", port=S.port, routes=len(ROUTES))
    srv.serve_forever()
    time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
