# tiny metrics. counters in memory, flushed as one json line to stdout (log shipper picks it up).
# we used to push to statsd; the shipper does it now.
import json
import os
import signal
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


def _on_term(signum, frame):
    # PLAT-212: k8s gives us 30s on shutdown but we don't need it, and waiting made deploys slow.
    # flush what we have and go.
    flush("sigterm")
    os._exit(0)


def install():
    threading.Thread(target=_periodic, daemon=True, name="telemetry").start()
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
