"""Run the SimSaaS providers a task uses, from a seed file.

    SIMSAAS_ADMIN_TOKEN=... SIMSAAS_SEED=/seed/simsaas.yaml simsaas

Seed:
    identity:                       # Passkeep ID (omit to disable)
      port: 7600
      issuer: http://simsaas:7600
      clients: [{client_id, redirect_uris: [...], scopes: [...], secret?: ..., audience?: ...}]
      users:   [{username, password, claims?: {...}}]
    payments:                       # Tillpoint (omit to disable)
      port: 7700
      accounts:  [{account, api_key}]
      endpoints: [{account, url, events?: [...], secret?: whsec_...}]
      faults:    {duplicate_every: 0, reorder: false}
      charges:   [{account, id, amount, currency, metadata?, created?, amount_refunded?}]  history
"""

import os
import sys
import threading
import time
from pathlib import Path

import uvicorn
import yaml

from .identity import Passkeep
from .identity import create_app as identity_app
from .payments import Tillpoint
from .payments import create_app as payments_app


def build(seed: dict, admin_token: str) -> list[tuple[object, int]]:
    apps = []
    if "identity" in seed:
        cfg = seed["identity"]
        pk = Passkeep(cfg.get("issuer", f"http://127.0.0.1:{cfg.get('port', 7600)}"))
        for c in cfg.get("clients", []):
            pk.add_client(c["client_id"], c["redirect_uris"], c.get("scopes", ["openid"]), c.get("secret"),
                          c.get("audience"))
        for u in cfg.get("users", []):
            pk.add_user(u["username"], u["password"], u.get("claims"))
        apps.append((identity_app(pk, admin_token), int(cfg.get("port", 7600))))
    if "payments" in seed:
        cfg = seed["payments"]
        tp = Tillpoint()
        for a in cfg.get("accounts", []):
            tp.add_account(a["account"], a["api_key"])
        for e in cfg.get("endpoints", []):
            tp.add_endpoint(e["account"], e["url"], e.get("events"), e.get("secret"))
        tp.state.faults.update(cfg.get("faults", {}))
        for ch in cfg.get("charges", []):
            tp.state.charges[ch["id"]] = {"id": ch["id"], "object": "charge", "amount": int(ch["amount"]),
                                          "currency": ch.get("currency", "usd"),
                                          "amount_refunded": int(ch.get("amount_refunded", 0)), "status": "succeeded",
                                          "metadata": ch.get("metadata", {}), "created": int(ch.get("created", 0)),
                                          "account": ch["account"]}

        def deliver():
            while True:
                try:
                    tp.deliver_due()
                except Exception as exc:  # keep delivering; log the failure
                    tp.log("delivery_loop_error", error=repr(exc)[:200])
                time.sleep(1)
        threading.Thread(target=deliver, daemon=True, name="tillpoint-webhooks").start()
        apps.append((payments_app(tp, admin_token), int(cfg.get("port", 7700))))
    return apps


def main() -> int:
    token = os.environ.get("SIMSAAS_ADMIN_TOKEN")
    seed_path = os.environ.get("SIMSAAS_SEED")
    if not token or not seed_path:
        print("SIMSAAS_ADMIN_TOKEN and SIMSAAS_SEED must be set", file=sys.stderr)
        return 2
    apps = build(yaml.safe_load(Path(seed_path).read_text()) or {}, token)
    if not apps:
        print("the seed enables no providers", file=sys.stderr)
        return 2
    servers = [uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")) for app, port in apps]
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers[1:]]
    for t in threads:
        t.start()
    servers[0].run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
