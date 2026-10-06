import base64
import concurrent.futures
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from simsaas.payments import RETRY_DELAYS, Tillpoint, create_app, sign

KEY = {"Authorization": "Bearer tp_test_shop"}


class Clock:
    t = 1_790_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def pay():
    received, statuses = [], []

    def receiver(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(statuses.pop(0) if statuses else 200)

    clock = Clock()
    tp = Tillpoint(clock=clock, http=httpx.Client(transport=httpx.MockTransport(receiver)))
    tp.add_account("shop", "tp_test_shop")
    ep = tp.add_endpoint("shop", "http://shop.internal/webhooks/tillpoint")
    return tp, TestClient(create_app(tp, "op-admin")), clock, received, statuses, ep


def charge(c, amount=1299, key=None, **extra):
    headers = {**KEY, **({"Idempotency-Key": key} if key else {})}
    return c.post("/v1/charges", json={"amount": amount, "currency": "usd", **extra}, headers=headers)


def test_charge_and_validation(pay):
    tp, c, *_ = pay
    r = charge(c)
    assert r.status_code == 201 and r.json()["amount"] == 1299
    assert charge(c, amount=12.99).status_code == 400  # minor units only
    assert c.post("/v1/charges", json={"amount": 1, "currency": "usd"}).status_code == 401


def test_idempotency_replay_mismatch(pay):
    tp, c, *_ = pay
    a, b = charge(c, key="k1"), charge(c, key="k1")
    assert a.json()["id"] == b.json()["id"] and b.headers["idempotent-replayed"] == "true"
    assert len(tp.state.charges) == 1
    assert charge(c, amount=500, key="k1").status_code == 422


def test_concurrent_duplicates_make_one_charge(pay):
    tp, c, *_ = pay
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        codes = [f.result().status_code for f in [pool.submit(charge, c, 700, "same") for _ in range(8)]]
    assert len(tp.state.charges) == 1
    assert set(codes) <= {201, 409}


def test_refund_limits(pay):
    tp, c, *_ = pay
    ch = charge(c, amount=1000).json()
    assert c.post("/v1/refunds", json={"charge": ch["id"], "amount": 600}, headers=KEY).status_code == 201
    assert c.post("/v1/refunds", json={"charge": ch["id"], "amount": 600}, headers=KEY).status_code == 400
    assert c.post("/v1/refunds", json={"charge": ch["id"]}, headers=KEY).json()["amount"] == 400
    assert c.get(f"/v1/charges/{ch['id']}", headers=KEY).json()["amount_refunded"] == 1000


def _verify(secret, req):
    msg_id, ts, sig = req.headers["webhook-id"], int(req.headers["webhook-timestamp"]), req.headers["webhook-signature"]
    key = base64.b64decode(secret.split("_", 1)[1])
    expected = "v1," + base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + req.content,
                                                 hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, sig)


def test_webhooks_are_signed_and_retried(pay):
    tp, c, clock, received, statuses, ep = pay
    statuses.extend([500, 503])
    charge(c)
    tp.deliver_due()
    assert len(received) == 1 and _verify(ep.secret, received[0])
    assert json.loads(received[0].content)["type"] == "charge.succeeded"
    tp.deliver_due()
    assert len(received) == 1  # not yet due
    clock.t += RETRY_DELAYS[0]
    tp.deliver_due()
    clock.t += RETRY_DELAYS[1]
    tp.deliver_due()
    assert len(received) == 3 and len({r.headers["webhook-id"] for r in received}) == 1  # same event id each time
    attempts = [e for e in tp.state.events if e["event"] == "webhook_delivery"]
    assert [a["status"] for a in attempts] == [500, 503, 200]


def test_tampered_body_fails_verification(pay):
    tp, c, clock, received, statuses, ep = pay
    charge(c)
    tp.deliver_due()
    req = received[0]
    forged = httpx.Request("POST", req.url, headers=req.headers, content=req.content.replace(b"1299", b"1"))
    assert not _verify(ep.secret, forged)
    assert sign(ep.secret, "x", 1, b"a") != sign(ep.secret, "x", 2, b"a")  # timestamp is signed


def test_duplicate_and_reorder_faults(pay):
    tp, c, clock, received, statuses, ep = pay
    c.put("/admin/faults", json={"duplicate_every": 2, "reorder": True}, headers={"Authorization": "Bearer op-admin"})
    ids = [charge(c, amount=100 + i).json()["id"] for i in range(4)]
    tp.deliver_due()
    delivered = [json.loads(r.content)["data"]["object"]["id"] for r in received]
    assert len(delivered) == 6  # events 2 and 4 delivered twice
    assert delivered[0] == ids[-1]  # newest first
    assert sorted(set(delivered)) == sorted(ids)


def test_operator_endpoints(pay):
    tp, c, *_ = pay
    assert c.get("/admin/events").status_code == 403
    assert c.put("/admin/faults", json={}, headers=KEY).status_code == 403


def test_payouts_idempotency_listing_and_every_nth_fault():
    from fastapi.testclient import TestClient
    from simsaas.payments import Tillpoint, create_app
    tp = Tillpoint()
    tp.add_account("acct_1", "tp_test_key")
    c = TestClient(create_app(tp, "op"))
    h = {"Authorization": "Bearer tp_test_key"}
    body = {"amount": 1250, "currency": "GBP", "destination": "ba_seller_42", "metadata": {"run": "2026-10-05"}}
    a = c.post("/v1/payouts", json=body, headers={**h, "Idempotency-Key": "k1"})
    b = c.post("/v1/payouts", json=body, headers={**h, "Idempotency-Key": "k1"})
    assert a.status_code == 201 and b.json()["id"] == a.json()["id"] and b.headers["idempotent-replayed"] == "true"
    assert c.post("/v1/payouts", json={**body, "amount": 1}, headers={**h, "Idempotency-Key": "k1"}).status_code == 422
    assert c.put("/admin/faults", json={"type": "errors_every_nth", "path": "/v1/payouts", "n": 3, "status": 503},
                 headers={"Authorization": "Bearer op"}).status_code == 200
    statuses = [c.post("/v1/payouts", json=body, headers={**h, "Idempotency-Key": f"n{i}"}).status_code for i in range(6)]
    assert statuses == [201, 201, 503, 201, 201, 503]
    retry = c.post("/v1/payouts", json=body, headers={**h, "Idempotency-Key": "n2"})  # the key freed by the 503
    assert retry.status_code == 201
    listed = c.get("/v1/payouts", params={"limit": 2}, headers=h).json()
    assert len(listed["data"]) == 2 and listed["has_more"] and listed["data"][0]["id"] == retry.json()["id"]
    state = c.get("/admin/state", headers={"Authorization": "Bearer op"}).json()
    assert len(state["payouts"]) == 6  # k1 once, four successes, the retried n2
