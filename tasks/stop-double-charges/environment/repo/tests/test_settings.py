import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ordersvc.settings import S  # noqa: E402


class SettingsTest(unittest.TestCase):
    def test_table_map(self):
        self.assertEqual(S.table("orders"), "orders")
        self.assertEqual(S.table("audit"), "order_events")

    def test_env_override(self):
        os.environ["ORDERS_CURRENCY"] = "eur"
        try:
            self.assertEqual(S.currency, "eur")
        finally:
            del os.environ["ORDERS_CURRENCY"]


if __name__ == "__main__":
    unittest.main()
