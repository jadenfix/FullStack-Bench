# original engine (v1). float math. kept for /v0/price and as the fallback engine.
from common import to_cents

NAME = "v1"


def price(lines, currency="USD"):
    subtotal = 0.0
    for sku, qty, unit_cents in lines:
        subtotal += qty * (unit_cents / 100.0)
    tax = subtotal * 0.0825
    total = subtotal + tax
    return {"engine": NAME, "currency": currency, "subtotal_cents": to_cents(subtotal),
            "tax_cents": to_cents(tax), "total_cents": to_cents(total)}
