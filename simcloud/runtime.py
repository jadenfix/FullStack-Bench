"""The runtime: runs service instances as real processes and supervises them.

Behaviour a service author can rely on (documented in the skill):
- Each instance gets `PORT`; it is routed to only after its readiness probe
  passes, and is taken out of routing after `failure_threshold` failed probes.
- An instance that exits on its own is restarted with exponential backoff
  (1 s, 2 s, 4 s ... capped at 30 s); `restarts` and `last_exit_code` are
  reported.
- Stopping an instance (rollout, scale-down, restart) removes it from routing
  first, then sends SIGTERM; it is killed if still running after
  `drain_seconds`. Requests still in flight when it dies fail.

The runtime owns an asyncio loop in a background thread; the control plane
calls it through the synchronous methods at the bottom.
"""

import asyncio
import collections
import itertools
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

Key = tuple[str, str, str]  # (project, env, service)


@dataclass
class ReleaseRun:
    """What the runtime needs to run one release of a service."""
    release_id: str
    workdir: str
    command: list[str]
    env: dict[str, str]
    probe_path: str = "/healthz"
    probe_interval: float = 5.0
    probe_timeout: float = 2.0
    failure_threshold: int = 3
    drain_seconds: float = 10.0


@dataclass
class Instance:
    id: str
    key: Key
    release_id: str
    port: int
    state: str = "starting"  # starting | ready | unready | stopping | stopped | crashed
    restarts: int = 0
    last_exit_code: int | None = None
    pid: int | None = None
    started_at: float = 0.0
    probe_failures: int = 0
    stopping: bool = False
    proc: asyncio.subprocess.Process | None = field(default=None, repr=False)
    tasks: list = field(default_factory=list, repr=False)

    def public(self) -> dict:
        return {"id": self.id, "release": self.release_id, "port": self.port, "state": self.state,
                "restarts": self.restarts, "last_exit_code": self.last_exit_code, "pid": self.pid}


class LogSink:
    """Per-service log lines in memory (bounded) and on disk."""

    def __init__(self, root: Path, max_lines: int = 5000):
        self.root = root
        self.max_lines = max_lines
        self._lines: dict[Key, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=max_lines))
        self._lock = threading.Lock()

    def write(self, key: Key, source: str, line: str) -> None:
        rec = {"ts": time.time(), "source": source, "line": line}
        with self._lock:
            self._lines[key].append(rec)
        path = self.root / key[0] / key[1] / f"{key[2]}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(f"{rec['ts']:.3f} [{source}] {line}\n")

    def read(self, key: Key, since: float = 0.0, limit: int = 500, source_prefix: str = "") -> list[dict]:
        with self._lock:
            lines = [r for r in self._lines.get(key, ()) if r["ts"] > since and r["source"].startswith(source_prefix)]
        return lines[-limit:]

    def all_text(self) -> str:
        with self._lock:
            return "\n".join(r["line"] for q in self._lines.values() for r in q)


class Supervisor:
    def __init__(self, data_dir: Path, port_range: tuple[int, int] = (21000, 21999)):
        self.data_dir = data_dir
        self.logs = LogSink(data_dir / "logs")
        self._ports = itertools.cycle(range(*port_range))
        self._used_ports: set[int] = set()
        self._instances: dict[Key, list[Instance]] = collections.defaultdict(list)
        self._ids = itertools.count(1)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name="simcloud-runtime", daemon=True)
        self._thread.start()
        self._http = None

    # ---- internals (run on the runtime loop) -----------------------------------

    def _alloc_port(self) -> int:
        for _ in range(2000):
            p = next(self._ports)
            if p not in self._used_ports:
                self._used_ports.add(p)
                return p
        raise RuntimeError("no free ports")

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=5.0)
        return self._http

    async def _start(self, key: Key, run: ReleaseRun) -> Instance:
        inst = Instance(id=f"i{next(self._ids):05d}", key=key, release_id=run.release_id, port=self._alloc_port())
        self._instances[key].append(inst)
        inst.tasks.append(asyncio.ensure_future(self._lifecycle(inst, run)))
        return inst

    async def _lifecycle(self, inst: Instance, run: ReleaseRun) -> None:
        backoff = 1.0
        while not inst.stopping:
            env = {**os.environ, **run.env, "PORT": str(inst.port), "SIMCLOUD_INSTANCE": inst.id,
                   "SIMCLOUD_RELEASE": run.release_id}
            try:
                inst.proc = await asyncio.create_subprocess_exec(
                    *run.command, cwd=run.workdir, env=env, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, start_new_session=True)
            except (FileNotFoundError, PermissionError) as e:
                self.logs.write(inst.key, f"platform/{inst.id}", f"failed to start {run.command!r}: {e}")
                inst.state, inst.last_exit_code = "crashed", 127
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                inst.restarts += 1
                continue
            inst.pid, inst.started_at, inst.state, inst.probe_failures = inst.proc.pid, time.time(), "starting", 0
            self.logs.write(inst.key, f"platform/{inst.id}", f"started release {run.release_id} pid {inst.pid} port {inst.port}")
            pump = asyncio.ensure_future(self._pump(inst))
            probe = asyncio.ensure_future(self._probe(inst, run))
            code = await inst.proc.wait()
            probe.cancel()
            await pump
            inst.last_exit_code = code
            if inst.stopping:
                break
            inst.state = "crashed"
            self.logs.write(inst.key, f"platform/{inst.id}", f"exited with code {code}; restarting in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            inst.restarts += 1
        inst.state = "stopped"
        self._used_ports.discard(inst.port)

    async def _pump(self, inst: Instance) -> None:
        assert inst.proc and inst.proc.stdout
        while True:
            line = await inst.proc.stdout.readline()
            if not line:
                return
            self.logs.write(inst.key, f"app/{inst.id}", line.decode(errors="replace").rstrip("\n"))

    async def _probe(self, inst: Instance, run: ReleaseRun) -> None:
        client = await self._client()
        url = f"http://127.0.0.1:{inst.port}{run.probe_path}"
        while not inst.stopping:
            try:
                r = await client.get(url, timeout=run.probe_timeout)
                ok = r.status_code < 400
            except httpx.HTTPError:
                ok = False
            if ok:
                inst.probe_failures = 0
                if inst.state in ("starting", "unready"):
                    inst.state = "ready"
            else:
                inst.probe_failures += 1
                if inst.state == "ready" and inst.probe_failures >= run.failure_threshold:
                    inst.state = "unready"
                    self.logs.write(inst.key, f"platform/{inst.id}", "readiness probe failing; removed from routing")
            await asyncio.sleep(run.probe_interval if inst.state == "ready" else min(run.probe_interval, 0.25))

    async def _stop(self, inst: Instance, drain_seconds: float) -> None:
        inst.stopping = True
        inst.state = "stopping"
        proc = inst.proc
        if proc and proc.returncode is None:
            self.logs.write(inst.key, f"platform/{inst.id}", "removed from routing; sending SIGTERM")
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=drain_seconds)
            except asyncio.TimeoutError:
                self.logs.write(inst.key, f"platform/{inst.id}", f"still running after {drain_seconds}s; SIGKILL")
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
        for t in inst.tasks:
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        self._instances[inst.key] = [i for i in self._instances[inst.key] if i is not inst]

    async def _wait_ready(self, insts: list[Instance], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(i.state == "ready" for i in insts):
                return True
            await asyncio.sleep(0.05)
        return False

    async def _rollout(self, key: Key, run: ReleaseRun, count: int, timeout: float, replace: bool) -> dict:
        """Start `count` instances of the release; if they all become ready and
        `replace`, stop every instance of other releases. Never leaves the
        service with no ready instances because of a bad release."""
        new = [await self._start(key, run) for _ in range(count)]
        ok = await self._wait_ready(new, timeout)
        if not ok:
            for i in new:
                await self._stop(i, run.drain_seconds)
            return {"ready": False, "reason": "new instances did not pass readiness in time"}
        if replace:
            old = [i for i in self._instances[key] if i.release_id != run.release_id and not i.stopping]
            await asyncio.gather(*(self._stop(i, run.drain_seconds) for i in old))
        return {"ready": True}

    # ---- synchronous API for the control plane ----------------------------------

    def _call(self, coro, timeout: float | None = None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def rollout(self, key: Key, run: ReleaseRun, count: int = 1, timeout: float = 60.0, replace: bool = True) -> dict:
        return self._call(self._rollout(key, run, count, timeout, replace), timeout + 120)

    def scale_release(self, key: Key, run: ReleaseRun, count: int, timeout: float = 60.0) -> dict:
        """Bring one release to exactly `count` instances, leaving other releases alone (canaries)."""
        async def go():
            current = [i for i in self._instances[key] if i.release_id == run.release_id and not i.stopping]
            if len(current) < count:
                new = [await self._start(key, run) for _ in range(count - len(current))]
                if not await self._wait_ready(new, timeout):
                    return {"ready": False, "reason": "new instances did not pass readiness in time"}
            for i in current[count:]:
                await self._stop(i, run.drain_seconds)
            return {"ready": True}
        return self._call(go(), timeout + 120)

    def stop_release(self, key: Key, release_id: str, drain_seconds: float = 10.0) -> None:
        async def go():
            await asyncio.gather(*(self._stop(i, drain_seconds) for i in list(self._instances[key])
                                   if i.release_id == release_id))
        self._call(go(), drain_seconds + 30)

    def stop_service(self, key: Key, drain_seconds: float = 10.0) -> None:
        async def go():
            await asyncio.gather(*(self._stop(i, drain_seconds) for i in list(self._instances[key])))
        self._call(go(), drain_seconds + 30)

    def restart(self, key: Key, runs: dict[str, ReleaseRun], timeout: float = 60.0) -> dict:
        """Rolling restart: replace each instance with a fresh one of the same release."""
        async def go():
            for old in [i for i in self._instances[key] if not i.stopping]:
                run = runs[old.release_id]
                new = await self._start(key, run)
                if not await self._wait_ready([new], timeout):
                    await self._stop(new, run.drain_seconds)
                    return {"ready": False, "reason": f"replacement for {old.id} did not become ready"}
                await self._stop(old, run.drain_seconds)
            return {"ready": True}
        return self._call(go(), timeout * 4 + 120)

    def instances(self, key: Key) -> list[Instance]:
        return list(self._instances.get(key, []))

    def ready_instances(self, key: Key, release_id: str) -> list[Instance]:
        return [i for i in self._instances.get(key, []) if i.release_id == release_id and i.state == "ready"
                and not i.stopping]

    def shutdown(self) -> None:
        async def go():
            await asyncio.gather(*(self._stop(i, 2.0) for insts in self._instances.values() for i in list(insts)))
            if self._http:
                await self._http.aclose()
        try:
            self._call(go(), 60)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
