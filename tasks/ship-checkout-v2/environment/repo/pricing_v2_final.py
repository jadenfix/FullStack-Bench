# v2 engine after the rounding review: integer cents end to end, banker's rounding on tax.
from decimal import Decimal

from common import cents, conf

NAME = "v2"
TAX_BP = {"default": 825, "sku-3003": 0}  # basis points


def price(lines, currency="USD"):
    mode = conf("pricing.round", "half_even")
    subtotal = 0
    tax = Decimal(0)
    for sku, qty, unit_cents in lines:
        line = int(qty) * int(unit_cents)
        subtotal += line
        tax += Decimal(line) * TAX_BP.get(sku, TAX_BP["default"]) / Decimal(10000)
    tax_c = cents(tax, mode)
    return {"engine": NAME, "currency": currency, "subtotal_cents": subtotal, "tax_cents": tax_c,
            "total_cents": subtotal + tax_c}
