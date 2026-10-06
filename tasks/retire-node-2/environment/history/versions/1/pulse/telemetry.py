# tiny metrics. counters in memory, flushed as one json line to stdout (log shipper picks it up).
# we used to push to statsd; the shipper does it now.
import json
import os
import sys
import threading
import time

_c = {}
_lock = threading.Lock()
_started = time.time()


def inc(name, n=1):
    with _lock:
        _c[name] = _c.get(name, 0) + n


def snapshot():
    with _lock:
        return dict(_c, uptime_s=round(time.time() - _started, 1))


def flush(reason="periodic"):
    print(json.dumps({"metrics": snapshot(), "reason": reason}), flush=True)
    sys.stdout.flush()


def _periodic():
    while True:
        time.sleep(30)
        flush()


def install():
    threading.Thread(target=_periodic, daemon=True, name="telemetry").start()
