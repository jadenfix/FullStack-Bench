"""A tiny service used by runtime tests.

Env:
  GRACEFUL=1     on SIGTERM, stop accepting, finish in-flight requests, exit 0
  READY_AFTER=s  /healthz fails until s seconds after start
  VERSION=x      reported by /
"""

import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

START = time.time()
healthy = True
inflight = 0
lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        global inflight, healthy
        url = urlparse(self.path)
        with lock:
            inflight += 1
        try:
            if url.path == "/toggle-health":
                healthy = not healthy
                return self._send(200, f"healthy={healthy}")
            if url.path == "/healthz":
                ready = healthy and time.time() - START >= float(os.environ.get("READY_AFTER", "0"))
                return self._send(200 if ready else 503, "ok" if ready else "warming")
            if url.path == "/slow":
                time.sleep(int(parse_qs(url.query).get("ms", ["200"])[0]) / 1000)
                return self._send(200, "slow-done")
            if url.path == "/crash":
                print("crashing on request", flush=True)
                os._exit(3)
            if url.path.startswith("/env/"):
                return self._send(200, os.environ.get(url.path[5:], ""))
            print(f"handled {url.path}", flush=True)
            return self._send(200, os.environ.get("VERSION", "v?"))
        finally:
            with lock:
                inflight -= 1


server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), H)
server.daemon_threads = True


def on_term(*_):
    if os.environ.get("GRACEFUL") == "1":
        def drain():
            server.shutdown()  # stop accepting
            while True:
                with lock:
                    if inflight == 0:
                        break
                time.sleep(0.01)
            os._exit(0)
        threading.Thread(target=drain, daemon=True).start()
    else:
        os._exit(143)


signal.signal(signal.SIGTERM, on_term)
print(f"listening on {os.environ['PORT']}", flush=True)
server.serve_forever()
time.sleep(60)  # graceful drain thread exits the process
