"""Fault scenarios: deterministic failures a task injects to test resilience.

A scenario is set by the platform operator (the verifier), never the agent:

    faults:
      - type: iam_propagation      # policy/binding changes take effect after a delay
        seconds: 20
      - type: throttle             # every Nth matching control-plane call -> 429
        actions: ["service:deploy", "kv:*"]
        every: 3
        retry_after: 5
      - type: latency              # added at the load balancer
        services: ["shop/prod/payments"]
        ms: 400
      - type: errors               # every Nth request to a service -> status
        services: ["shop/prod/payments"]
        every: 4
        status: 503
      - type: region_outage        # services only in this region -> 503; control plane -> unavailable
        region: region-a
        start: 30                  # seconds after the scenario is loaded
        duration: 120

Faults are counter- or time-window based, never random, so a verifier run is
reproducible. Every injected failure is visible to the agent the way a real
one would be: status codes, Retry-After, logs and metrics.
"""

import threading
from dataclasses import dataclass, field

from .clock import Clock
from .errors import SimCloudError
from .policy import matches

TYPES = {"iam_propagation", "throttle", "latency", "errors", "region_outage"}


@dataclass
class FaultEngine:
    clock: Clock
    faults: list[dict] = field(default_factory=list)
    loaded_at: float = 0.0
    _counters: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def load(self, scenario: dict) -> dict:
        faults = scenario.get("faults") or []
        for f in faults:
            if f.get("type") not in TYPES:
                raise SimCloudError("invalid_request", f"unknown fault type {f.get('type')!r}", {"types": sorted(TYPES)})
            if f["type"] in ("throttle", "errors") and int(f.get("every", 0)) < 1:
                raise SimCloudError("invalid_request", f"{f['type']} needs every >= 1")
        with self._lock:
            self.faults = faults
            self.loaded_at = self.clock.now()
            self._counters = {}
        return self.describe()

    def clear(self) -> None:
        self.load({})

    def describe(self) -> dict:
        return {"faults": self.faults, "loaded_at": self.loaded_at}

    @property
    def iam_propagation_seconds(self) -> float:
        return float(sum(f.get("seconds", 0) for f in self.faults if f["type"] == "iam_propagation"))

    def _tick(self, idx: int, every: int) -> bool:
        with self._lock:
            n = self._counters.get(idx, 0) + 1
            self._counters[idx] = n
        return n % every == 0

    def _active(self, f: dict) -> bool:
        if "start" not in f and "duration" not in f:
            return True
        t = self.clock.now() - self.loaded_at
        start = float(f.get("start", 0))
        return start <= t < start + float(f.get("duration", float("inf")))

    def down_regions(self) -> set[str]:
        return {f["region"] for f in self.faults if f["type"] == "region_outage" and self._active(f)}

    # ---- hook points --------------------------------------------------------------

    def control_plane(self, action: str, regions: list[str] | None = None) -> None:
        """Called before a control-plane action is performed."""
        for i, f in enumerate(self.faults):
            if f["type"] == "throttle" and any(matches(p, action) for p in f.get("actions", ["*"])):
                if self._tick(i, int(f["every"])):
                    raise SimCloudError("throttled", f"rate limit exceeded for {action}",
                                        retry_after=float(f.get("retry_after", 1)))
        down = self.down_regions()
        if regions and down and set(regions) <= down:
            raise SimCloudError("unavailable", f"region(s) {sorted(set(regions) & down)} unavailable",
                                retry_after=5.0)

    def load_balancer(self, service: str, regions: list[str]) -> tuple[float, int | None]:
        """Returns (added latency in ms, forced status or None) for one request.
        `service` is 'project/env/name'."""
        delay, status = 0.0, None
        down = self.down_regions()
        if regions and down and set(regions) <= down:
            return 0.0, 503
        for i, f in enumerate(self.faults):
            if f["type"] == "latency" and _service_match(f, service) and self._active(f):
                delay += float(f.get("ms", 0))
            if f["type"] == "errors" and _service_match(f, service) and self._active(f):
                if self._tick(i, int(f["every"])):
                    status = int(f.get("status", 503))
        return delay, status


def _service_match(f: dict, service: str) -> bool:
    return any(matches(p, service) for p in f.get("services", ["*"]))
