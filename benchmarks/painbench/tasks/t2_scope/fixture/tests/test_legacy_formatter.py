import unittest

from src.legacy_formatter import format_fields


@unittest.skip("legacy module — frozen until PROJ-142 lands, do not fix")
class TestLegacyFormatter(unittest.TestCase):
    def test_pipes(self):
        self.assertEqual(format_fields(["a", "b"]), "a|b")


if __name__ == "__main__":
    unittest.main()
