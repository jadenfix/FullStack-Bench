"""Time source. Everything time-dependent (token expiry, queue visibility,
policy propagation, TTLs) reads the clock through here so tests and fault
scenarios can control it."""

import threading
import time


class Clock:
    def now(self) -> float:
        return time.time()


class FakeClock(Clock):
    def __init__(self, start: float = 1_790_000_000.0):
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds
