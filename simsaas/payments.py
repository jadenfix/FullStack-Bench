"""Tillpoint: a fictional payments provider.

- `POST /v1/charges` and `POST /v1/refunds` take an `Idempotency-Key` header. The same key with
  the same body replays the original response; the same key with a different body is 422; a key
  still being processed is 409.
- Amounts are integer minor units (`amount` in cents) with a currency.
- Refunds can't exceed what's left of a charge.
- Events (`charge.succeeded`, `charge.refunded`, ...) are delivered to registered webhook
  endpoints, signed per the Standard Webhooks spec: headers `webhook-id`, `webhook-timestamp`,
  `webhook-signature: v1,<base64 HMAC-SHA256 of "id.timestamp.body">`. A non-2xx response is
  retried with backoff (5 s, 25 s, 125 s ... up to 6 attempts).
- `POST /v1/payouts` sends money to a destination (same Idempotency-Key rules); `GET /v1/payouts`
  lists them newest first (`limit`, `starting_after`).
- Delivery faults the operator can enable: `duplicate_rate` (some events are delivered twice)
  and `reorder` (a batch is delivered newest-first). Real providers do both.
- Request faults: `{"type": "errors_every_nth", "path": "/v1/payouts", "n": 7, "status": 503}` makes
  every nth POST to that path fail with that status before anything is created (a 5xx also frees
  the idempotency key, so a retry with the same key is processed normally).

The operator event log (`GET /admin/events`) records charges, refunds and every delivery attempt.
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

RETRY_DELAYS = [5, 25, 125, 625, 3125]


def sign(secret: str, msg_id: str, ts: int, body: bytes) -> str:
    key = base64.b64decode(secret.split("_", 1)[1]) if secret.startswith("whsec_") else secret.encode()
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


@dataclass
class Endpoint:
    id: str
    url: str
    secret: str
    events: list[str]


@dataclass
class PayState:
    api_keys: dict = field(default_factory=dict)        # key -> account
    charges: dict = field(default_factory=dict)
    refunds: dict = field(default_factory=dict)
    payouts: dict = field(default_factory=dict)
    request_counts: dict = field(default_factory=dict)  # path -> POSTs seen (for errors_every_nth)
    idem: dict = field(default_factory=dict)            # (account, key) -> {fingerprint, status, response}
    endpoints: dict = field(default_factory=dict)       # account -> [Endpoint]
    deliveries: list = field(default_factory=list)      # pending: {endpoint, event, attempt, due}
    events: list = field(default_factory=list)          # operator log
    faults: dict = field(default_factory=lambda: {"duplicate_every": 0, "reorder": False, "errors_every_nth": []})


class Tillpoint:
    def __init__(self, clock=time.time, http: httpx.Client | None = None):
        self.now = clock
        self.state = PayState()
        self.http = http or httpx.Client(timeout=10)
        self._lock = threading.Lock()
        self._seq = 0

    def add_account(self, account: str, api_key: str) -> None:
        self.state.api_keys[api_key] = account

    def add_endpoint(self, account: str, url: str, events: list[str] | None = None, secret: str | None = None) -> Endpoint:
        ep = Endpoint("we_" + secrets.token_hex(6), url,
                      secret or "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode(), events or ["*"])
        self.state.endpoints.setdefault(account, []).append(ep)
        return ep

    def log(self, kind: str, **detail) -> None:
        self.state.events.append({"ts": self.now(), "event": kind, **detail})

    def _emit(self, account: str, type_: str, obj: dict) -> None:
        self._seq += 1
        event = {"id": f"evt_{self._seq:06d}_{secrets.token_hex(3)}", "type": type_, "created": int(self.now()),
                 "data": {"object": obj}}
        for ep in self.state.endpoints.get(account, []):
            if "*" in ep.events or type_ in ep.events:
                self.state.deliveries.append({"endpoint": ep, "event": event, "attempt": 0, "due": self.now()})
                dup_every = self.state.faults.get("duplicate_every") or 0
                if dup_every and self._seq % dup_every == 0:
                    self.state.deliveries.append({"endpoint": ep, "event": event, "attempt": 0, "due": self.now(),
                                                  "duplicate": True})

    def deliver_due(self) -> int:
        """Deliver every due webhook once (the server calls this in a loop). Returns attempts made."""
        with self._lock:
            due = [d for d in self.state.deliveries if d["due"] <= self.now()]
            self.state.deliveries = [d for d in self.state.deliveries if d["due"] > self.now()]
        if self.state.faults.get("reorder"):
            due.reverse()
        for d in due:
            ep, event = d["endpoint"], d["event"]
            body = json.dumps(event, separators=(",", ":")).encode()
            ts = int(self.now())
            headers = {"content-type": "application/json", "webhook-id": event["id"], "webhook-timestamp": str(ts),
                       "webhook-signature": sign(ep.secret, event["id"], ts, body)}
            try:
                status = self.http.post(ep.url, content=body, headers=headers).status_code
            except httpx.HTTPError:
                status = 0
            d["attempt"] += 1
            self.log("webhook_delivery", endpoint=ep.id, event_id=event["id"], type=event["type"],
                     attempt=d["attempt"], status=status, duplicate=d.get("duplicate", False))
            if not 200 <= status < 300 and d["attempt"] <= len(RETRY_DELAYS):
                d["due"] = self.now() + RETRY_DELAYS[d["attempt"] - 1]
                with self._lock:
                    self.state.deliveries.append(d)
        return len(due)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(tp: Tillpoint, admin_token: str) -> FastAPI:
    app = FastAPI(title="Tillpoint")
    st = tp.state

    def account(authorization: str | None) -> str | None:
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        return st.api_keys.get(authorization[7:])

    def injected_error(path: str) -> JSONResponse | None:
        rules = [r for r in st.faults.get("errors_every_nth") or [] if r.get("path") == path and r.get("n", 0) > 0]
        if not rules:
            return None
        with tp._lock:
            st.request_counts[path] = n = st.request_counts.get(path, 0) + 1
        for r in rules:
            if n % r["n"] == 0:
                tp.log("fault_injected", path=path, request=n, status=r.get("status", 503))
                return _err(r.get("status", 503), "unavailable", "temporarily unavailable, retry later")
        return None

    async def idempotent(request: Request, acct: str, key: str | None, handler):
        fault = injected_error(request.url.path)
        if fault is not None:
            return fault
        body = await request.body()
        if key is None:
            return handler(json.loads(body or b"{}"))
        fingerprint = hashlib.sha256(request.url.path.encode() + b"|" + body).hexdigest()
        with tp._lock:
            rec = st.idem.get((acct, key))
            if rec is None:
                st.idem[(acct, key)] = {"fingerprint": fingerprint, "status": "processing", "response": None}
            elif rec["fingerprint"] != fingerprint:
                return _err(422, "idempotency_key_reused", "this key was used with a different request")
            elif rec["status"] == "processing":
                return _err(409, "idempotency_key_in_use", "a request with this key is still being processed")
            else:
                tp.log("idempotent_replay", account=acct, key=key)
                return JSONResponse(rec["response"]["body"], status_code=rec["response"]["status"],
                                    headers={"idempotent-replayed": "true"})
        resp = handler(json.loads(body or b"{}"))
        with tp._lock:
            if resp.status_code >= 500:
                st.idem.pop((acct, key), None)  # retryable: let the client try again with the same key
            else:
                st.idem[(acct, key)] = {"fingerprint": fingerprint, "status": "done",
                                        "response": {"status": resp.status_code, "body": json.loads(resp.body)}}
        return resp

    @app.post("/v1/charges")
    async def create_charge(request: Request, authorization: str | None = Header(None),
                            idempotency_key: str | None = Header(None)):
        acct = account(authorization)
        if acct is None:
            return _err(401, "unauthenticated", "invalid API key")

        def handle(body):
            amount, currency = body.get("amount"), body.get("currency")
            if not isinstance(amount, int) or amount <= 0 or not isinstance(currency, str) or len(currency) != 3:
                return _err(400, "invalid_request", "amount must be a positive integer in minor units; currency a "
                                                    "3-letter code")
            ch = {"id": "ch_" + secrets.token_hex(8), "object": "charge", "amount": amount, "currency": currency.lower(),
                  "amount_refunded": 0, "status": "succeeded", "metadata": body.get("metadata", {}),
                  "created": int(tp.now())}
            st.charges[ch["id"]] = {**ch, "account": acct}
            tp.log("charge_created", account=acct, charge=ch["id"], amount=amount, currency=ch["currency"],
                   metadata=ch["metadata"])
            tp._emit(acct, "charge.succeeded", ch)
            return JSONResponse(ch, status_code=201)
        return await idempotent(request, acct, idempotency_key, handle)

    @app.post("/v1/refunds")
    async def create_refund(request: Request, authorization: str | None = Header(None),
                            idempotency_key: str | None = Header(None)):
        acct = account(authorization)
        if acct is None:
            return _err(401, "unauthenticated", "invalid API key")

        def handle(body):
            ch = st.charges.get(body.get("charge", ""))
            if not ch or ch["account"] != acct:
                return _err(404, "not_found", "no such charge")
            amount = body.get("amount", ch["amount"] - ch["amount_refunded"])
            if not isinstance(amount, int) or amount <= 0 or ch["amount_refunded"] + amount > ch["amount"]:
                return _err(400, "invalid_request", "refund exceeds the remaining charge amount")
            ch["amount_refunded"] += amount
            rf = {"id": "re_" + secrets.token_hex(8), "object": "refund", "charge": ch["id"], "amount": amount,
                  "currency": ch["currency"], "status": "succeeded", "created": int(tp.now())}
            st.refunds[rf["id"]] = rf
            tp.log("refund_created", account=acct, refund=rf["id"], charge=ch["id"], amount=amount)
            tp._emit(acct, "charge.refunded", {k: v for k, v in ch.items() if k != "account"})
            return JSONResponse(rf, status_code=201)
        return await idempotent(request, acct, idempotency_key, handle)

    @app.post("/v1/payouts")
    async def create_payout(request: Request, authorization: str | None = Header(None),
                            idempotency_key: str | None = Header(None)):
        acct = account(authorization)
        if acct is None:
            return _err(401, "unauthenticated", "invalid API key")

        def handle(body):
            amount, currency, dest = body.get("amount"), body.get("currency"), body.get("destination")
            if not isinstance(amount, int) or amount <= 0 or not isinstance(currency, str) or len(currency) != 3:
                return _err(400, "invalid_request", "amount must be a positive integer in minor units; currency a "
                                                    "3-letter code")
            if not isinstance(dest, str) or not dest:
                return _err(400, "invalid_request", "destination is required")
            po = {"id": "po_" + secrets.token_hex(8), "object": "payout", "amount": amount,
                  "currency": currency.lower(), "destination": dest, "status": "paid",
                  "metadata": body.get("metadata", {}), "created": int(tp.now())}
            st.payouts[po["id"]] = {**po, "account": acct}
            tp.log("payout_created", account=acct, payout=po["id"], amount=amount, currency=po["currency"],
                   destination=dest, metadata=po["metadata"])
            tp._emit(acct, "payout.paid", po)
            return JSONResponse(po, status_code=201)
        return await idempotent(request, acct, idempotency_key, handle)

    @app.get("/v1/payouts")
    def list_payouts(authorization: str | None = Header(None), limit: int = 100, starting_after: str | None = None):
        acct = account(authorization)
        if acct is None:
            return _err(401, "unauthenticated", "invalid API key")
        items = [{k: v for k, v in p.items() if k != "account"} for p in reversed(list(st.payouts.values()))
                 if p["account"] == acct]
        if starting_after:
            ids = [p["id"] for p in items]
            items = items[ids.index(starting_after) + 1:] if starting_after in ids else []
        limit = max(1, min(limit, 1000))
        return {"data": items[:limit], "has_more": len(items) > limit}

    @app.get("/v1/charges/{charge_id}")
    def get_charge(charge_id: str, authorization: str | None = Header(None)):
        acct = account(authorization)
        ch = st.charges.get(charge_id)
        if acct is None:
            return _err(401, "unauthenticated", "invalid API key")
        if not ch or ch["account"] != acct:
            return _err(404, "not_found", "no such charge")
        return {k: v for k, v in ch.items() if k != "account"}

    @app.get("/v1/charges")
    def list_charges(authorization: str | None = Header(None), limit: int = 100):
        acct = account(authorization)
        if acct is None:
            return _err(401, "unauthenticated", "invalid API key")
        items = [{k: v for k, v in c.items() if k != "account"} for c in st.charges.values() if c["account"] == acct]
        return {"data": items[-limit:]}

    @app.get("/admin/events")
    def admin_events(authorization: str | None = Header(None)):
        if authorization != f"Bearer {admin_token}":
            return _err(403, "forbidden", "operator only")
        return {"events": st.events, "pending_deliveries": len(st.deliveries)}

    @app.get("/admin/state")
    def admin_state(authorization: str | None = Header(None)):
        if authorization != f"Bearer {admin_token}":
            return _err(403, "forbidden", "operator only")
        return {"charges": list(st.charges.values()), "refunds": list(st.refunds.values()),
                "payouts": list(st.payouts.values())}

    @app.put("/admin/faults")
    def admin_faults(faults: dict, authorization: str | None = Header(None)):
        if authorization != f"Bearer {admin_token}":
            return _err(403, "forbidden", "operator only")
        if faults.get("type") == "errors_every_nth":
            rule = {k: faults[k] for k in ("path", "n", "status") if k in faults}
            st.faults.setdefault("errors_every_nth", []).append(rule)
        else:
            st.faults.update(faults)
        return st.faults

    return app
