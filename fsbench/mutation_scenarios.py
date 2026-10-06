"""Operator-owned HTTP/Postgres schedules for durable delivery mutations.

No solver implementation is imported. The same checks exercise baseline, reference and mutants.
The caller supplies an isolated database, probe customer/address and service restart callback.
"""

import concurrent.futures
import json
import http.client
from datetime import datetime, timezone
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

CASES = (
    "replay",
    "conflict",
    "sdk_conflict",
    "scoped_keys",
    "concurrent_duplicates",
    "atomic_failure",
    "cancellation_race",
    "restart_replay",
    "lost_response",
    "concierge_replay",
    "unkeyed",
)


class Scenarios:
    def __init__(self, orders_url, dsn, customer, address, restart, concierge_url=None):
        self.url = orders_url.rstrip("/")
        self.dsn = dsn
        self.customer, self.address = customer, address
        self.restart = restart
        self.concierge = concierge_url
        self.results = {}

    def request(self, method, path, body=None, key=None, base=None, sdk="4.0.0"):
        headers = {"x-hb-sdk": sdk, "content-type": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        req = urllib.request.Request(
            (base or self.url) + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            response = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                # The contract specifies conflict/cancellation status and state,
                # without prescribing a JSON error body for 4xx responses.
                if not 400 <= response.status < 500:
                    raise
                payload = None
            return response.status, payload

    @staticmethod
    def body(hour):
        return {
            "delivery_window": {
                "start": f"2026-10-25T{hour:02d}:00:00Z",
                "end": f"2026-10-25T{hour + 1:02d}:00:00Z",
            }
        }

    def order(self):
        body = {
            "customer_id": self.customer,
            "address_id": self.address,
            "channel": "web",
            **self.body(8),
        }
        status, result = self.request("POST", "/v2/orders", body)
        assert status == 201, f"probe order creation: {status}"
        order = result["order_id"]
        self.assert_window(order, 8)
        return order

    def state(self, order):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status, window_start, window_end FROM orders WHERE order_id=%s",
                (order,),
            ).fetchone()
            count = conn.execute(
                "SELECT count(*) FROM order_events WHERE order_id=%s AND kind='updated'",
                (order,),
            ).fetchone()[0]
        return {
            "row": [row[0], *[x.astimezone(timezone.utc).isoformat() for x in row[1:]]],
            "events": count,
        }

    def assert_window(self, order, hour):
        expected = [
            datetime.fromisoformat(x.replace("Z", "+00:00")).isoformat()
            for x in self.body(hour)["delivery_window"].values()
        ]
        assert self.state(order)["row"][1:] == expected, (
            "successful mutation did not reach stored window"
        )

    def patch(self, order, body, key=None):
        response = self.request("PATCH", f"/v2/orders/{order}/delivery", body, key)
        if response[0] == 200:
            window = response[1].get("delivery_window", {})
            assert response[1].get("order_id") == order, (
                "response belongs to another order"
            )
            for field in ("start", "end"):
                actual = datetime.fromisoformat(
                    window.get(field, "").replace("Z", "+00:00")
                )
                wanted = datetime.fromisoformat(
                    body["delivery_window"][field].replace("Z", "+00:00")
                )
                assert actual == wanted, "response window is wrong"
        return response

    def wait_blocked(self, count, allow_serialized=False):
        deadline = time.monotonic() + 8
        blocked = 0
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            while time.monotonic() < deadline:
                blocked = conn.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' "
                    "AND datname=current_database() AND pid <> pg_backend_pid()"
                ).fetchone()[0]
                if blocked >= count:
                    return
                time.sleep(0.02)
        if allow_serialized and blocked >= 1:
            # A valid implementation may serialize requests before reaching PostgreSQL.
            # The operator still observed the first mutation blocked under its held row.
            return
        raise AssertionError(
            f"schedule did not establish {count} blocked HTTP mutations"
        )

    def replay(self):
        order = self.order()
        key = str(uuid.uuid4())
        first = self.patch(order, self.body(10), key)
        assert first[0] == 200
        self.assert_window(order, 10)
        assert self.patch(order, self.body(14), str(uuid.uuid4()))[0] == 200
        self.assert_window(order, 14)
        before = self.state(order)
        replay = self.patch(order, self.body(10), key)
        assert replay == first, (
            "retry returned the current order rather than its original receipt"
        )
        assert self.state(order) == before, (
            "replay changed state or appended another event"
        )
        # Object key order is not part of the request identity.
        reordered = {
            "delivery_window": dict(
                reversed(list(self.body(10)["delivery_window"].items()))
            )
        }
        assert self.patch(order, reordered, key) == first
        assert self.state(order) == before

    def conflict(self):
        order = self.order()
        key = str(uuid.uuid4())
        assert self.patch(order, self.body(10), key)[0] == 200
        before = self.state(order)
        assert self.patch(order, self.body(14), key)[0] == 409, (
            "same key accepted a different body"
        )
        assert self.state(order) == before, "conflicting retry mutated the order"

    def scoped_keys(self):
        first, second = self.order(), self.order()
        key = str(uuid.uuid4())
        assert self.patch(first, self.body(10), key)[0] == 200
        status, response = self.patch(second, self.body(14), key)
        assert status == 200 and response["order_id"] == second, (
            "key leaked across orders"
        )
        self.assert_window(first, 10)
        self.assert_window(second, 14)

    def sdk_conflict(self):
        order = self.order()
        key = str(uuid.uuid4())
        assert self.patch(order, self.body(10), key)[0] == 200
        before = self.state(order)
        status, _ = self.request(
            "PATCH", f"/v2/orders/{order}/delivery", self.body(10), key, sdk="3.4.1"
        )
        assert status == 409, "same key accepted another SDK major"
        assert self.state(order) == before, "SDK-major conflict changed state"
        replay = self.request(
            "PATCH", f"/v2/orders/{order}/delivery", self.body(10), key, sdk="4.7.2"
        )
        original = self.request(
            "PATCH", f"/v2/orders/{order}/delivery", self.body(10), key
        )
        assert replay == original and replay[0] == 200, (
            "minor SDK release could not replay its major's receipt"
        )
        assert self.state(order) == before

    def unkeyed(self):
        order = self.order()
        before = self.state(order)
        for hour in (10, 14):
            assert self.patch(order, self.body(hour))[0] == 200
            self.assert_window(order, hour)
        assert self.state(order)["events"] == before["events"] + 2, (
            "unkeyed requests were deduplicated"
        )

    def concurrent_duplicates(self):
        order = self.order()
        key = str(uuid.uuid4())
        before = self.state(order)
        with (
            psycopg.connect(self.dsn, autocommit=True) as lock,
            concurrent.futures.ThreadPoolExecutor(4) as pool,
        ):
            lock.execute("BEGIN")
            try:
                lock.execute(
                    "SELECT 1 FROM orders WHERE order_id=%s FOR UPDATE", (order,)
                )
                calls = [
                    pool.submit(self.patch, order, self.body(10), key) for _ in range(4)
                ]
                self.wait_blocked(4, allow_serialized=True)
            finally:
                # Release the row before joining HTTP threads, including a failed schedule.
                lock.execute("COMMIT")
            replies = [call.result(timeout=16) for call in calls]
        self.assert_window(order, 10)
        assert all(reply[0] == 200 for reply in replies), (
            "duplicate produced a server error/conflict"
        )
        assert all(reply == replies[0] for reply in replies), (
            "duplicate responses differ"
        )
        assert self.state(order)["events"] == before["events"] + 1, (
            "duplicate durable journal entries"
        )

    def atomic_failure(self):
        order = self.order()
        key = str(uuid.uuid4())
        before = self.state(order)
        suffix = uuid.uuid4().hex
        function, trigger = f"fsbench_reject_{suffix}", f"fsbench_reject_{suffix}"
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            # Only this fresh operator probe order is affected. Nothing in the customer workload is touched.
            conn.execute(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                f"IF NEW.order_id = '{order}'::uuid AND NEW.kind='updated' THEN "
                "RAISE EXCEPTION 'operator probe journal failure'; END IF; RETURN NEW; END $$"
            )
            conn.execute(
                f"CREATE TRIGGER {trigger} BEFORE INSERT ON order_events FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
            try:
                failed = self.patch(order, self.body(10), key)
                failed_state = self.state(order)
            finally:
                conn.execute(f"DROP TRIGGER {trigger} ON order_events")
                conn.execute(f"DROP FUNCTION {function}()")
        # request() already requires valid JSON for a 5xx response. The brief
        # does not prescribe an object schema for that error body.
        assert 500 <= failed[0] < 600, (
            "journal rejection returned success"
        )
        assert failed_state == before, (
            "failure left a partial window mutation or journal entry"
        )
        retry = self.patch(order, self.body(10), key)
        assert retry[0] == 200, "failed mutation poisoned the retry key"
        self.assert_window(order, 10)
        assert self.state(order)["events"] == before["events"] + 1

    def cancellation_race(self):
        order = self.order()
        before = self.state(order)
        with (
            psycopg.connect(self.dsn, autocommit=True) as lock,
            concurrent.futures.ThreadPoolExecutor(2) as pool,
        ):
            lock.execute("BEGIN")
            try:
                lock.execute(
                    "SELECT 1 FROM orders WHERE order_id=%s FOR UPDATE", (order,)
                )
                cancel = pool.submit(
                    self.request, "POST", f"/v2/orders/{order}/cancel", {}
                )
                self.wait_blocked(1)
                move = pool.submit(self.patch, order, self.body(14), str(uuid.uuid4()))
                self.wait_blocked(2, allow_serialized=True)
            finally:
                lock.execute("COMMIT")
            cancelled, moved = cancel.result(timeout=16), move.result(timeout=16)
        assert cancelled[0] == 200 and cancelled[1]["status"] == "cancelled"
        assert moved[0] in (400, 409), (
            "reschedule accepted a stale pre-cancellation read"
        )
        after = self.state(order)
        assert after["row"] == ["cancelled", *before["row"][1:]], (
            "cancelled order's window changed"
        )
        assert after["events"] == before["events"] + 1

    def restart_replay(self):
        order = self.order()
        key = str(uuid.uuid4())
        first = self.patch(order, self.body(10), key)
        assert first[0] == 200
        self.assert_window(order, 10)
        assert self.patch(order, self.body(14), str(uuid.uuid4()))[0] == 200
        self.assert_window(order, 14)
        before = self.state(order)
        self.restart()
        assert self.patch(order, self.body(10), key) == first, (
            "receipt lost across service restart"
        )
        assert self.state(order) == before

    def lost_response(self):
        order = self.order()
        key = str(uuid.uuid4())
        owner = self
        committed = []

        class DropReply(BaseHTTPRequestHandler):
            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                committed.append(owner.patch(order, body, key))
                # The upstream committed and answered; deliberately deliver none of that answer downstream.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()

            def log_message(self, *args):
                pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), DropReply)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            try:
                self.request(
                    "PATCH",
                    "/lost",
                    self.body(10),
                    key,
                    base=f"http://127.0.0.1:{proxy.server_port}",
                )
            except (OSError, urllib.error.URLError, http.client.RemoteDisconnected):
                pass
            else:
                raise AssertionError("fault proxy accidentally delivered the response")
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=3)
        assert len(committed) == 1 and committed[0][0] == 200
        self.assert_window(order, 10)
        assert self.patch(order, self.body(14), str(uuid.uuid4()))[0] == 200
        self.assert_window(order, 14)
        before = self.state(order)
        assert self.patch(order, self.body(10), key) == committed[0], (
            "lost-reply retry did not recover the committed receipt"
        )
        assert self.state(order) == before

    def concierge_replay(self):
        assert self.concierge, "concierge URL missing"
        order = self.order()
        key = str(uuid.uuid4())

        def call(hour, idem):
            window = self.body(hour)["delivery_window"]
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "reschedule_delivery",
                    "arguments": {
                        "order_id": order,
                        "window_start": window["start"],
                        "window_end": window["end"],
                        "idempotency_key": idem,
                    },
                },
            }
            status, response = self.request(
                "POST", "/mcp", request, base=self.concierge
            )
            assert status == 200 and "error" not in response, "MCP request rejected"
            result = response.get("result", {})
            assert not result.get("isError"), (
                "concierge rejected the optional retry key"
            )
            normal = dict(result)
            normal["content"] = [
                {**part, "text": json.loads(part["text"])}
                if part.get("type") == "text"
                else part
                for part in result.get("content", [])
            ]
            payload = normal.get("structuredContent") or next(
                (
                    part["text"]
                    for part in normal["content"]
                    if part.get("type") == "text"
                ),
                {},
            )
            assert payload.get("order_id") == order, "MCP returned the wrong order"
            for field in ("start", "end"):
                actual = datetime.fromisoformat(
                    payload.get("delivery_window", {})
                    .get(field, "")
                    .replace("Z", "+00:00")
                )
                wanted = datetime.fromisoformat(window[field].replace("Z", "+00:00"))
                assert actual == wanted, "MCP response window is wrong"
            return normal

        first = call(10, key)
        self.assert_window(order, 10)
        call(14, str(uuid.uuid4()))
        self.assert_window(order, 14)
        before = self.state(order)
        self.restart()
        replayed = call(10, key)
        if replayed != first:
            self.mcp_difference = {"first": first, "replayed": replayed}
        assert replayed == first, "MCP/SDK failed to preserve the original receipt"
        assert self.state(order) == before, "MCP replay re-applied its mutation"

    def run(self):
        for name in CASES:
            try:
                getattr(self, name)()
            except Exception as error:
                self.results[name] = {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "detail": str(error)[:180],
                }
            else:
                self.results[name] = {"ok": True}
        return self.results
