#!/usr/bin/env python3
# shopsrv: storefront backend. single process on purpose (see ADR-007, which we can't find anymore).
#
# Routes are plain functions named rt_<METHOD>__<path>__<parts>; they're collected at the bottom of
# this file. Don't add a router library, we tried (twice).
import importlib
import json
import os
import signal
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pricing as _pricing_v1
from common import (ToggleError, cfg, conf, envname, fmt_money, sign, stopwatch, to_cents,
                    toggle)

START = time.time()
_STATE = {"signer": None, "warm": False, "not_ready_reason": "starting", "inflight": 0, "requests": 0,
          "errors": 0, "engine_cache": {}}
_LOCK = threading.Lock()


def log(event, **kw):
    if not conf("service.log_requests", True) and event == "request":
        return
    rec = {"ts": round(time.time(), 3), "event": event, "svc": conf("service.name", "shopsrv")}
    rec.update(kw)
    print(json.dumps(rec, sort_keys=True), flush=True)


# ------------------------------------------------------------------------------------------------
# signer
# ------------------------------------------------------------------------------------------------
# key comes from PAYMENT_KEY (legacy/old_checkout.py has the history). rotate via the payments portal.

def _load_signer():
    provider = conf("payments.provider", "payments")
    var = envname(provider, "signing", "key")
    key = os.environ.get(var)
    if not key:
        # don't crash: the platform restarts crashing instances and we'd hammer the partner on boot.
        _STATE["not_ready_reason"] = "signer: missing key"
        log("signer_missing", provider=provider)
        return None
    log("signer_loaded", provider=provider, key_len=len(key))
    return key


def _warm():
    # pre-price the demo carts so the first real quote isn't slow. takes a few seconds on purpose
    # (mirrors the partner's catalogue sync).
    time.sleep(float(conf("service.warm_cache_seconds", 5)))
    _STATE["signer"] = _load_signer()
    if _STATE["signer"]:
        _STATE["warm"] = True
        _STATE["not_ready_reason"] = None
        log("ready")


# ------------------------------------------------------------------------------------------------
# engines
# ------------------------------------------------------------------------------------------------

def _engine_module(name):
    mod = _STATE["engine_cache"].get(name)
    if mod is None:
        mod = importlib.import_module(name)
        _STATE["engine_cache"][name] = mod
    return mod


def _engine():
    which = toggle("engine")
    if which == "v1":
        return _pricing_v1
    if which == "v2":
        return _engine_module(conf("pricing.module", "pricing_v2"))
    raise ToggleError("unknown engine %r" % which)


def _cart(cart_id):
    carts = conf("carts", {})
    if cart_id not in carts:
        raise KeyError(cart_id)
    return carts[cart_id]


# ------------------------------------------------------------------------------------------------
# routes
# ------------------------------------------------------------------------------------------------

def rt_GET__healthz(req, q):
    if not _STATE["warm"]:
        return 503, {"status": "warming", "reason": _STATE["not_ready_reason"]}
    return 200, {"status": "ok", "uptime_s": round(time.time() - START, 1)}


def rt_GET__checkout__quote(req, q):
    cart_id = (q.get("cart") or [""])[0]
    try:
        lines = _cart(cart_id)
    except KeyError:
        return 404, {"error": "no such cart", "cart": cart_id}
    engine = _engine()
    quote = engine.price(lines, conf("pricing.currency", "USD"))
    quote["cart"] = cart_id
    quote["display_total"] = fmt_money(quote["total_cents"], quote["currency"])
    quote["signature"] = sign(_STATE["signer"], {k: quote[k] for k in ("cart", "total_cents", "currency")})
    return 200, quote


def rt_POST__checkout__confirm(req, q):
    # TODO(maya): wire to the order service. until then the mobile app only calls quote.
    return 501, {"error": "not implemented"}


def rt_GET__v0__price(req, q):
    # legacy float pricing for the 3.x mobile app. do not touch.
    cart_id = (q.get("cart") or ["demo"])[0]
    try:
        lines = _cart(cart_id)
    except KeyError:
        return 404, {"error": "no such cart"}
    total = sum(qty * unit / 100.0 for _, qty, unit in lines) * 1.0825
    return 200, {"total": round(total, 2), "total_cents": to_cents(total)}


def rt_GET__internal__metrics(req, q):
    return 200, {"requests": _STATE["requests"], "errors": _STATE["errors"], "inflight": _STATE["inflight"]}


def rt_GET__internal__config(req, q):
    redacted = json.loads(json.dumps(cfg()))
    redacted.pop("carts", None)
    return 200, redacted


# ------------------------------------------------------------------------------------------------
# http plumbing
# ------------------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _go(self, method):
        rid = self.headers.get("x-request-id") or uuid.uuid4().hex
        url = urlparse(self.path)
        fn = ROUTES.get((method, url.path.rstrip("/") or "/"))
        took = stopwatch()
        with _LOCK:
            _STATE["inflight"] += 1
            _STATE["requests"] += 1
        try:
            if fn is None:
                code, body = 404, {"error": "not found"}
            else:
                code, body = fn(self, parse_qs(url.query))
        except ToggleError as e:
            code, body = 500, {"error": "configuration unavailable"}
            log("error", rid=rid, path=url.path, kind="toggle", detail=str(e))
        except Exception:  # noqa
            code, body = 500, {"error": "internal"}
            log("error", rid=rid, path=url.path, kind="unhandled", detail=traceback.format_exc()[-400:])
        finally:
            with _LOCK:
                _STATE["inflight"] -= 1
        if code >= 500:
            _STATE["errors"] += 1
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.send_header("x-request-id", rid)
        if code == 200 and "signature" in body:
            self.send_header(conf("payments.signature_header", "x-shop-signature"), body["signature"])
        self.end_headers()
        self.wfile.write(data)
        log("request", rid=rid, method=method, path=url.path, status=code, ms=took())

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


def _collect_routes(ns):
    out = {}
    for name, fn in list(ns.items()):
        if not name.startswith("rt_") or not callable(fn):
            continue
        method, *parts = name[3:].split("__")
        out[(method, "/" + "/".join(parts))] = fn
    return out


ROUTES = _collect_routes(globals())


def main():
    port = int(os.environ.get("PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=_warm, daemon=True).start()

    def drain(*_):
        def go():
            srv.shutdown()
            deadline = time.time() + 20
            while _STATE["inflight"] and time.time() < deadline:
                time.sleep(0.02)
            log("stopped")
            os._exit(0)
        threading.Thread(target=go, daemon=True).start()

    signal.signal(signal.SIGTERM, drain)
    log("listening", port=port, env=cfg()["_env"], routes=len(ROUTES))
    srv.serve_forever()
    time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
