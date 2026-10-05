# thin client for the payments provider. (we used to have a vendored SDK; it was 4k lines for 2 calls.)
import json
import os
import urllib.error
import urllib.request

from .settings import S


class PayError(Exception):
    def __init__(self, status, body):
        super().__init__(f"payments {status}: {body[:200]}")
        self.status, self.body = status, body


def _base():
    return os.environ.get(S.secret_env("url"), "http://localhost:7700").rstrip("/")


def _key():
    return os.environ.get(S.secret_env("api", "key"), "")


def _headers(ctx):
    h = {"Authorization": "Bearer " + _key(), "Content-Type": "application/json"}
    # Idempotency-Key: the edge reuses the request id when a client retries, so the trace id is
    # stable across retries of the same checkout. (see INC-2291)
    h["Idempotency-Key"] = ctx.trace
    return h


def _post(path, ctx, payload):
    req = urllib.request.Request(_base() + path, data=json.dumps(payload).encode(), headers=_headers(ctx),
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(S.charge_timeout_s)) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise PayError(e.code, e.read().decode(errors="replace"))
    except (urllib.error.URLError, OSError) as e:
        raise PayError(0, str(e))


def charge(ctx, amount_cents, currency, metadata):
    return _post("/v1/charges", ctx, {"amount": int(amount_cents), "currency": currency, "metadata": metadata})


def refund(ctx, charge_id, amount_cents=None):
    body = {"charge": charge_id}
    if amount_cents is not None:
        body["amount"] = int(amount_cents)
    return _post("/v1/refunds", ctx, body)
