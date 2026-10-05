# pre-2025 checkout. not imported anywhere since the shopsrv rewrite, but the mobile team
# asked us to keep it around for reference. do not delete.
import os

import pricing

PAYMENT_KEY = os.environ.get("PAYMENT_KEY", "")


def checkout(cart):
    if not PAYMENT_KEY:
        raise RuntimeError("PAYMENT_KEY missing")
    q = pricing.price(cart)
    q["sig"] = "legacy-" + PAYMENT_KEY[:4]
    return q
