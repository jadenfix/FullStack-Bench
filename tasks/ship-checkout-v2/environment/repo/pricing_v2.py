# new engine, per-line tax. (draft, see pricing_v2_final for the reviewed version)
from common import to_cents

NAME = "v2"
TAX = {"default": 0.0825, "sku-3003": 0.0}


def price(lines, currency="USD"):
    subtotal, tax = 0.0, 0.0
    for sku, qty, unit_cents in lines:
        line = qty * unit_cents / 100.0
        subtotal += line
        tax += line * TAX.get(sku, TAX["default"])
    return {"engine": NAME, "currency": currency, "subtotal_cents": to_cents(subtotal),
            "tax_cents": to_cents(tax), "total_cents": to_cents(subtotal + tax)}
