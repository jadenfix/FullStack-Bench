#!/usr/bin/env python3
# shopsrv 1.x: what prod runs before checkout v2.
import json
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CARTS = {"demo": [["sku-1001", 2, 1299], ["sku-2002", 1, 4550], ["sku-3003", 3, 199]]}
inflight = 0
lock = threading.Lock()


def price(lines):
    subtotal = sum(q * u / 100.0 for _, q, u in lines)
    tax = subtotal * 0.0825
    return {"engine": "v1", "currency": "USD", "subtotal_cents": int(round(subtotal * 100)),
            "tax_cents": int(round(tax * 100)), "total_cents": int(round((subtotal + tax) * 100))}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        global inflight
        with lock:
            inflight += 1
        try:
            url = urlparse(self.path)
            if url.path == "/healthz":
                code, body = 200, {"status": "ok"}
            elif url.path == "/checkout/quote":
                cart = parse_qs(url.query).get("cart", [""])[0]
                code, body = (200, {**price(CARTS[cart]), "cart": cart}) if cart in CARTS else (404, {"error": "no cart"})
            else:
                code, body = 404, {"error": "not found"}
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            with lock:
                inflight -= 1


srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), H)
srv.daemon_threads = True


def drain(*_):
    def go():
        srv.shutdown()
        while inflight:
            time.sleep(0.02)
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()


signal.signal(signal.SIGTERM, drain)
srv.serve_forever()
time.sleep(30)
