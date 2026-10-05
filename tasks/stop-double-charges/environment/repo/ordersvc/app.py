# orders API. routes register themselves with @route below; the server is stdlib on purpose
# (the platform's python image has no framework and nobody wanted to vendor one).
import json
import os
import signal
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, payclient
from .settings import S

ROUTES = {}
_inflight = [0]
_lock = threading.Lock()


def route(method, pattern):
    def deco(fn):
        ROUTES[(method, pattern)] = fn
        return fn
    return deco


class Ctx:
    def __init__(self, handler):
        self.handler = handler
        self.trace = handler.headers.get("x-request-id") or "local-" + str(time.time_ns())


def _log(**kw):
    print(json.dumps({"ts": round(time.time(), 3), **kw}), flush=True)


@route("GET", "/healthz")
def health(ctx, body, *args):
    return 200, {"ok": True}


@route("POST", "/checkout")
def checkout(ctx, body, *args):
    try:
        cart, customer, amount = body["cart_id"], body["customer_id"], int(body["amount_cents"])
    except (KeyError, TypeError, ValueError):
        return 400, {"error": "cart_id, customer_id and amount_cents are required"}
    oid = db.new_order(cart, customer, amount, ctx.trace)
    try:
        ch = payclient.charge(ctx, amount, S.currency, {"order_id": oid, "cart_id": cart})
    except payclient.PayError as e:
        db.set_status(oid, "failed")
        _log(event="charge_failed", order=oid, status=e.status)
        return 402, {"error": "payment failed", "order_id": oid}
    db.mark_paid(oid, ch["id"])
    _log(event="checkout", order=oid, cart=cart, charge=ch["id"], trace=ctx.trace)
    return 200, {"order_id": oid, "charge_id": ch["id"], "status": "paid"}


@route("GET", "/orders/{id}")
def get_order(ctx, body, oid):
    row = db.get(oid)
    return (200, row) if row else (404, {"error": "no such order"})


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
                code, out = fn(Ctx(self), body, *args)
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
