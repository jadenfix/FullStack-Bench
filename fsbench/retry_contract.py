"""One logical client request, under the declared safe retry and time limits."""

import math
import time
from email.utils import parsedate_to_datetime


def retry_delay(value, wall_clock=time.time):
    if value is None or not str(value).strip():
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = max(
                0.0, parsedate_to_datetime(str(value)).timestamp() - wall_clock()
            )
        except (TypeError, ValueError, OverflowError):
            return None
    return delay if math.isfinite(delay) and delay >= 0 else None


def logical_request(
    once,
    *,
    retry_for=20.0,
    request_timeout=10.0,
    clock=time.monotonic,
    sleep=time.sleep,
    wall_clock=time.time,
):
    start = clock()
    deadline = start + retry_for
    attempts = 0
    while True:
        attempts += 1
        status, data, header, error = once(
            min(request_timeout, max(0.001, deadline - clock()))
        )
        delay = (
            retry_delay(header, wall_clock)
            if status == 503
            else (1.0 if error == "refused" else None)
        )
        remaining = deadline - clock()
        if delay is None or remaining <= 0:
            break
        delay = max(0.01, delay)
        sleep(min(delay, remaining))
        if clock() >= deadline:
            break
    return {
        "status": status,
        "data": data,
        "err": error,
        "attempts": attempts,
        "ms": round((clock() - start) * 1000, 1),
    }
