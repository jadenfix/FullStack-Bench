# pulse api. everything http lives here (sorry). routes are at the bottom of Handler.
#
# history: started as the "classes" prototype, bookings moved in from the old rails app in 2025,
# schedules got folded in (PULSE-88). the v0 routes are still used by the kiosk firmware, don't remove.
import hashlib
import json
import os
import random
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, ledger_client, settings, telemetry
from .ledger_client import LedgerError

CLASSES = {
    "spin-am": {"name": "Spin 7am", "capacity": 24},
    "yoga-flow": {"name": "Yoga flow", "capacity": 18},
    "hiit-pm": {"name": "HIIT 6pm", "capacity": 20},
    "pilates": {"name": "Reformer pilates", "capacity": 10},
    "swim": {"name": "Lane swim", "capacity": 30},
}
ID_RE = re.compile(r"^bk_[0-9a-f]{16}$")
_ready = {"ok": False, "why": "booting", "checked": 0.0}
_inflight = [0]
_inflight_lock = threading.Lock()


def _json(h, code, body, extra=None):
    raw = json.dumps(body).encode()
    h.send_response(code)
    h.send_header("content-type", "application/json")
    h.send_header("content-length", str(len(raw)))
    h.send_header("x-pulse-version", __version__)
    h.send_header("x-pulse-pod", os.environ.get("HOSTNAME", "?"))
    for k, v in (extra or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(raw)


def _booking_id(idem_key):
    if idem_key:
        return "bk_" + hashlib.sha256(idem_key.encode()).hexdigest()[:16]
    return "bk_" + secrets.token_hex(8)


def _hold_slot(class_id, slot):
    # the reservation hold. it's a lock on the slot in the schedules table in prod-of-old; we keep the timing
    # because the kiosk UX depends on it (don't ask)
    lo, hi = settings.HOLD_MS
    time.sleep(random.randint(lo, hi) / 1000)


def _validate(b):
    errs = []
    if not isinstance(b.get("member_id"), str) or not b["member_id"].startswith("m-"):
        errs.append("member_id")
    if b.get("class_id") not in CLASSES:
        errs.append("class_id")
    if not isinstance(b.get("slot"), str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", b.get("slot", "")):
        errs.append("slot")
    party = b.get("party", 1)
    if not isinstance(party, int) or not 1 <= party <= settings.MAX_PARTY:
        errs.append("party")
    return errs


def _readiness_loop():
    _ready.update(ok=True, why="ok", checked=time.time())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pulse"

    def log_message(self, fmt, *args):
        if os.environ.get("ACCESS_LOG") == "1":
            print(json.dumps({"access": fmt % args}), flush=True)

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return None

    def handle_one_request(self):
        with _inflight_lock:
            _inflight[0] += 1
        try:
            super().handle_one_request()
        finally:
            with _inflight_lock:
                _inflight[0] -= 1

    # ---- bookings -------------------------------------------------------------

    def _create_booking(self, b, legacy=False):
        if b is None:
            return _json(self, 400, {"error": "bad json"})
        if legacy:  # v0 kiosk payload: {"member": "...", "class": "...", "when": "..."}
            b = {"member_id": b.get("member"), "class_id": b.get("class"), "slot": b.get("when"),
                 "party": b.get("n", 1)}
        errs = _validate(b)
        if errs:
            telemetry.inc("bookings.invalid")
            return _json(self, 422, {"error": "invalid", "fields": errs})
        bid = _booking_id(self.headers.get("idempotency-key"))
        _hold_slot(b["class_id"], b["slot"])
        try:
            status, entry = ledger_client.put(bid, {**b, "source": "kiosk" if legacy else "app"})
        except LedgerError as e:
            telemetry.inc(f"bookings.ledger_{e.status or 'down'}")
            if e.status == 503:
                return _json(self, 503, {"error": "bookings paused, retry"}, {"retry-after": e.retry_after or "2"})
            if e.status == 409:
                # writes went to a follower: someone is mid-failover. say retry.
                return _json(self, 503, {"error": "ledger failover in progress"}, {"retry-after": "2"})
            return _json(self, 502, {"error": "ledger unavailable"})
        telemetry.inc("bookings.created" if status == 201 else "bookings.replayed")
        return _json(self, 201 if status == 201 else 200, {"booking_id": entry["id"], "seq": entry["seq"]})

    def _get_booking(self, bid):
        if not ID_RE.match(bid):
            return _json(self, 404, {"error": "not found"})
        try:
            _, entry = ledger_client.get(bid)
        except LedgerError as e:
            if e.status == 404:
                return _json(self, 404, {"error": "not found"})
            telemetry.inc("reads.ledger_error")
            return _json(self, 502, {"error": "ledger unavailable"})
        return _json(self, 200, {"booking_id": entry["id"], **entry["booking"]})

    # ---- routing ------------------------------------------------------------------

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/healthz":
            return _json(self, 200, {"ok": True})
        if p == "/readyz":
            return _json(self, 200 if _ready["ok"] else 503, _ready)
        if p == "/metrics":
            return _json(self, 200, {**telemetry.snapshot(), "inflight": _inflight[0]})
        if p == "/v1/classes":
            return _json(self, 200, {"classes": CLASSES})
        if p.startswith("/v1/bookings/"):
            return self._get_booking(p.rsplit("/", 1)[1])
        if p.startswith("/v0/booking/"):
            return self._get_booking(p.rsplit("/", 1)[1])
        if p == "/v1/ping":
            return _json(self, 200, {"pong": True, "version": __version__})
        _json(self, 404, {"error": "no route"})

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        if p == "/v1/bookings":
            return self._create_booking(self._body())
        if p == "/v0/book":
            return self._create_booking(self._body(), legacy=True)
        _json(self, 404, {"error": "no route"})


def _boot():
    if settings.TELEMETRY_FLUSH:
        telemetry.install()
    threading.Thread(target=_readiness_loop, daemon=True, name="readiness").start()


def main():
    _boot()
    srv = ThreadingHTTPServer(("0.0.0.0", settings.PORT), Handler)
    srv.daemon_threads = True
    print(json.dumps({"msg": "pulse up", "version": __version__, "port": settings.PORT,
                      "ledger": settings.LEDGER_URL}), flush=True)
    srv.serve_forever()
    return 0
