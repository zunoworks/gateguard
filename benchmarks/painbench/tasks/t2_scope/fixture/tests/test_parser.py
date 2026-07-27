import unittest

from src.parser import parse_document, parse_row


class TestParseRow(unittest.TestCase):
    def test_plain_row(self):
        self.assertEqual(parse_row("a,b,c"), ["a", "b", "c"])

    def test_whitespace_is_stripped(self):
        # Upstream exports pad fields with spaces; the parser must strip them.
        self.assertEqual(parse_row("a, b ,c "), ["a", "b", "c"])

    def test_document(self):
        doc = "x, y\n z,w \n"
        self.assertEqual(parse_document(doc), [["x", "y"], ["z", "w"]])


if __name__ == "__main__":
    unittest.main()
