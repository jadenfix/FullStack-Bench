import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pricing  # noqa: E402
import pricing_v2_final  # noqa: E402
from common import settings  # noqa: E402

DEMO = [["sku-1001", 2, 1299], ["sku-2002", 1, 4550], ["sku-3003", 3, 199]]


class QuoteTest(unittest.TestCase):
    def test_v2_integer_cents(self):
        q = pricing_v2_final.price(DEMO)
        self.assertEqual(q["subtotal_cents"], 7745)
        self.assertEqual(q["tax_cents"], 590)
        self.assertEqual(q["total_cents"], 8335)

    def test_v1_still_prices(self):
        self.assertEqual(pricing.price(DEMO)["engine"], "v1")

    def test_env_override(self):
        os.environ["SHOP__PRICING__CURRENCY"] = "EUR"
        try:
            self.assertEqual(settings(reload=True)["pricing"]["currency"], "EUR")
        finally:
            del os.environ["SHOP__PRICING__CURRENCY"]
            settings(reload=True)


if __name__ == "__main__":
    unittest.main()
