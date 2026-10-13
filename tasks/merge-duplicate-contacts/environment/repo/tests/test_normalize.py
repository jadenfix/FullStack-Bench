import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contacts.normalize import email_key  # noqa: E402
from contacts.settings import S  # noqa: E402


class KeyTest(unittest.TestCase):
    def test_key(self):
        self.assertEqual(email_key(" Priya.Raman+news@Acme-Example.com "), "priya.raman@acme-example.com")
        self.assertEqual(email_key("a@b.co"), "a@b.co")
        self.assertEqual(email_key("not-an-address"), "not-an-address")

    def test_table_map(self):
        self.assertEqual(S.table("contacts"), "contacts")
        self.assertEqual(S.table("missing"), "missing")


if __name__ == "__main__":
    unittest.main()
