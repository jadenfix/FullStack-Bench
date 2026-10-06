"""ledgerd: the bookings ledger. Append-only, one leader.

Storage is one file, $LEDGER_DATA/ledger.jsonl. Every line is an entry {"seq", "id", "booking", "ts"}.
The whole log is replayed into memory at start (it's small: ~1 KB per booking).

HTTP (port $LEDGER_HTTP_PORT, default 7000):
  POST /entries            {"id": "...", "booking": {...}}  -> 201 {"seq": n, "id": ...}
                           same id twice -> 200 with the original entry (idempotent)
  GET  /entries/<id>       -> 200 entry | 404
  GET  /log?after=N&limit=M  entries with seq > N, oldest first (used by followers)
  GET  /stats              {"role", "count", "last_seq", "frozen", "following", "lag"}
  GET  /healthz
  POST /admin/freeze       stop accepting writes (503 + Retry-After) until unfreeze/promote
  POST /admin/unfreeze
  POST /admin/promote      follower -> leader (stops following, accepts writes)

LEDGER_BOOTSTRAP=<file>: a leader started on an empty volume copies this log first (the image ships
the pre-2026 history export at /opt/ledger/history.jsonl).

Follower mode: start with LEDGER_FOLLOW=http://<leader>:7000 and it copies the leader's log,
polling every 200 ms, and refuses writes (409) until promoted. A follower started on an empty
volume catches up from seq 0. See OPERATIONS.md.
"""

import json
import os
import signal
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DATA = Path(os.environ.get("LEDGER_DATA", "/data"))
PORT = int(os.environ.get("LEDGER_HTTP_PORT", "7000"))  # not LEDGER_PORT: k8s sets that for the Service
FOLLOW = os.environ.get("LEDGER_FOLLOW") or None
BOOTSTRAP = os.environ.get("LEDGER_BOOTSTRAP")  # a ledger.jsonl to start from when the volume is empty


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.entries: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.frozen = False
        self.following = FOLLOW
        self.leader_seq = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        if BOOTSTRAP and not FOLLOW and not path.exists() and Path(BOOTSTRAP).exists():
            path.write_text(Path(BOOTSTRAP).read_text())
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    e = json.loads(line)
                    self.entries.append(e)
                    self.by_id[e["id"]] = e
        self.f = path.open("a")

    def _append(self, e: dict) -> None:
        self.f.write(json.dumps(e, separators=(",", ":")) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())
        self.entries.append(e)
        self.by_id[e["id"]] = e

    def write(self, entry_id: str, booking: dict) -> tuple[int, dict]:
        with self.lock:
            if entry_id in self.by_id:
                return 200, self.by_id[entry_id]
            if self.following:
                return 409, {"error": "read-only follower", "following": self.following}
            if self.frozen:
                return 503, {"error": "frozen"}
            e = {"seq": len(self.entries) + 1, "id": entry_id, "booking": booking, "ts": round(time.time(), 3)}
            self._append(e)
            return 201, e

    def copy(self, entries: list[dict]) -> None:
        with self.lock:
            for e in entries:
                if e["seq"] == len(self.entries) + 1 and e["id"] not in self.by_id:
                    self._append(e)

    def stats(self) -> dict:
        last = self.entries[-1]["seq"] if self.entries else 0
        return {"role": "follower" if self.following else "leader", "count": len(self.entries), "last_seq": last,
                "frozen": self.frozen, "following": self.following,
                "lag": max(0, self.leader_seq - last) if self.following else 0}


L = Ledger(DATA / "ledger.jsonl")


def follow_loop():
    while True:
        src = L.following
        if not src:
            return
        try:
            after = L.entries[-1]["seq"] if L.entries else 0
            with urllib.request.urlopen(f"{src}/log?after={after}&limit=1000", timeout=5) as r:
                data = json.loads(r.read())
            L.leader_seq = data["last_seq"]
            L.copy(data["entries"])
        except Exception as e:  # leader briefly unreachable: keep trying
            print(json.dumps({"msg": "follow error", "error": repr(e)[:200]}), flush=True)
        time.sleep(0.2)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: dict, headers: dict | None = None):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/healthz":
            return self._send(200, {"ok": True})
        if u.path == "/stats":
            return self._send(200, L.stats())
        if u.path.startswith("/entries/"):
            e = L.by_id.get(u.path.split("/", 2)[2])
            return self._send(200, e) if e else self._send(404, {"error": "not found"})
        if u.path == "/log":
            q = parse_qs(u.query)
            after = int(q.get("after", ["0"])[0])
            limit = min(int(q.get("limit", ["500"])[0]), 5000)
            with L.lock:
                out = L.entries[after:after + limit]
                last = L.entries[-1]["seq"] if L.entries else 0
            return self._send(200, {"entries": out, "last_seq": last})
        self._send(404, {"error": "no route"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if u.path == "/entries":
            if not body.get("id"):
                return self._send(400, {"error": "id required"})
            code, e = L.write(body["id"], body.get("booking") or {})
            return self._send(code, e, {"retry-after": "2"} if code == 503 else None)
        if u.path == "/admin/freeze":
            L.frozen = True
            return self._send(200, L.stats())
        if u.path == "/admin/unfreeze":
            L.frozen = False
            return self._send(200, L.stats())
        if u.path == "/admin/promote":
            L.following = None
            L.frozen = False
            return self._send(200, L.stats())
        self._send(404, {"error": "no route"})


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    srv.daemon_threads = True
    if FOLLOW:
        threading.Thread(target=follow_loop, daemon=True).start()

    def stop(*_):
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    print(json.dumps({"msg": "ledgerd up", **L.stats()}), flush=True)
    srv.serve_forever()
    L.f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
