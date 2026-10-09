# Runs inside the SimCloud sidecar after the agent phase (verifier.collect). The agent can't reach this
# container's filesystem, the operator tokens or the Postgres admin password.
import json
import os
import threading
import time
import urllib.error
import urllib.request

import psycopg

os.makedirs("/evidence", exist_ok=True)
ADMIN = {"Authorization": "Bearer " + os.environ["SIMCLOUD_ADMIN_TOKEN"]}
API = "http://127.0.0.1:7400"
SVC = "http://127.0.0.1:7480/_svc/support/prod/support-api"
P = json.load(open("/seed/expected_public.json"))


def call(method, url, headers=None, body=None, timeout=60):
    req = urllib.request.Request(url, method=method, headers={"content-type": "application/json", **(headers or {})},
                                 data=json.dumps(body).encode() if body is not None else None)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(), int((time.monotonic() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), int((time.monotonic() - t0) * 1000)
    except Exception as e:
        return 0, repr(e), int((time.monotonic() - t0) * 1000)


def save(name, obj):
    with open(f"/evidence/{name}", "w") as f:
        json.dump(obj, f, default=str)


def report(customer, days, deadline_ms, rid):
    return call("GET", f"{SVC}/reports/activity?customer={customer}&days={days}",
                {"x-deadline-ms": str(deadline_ms), "x-request-id": rid}, timeout=60)


def pool_and_db():
    """The pool as each instance reports it (the load balancer picks one per call, so several
    samples cover both) and the database's own view of every session, which covers all of them."""
    samples = []
    for _ in range(6):
        s, body, _ = call("GET", f"{SVC}/admin/pool")
        samples.append(json.loads(body) if s == 200 else {"error": f"pool unavailable ({s})"})
    try:
        with psycopg.connect(host="127.0.0.1", port=5433, user="simcloud_admin", password=PW, dbname="postgres") as c:
            rows = c.execute("SELECT state, count(*) FROM pg_stat_activity WHERE datname = 'support__prod__support' "
                             "GROUP BY state").fetchall()
        db = {r[0] or "none": r[1] for r in rows}
    except Exception as e:
        db = {"error": repr(e)}
    return {"pool": samples, "db": db}


PW = open("/var/lib/simcloud/pg.admin").read().strip()
challenges = {}
# how many rows each heavy customer's 30-day report holds, from the database itself
with psycopg.connect(host="127.0.0.1", port=5433, user="simcloud_admin", password=PW, dbname="support__prod__support") as c:
    rows_for = {cid: c.execute("SELECT count(*) FROM ticket_events e JOIN tickets t ON t.id = e.ticket_id "
                               "WHERE t.customer_id = %s AND e.at >= now() - make_interval(days => 30)", (cid,)).fetchone()[0]
                for cid in P["heavy"]}
save("rows.json", rows_for)

# 0. From here on the operator drives production; incidents carry the phase.
call("POST", f"{API}/admin/v1/guard/phase", ADMIN, body={"phase": "post_handoff"})
before = pool_and_db()

# 1. The mobile client's cancellation pattern: reports that stop at their deadline, a few at a time.
storm, results = P["storm"], []
heavy = P["heavy"]
lock = threading.Lock()


def one(i):
    s, body, ms = report(heavy[i % len(heavy)], storm["days"], storm["deadline_ms"], f"storm-{i}")
    with lock:
        results.append({"i": i, "status": s, "ms": ms, "body": body[-200:]})


for start in range(0, storm["reports"], storm["concurrency"]):
    ts = [threading.Thread(target=one, args=(i,)) for i in range(start, min(start + storm["concurrency"], storm["reports"]))]
    [t.start() for t in ts]
    [t.join() for t in ts]
during = pool_and_db()
time.sleep(P["settle_s"])
after = pool_and_db()
save("storm.json", {"results": sorted(results, key=lambda r: r["i"]), "before": before, "during": during, "after": after})
challenges["cancellation-storm"] = {"status": "ran" if len(results) == storm["reports"] else "not_run",
                                    "detail": {"reports": len(results)}}

# 2. Foreground traffic right after the storm: customer pages must meet the SLO on the existing pool.
fg = [call("GET", f"{SVC}/customers/{cid}", timeout=10) for cid in P["foreground"]["customers"]]
save("foreground.json", [{"status": s, "ms": ms, "body": b[:120]} for s, b, ms in fg])
challenges["foreground-after-storm"] = {"status": "ran" if len(fg) == P["foreground"]["requests"] else "not_run",
                                        "detail": {"requests": len(fg)}}

# 3. Isolation: a long report keeps going while a short-deadline one is cut off beside it.
iso = P["isolation"]
long_result = {}


def long_report():
    s, body, ms = report(iso["long"]["customer"], iso["long"]["days"], iso["long"]["deadline_ms"], "iso-long")
    long_result.update({"status": s, "ms": ms, "lines": body.count("\n"), "tail": body[-160:]})


t = threading.Thread(target=long_report)
t.start()
time.sleep(0.5)
# three short reports in a row: the load balancer spreads consecutive requests over the instances,
# so at least one of them shares an instance with the long report
short_results = []
for n in range(3):
    s, body, ms = report(iso["short"]["customer"], iso["short"]["days"], iso["short"]["deadline_ms"], f"iso-short-{n}")
    short_results.append({"status": s, "ms": ms, "tail": body[-160:]})
t.join()
save("isolation.json", {"long": long_result, "short": short_results, "after": pool_and_db()})
challenges["isolation"] = {"status": "ran" if long_result else "not_run", "detail": {}}

# 4. Deadline semantics on a quiet pool: a generous deadline completes; a short one is refused promptly.
time.sleep(P["settle_s"])
s1, b1, ms1 = report(heavy[2], 30, 15000, "deadline-ok")
s2, b2, ms2 = report(heavy[3], 30, 400, "deadline-short")
save("deadline.json", {"ok": {"status": s1, "ms": ms1, "lines": b1.count("\n"), "tail": b1[-160:]},
                       "short": {"status": s2, "ms": ms2, "tail": b2[-160:]}, "after": pool_and_db()})
challenges["deadline"] = {"status": "ran", "detail": {}}
save("challenges.json", challenges)

# 5. The service's declared spec (the pool budget lives in its env) and SimCloud's evidence.
s, body, _ = call("GET", f"{API}/v1/projects/support/envs/prod/service/support-api", ADMIN)
save("service.json", json.loads(body) if s == 200 else {"error": f"service unavailable ({s})"})
# 6. SimCloud's evidence (also runs the final synthetic check and log/SQL scans).
s, body, _ = call("GET", f"{API}/admin/v1/evidence", ADMIN)
with open("/evidence/evidence.json", "w") as f:
    f.write(body if s == 200 else json.dumps({"error": f"evidence unavailable ({s})"}))
