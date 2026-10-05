"""The load balancer: routes requests to ready instances and records metrics.

Addressing (either works):
  Host: <service>.<env>.<project>.simcloud.internal
  Path: /_svc/<project>/<env>/<service>/<rest>

Traffic between releases is split with smooth weighted round-robin, so a
90/10 split is exact over every 10 requests. Within a release, instances are
used round-robin. Metrics are measured here, at the load balancer, not by the
application.
"""

import asyncio
import collections
import itertools
import threading
import time
import uuid
from typing import Callable

import httpx

from .runtime import Key, Supervisor

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length"}
SUFFIX = ".simcloud.internal"
BUCKETS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000]


class Metrics:
    def __init__(self, window: int = 20000):
        self._lock = threading.Lock()
        self._data: dict = collections.defaultdict(lambda: {"count": 0, "status": collections.Counter(),
                                                            "latency_ms": collections.deque(maxlen=window),
                                                            "events": collections.deque(maxlen=window)})

    def record(self, key: Key, release: str | None, status: int, ms: float) -> None:
        now = time.time()
        with self._lock:
            for k in {(key, release), (key, None)}:  # a set: no double count when release is None
                d = self._data[k]
                d["count"] += 1
                d["status"][f"{status // 100}xx"] += 1
                d["latency_ms"].append(ms)
                d["events"].append((now, status, ms))

    def summary(self, key: Key, release: str | None = None, since: float = 0.0) -> dict:
        with self._lock:
            d = self._data.get((key, release))
            events = [e for e in d["events"] if e[0] >= since] if d else []
        lat = sorted(e[2] for e in events)

        def pct(p):
            return round(lat[min(len(lat) - 1, int(p * len(lat)))], 2) if lat else None
        status = collections.Counter(f"{e[1] // 100}xx" for e in events)
        return {"requests": len(events), "status": dict(status), "p50_ms": pct(0.50), "p95_ms": pct(0.95),
                "p99_ms": pct(0.99), "error_rate": round(status.get("5xx", 0) / len(events), 4) if events else 0.0}


class WeightedPicker:
    """Smooth weighted round-robin (as in nginx)."""

    def __init__(self):
        self._current: dict = collections.defaultdict(int)
        self._lock = threading.Lock()

    def pick(self, scope_key, weights: dict[str, int]) -> str | None:
        live = {r: w for r, w in weights.items() if w > 0}
        if not live:
            return None
        with self._lock:
            total = sum(live.values())
            best = None
            for r in sorted(live):
                self._current[(scope_key, r)] += live[r]
                if best is None or self._current[(scope_key, r)] > self._current[(scope_key, best)]:
                    best = r
            self._current[(scope_key, best)] -= total
            return best


class Router:
    """ASGI application."""

    def __init__(self, supervisor: Supervisor, traffic: Callable[[Key], dict[str, int]], request_timeout: float = 30.0,
                 faults: Callable[[Key], tuple[float, int | None]] | None = None):
        self.supervisor = supervisor
        self.traffic = traffic
        self.faults = faults
        self.metrics = Metrics()
        self.picker = WeightedPicker()
        self._rr = collections.defaultdict(itertools.count)
        self.timeout = request_timeout
        self._client: httpx.AsyncClient | None = None
        self.access: collections.deque = collections.deque(maxlen=50000)

    def _done(self, key, release, instance, status, start, request_id, scope, path) -> None:
        ms = (time.perf_counter() - start) * 1000
        self.metrics.record(key, release, status, ms)
        self.access.append({"ts": time.time(), "service": "/".join(key), "release": release, "instance": instance,
                            "status": status, "ms": round(ms, 2), "request_id": request_id,
                            "method": scope["method"], "path": path})

    def find_requests(self, request_id: str, project: str) -> list[dict]:
        return [a for a in list(self.access) if a["request_id"] == request_id and a["service"].startswith(project + "/")]

    def _target(self, scope) -> tuple[Key | None, str]:
        path = scope["path"]
        if path.startswith("/_svc/"):
            parts = path.split("/", 5)
            if len(parts) >= 5:
                rest = "/" + (parts[5] if len(parts) > 5 else "")
                return (parts[2], parts[3], parts[4]), rest
        host = dict(scope["headers"]).get(b"host", b"").decode().split(":")[0]
        if host.endswith(SUFFIX):
            labels = host[: -len(SUFFIX)].split(".")
            if len(labels) == 3:
                service, env, project = labels
                return (project, env, service), path
        return None, path

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    if self._client:
                        await self._client.aclose()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        start = time.perf_counter()
        request_id = dict(scope["headers"]).get(b"x-request-id", b"").decode() or uuid.uuid4().hex
        key, path = self._target(scope)
        if key is None:
            return await _plain(send, 404, b"unknown service: use Host <service>.<env>.<project>.simcloud.internal "
                                           b"or /_svc/<project>/<env>/<service>/")
        if self.faults:
            delay_ms, forced = self.faults(key)
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)
            if forced:
                self._done(key, None, None, forced, start, request_id, scope, path)
                return await _plain(send, forced, b"injected fault", {"retry-after": "1"} if forced == 503 else None)
        release = self.picker.pick(key, self.traffic(key))
        ready = self.supervisor.ready_instances(key, release) if release else []
        if not ready:
            # Fall back to any release with ready instances rather than fail outright.
            for r, w in sorted(self.traffic(key).items(), key=lambda kv: -kv[1]):
                ready = self.supervisor.ready_instances(key, r)
                if ready:
                    release = r
                    break
        if not ready:
            self._done(key, release, None, 503, start, request_id, scope, path)
            return await _plain(send, 503, b"no ready instances", {"retry-after": "1"})
        inst = ready[next(self._rr[(key, release)]) % len(ready)]
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        headers = [(k.decode(), v.decode()) for k, v in scope["headers"] if k.decode().lower() not in HOP_BY_HOP]
        client_ip = (scope.get("client") or ("unknown",))[0]
        headers += [("x-forwarded-for", client_ip), ("x-request-id", request_id)]
        qs = scope.get("query_string", b"").decode()
        url = f"http://127.0.0.1:{inst.port}{path}" + (f"?{qs}" if qs else "")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        try:
            r = await self._client.request(scope["method"], url, headers=headers, content=body)
        except httpx.TimeoutException:
            self._done(key, release, inst.id, 504, start, request_id, scope, path)
            return await _plain(send, 504, b"upstream timed out")
        except httpx.HTTPError:
            # Connection refused or reset: the instance died mid-request.
            self._done(key, release, inst.id, 502, start, request_id, scope, path)
            return await _plain(send, 502, b"upstream connection failed")
        out_headers = [(k.encode(), v.encode()) for k, v in r.headers.items() if k.lower() not in HOP_BY_HOP]
        out_headers += [(b"x-simcloud-release", release.encode()), (b"x-simcloud-instance", inst.id.encode()),
                        (b"x-request-id", request_id.encode())]
        await send({"type": "http.response.start", "status": r.status_code, "headers": out_headers})
        await send({"type": "http.response.body", "body": r.content})
        self._done(key, release, inst.id, r.status_code, start, request_id, scope, path)


async def _plain(send, status: int, body: bytes, extra: dict | None = None):
    headers = [(b"content-type", b"text/plain")] + [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def serve_router(router: Router, supervisor: Supervisor, host: str, port: int):
    """Run the router with uvicorn on the runtime loop. Returns the uvicorn server."""
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(router, host=host, port=port, log_level="warning", lifespan="on"))
    server.future = asyncio.run_coroutine_threadsafe(server.serve(), supervisor.loop)
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("router failed to start")
    return server


def stop_router(server, timeout: float = 10.0) -> None:
    server.should_exit = True
    try:
        server.future.result(timeout)
    except Exception:
        pass
