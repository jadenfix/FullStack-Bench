"""Activity reports: every ticket event for a customer over a window, streamed as JSON lines.

A report holds a pooled connection and a server-side cursor for its lifetime, so memory stays
flat however large the window is. The connection goes back to the pool when the report
completes (`finish`); the registry below is what the operator's /admin/pool sees as `owner`.
The mobile client sends `X-Deadline-Ms`; once it has passed we stop sending rows.
"""
import json
import time

from . import db
from .settings import S


class DeadlineExceeded(Exception):
    pass


def _begin(conn, customer, days):
    conn.execute("BEGIN")
    cur = conn.cursor(name=f"report_{int(time.time() * 1000)}")
    cur.itersize = int(S.report_chunk_rows)
    cur.execute("SELECT e.id, e.ticket_id, e.kind, e.note, e.at FROM ticket_events e JOIN tickets t ON t.id = e.ticket_id "
                "WHERE t.customer_id = %s AND e.at >= now() - make_interval(days => %s) ORDER BY e.at", (customer, days))
    return cur


def stream(request_id, customer, days, deadline_ms):
    """Yields JSON lines. The connection is acquired here and returned by `finish` once the
    final line has gone out."""
    started = time.monotonic()
    deadline = started + deadline_ms / 1000
    conn = db.POOL.acquire(f"report:{request_id}", timeout=max(0.0, deadline - time.monotonic()))
    cur = _begin(conn, customer, days)
    rows = 0
    chunk = int(S.report_chunk_rows)
    while True:
        batch = cur.fetchmany(chunk)
        if not batch:
            break
        for r in batch:
            rows += 1
            yield json.dumps({"id": r[0], "ticket": r[1], "kind": r[2], "note": r[3], "at": r[4].isoformat()}) + "\n"
        if time.monotonic() > deadline:
            raise DeadlineExceeded(f"report {request_id} exceeded {deadline_ms} ms after {rows} rows")
        time.sleep(float(S.report_chunk_delay_s))
    yield json.dumps({"done": True, "rows": rows, "elapsed_ms": int((time.monotonic() - started) * 1000)}) + "\n"
    finish(conn, cur)


def finish(conn, cur):
    cur.close()
    conn.execute("COMMIT")
    db.POOL.release(conn)
