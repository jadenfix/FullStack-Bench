import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supportapi.settings import S  # noqa: E402


class SettingsTest(unittest.TestCase):
    def test_defaults_and_override(self):
        self.assertEqual(S.pool_max, "8")
        os.environ["SUPPORT_POOL_MAX"] = "3"
        try:
            self.assertEqual(S.pool_max, "3")
        finally:
            del os.environ["SUPPORT_POOL_MAX"]

    def test_port_alias(self):
        os.environ["PORT"] = "9999"
        try:
            self.assertEqual(S.port, "9999")
        finally:
            del os.environ["PORT"]


if __name__ == "__main__":
    unittest.main()
